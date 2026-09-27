#!/usr/bin/env python3
"""restic-fleet dashboard: tracks backups across a fleet of restic clients.

Clients POST the outcome of every backup job to /api/v1/report (per-client bearer token);
the backup server POSTs repository statistics and maintenance results to /api/v1/server-report.
Humans sign in and watch the fleet at /. Python 3.9+ standard library only.

    restic_fleet_dashboard.py serve                  run the web server
    restic_fleet_dashboard.py set-password USER      create/update a user (password on stdin)
    restic_fleet_dashboard.py verify-password USER   exit 0 if stdin matches USER's password
    restic_fleet_dashboard.py delete-user USER
    restic_fleet_dashboard.py users

Environment: RFD_CONFIG (default /etc/restic-fleet/dashboard.json),
             RFD_STATE_DIR (default /var/lib/restic-fleet-dashboard).
"""

from __future__ import annotations

import base64
import getpass
import gzip
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"
CONFIG_PATH = os.environ.get("RFD_CONFIG", "/etc/restic-fleet/dashboard.json")
STATE_DIR = os.environ.get("RFD_STATE_DIR", "/var/lib/restic-fleet-dashboard")

MAX_BODY = 64 * 1024
SESSION_SECONDS = 12 * 3600
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_FAILURES = 10
RUNNING_STALE_SECONDS = 24 * 3600      # a "running" report older than this is treated as dead
HISTORY_DAYS = 30
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
RUN_STATUSES = {"running", "success", "warning", "failed"}
SEVERITY = {"success": 0, "warning": 1, "failed": 2}


# ----------------------------------------------------------------------------- config & users
class Config:
    def __init__(self, path: str = CONFIG_PATH):
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.fleet_name: str = raw.get("fleet_name", "restic-fleet")
        self.listen: str = raw.get("listen", "127.0.0.1")
        self.port: int = int(raw.get("port", 8443))
        self.tls_cert: str | None = raw.get("tls_cert") or None
        self.tls_key: str | None = raw.get("tls_key") or None
        self.trusted_proxy: bool = bool(raw.get("trusted_proxy", False))
        self.clients: dict[str, dict] = raw.get("clients", {})
        self.server_token_sha256: str = raw.get("server_token_sha256", "")
        self.metrics_token_sha256: str = raw.get("metrics_token_sha256", "")
        notify = raw.get("notify", {})
        self.telegram_token: str = notify.get("telegram_token", "")
        self.telegram_chat_ids: list[str] = [str(c) for c in notify.get("telegram_chat_ids", [])]
        self.webhook_url: str = notify.get("webhook_url", "")
        self.overdue_factor: float = float(raw.get("overdue_factor", 1.5))

    @property
    def tls(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    def client_for_token(self, token: str) -> str | None:
        digest = sha256(token)
        found = None
        for name, meta in self.clients.items():   # constant-time compare against every client
            if hmac.compare_digest(digest, meta.get("token_sha256", "")):
                found = name
        return found

    def is_server_token(self, token: str) -> bool:
        return bool(self.server_token_sha256) and hmac.compare_digest(sha256(token), self.server_token_sha256)

    def is_metrics_token(self, token: str) -> bool:
        return bool(self.metrics_token_sha256) and hmac.compare_digest(sha256(token), self.metrics_token_sha256)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _users_path() -> str:
    return os.path.join(STATE_DIR, "users.json")


def load_users() -> dict[str, str]:
    try:
        with open(_users_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def save_users(users: dict[str, str]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = _users_path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2)
    os.replace(tmp, _users_path())


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt$15$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, log_n, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2 ** int(log_n), r=8, p=1,
                                maxmem=64 * 1024 * 1024, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(base64.b64encode(digest).decode(), digest_b64)


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def session_secret() -> bytes:
    path = os.path.join(STATE_DIR, "session.key")
    try:
        with open(path, "rb") as fh:
            key = fh.read()
        if len(key) >= 32:
            return key
    except FileNotFoundError:
        pass
    os.makedirs(STATE_DIR, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, key)
    os.close(fd)
    return key


# ----------------------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    host TEXT NOT NULL, job TEXT NOT NULL, run_id TEXT NOT NULL,
    status TEXT NOT NULL, started REAL NOT NULL, finished REAL,
    data_added INTEGER, bytes_processed INTEGER, files_new INTEGER, files_changed INTEGER,
    snapshot_id TEXT, message TEXT,
    UNIQUE (host, run_id, job)
);
CREATE INDEX IF NOT EXISTS runs_host_started ON runs (host, started DESC);
CREATE INDEX IF NOT EXISTS runs_started ON runs (started DESC);
CREATE TABLE IF NOT EXISTS repos (
    host TEXT PRIMARY KEY, updated REAL NOT NULL, size_bytes INTEGER,
    snapshots INTEGER, last_snapshot REAL
);
CREATE TABLE IF NOT EXISTS maintenance (
    host TEXT PRIMARY KEY, ts REAL NOT NULL, ok INTEGER NOT NULL, message TEXT
);
CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY, ts REAL NOT NULL);
"""


class Store:
    """SQLite in WAL mode; one connection per thread; writers serialised by a lock."""

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self.version = 0
        with self._write_lock:
            conn = self.conn()
            conn.executescript(SCHEMA)
            conn.commit()

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def write(self, sql: str, params: tuple = ()) -> None:
        with self._write_lock:
            self.conn().execute(sql, params)
            self.version += 1

    def write_many(self, statements: list[tuple[str, tuple]]) -> None:
        with self._write_lock:
            conn = self.conn()
            conn.execute("BEGIN")
            try:
                for sql, params in statements:
                    conn.execute(sql, params)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            self.version += 1

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def prune(self, keep_days: int = 400) -> None:
        self.write("DELETE FROM runs WHERE started < ?", (time.time() - keep_days * 86400,))


# ----------------------------------------------------------------------------- validation
class BadRequest(Exception):
    pass


def _int(value, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 2 ** 62:
        raise BadRequest(f"{field} must be a non-negative number")
    return int(value)


def _ts(value, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (946684800 <= value <= 4102444800):
        raise BadRequest(f"{field} must be a unix timestamp")
    return float(value)


def _text(value, field: str, limit: int, pattern: re.Pattern | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise BadRequest(f"{field} is required (max {limit} chars)")
    if pattern and not pattern.match(value):
        raise BadRequest(f"{field} has invalid characters")
    return value


def parse_report(body: dict) -> dict:
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    status = body.get("status")
    if status not in RUN_STATUSES:
        raise BadRequest("status must be one of " + ", ".join(sorted(RUN_STATUSES)))
    started = _ts(body.get("started"), "started")
    if started is None:
        raise BadRequest("started is required")
    finished = _ts(body.get("finished"), "finished")
    if status != "running" and finished is None:
        finished = time.time()
    message = body.get("message") or ""
    if not isinstance(message, str):
        raise BadRequest("message must be a string")
    snapshot = body.get("snapshot_id") or None
    if snapshot is not None and not re.fullmatch(r"[0-9a-f]{8,64}", str(snapshot)):
        raise BadRequest("snapshot_id must be hex")
    return {
        "run_id": _text(body.get("run_id"), "run_id", 64, re.compile(r"^[A-Za-z0-9_.:-]+$")),
        "job": _text(body.get("job"), "job", 80, JOB_RE),
        "status": status, "started": started, "finished": finished,
        "data_added": _int(body.get("data_added"), "data_added"),
        "bytes_processed": _int(body.get("bytes_processed"), "bytes_processed"),
        "files_new": _int(body.get("files_new"), "files_new"),
        "files_changed": _int(body.get("files_changed"), "files_changed"),
        "snapshot_id": snapshot, "message": message[:4000],
    }


# ----------------------------------------------------------------------------- fleet state
def host_state(cfg: Config, store: Store, name: str, now: float) -> dict:
    meta = cfg.clients.get(name, {})
    interval_h = float(meta.get("expected_interval_hours", 24))
    rows = store.query(
        "SELECT job, run_id, status, started, finished, data_added, bytes_processed, files_new, files_changed,"
        " snapshot_id, message FROM runs WHERE host = ? AND started >= ? ORDER BY started DESC",
        (name, now - HISTORY_DAYS * 86400))
    runs = [dict(r) for r in rows]
    for r in runs:   # a "running" report nobody finished within a day is a dead run
        if r["status"] == "running" and now - r["started"] > RUNNING_STALE_SECONDS:
            r["status"], r["message"] = "failed", r["message"] or "no final report (the backup was interrupted)"

    latest_by_job: dict[str, dict] = {}
    for r in runs:
        latest_by_job.setdefault(r["job"], r)
    last_success = store.query(
        "SELECT MAX(COALESCE(finished, started)) AS t FROM runs WHERE host = ? AND status IN ('success','warning')",
        (name,))[0]["t"]
    running = any(r["status"] == "running" for r in latest_by_job.values())
    finished_latest = [r for r in latest_by_job.values() if r["status"] != "running"]
    worst = max((SEVERITY[r["status"]] for r in finished_latest), default=None)
    overdue = (last_success is None and runs and now - runs[-1]["started"] > interval_h * 3600 * cfg.overdue_factor) \
        or (last_success is not None and now - last_success > interval_h * 3600 * cfg.overdue_factor)

    if running:
        status = "running"
    elif worst == 2:
        status = "failed"
    elif overdue:
        status = "overdue"
    elif worst == 1:
        status = "warning"
    elif worst == 0:
        status = "ok"
    else:
        status = "never"

    days: dict[str, int] = {}
    for r in runs:
        if r["status"] == "running":
            continue
        day = datetime.fromtimestamp(r["started"], timezone.utc).strftime("%Y-%m-%d")
        days[day] = max(days.get(day, -1), SEVERITY[r["status"]])
    today = datetime.fromtimestamp(now, timezone.utc).date()
    history = []
    for i in range(HISTORY_DAYS - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        history.append({"day": d, "s": {0: "success", 1: "warning", 2: "failed"}.get(days.get(d, -1), "none")})

    repo = store.query("SELECT updated, size_bytes, snapshots, last_snapshot FROM repos WHERE host = ?", (name,))
    maint = store.query("SELECT ts, ok, message FROM maintenance WHERE host = ?", (name,))
    return {
        "name": name, "status": status, "overdue": bool(overdue), "expected_interval_hours": interval_h,
        "last_success": last_success,
        "jobs": sorted(latest_by_job.values(), key=lambda r: r["job"]),
        "recent": runs[:25], "history": history,
        "repo": dict(repo[0]) if repo else None,
        "maintenance": {**dict(maint[0]), "ok": bool(maint[0]["ok"])} if maint else None,
    }


def fleet_state(cfg: Config, store: Store, now: float | None = None) -> dict:
    now = now or time.time()
    names = sorted(set(cfg.clients) | {r["host"] for r in store.query("SELECT DISTINCT host FROM runs")})
    hosts = [host_state(cfg, store, n, now) for n in names if n in cfg.clients]
    counts: dict[str, int] = {}
    for h in hosts:
        counts[h["status"]] = counts.get(h["status"], 0) + 1
    stored = sum((h["repo"] or {}).get("size_bytes") or 0 for h in hosts)
    events = [dict(r) for r in store.query(
        "SELECT host, job, status, started, finished, data_added, message FROM runs "
        "WHERE status != 'running' ORDER BY COALESCE(finished, started) DESC LIMIT 40")]
    return {"fleet": cfg.fleet_name, "version": VERSION, "generated": now, "counts": counts,
            "stored_bytes": stored, "hosts": hosts, "events": events}


# ----------------------------------------------------------------------------- notifications
class Notifier:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store

    @property
    def enabled(self) -> bool:
        return bool((self.cfg.telegram_token and self.cfg.telegram_chat_ids) or self.cfg.webhook_url)

    def once(self, key: str, text: str) -> None:
        """Send `text` unless an alert with this key was already sent."""
        if not self.enabled:
            return
        with self.store._write_lock:
            cur = self.store.conn().execute("INSERT OR IGNORE INTO alerts (key, ts) VALUES (?, ?)", (key, time.time()))
            if cur.rowcount == 0:
                return
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def forget(self, prefix: str) -> None:
        self.store.write("DELETE FROM alerts WHERE key LIKE ?", (prefix.replace("%", "") + "%",))

    def _send(self, text: str) -> None:
        text = f"[{self.cfg.fleet_name}] {text}"
        for chat in self.cfg.telegram_chat_ids:
            _post(f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                  urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode(),
                  "application/x-www-form-urlencoded")
        if self.cfg.webhook_url:
            _post(self.cfg.webhook_url, json.dumps({"text": text, "fleet": self.cfg.fleet_name}).encode(),
                  "application/json")


def _post(url: str, data: bytes, ctype: str) -> None:
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype, "User-Agent": "restic-fleet"})
        urllib.request.urlopen(req, timeout=15).read()
    except OSError as exc:
        print(f"notification failed: {type(exc).__name__}", file=sys.stderr, flush=True)


def watchdog_loop(cfg: Config, store: Store, notifier: Notifier) -> None:
    """Overdue and recovery alerts; runs once a minute."""
    while True:
        try:
            now = time.time()
            for h in fleet_state(cfg, store, now)["hosts"]:
                if h["status"] == "overdue":
                    since = h["last_success"] or 0
                    ago = "never succeeded" if not since else f"last success {_ago(now - since)} ago"
                    notifier.once(f"overdue:{h['name']}:{int(since)}", f"⏰ {h['name']}: backup overdue ({ago})")
                elif h["status"] == "ok":
                    had = store.query("SELECT 1 FROM alerts WHERE key LIKE ? OR key LIKE ? LIMIT 1",
                                      (f"overdue:{h['name']}:%", f"failed:{h['name']}:%"))
                    if had:
                        notifier.forget(f"overdue:{h['name']}:")
                        notifier.forget(f"failed:{h['name']}:")
                        notifier.once(f"recovered:{h['name']}:{int(now)}", f"✅ {h['name']}: backups are healthy again")
            if int(now) % 86400 < 60:
                store.prune()
        except Exception as exc:   # the watchdog must never die
            print(f"watchdog error: {exc!r}", file=sys.stderr, flush=True)
        time.sleep(60)


def _ago(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


# ----------------------------------------------------------------------------- metrics
def metrics_text(cfg: Config, store: Store) -> str:
    state = fleet_state(cfg, store)
    out = ["# HELP restic_fleet_host_status 1 for the host's current status.", "# TYPE restic_fleet_host_status gauge"]
    for h in state["hosts"]:
        for s in ("ok", "running", "warning", "failed", "overdue", "never"):
            out.append(f'restic_fleet_host_status{{host="{h["name"]}",status="{s}"}} {int(h["status"] == s)}')
    out += ["# HELP restic_fleet_last_success_timestamp_seconds Last successful backup.",
            "# TYPE restic_fleet_last_success_timestamp_seconds gauge"]
    out += [f'restic_fleet_last_success_timestamp_seconds{{host="{h["name"]}"}} {h["last_success"] or 0}'
            for h in state["hosts"]]
    out += ["# HELP restic_fleet_repo_size_bytes Repository size on the backup server.",
            "# TYPE restic_fleet_repo_size_bytes gauge"]
    out += [f'restic_fleet_repo_size_bytes{{host="{h["name"]}"}} {(h["repo"] or {}).get("size_bytes") or 0}'
            for h in state["hosts"]]
    out += ["# HELP restic_fleet_repo_snapshots Snapshots in the repository.", "# TYPE restic_fleet_repo_snapshots gauge"]
    out += [f'restic_fleet_repo_snapshots{{host="{h["name"]}"}} {(h["repo"] or {}).get("snapshots") or 0}'
            for h in state["hosts"]]
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- web UI
# Static assets live next to this file in static/ (design tokens and components in app.css,
# see docs/design-system.md). They are read once at start-up and served from memory.
STATIC_DIR = os.environ.get("RFD_STATIC_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"))
STATIC_TYPES = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "logo.svg": "image/svg+xml",
    "favicon.svg": "image/svg+xml",
}


def load_static() -> dict[str, bytes]:
    files = {}
    for name in STATIC_TYPES:
        with open(os.path.join(STATIC_DIR, name), "rb") as fh:
            files[name] = fh.read()
    return files


STATIC = load_static()
_ASSET_V = hashlib.sha256(b"".join(STATIC[n] for n in sorted(STATIC))).hexdigest()[:10]


def page(title: str, body: str) -> bytes:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            f'<meta name="color-scheme" content="light dark">'
            f'<title>{html.escape(title)}</title>'
            f'<link rel="icon" href="/static/favicon.svg?v={_ASSET_V}" type="image/svg+xml">'
            f'<link rel="stylesheet" href="/static/app.css?v={_ASSET_V}"></head>'
            f'<body>{body}</body></html>').encode()


DASHBOARD_BODY = """
<header class="topbar"><div class="topbar-inner">
  <a class="brand" href="/"><img src="/static/logo.svg?v={v}" alt="" width="30" height="30">
    <span class="brand-name">restic<b>fleet</b></span></a>
  <span class="fleet-name" id="fleet"></span>
  <div class="topbar-end"><span class="live" id="live">Connecting…</span>
    <form method="post" action="/logout"><button class="btn btn-quiet" title="Signed in as {user}">Sign out</button></form></div>
</div></header>
<main class="page">
  <section class="summary" id="summary" aria-live="polite"></section>
  <div class="toolbar">
    <div class="segmented" id="filters" role="group" aria-label="Filter machines">
      <button type="button" data-filter="all" aria-pressed="true">All<span class="count"></span></button>
      <button type="button" data-filter="attention" aria-pressed="false">Needs attention<span class="count"></span></button>
      <button type="button" data-filter="healthy" aria-pressed="false">Healthy<span class="count"></span></button>
    </div>
    <label class="visually-hidden" for="search">Search machines</label>
    <input class="search" id="search" type="search" placeholder="Search machines or jobs  ( / )" autocomplete="off">
  </div>
  <div class="grid" id="grid"></div>
  <section class="section">
    <div class="section-head"><h2>Recent activity</h2><span class="muted">Newest first</span></div>
    <div class="table-wrap"><table><thead><tr><th>When</th><th>Machine</th><th>Job</th><th>Result</th>
      <th class="num">New data</th><th>Message</th></tr></thead><tbody id="events"></tbody></table></div>
  </section>
</main>
<footer class="page"><span>restic-fleet {version} · backups by <a href="https://restic.net" rel="noreferrer">restic</a></span>
  <span>Signed in as {user}</span></footer>
<dialog class="drawer" id="drawer" aria-labelledby="d-title">
  <div class="drawer-head"><h2 id="d-title"></h2><span id="d-pill"></span>
    <button class="btn" id="d-close" type="button">Close</button></div>
  <div class="drawer-body" id="d-body"></div>
</dialog>
<script src="/static/app.js?v={v}"></script>
"""


def login_page(error: str = "") -> bytes:
    err = f'<div class="alert" role="alert">{html.escape(error)}</div>' if error else ""
    return page("Sign in · restic-fleet", f"""
<main class="auth"><form class="auth-card" method="post" action="/login">
  <header><img src="/static/logo.svg?v={_ASSET_V}" alt="" width="44" height="44">
    <div><h1>Sign in to restic-fleet</h1><p>Backup status for your whole fleet.</p></div></header>
  {err}
  <div class="field"><label for="u">Username</label><input id="u" name="username" autocomplete="username" required autofocus></div>
  <div class="field"><label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required></div>
  <button class="btn btn-primary" type="submit">Sign in</button>
  <p class="auth-foot">Forgot the password? Run <span class="mono">./setup.sh info</span> on your admin machine.</p>
</form></main>""")


# ----------------------------------------------------------------------------- HTTP
class App:
    def __init__(self, cfg: Config, store: Store, notifier: Notifier):
        self.cfg, self.store, self.notifier = cfg, store, notifier
        self.secret = session_secret()
        self.failures: dict[str, list[float]] = {}
        self.failures_lock = threading.Lock()
        self.cookie_name = "__Host-rfsession" if cfg.tls else "rfsession"
        self._state_cache: tuple[str, bytes] | None = None

    # sessions
    def make_session(self, user: str) -> str:
        payload = f"{user}|{int(time.time()) + SESSION_SECONDS}"
        sig = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()

    def read_session(self, value: str) -> str | None:
        try:
            user, expires, sig = base64.urlsafe_b64decode(value.encode()).decode().rsplit("|", 2)
            good = hmac.new(self.secret, f"{user}|{expires}".encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, good) and int(expires) > time.time() and user in load_users():
                return user
        except (ValueError, UnicodeDecodeError, TypeError):
            pass
        return None

    def rate_limited(self, ip: str) -> bool:
        now = time.time()
        with self.failures_lock:
            recent = [t for t in self.failures.get(ip, []) if now - t < LOGIN_WINDOW]
            self.failures[ip] = recent
            if len(self.failures) > 10000:   # bound memory under a spray of source IPs
                self.failures = {k: v for k, v in self.failures.items() if v}
            return len(recent) >= LOGIN_MAX_FAILURES

    def note_failure(self, ip: str) -> None:
        with self.failures_lock:
            self.failures.setdefault(ip, []).append(time.time())

    # state JSON, cached per store version + minute (overdue depends on the clock)
    def state_json(self) -> tuple[str, bytes]:
        tag = f'"{self.store.version}-{int(time.time() // 60)}"'
        cached = self._state_cache
        if cached and cached[0] == tag:
            return cached
        body = json.dumps(fleet_state(self.cfg, self.store), separators=(",", ":")).encode()
        self._state_cache = (tag, body)
        return tag, body

    # ingestion
    def ingest_report(self, client: str, body: dict) -> None:
        r = parse_report(body)
        self.store.write(
            "INSERT INTO runs (host, job, run_id, status, started, finished, data_added, bytes_processed, files_new,"
            " files_changed, snapshot_id, message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (host, run_id, job) DO UPDATE SET status=excluded.status, finished=excluded.finished,"
            " data_added=excluded.data_added, bytes_processed=excluded.bytes_processed, files_new=excluded.files_new,"
            " files_changed=excluded.files_changed, snapshot_id=excluded.snapshot_id, message=excluded.message",
            (client, r["job"], r["run_id"], r["status"], r["started"], r["finished"], r["data_added"],
             r["bytes_processed"], r["files_new"], r["files_changed"], r["snapshot_id"], r["message"]))
        if r["status"] == "failed":
            self.notifier.once(f"failed:{client}:{r['run_id']}:{r['job']}",
                               f"❌ {client}: backup job '{r['job']}' failed\n{r['message'][:500]}")

    def ingest_server(self, body: dict) -> None:
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        statements = []
        now = time.time()
        for host, repo in (body.get("repos") or {}).items():
            if host not in self.cfg.clients or not isinstance(repo, dict):
                continue
            statements.append((
                "INSERT INTO repos (host, updated, size_bytes, snapshots, last_snapshot) VALUES (?,?,?,?,?)"
                " ON CONFLICT (host) DO UPDATE SET updated=excluded.updated, size_bytes=excluded.size_bytes,"
                " snapshots=COALESCE(excluded.snapshots, repos.snapshots),"
                " last_snapshot=COALESCE(excluded.last_snapshot, repos.last_snapshot)",
                (host, now, _int(repo.get("size_bytes"), "size_bytes"), _int(repo.get("snapshots"), "snapshots"),
                 _ts(repo.get("last_snapshot"), "last_snapshot"))))
        failures = []
        for host, m in (body.get("maintenance") or {}).items():
            if host not in self.cfg.clients or not isinstance(m, dict):
                continue
            ok = bool(m.get("ok"))
            msg = str(m.get("message") or "")[:4000]
            statements.append(("INSERT INTO maintenance (host, ts, ok, message) VALUES (?,?,?,?)"
                               " ON CONFLICT (host) DO UPDATE SET ts=excluded.ts, ok=excluded.ok, message=excluded.message",
                               (host, now, int(ok), msg)))
            if not ok:
                failures.append((host, msg))
        if statements:
            self.store.write_many(statements)
        for host, msg in failures:
            self.notifier.once(f"maintenance:{host}:{int(now // 3600)}",
                               f"🧹 {host}: repository maintenance failed\n{msg[:500]}")


def make_handler(app: App):
    cfg = app.cfg

    class Handler(BaseHTTPRequestHandler):
        server_version = "restic-fleet"
        sys_version = ""
        timeout = 30                      # drop idle / slow clients (slowloris)
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):   # keep logs quiet and free of secrets
            pass

        # helpers
        def client_ip(self) -> str:
            if cfg.trusted_proxy:
                fwd = self.headers.get("X-Forwarded-For", "")
                if fwd:
                    return fwd.split(",")[0].strip()
            return self.client_address[0]

        def send(self, status: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8",
                 headers: dict | None = None, cacheable: bool = False) -> None:
            if body and len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", ""):
                body = gzip.compress(body, compresslevel=5)
                headers = {**(headers or {}), "Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cacheable else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # same-origin (not no-referrer): with no-referrer, browsers send "Origin: null" on form
            # POSTs, which the cross-site check below would (rightly) refuse.
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                             "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            if cfg.tls:
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json_response(self, status: int, obj) -> None:
            self.send(status, json.dumps(obj).encode(), "application/json")

        def redirect(self, location: str, cookie: str | None = None) -> None:
            headers = {"Location": location}
            if cookie is not None:
                headers["Set-Cookie"] = cookie
            self.send(HTTPStatus.SEE_OTHER, headers=headers)

        def bearer(self) -> str:
            auth = self.headers.get("Authorization", "")
            return auth[7:].strip() if auth.startswith("Bearer ") else ""

        def read_body(self) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self.send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"too large", "text/plain")
                return None
            return self.rfile.read(length)

        def session_user(self) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == app.cookie_name:
                    return app.read_session(value)
            return None

        def same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None or origin == "null":
                referer = self.headers.get("Referer")
                if not referer:
                    return origin is None       # non-browser client with neither header
                origin = referer
            return urllib.parse.urlsplit(origin).netloc.lower() == (self.headers.get("Host") or "").lower()

        def cookie(self, value: str, max_age: int) -> str:
            secure = "; Secure" if cfg.tls else ""
            return f"{app.cookie_name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"

        # routes
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                return self.send(200, b"ok", "text/plain")
            if path.startswith("/static/") and path[8:] in STATIC:
                name = path[8:]
                return self.send(200, STATIC[name], STATIC_TYPES[name], cacheable=True)
            if path == "/metrics":
                if not app.cfg.metrics_token_sha256 or not cfg.is_metrics_token(self.bearer()):
                    return self.send(HTTPStatus.UNAUTHORIZED, b"unauthorized", "text/plain")
                return self.send(200, metrics_text(cfg, app.store).encode(), "text/plain; version=0.0.4")
            if path == "/login":
                return self.send(200, login_page())
            user = self.session_user()
            if path == "/api/v1/state":
                if not user:
                    return self.json_response(HTTPStatus.UNAUTHORIZED, {"error": "sign in"})
                tag, body = app.state_json()
                if self.headers.get("If-None-Match") == tag:
                    return self.send(HTTPStatus.NOT_MODIFIED, headers={"ETag": tag})
                return self.send(200, body, "application/json", headers={"ETag": tag})
            if path == "/":
                if not user:
                    return self.redirect("/login")
                body = (DASHBOARD_BODY.replace("{user}", html.escape(user)).replace("{v}", _ASSET_V)
                        .replace("{version}", VERSION))
                return self.send(200, page(cfg.fleet_name, body))
            self.send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            body = self.read_body()
            if body is None:
                return
            if path in ("/api/v1/report", "/api/v1/server-report"):
                token = self.bearer()
                try:
                    data = json.loads(body or b"{}")
                    if path == "/api/v1/report":
                        client = cfg.client_for_token(token) if token else None
                        if not client:
                            return self.json_response(HTTPStatus.UNAUTHORIZED, {"error": "bad token"})
                        app.ingest_report(client, data)
                    else:
                        if not token or not cfg.is_server_token(token):
                            return self.json_response(HTTPStatus.UNAUTHORIZED, {"error": "bad token"})
                        app.ingest_server(data)
                except (ValueError, BadRequest) as exc:
                    return self.json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:200]})
                return self.json_response(200, {"ok": True})
            if not self.same_origin():
                return self.send(HTTPStatus.FORBIDDEN, b"cross-site request refused", "text/plain")
            if path == "/logout":
                return self.redirect("/login", self.cookie("", 0))
            if path == "/login":
                ip = self.client_ip()
                if app.rate_limited(ip):
                    return self.send(HTTPStatus.TOO_MANY_REQUESTS, login_page("Too many attempts. Try again later."))
                form = urllib.parse.parse_qs(body.decode(errors="replace"))
                username = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                stored = load_users().get(username)
                ok = verify_password(password, stored or _DUMMY_HASH) and stored is not None
                if not ok:
                    app.note_failure(ip)
                    print(f"login failed for {username[:40]!r} from {ip}", file=sys.stderr, flush=True)
                    return self.send(HTTPStatus.UNAUTHORIZED, login_page("Wrong username or password."))
                return self.redirect("/", self.cookie(app.make_session(username), SESSION_SECONDS))
            self.send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    return Handler


def serve() -> None:
    cfg = Config()
    os.makedirs(STATE_DIR, exist_ok=True)
    store = Store(os.path.join(STATE_DIR, "state.db"))
    notifier = Notifier(cfg, store)
    app = App(cfg, store, notifier)
    httpd = ThreadingHTTPServer((cfg.listen, cfg.port), make_handler(app))
    httpd.daemon_threads = True
    if cfg.tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cfg.tls_cert, cfg.tls_key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=watchdog_loop, args=(cfg, store, notifier), daemon=True).start()
    scheme = "https" if cfg.tls else "http"
    print(f"restic-fleet dashboard {VERSION} on {scheme}://{cfg.listen}:{cfg.port}", flush=True)
    httpd.serve_forever()


# ----------------------------------------------------------------------------- CLI
def _read_password() -> str:
    if sys.stdin.isatty():
        first = getpass.getpass("Password: ")
        if first != getpass.getpass("Repeat: "):
            sys.exit("Passwords do not match.")
        return first
    return sys.stdin.readline().rstrip("\n")


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "serve":
        serve()
    elif cmd in ("set-password", "verify-password", "delete-user") and len(argv) == 3:
        user = argv[2]
        if not NAME_RE.match(user):
            sys.exit("Invalid username.")
        users = load_users()
        if cmd == "delete-user":
            users.pop(user, None)
            save_users(users)
        elif cmd == "verify-password":
            sys.exit(0 if user in users and verify_password(_read_password(), users[user]) else 1)
        else:
            password = _read_password()
            if len(password) < 12:
                sys.exit("Use at least 12 characters.")
            users[user] = hash_password(password)
            save_users(users)
            print(f"Password set for {user}.")
    elif cmd == "users":
        print("\n".join(sorted(load_users())) or "(no users)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
