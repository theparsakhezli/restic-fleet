# Configuration

A fleet is an ordinary Ansible inventory in `inventories/<fleet>/`. The wizard writes it for
you, and editing it by hand is expected. After any change, run:

```bash
./setup.sh deploy <fleet>                   # everything
./setup.sh deploy <fleet> --limit web1      # just one machine (plus the server)
./setup.sh deploy <fleet> --check --diff    # preview without changing anything
```

- [Layout](#layout)
- [Per-machine settings](#per-machine-settings)
- [Schedules](#schedules)
- [Retention](#retention)
- [Hooks](#hooks)
- [Fleet-wide settings](#fleet-wide-settings)
- [Notifications](#notifications)
- [Firewall and networking](#firewall-and-networking)
- [Secrets in the inventory](#secrets-in-the-inventory)
- [Using plain Ansible](#using-plain-ansible)

## Layout

```
inventories/prod/
├── hosts.yml                  # the server and the machines, with per-machine settings
├── group_vars/all/
│   ├── main.yml               # fleet-wide settings
│   └── vault.yml              # secrets (optional, ansible-vault encrypted)
└── credentials/               # generated; never edit, never commit, always back up
```

`group_vars/all.yml` (a single file) works too, which is what `inventories/example` uses.

## Per-machine settings

Set these on each host under `backup_clients`:

```yaml
backup_clients:
  hosts:
    web1:
      ansible_host: 203.0.113.21
      backup_paths: [/etc, /srv/app, /home]
      backup_excludes: ["/srv/app/tmp", "*.log", "/home/*/Downloads"]
      backup_schedule: "*-*-* 02:30:00"
      backup_interval_hours: 24
      backup_retention: { daily: 14, weekly: 8, monthly: 12 }
      backup_one_file_system: false
      backup_pre_hook: "systemctl stop myapp-worker"
      backup_post_hook: "systemctl start myapp-worker"
      backup_databases: []           # see databases.md
```

| Setting | Default | Description |
|---|---|---|
| `backup_paths` | `[]` | Files and folders to back up. |
| `backup_excludes` | `[]` | Extra [restic exclude patterns](https://restic.readthedocs.io/en/stable/040_backup.html#excluding-files), added to `fleet_default_excludes`. |
| `backup_databases` | `[]` | Database dump jobs. See [Databases](databases.md). |
| `backup_schedule` | `fleet_backup_schedule` | When to back up, as a systemd `OnCalendar` expression. |
| `backup_interval_hours` | `fleet_backup_interval_hours` | How often a backup is *expected*. With no success for 1.5 × this, the machine is **Overdue**. Keep it in line with the schedule. |
| `backup_retention` | `fleet_retention` | Which snapshots to keep for this machine. |
| `backup_one_file_system` | `fleet_one_file_system` | Don't cross into other mounted file systems. |
| `backup_pre_hook` / `backup_post_hook` | `""` | Shell commands run before and after the backup. See [Hooks](#hooks). |
| `fleet_source_address` | `ansible_host` | The address this machine's traffic *comes from*, if different from how you SSH in. Used for firewall rules. |

A machine needs `backup_paths` or `backup_databases`, or both. The machine's inventory name
becomes its repository name, so renaming it later starts a new, empty repository.

## Schedules

Schedules are [systemd calendar expressions](https://www.freedesktop.org/software/systemd/man/systemd.time.html#Calendar%20Events).
Check one with `systemd-analyze calendar "<expr>"`.

| You want | `backup_schedule` | `backup_interval_hours` |
|---|---|---|
| every day at 02:30 | `"*-*-* 02:30:00"` | `24` |
| every 6 hours | `"*-*-* 00/6:00:00"` | `6` |
| every hour | `"hourly"` | `1` |
| twice a day | `"*-*-* 03,15:00:00"` | `12` |
| weekdays at 23:00 | `"Mon..Fri *-*-* 23:00:00"` | `72` (covers the weekend) |
| Sunday nights | `"Sun *-*-* 01:00:00"` | `168` |

To avoid every machine starting at the same second, each run starts after a random delay of up to
`fleet_randomized_delay` (30 minutes by default). A machine that was off at its scheduled time
catches up when it boots (`Persistent=true`).

## Retention

Retention decides which snapshots survive the weekly maintenance. It uses restic's
`forget --keep-*` rules and applies **to each job separately**, so files and each database keep
their own history.

```yaml
fleet_retention:          # fleet default
  last: 0
  hourly: 0
  daily: 7
  weekly: 4
  monthly: 6
  yearly: 1
```

This keeps the newest snapshot of each of the last 7 days, 4 weeks, 6 months and 1 year: about
18 restore points spread over a year. Override it for one machine with `backup_retention`, and set
only the keys you want to change.

Pruning runs on the server, not on the machines, because machines are append-only and can't delete
anything. If you set `fleet_server_maintenance: false`, nothing is pruned automatically. See
[Security → trust model](security.md#the-maintenance-trade-off).

## Hooks

`backup_pre_hook` runs before the first job, and `backup_post_hook` runs after the last one. Both
run as root with `sh`.

- If the **pre-hook fails**, the backup is aborted and reported as failed.
- The **post-hook always runs**, even when a job failed, so services you stopped get started again.
- A hook's output is shown on the dashboard when it fails.

```yaml
backup_pre_hook: "docker compose -f /srv/app/compose.yml stop worker"
backup_post_hook: "docker compose -f /srv/app/compose.yml start worker"
```

Prefer database jobs over stopping databases. They take consistent dumps with no downtime.

## Fleet-wide settings

Put these in `inventories/<fleet>/group_vars/all/main.yml`. The source of truth, with comments, is
[`roles/fleet_common/defaults/main.yml`](../roles/fleet_common/defaults/main.yml).

### Backup server

| Setting | Default | Description |
|---|---|---|
| `fleet_name` | `restic-fleet` | Shown on the dashboard and in alerts. |
| `fleet_data_dir` | `/srv/restic-fleet` | Where repositories are stored. |
| `fleet_public_address` *(host var on the server)* | its `ansible_host` | The address machines use to reach the server. It goes into the TLS certificate. |
| `fleet_rest_port` | `8000` | rest-server port. |
| `fleet_rest_listen` | `""` (all) | Bind rest-server to one address, e.g. a private IP. |
| `fleet_rest_max_size` | `""` | Optional cap on total repository size, in bytes. |
| `fleet_server_maintenance` | `true` | Prune and check on the server. See [the trade-off](security.md#the-maintenance-trade-off). |
| `fleet_maintenance_schedule` | `Sun *-*-* 04:00:00` | When maintenance runs. |
| `fleet_check_subset` | `5%` | How much real data `restic check` reads each week. Use `100%` for a full read. |
| `fleet_collect_schedule` | `*:0/10` | How often repository sizes are refreshed. |
| `fleet_retention` | see [Retention](#retention) | Default retention. |

### Dashboard

| Setting | Default | Description |
|---|---|---|
| `fleet_dashboard_port` | `8443` | HTTPS port. |
| `fleet_dashboard_listen` | `0.0.0.0` | Bind address. Use `127.0.0.1` behind a local reverse proxy. |
| `fleet_dashboard_admin` | `admin` | Name of the admin user created on deploy. |
| `fleet_dashboard_url` | `https://<server>:8443` | The address people use, printed after each deploy. Set it when you use a reverse proxy. Machines always report to the server directly. |
| `fleet_dashboard_allow_from` | `["any"]` | Who may open the dashboard (ufw), as a list of CIDRs. |
| `fleet_dashboard_trusted_proxy` | `false` | Trust `X-Forwarded-For` from a reverse proxy. |
| `fleet_metrics_enabled` | `false` | Expose `/metrics` for Prometheus. |

### Machines

| Setting | Default | Description |
|---|---|---|
| `fleet_backup_schedule` | `*-*-* 02:30:00` | Default schedule. |
| `fleet_backup_interval_hours` | `24` | Default expected interval. |
| `fleet_randomized_delay` | `30min` | Random start delay per run. |
| `fleet_compression` | `auto` | restic compression: `auto`, `max` or `off`. |
| `fleet_pack_size_mib` | `32` | restic pack size. Bigger means fewer files on the server. |
| `fleet_one_file_system` | `false` | Default for `backup_one_file_system`. |
| `fleet_default_excludes` | `/proc`, `/sys`, `/dev`, `/run`, `/tmp`, `/var/tmp`, `node_modules`, `.cache`, `__pycache__`, … | Always excluded. Replace the list to change it. |

### Versions

| Setting | Default |
|---|---|
| `fleet_restic_version` + `fleet_restic_sha256` | `0.19.1` |
| `fleet_rest_server_version` + `fleet_rest_server_sha256` | `0.14.0` |

To upgrade, change the version **and** both SHA-256 values (amd64, arm64) from the release's
`SHA256SUMS`. The download is refused if the checksum doesn't match.

## Notifications

The dashboard sends alerts when:

- a job fails;
- a machine becomes overdue;
- weekly maintenance fails;
- a machine is healthy again after any of the above.

Each incident is announced once, not on every check. Warnings (some files couldn't be read)
show on the dashboard but don't send an alert.

```yaml
# Telegram
fleet_notify_telegram_token: "123456:ABC-DEF..."          # better: in vault.yml
fleet_notify_telegram_chat_ids: ["123456789", "-1001234567890"]

# Any webhook: receives POST {"text": "...", "fleet": "..."} as JSON
fleet_notify_webhook_url: "https://hooks.slack.com/services/T000/B000/XXXX"
```

| Service | Webhook URL |
|---|---|
| Slack | an *incoming webhook* URL |
| Discord | the channel webhook URL with `/slack` appended |
| Mattermost, Rocket.Chat | an incoming webhook URL (both accept `text`) |
| Anything else | your own endpoint; the body is `{"text": …, "fleet": …}` |

## Firewall and networking

| Port | On | Who needs it |
|---|---|---|
| 8000/tcp | backup server | the machines (restic → rest-server) |
| 8443/tcp | backup server | the machines (reports) and you (dashboard) |

If `ufw` is installed **and already active** on the backup server, restic-fleet adds rules:

- port 8000 only from each machine's address;
- port 8443 from each machine and from `fleet_dashboard_allow_from`.

It never enables ufw and never touches other rules, so it can't lock you out. With any other
firewall (firewalld, nftables, a cloud security group), open the ports yourself.

Machines behind NAT connect from an address other than the one you SSH to. Set
`fleet_source_address` on those hosts so the rules match.

To reach the server over a private network, set `fleet_public_address` on the server host to its
private IP. It is written into the certificate, and the machines use it.

## Secrets in the inventory

Anything secret you add yourself, like the Telegram token, belongs in an encrypted vars file:

```bash
$EDITOR inventories/prod/group_vars/all/vault.yml     # fleet_notify_telegram_token: "…"
.venv/bin/ansible-vault encrypt inventories/prod/group_vars/all/vault.yml
```

`./setup.sh deploy` notices the encrypted file and asks for the vault password.

Generated secrets (repository passwords, logins, tokens) are created automatically in
`credentials/` on first deploy and reused afterwards. Don't put them in vars files.

## Using plain Ansible

The wizard is optional. It only writes the inventory and calls `ansible-playbook`.

```bash
python3 -m venv .venv && .venv/bin/pip install 'ansible-core>=2.16,<2.20'
cp -r inventories/example inventories/prod
$EDITOR inventories/prod/hosts.yml inventories/prod/group_vars/all.yml
.venv/bin/ansible-playbook -i inventories/prod site.yml -e fleet_run_backup_now=true
```

The roles use only `ansible.builtin` modules, so no collections are needed. The inventory needs
exactly one host in `backup_server` and at least one in `backup_clients`. To use your own group
names, set `fleet_server_group` and `fleet_client_group`.
