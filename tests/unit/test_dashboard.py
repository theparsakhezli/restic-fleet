"""Unit tests for the restic-fleet dashboard (stdlib unittest; no third-party packages).

Run: python -m unittest discover -s tests/unit -v
"""
import http.client
import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "roles" / "dashboard" / "files" / "restic_fleet_dashboard.py"

TOKENS = {"web1": "web1-token-" + "a" * 30, "db1": "db1-token-" + "b" * 30}
SERVER_TOKEN = "server-token-" + "c" * 30
METRICS_TOKEN = "metrics-token-" + "d" * 30


def load_module(state_dir: str, config_path: str):
    os.environ["RFD_STATE_DIR"] = state_dir
    os.environ["RFD_CONFIG"] = config_path
    spec = importlib.util.spec_from_file_location("rfd", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        state = os.path.join(cls.tmp.name, "state")
        cfg_path = os.path.join(cls.tmp.name, "dashboard.json")
        cls.rfd_probe = load_module(state, cfg_path)   # for sha256 helper
        sha = cls.rfd_probe.sha256
        with open(cfg_path, "w") as fh:
            json.dump({
                "fleet_name": "test-fleet", "listen": "127.0.0.1", "port": 0,
                "clients": {"web1": {"token_sha256": sha(TOKENS["web1"]), "expected_interval_hours": 24},
                            "db1": {"token_sha256": sha(TOKENS["db1"]), "expected_interval_hours": 6}},
                "server_token_sha256": sha(SERVER_TOKEN), "metrics_token_sha256": sha(METRICS_TOKEN),
            }, fh)
        cls.rfd = load_module(state, cfg_path)
        os.makedirs(state, exist_ok=True)
        users = cls.rfd.load_users()
        users["admin"] = cls.rfd.hash_password("correct horse battery")
        cls.rfd.save_users(users)
        cls.cfg = cls.rfd.Config(cfg_path)
        cls.store = cls.rfd.Store(os.path.join(state, "state.db"))
        cls.notifier = cls.rfd.Notifier(cls.cfg, cls.store)
        cls.app = cls.rfd.App(cls.cfg, cls.store, cls.notifier)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.rfd.make_handler(cls.app))
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        conn.request(method, path, body=data, headers=headers or {})
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp, payload

    def report(self, client, **fields):
        body = {"run_id": "r1", "job": "files", "status": "success", "started": time.time() - 60,
                "finished": time.time(), **fields}
        return self.request("POST", "/api/v1/report", body,
                            {"Authorization": f"Bearer {TOKENS[client]}", "Content-Type": "application/json"})

    def login(self):
        form = urllib.parse.urlencode({"username": "admin", "password": "correct horse battery"}).encode()
        resp, _ = self.request("POST", "/login", form, {"Content-Type": "application/x-www-form-urlencoded",
                                                         "Origin": f"http://127.0.0.1:{self.port}",
                                                         "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(resp.status, 303)
        return resp.getheader("Set-Cookie").split(";")[0]

    # ------------------------------------------------------------------ tests
    def test_password_hashing(self):
        stored = self.rfd.hash_password("hunter2hunter2")
        self.assertTrue(self.rfd.verify_password("hunter2hunter2", stored))
        self.assertFalse(self.rfd.verify_password("wrong", stored))
        self.assertFalse(self.rfd.verify_password("x", "garbage"))

    def test_report_requires_valid_token(self):
        resp, _ = self.request("POST", "/api/v1/report", {"run_id": "x"}, {"Authorization": "Bearer nope"})
        self.assertEqual(resp.status, 401)
        resp, _ = self.request("POST", "/api/v1/report", {"run_id": "x"})
        self.assertEqual(resp.status, 401)

    def test_report_validation(self):
        resp, body = self.report("web1", status="exploded")
        self.assertEqual(resp.status, 400)
        resp, _ = self.report("web1", job="bad job name!")
        self.assertEqual(resp.status, 400)
        resp, _ = self.report("web1", data_added=-5)
        self.assertEqual(resp.status, 400)
        resp, _ = self.report("web1", snapshot_id="<script>")
        self.assertEqual(resp.status, 400)

    def test_client_cannot_impersonate_another_host(self):
        # the host comes from the token, never from the body
        resp, _ = self.report("web1", run_id="imp", host="db1")
        self.assertEqual(resp.status, 200)
        rows = self.store.query("SELECT host FROM runs WHERE run_id = 'imp'")
        self.assertEqual([r["host"] for r in rows], ["web1"])

    def test_state_requires_login_and_reflects_reports(self):
        resp, _ = self.request("GET", "/api/v1/state")
        self.assertEqual(resp.status, 401)
        self.report("web1", run_id="ok1", job="files", data_added=1234, snapshot_id="abcdef12")
        cookie = self.login()
        resp, body = self.request("GET", "/api/v1/state", headers={"Cookie": cookie})
        self.assertEqual(resp.status, 200)
        state = json.loads(body)
        web1 = next(h for h in state["hosts"] if h["name"] == "web1")
        self.assertIn(web1["status"], ("ok", "failed", "warning"))
        self.assertIsNotNone(web1["last_success"])
        # ETag round trip
        tag = resp.getheader("ETag")
        resp2, body2 = self.request("GET", "/api/v1/state", headers={"Cookie": cookie, "If-None-Match": tag})
        self.assertEqual(resp2.status, 304)
        self.assertEqual(body2, b"")

    def test_failed_then_status(self):
        self.report("db1", run_id="f1", job="db-appdb", status="failed", message="pg_dump: connection refused")
        state = self.rfd.fleet_state(self.cfg, self.store)
        db1 = next(h for h in state["hosts"] if h["name"] == "db1")
        self.assertEqual(db1["status"], "failed")
        # a later success on the same job clears it
        self.report("db1", run_id="f2", job="db-appdb", status="success", started=time.time() + 1,
                    finished=time.time() + 2)
        db1 = next(h for h in self.rfd.fleet_state(self.cfg, self.store)["hosts"] if h["name"] == "db1")
        self.assertNotEqual(db1["status"], "failed")

    def test_overdue(self):
        now = time.time()
        # db1 expects a backup every 6 h; pretend it's 12 h after the last success
        state = self.rfd.fleet_state(self.cfg, self.store, now + 12 * 3600)
        db1 = next(h for h in state["hosts"] if h["name"] == "db1")
        self.assertTrue(db1["overdue"])

    def test_stale_running_run_counts_as_failed(self):
        start = time.time() - 2 * 86400
        self.report("web1", run_id="stale", job="stalejob", status="running", started=start, finished=None)
        web1 = next(h for h in self.rfd.fleet_state(self.cfg, self.store)["hosts"] if h["name"] == "web1")
        job = next(j for j in web1["jobs"] if j["job"] == "stalejob")
        self.assertEqual(job["status"], "failed")

    def test_server_report(self):
        body = {"repos": {"web1": {"size_bytes": 5000, "snapshots": 3, "last_snapshot": time.time()},
                          "unknown-host": {"size_bytes": 1}},
                "maintenance": {"web1": {"ok": True, "message": "all good"}}}
        resp, _ = self.request("POST", "/api/v1/server-report", body, {"Authorization": f"Bearer {TOKENS['web1']}"})
        self.assertEqual(resp.status, 401, "a client token must not be accepted as the server token")
        resp, _ = self.request("POST", "/api/v1/server-report", body, {"Authorization": f"Bearer {SERVER_TOKEN}"})
        self.assertEqual(resp.status, 200)
        repo = self.store.query("SELECT size_bytes, snapshots FROM repos WHERE host='web1'")[0]
        self.assertEqual((repo["size_bytes"], repo["snapshots"]), (5000, 3))
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM repos WHERE host='unknown-host'")[0]["n"], 0)

    def test_login_rejects_cross_site_and_bad_password(self):
        form = urllib.parse.urlencode({"username": "admin", "password": "correct horse battery"}).encode()
        resp, _ = self.request("POST", "/login", form, {"Origin": "https://evil.example",
                                                         "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(resp.status, 403)
        bad = urllib.parse.urlencode({"username": "admin", "password": "nope"}).encode()
        resp, _ = self.request("POST", "/login", bad, {"Origin": f"http://127.0.0.1:{self.port}",
                                                        "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(resp.status, 401)

    def test_browser_login_flow(self):
        # What a browser does: load /login, then POST the form with the Origin it derives from the
        # page's Referrer-Policy. "no-referrer" would make that Origin "null" and break sign-in.
        resp, _ = self.request("GET", "/login")
        self.assertEqual(resp.getheader("Referrer-Policy"), "same-origin")
        form = urllib.parse.urlencode({"username": "admin", "password": "correct horse battery"}).encode()
        resp, _ = self.request("POST", "/login", form, {"Content-Type": "application/x-www-form-urlencoded",
                                                         "Origin": f"http://127.0.0.1:{self.port}",
                                                         "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(resp.status, 303)
        resp, _ = self.request("POST", "/login", form, {"Origin": "null", "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(resp.status, 403, "an opaque (null) origin must still be refused")

    def test_login_rate_limit(self):
        ip = "203.0.113.9"
        for _ in range(self.rfd.LOGIN_MAX_FAILURES):
            self.app.note_failure(ip)
        self.assertTrue(self.app.rate_limited(ip))
        self.assertFalse(self.app.rate_limited("203.0.113.10"))

    def test_forged_session_rejected(self):
        forged = self.app.make_session("admin")[:-4] + "AAAA"
        resp, _ = self.request("GET", "/api/v1/state", headers={"Cookie": f"{self.app.cookie_name}={forged}"})
        self.assertEqual(resp.status, 401)

    def test_security_headers_and_static(self):
        resp, body = self.request("GET", "/login")
        csp = resp.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        resp, body = self.request("GET", "/static/app.js")
        self.assertEqual(resp.status, 200)
        self.assertNotIn(b"innerHTML", body, "UI must build DOM with textContent only")

    def test_metrics_requires_token(self):
        resp, _ = self.request("GET", "/metrics")
        self.assertEqual(resp.status, 401)
        resp, body = self.request("GET", "/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
        self.assertEqual(resp.status, 200)
        self.assertIn(b"restic_fleet_last_success_timestamp_seconds", body)

    def test_body_size_limit(self):
        big = b"{" + b" " * (self.rfd.MAX_BODY + 10) + b"}"
        resp, _ = self.request("POST", "/api/v1/report", big, {"Authorization": f"Bearer {TOKENS['web1']}"})
        self.assertEqual(resp.status, 413)

    def test_notification_dedup(self):
        sent = []
        self.notifier._send = lambda text: sent.append(text)
        self.cfg.webhook_url = "https://example.invalid/hook"
        try:
            self.notifier.once("k1", "hello")
            self.notifier.once("k1", "hello again")
            time.sleep(0.2)
            self.assertEqual(sent, ["hello"])
        finally:
            self.cfg.webhook_url = ""


if __name__ == "__main__":
    unittest.main()
