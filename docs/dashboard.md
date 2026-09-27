# The dashboard

The dashboard runs on the backup server at `https://<server>:8443`. Sign in as `admin`, and run
`./setup.sh info` to see the password. It answers one question fast: **are my backups OK, and
if not, which machine needs me?**

- [Reading the page](#reading-the-page)
- [Statuses](#statuses)
- [Machine details and restore commands](#machine-details-and-restore-commands)
- [Alerts](#alerts)
- [Users](#users)
- [Trusting the certificate](#trusting-the-certificate)
- [Behind a reverse proxy](#behind-a-reverse-proxy)
- [Prometheus metrics](#prometheus-metrics)
- [Data and retention](#data-and-retention)

## Reading the page

From top to bottom:

1. **Top bar**: the fleet name and a live indicator. It's green while the page is updating
   (every 15 seconds) and turns amber with *Connection lost · showing last data* if the server
   stops answering.
2. **Verdict**: one sentence such as *All 6 machines are backed up* or *2 of 6 machines need
   attention*.
3. **Figures**: machines, machines needing attention, total stored, and backups run in the last 24 hours.
4. **Machines**: one card per machine, **problems first**. Filter with *All / Needs attention /
   Healthy*, or press <kbd>/</kbd> to search.
5. **Recent events**: the latest job results and maintenance runs across the fleet.

Each machine card shows:

- the name, schedule, time since the last successful backup, and a status;
- **stored** (repository size on the server), **snapshots**, and the **last run** duration;
- a chip for each job (`files`, `db-app`, …) with its own status dot;
- a **30-day history**: one bar per day, where the worst result of the day wins, with today outlined;
- the result of the last weekly integrity check.

## Statuses

A status is always an **icon + word + colour**, never colour alone.

| Status | Means | What to do |
|---|---|---|
| **Failed** | The latest run of at least one job failed. | Open the machine; the error message is in *Jobs*. Fix it, then `rfleet run`. |
| **Overdue** | No successful backup for 1.5 × the expected interval. | The machine may be off, the timer disabled, or it can't reach the server. See [Troubleshooting](troubleshooting.md#a-machine-is-overdue). |
| **Warning** | The backup finished, but some files couldn't be read (they changed or vanished while being read, or permission was denied). | Usually harmless: temp files, sockets. If it repeats, exclude the paths. |
| **Backing up** | A job is running right now. | Nothing. A run that is still "running" after 24 hours is shown as failed. |
| **No backup yet** | The machine has never reported. | Normal right after adding a machine. |
| **Healthy** | None of the above. | Nothing. |

A machine's status is the worst status of its jobs, and machines are sorted in the table's order.

## Machine details and restore commands

Click a card (or focus it and press <kbd>Enter</kbd>) to open the details panel:

- **Details**: repository size, snapshot count, schedule, and last maintenance with its message;
- **Jobs**: the latest result of every job, with duration, data added, files new or changed, the
  snapshot ID, and the error message if there is one;
- **Recent runs**: the last runs of every job;
- **Restore**: the exact `rfleet` commands for this machine, including one per database, each with
  a copy button.

<kbd>Esc</kbd> closes the panel.

## Alerts

Alerts go to Telegram and/or a webhook (see [Configuration → Notifications](configuration.md#notifications)):

| Alert | When |
|---|---|
| ❌ *web1: backup job 'db-app' failed* + the error | a job reports failure |
| ⏰ *web1: backup overdue (last success 2 days ago)* | a machine becomes overdue |
| 🧹 *web1: repository maintenance failed* + the error | the weekly forget/prune/check fails for a repository |
| ✅ *web1: backups are healthy again* | a machine that failed or was overdue is healthy |

Each incident is sent **once**: an overdue machine doesn't page you every minute. Overdue
detection runs on the server, so you're alerted even when a machine is completely dead.

## Users

`admin` is created on deploy, with its password in `credentials/dashboard-admin-password`. Every user
has the same read-only view. To manage users, SSH into the backup server:

```bash
D="sudo -u rfdash env RFD_STATE_DIR=/var/lib/restic-fleet-dashboard python3 /opt/restic-fleet/restic_fleet_dashboard.py"

$D users                                   # list
read -rs P && printf '%s' "$P" | $D set-password alice && unset P     # add or change (password on stdin)
$D delete-user alice
```

Passwords are stored as scrypt hashes. After 10 failed logins from one address within 15 minutes,
that address is blocked for the rest of the window. Sessions last 12 hours.

Deploys reset the `admin` password to the one in `credentials/` if it was changed on the server.
To change it for good, edit the credentials file and deploy.

## Trusting the certificate

The dashboard's certificate is signed by your fleet's private CA, so browsers warn about it until
you trust that CA. Import `inventories/<fleet>/credentials/ca.crt`:

| Where | How |
|---|---|
| Windows | Double-click `ca.crt` → *Install Certificate* → *Local Machine* → *Trusted Root Certification Authorities* |
| macOS | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ca.crt` |
| Debian / Ubuntu | `sudo cp ca.crt /usr/local/share/ca-certificates/restic-fleet.crt && sudo update-ca-certificates` |
| Firefox | *Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import* |

The certificate is issued for the server address you configured, so open the dashboard by that
exact address.

## Behind a reverse proxy

To serve the dashboard on your own domain with a public certificate, put a reverse proxy in
front of it:

```yaml
# group_vars/all/main.yml
fleet_dashboard_trusted_proxy: true               # rate limiting uses X-Forwarded-For
fleet_dashboard_url: https://backups.example.com  # shown after each deploy
fleet_dashboard_allow_from: ["127.0.0.1/32"]      # if the proxy runs on the same server
```

Machines keep reporting to `https://<server>:8443` directly (they trust only the fleet CA), so
keep that port reachable for them. With ufw, restic-fleet already allows each machine.

**Caddy**

```
backups.example.com {
    reverse_proxy https://127.0.0.1:8443 {
        transport http {
            tls_trusted_ca_certs /etc/restic-fleet/tls/ca.crt
            tls_server_name 203.0.113.10       # the address in the dashboard's certificate
        }
    }
}
```

**nginx**

```nginx
server {
    listen 443 ssl http2;
    server_name backups.example.com;
    # ssl_certificate … (e.g. from certbot)

    location / {
        proxy_pass https://127.0.0.1:8443;
        proxy_ssl_trusted_certificate /etc/restic-fleet/tls/ca.crt;
        proxy_ssl_verify on;
        proxy_ssl_name 203.0.113.10;           # the address in the dashboard's certificate
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

The proxy **must pass the original `Host` header** (`proxy_set_header Host $host`; Caddy does it
by default). Sign-in checks that the form was posted from the same site, and refuses the request
otherwise. The proxy must serve HTTPS: the session cookie is `Secure`.

## Prometheus metrics

```yaml
fleet_metrics_enabled: true
```

After deploying, `GET /metrics` needs a bearer token, which is in
`credentials/metrics-token`:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: restic-fleet
    scheme: https
    authorization:
      credentials_file: /etc/prometheus/restic-fleet.token
    tls_config:
      ca_file: /etc/prometheus/restic-fleet-ca.crt
    static_configs:
      - targets: ["203.0.113.10:8443"]
```

| Metric | Labels | Meaning |
|---|---|---|
| `restic_fleet_host_status` | `host`, `status` | 1 for the machine's current status (`ok`, `running`, `warning`, `failed`, `overdue`, `never`), 0 otherwise. |
| `restic_fleet_last_success_timestamp_seconds` | `host` | Unix time of the last successful backup, or 0 if there's none. |
| `restic_fleet_repo_size_bytes` | `host` | Repository size on the server. |
| `restic_fleet_repo_snapshots` | `host` | Number of snapshots. |

Example alert rules:

```yaml
groups:
  - name: restic-fleet
    rules:
      - alert: BackupFailed
        expr: restic_fleet_host_status{status="failed"} == 1
        for: 10m
      - alert: BackupOverdue
        expr: restic_fleet_host_status{status="overdue"} == 1
      - alert: BackupRepoGrowingFast
        expr: delta(restic_fleet_repo_size_bytes[1d]) > 50e9
```

## Data and retention

The dashboard keeps its data in SQLite at `/var/lib/restic-fleet-dashboard/`. It shows the last
30 days and deletes run history older than about 400 days. The file holds only status data: no
backup contents, and no passwords except scrypt hashes and token hashes. Losing it loses the
history, never a backup.
