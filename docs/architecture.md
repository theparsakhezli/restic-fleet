# Architecture

How the pieces fit together, for people extending restic-fleet or reviewing it.

- [Components](#components)
- [A backup run, step by step](#a-backup-run-step-by-step)
- [Server-side tasks](#server-side-tasks)
- [The dashboard](#the-dashboard)
- [Report API](#report-api)
- [Repository layout](#repository-layout)
- [Design principles](#design-principles)

## Components

```
                         ┌──────────────────────────── backup server ─────────────────────────────┐
                         │                                                                        │
 machine                 │  rest-server :8000                    restic-fleet-collect (10 min)    │
┌──────────────────┐     │  --append-only --private-repos        restic-fleet-maintain (weekly)   │
│ systemd timer    │     │  htpasswd (bcrypt)                        │  unlock · forget --prune   │
│  └ restic-fleet- │─────┼─► /srv/restic-fleet/<machine>/  ◄───────┘  check --read-data-subset  │
│    backup        │ TLS │                                                     │                  │
│    files, dumps  │     │  dashboard :8443  (rfdash)   ◄──── server report ───┘                  │
│                  │─────┼─► /api/v1/report   SQLite WAL  ──► Telegram / webhook                  │
│ rfleet           │     │   /  /api/v1/state  /metrics                                           │
└──────────────────┘     └────────────────────────────────────────────────────────────────────────┘
```

| Component | Language | Runs as | Role |
|---|---|---|---|
| `setup.sh` | bash | you, on the controller | Wizard: writes the inventory, runs Ansible. |
| `site.yml` + `roles/` | Ansible | controller → SSH | Installs and configures everything. Idempotent. |
| [rest-server](https://github.com/restic/rest-server) | Go (upstream) | `restic` user | Stores repositories over HTTPS. |
| `restic-fleet-backup` | bash | root, per run | Runs every job on a machine and reports each one. |
| `rfleet` | bash | root via sudo | restic with the machine's settings, plus `run`, `status`, `logs` and `db`. |
| `restic-fleet-server` | Python (stdlib) | `restic` user | `collect`: sizes and snapshot counts. `maintain`: unlock, forget/prune and check. |
| `restic_fleet_dashboard.py` | Python (stdlib) | `rfdash` user | Web UI, report API, alerts, metrics. |

### Roles

| Role | Hosts | Does |
|---|---|---|
| `fleet_common` | all | Shared defaults and variables. No tasks of its own beyond setup. |
| `restic_binary` | all | Downloads pinned restic, verifies SHA-256, installs it. |
| `fleet_tls` | server | Private CA and server certificate. Copies `ca.crt` to the controller. |
| `rest_server` | server | Installs rest-server, syncs logins, systemd unit, ufw rules. |
| `server_tasks` | server | Repository passwords (optional), collect and maintain timers. |
| `dashboard` | server | Dashboard program, static files, config, admin user, unit. |
| `client` | machines | Settings, credentials, dump scripts, hooks, timer, `rfleet`, repository init. |

`site.yml` runs the controller play (the credentials directory), then the server, then the
machines, and prints a summary.

## A backup run, step by step

1. `restic-fleet-backup.timer` fires (plus a random delay) and starts the oneshot service, at low
   CPU and IO priority. A `flock` makes sure only one run happens at a time.
2. The **pre-hook** runs, if there is one. If it fails, the run is reported and ends.
3. **Files job:** `restic backup --files-from paths.txt --exclude-file excludes.txt --tag files`.
   It reports `running` first, then the result with restic's JSON summary (data added, files new
   and changed, snapshot ID).
4. **Each database job:** `restic backup --stdin-from-command -- dumps/<job>.sh --stdin-filename
   <file> --tag db --tag <job>`. The dump's stdout streams into restic, and a non-zero exit fails
   the snapshot.
5. The **post-hook** runs (always, when the pre-hook succeeded).
6. The exit status is non-zero if any job failed, so `systemctl status` shows it too.

restic exit code 3 ("some files could not be read") is reported as **warning**, any other non-zero
code as **failed**.

## Server-side tasks

**`collect`** (every 10 minutes): measures each repository's size on disk. With repository passwords
(maintenance on), it also runs `restic snapshots --no-lock` to count snapshots. It never takes a
lock, so it can't block a backup. The results go to `/api/v1/server-report`.

**`maintain`** (weekly), for each repository:

1. `restic unlock` removes stale locks from killed runs;
2. `restic forget --prune --group-by host,paths,tags --keep-…` applies retention to each job;
3. `restic check --read-data-subset=5%` checks the structure and reads a sample of the real data.

Each step waits up to 1 hour for a lock. The results go to the dashboard, and a failure raises an alert.

## The dashboard

- A single-file, threaded `http.server` with TLS. It needs no dependencies.
- **Storage:** SQLite in WAL mode, with tables `runs`, `repos`, `maintenance` and `alerts`. Run history
  is kept for about 400 days.
- **State:** `/api/v1/state` computes the whole fleet view (statuses, 30-day history, events) and
  serves it with an `ETag`. The browser polls every 15 seconds, and an unchanged fleet costs a `304`.
- **Frontend:** plain JavaScript and CSS served from `/static/`. The page is built with DOM methods
  only, which keeps a strict CSP possible. See [Design system](design-system.md).
- **Watchdog:** a background thread checks for overdue machines and recoveries once a minute.
  Alerts are de-duplicated through the `alerts` table.

**Status rules**, per machine:

| Order | Status | Rule |
|---|---|---|
| 1 | `running` | a job reported `running` less than 24 h ago and hasn't finished |
| 2 | `failed` | the latest finished run of any job failed (or a `running` run is older than 24 h) |
| 3 | `overdue` | no success within `overdue_factor` (1.5) × `expected_interval_hours` |
| 4 | `warning` | the latest run of a job finished with a warning |
| 5 | `ok` | at least one success and none of the above |
| 6 | `never` | no reports yet |

The UI sorts machines by *need for attention*: failed, overdue, warning, running, never, ok.

## Report API

Machines report with a per-machine bearer token. The dashboard stores only its SHA-256 hash and
compares in constant time.

```http
POST /api/v1/report
Authorization: Bearer <report-token>
Content-Type: application/json

{
  "run_id": "1790000000-4821",          // same for all jobs of one run  [A-Za-z0-9_.:-]{1,64}
  "job": "files",                     // or "db-<name>", "pre-hook", "post-hook"
  "status": "success",                // running | success | warning | failed
  "started": 1790000000,              // unix seconds
  "finished": 1790000125,             // omit while running
  "data_added": 10485760,             // bytes (optional)
  "bytes_processed": 2147483648,      // (optional)
  "files_new": 12, "files_changed": 40,
  "snapshot_id": "4f2a9c1e…",         // hex (optional)
  "message": "…"                      // error text, max 4000 chars (optional)
}
```

The machine name comes from the token, never from the body. A repeated `(run_id, job)` updates
the earlier report. That's how `running` becomes `success`. Responses: `200 {"ok": true}`,
`400` invalid body (with the reason), `401` bad token, `413` body over 64 KiB.

`POST /api/v1/server-report` (server token) carries `repos` (size, snapshots, last snapshot) and
`maintenance` results. `GET /metrics` (metrics token) returns Prometheus text. `GET /healthz` returns
`ok` without authentication, for load balancers.

This API makes it easy to report from other tools. Anything that can `curl` with the machine's
token can add a job to the dashboard.

## Repository layout

```
restic-fleet/
├── setup.sh                     interactive wizard
├── site.yml                     the playbook
├── ansible.cfg
├── inventories/example/         a documented example fleet
├── roles/
│   ├── fleet_common/            defaults for every setting
│   ├── restic_binary/
│   ├── fleet_tls/
│   ├── rest_server/             + files/htpasswd-sync
│   ├── server_tasks/            + files/restic-fleet-server
│   ├── dashboard/               + files/restic_fleet_dashboard.py, files/static/
│   └── client/                  + files/restic-fleet-backup, files/rfleet, templates/dump.sh.j2
├── design/logo/                 logo files
├── docs/                        this documentation
└── tests/
    ├── unit/                    dashboard tests (stdlib unittest)
    └── e2e/                     Docker: server + 2 machines + controller, real deploy and restores
```

## Design principles

- **Secure by default, not by configuration.** Append-only, private repositories, TLS, hashed
  tokens and sandboxing are always on.
- **No new moving parts on your servers.** Everything is a systemd unit and a single binary or
  script: no containers, databases, message queues or language package managers.
- **Plain restic underneath.** Repositories are standard, so you can always walk away with
  plain `restic`.
- **Idempotent and reviewable.** A second deploy changes nothing, and every file on a server is
  generated from something you can read in this repository.
- **Tested for real.** The end-to-end suite deploys to real systemd containers and restores files
  and a PostgreSQL database.
