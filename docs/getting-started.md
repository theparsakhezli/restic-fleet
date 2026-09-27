# Getting started

This guide takes you from nothing to a first backup you have verified. Plan on about 15 minutes,
most of it spent answering questions.

- [1. What you need](#1-what-you-need)
- [2. Prepare SSH access](#2-prepare-ssh-access)
- [3. Run the wizard](#3-run-the-wizard)
- [4. What the deploy does](#4-what-the-deploy-does)
- [5. Check your first backup](#5-check-your-first-backup)
- [6. Save your credentials](#6-save-your-credentials)
- [Next steps](#next-steps)

## 1. What you need

**A controller.** This is the computer you run the setup from: your laptop, a CI runner or an
admin box. It needs `bash`, `python3` (with `venv`), `ssh` and `git`. Linux, macOS and WSL all work.
If Ansible isn't installed, the wizard installs `ansible-core` into `.venv/` inside the repository.
Nothing is installed system-wide.

**A backup server.** Pick one machine to store all the backups.

- It needs enough disk for your data. The first backup is roughly the size of the data after
  compression and deduplication, and later backups add only what changed.
- Clients must reach it on TCP **8000** (rest-server) and **8443** (dashboard and reports).
- It should **not** be one of the machines you are protecting. If it's in the same data centre, plan an
  [off-site copy](operations.md#off-site-copy).

**The machines to back up.** Supported systems:

| | |
|---|---|
| Operating system | Debian 11+, Ubuntu 20.04+, RHEL / Rocky / AlmaLinux 8+ |
| Init | systemd |
| CPU | x86_64 (amd64) or arm64 |
| Access | SSH, as a user with `sudo` (or root) |
| Software | `python3` (preinstalled on all of the above; Ansible needs it) |

## 2. Prepare SSH access

The controller must be able to SSH into every machine without typing a password. If you can
already run `ssh user@server sudo true` for each one, skip this step.

```bash
ssh-keygen -t ed25519            # only if you don't have a key yet
ssh-copy-id ubuntu@203.0.113.10  # repeat for every machine
```

If `sudo` asks for a password on your machines, run the deploy with `-K`:
`./setup.sh deploy prod -K`.

## 3. Run the wizard

```bash
git clone https://github.com/<your-github-user>/restic-fleet.git
cd restic-fleet
./setup.sh           # choose "Create a new backup fleet", or run: ./setup.sh new
```

Nothing changes on any machine until you confirm at the end. Here is every question:

### The fleet

| Question | What to answer |
|---|---|
| **Fleet name** | A folder name under `inventories/`, e.g. `prod`. You can have several fleets. |
| **SSH user on all machines** | The user you log in as. It needs `sudo`. |
| **SSH private key** | Leave empty to use your SSH agent or default key. |

### The backup server

| Question | What to answer |
|---|---|
| **Name** | A short name, e.g. `backup`. |
| **IP address or DNS name clients will use** | The address the *machines* use to reach the server. It goes into the TLS certificate, so use the one they will really connect to (a private IP if they share a network). |
| **Where to store the repositories** | Default `/srv/restic-fleet`. Point it at your big disk. |

### Each machine

| Question | What to answer |
|---|---|
| **Name** | Becomes the repository name and the name on the dashboard, e.g. `web1`. Letters, digits, `.` `_` `-`. |
| **IP address or DNS name** | How the controller reaches it over SSH. |
| **Folders to back up** | Comma-separated, e.g. `/etc,/srv/app,/home`. Leave empty if you only want database backups. |
| **Paths or patterns to skip** | Optional, e.g. `/srv/app/cache,*.log`. Common junk (`/proc`, `node_modules`, `.cache`, …) is always skipped. |
| **How often** | *every day* at a time, *every N hours* (1–12), or *every N days* (2–14). This also sets when a machine counts as overdue. |
| **Back up a database?** | Answer *y* for each database. See below. |

For each database the wizard asks:

- **type**: `postgres`, `mysql` (also MariaDB), `mongodb`, or `command` (anything that writes a
  backup to stdout);
- **job name**: used in `rfleet db <name>` and on the dashboard;
- **Docker or native**: for Docker, the container name as shown by `docker ps`;
- **database**: one database, or `all`;
- **user**: for PostgreSQL and MySQL.

All options are described in [Databases](databases.md).

### Notifications (optional)

- **Telegram bot token and chat IDs**: create a bot with [@BotFather](https://t.me/BotFather),
  send it a message, and read your chat ID from
  `https://api.telegram.org/bot<token>/getUpdates`. The wizard can encrypt the token with
  `ansible-vault`. You then type the vault password on every deploy.
- **Webhook URL**: any `https://` endpoint that accepts JSON `{"text": "..."}`, such as a Slack incoming
  webhook, a Discord webhook with `/slack` appended, or your own endpoint.

### Review and deploy

The wizard shows the inventory it wrote (`inventories/<fleet>/hosts.yml`) and asks
**Deploy now?** It then tests SSH to every machine, runs the playbook, and starts a first backup
on every machine.

## 4. What the deploy does

The first deploy takes a few minutes. Later deploys only change what you changed.

**On the backup server:**

1. installs restic and rest-server (pinned versions, SHA-256 verified);
2. creates a private certificate authority and a TLS certificate for the server;
3. creates a rest-server login and an empty, encrypted repository for each machine;
4. starts rest-server on :8000 in append-only, private-repository mode;
5. starts the dashboard on :8443, and the statistics and weekly maintenance timers;
6. if `ufw` is active, allows only your machines to reach :8000.

**On each machine:**

1. installs restic;
2. writes its settings to `/etc/restic-fleet/`: repository address, credentials (root-only),
   the CA certificate, paths, excludes and database dump scripts;
3. installs the `restic-fleet-backup` service with a timer on your schedule, and the `rfleet` command.

All passwords and tokens are generated on the controller under `inventories/<fleet>/credentials/`.
They never appear on a command line or in Ansible's output.

At the end you'll see:

```
Dashboard
  URL:      https://203.0.113.10:8443
  User:     admin
  Password: ••••••••••••••••
```

Run `./setup.sh info` any time to see this again.

## 5. Check your first backup

**Open the dashboard.** Your browser will warn about the certificate. That's expected: the
certificate is signed by your fleet's own CA, not a public one. Either click through, or import
`inventories/<fleet>/credentials/ca.crt` into your browser or OS as a trusted authority to remove
the warning for good.

Each machine appears as **Backing up** and then **Healthy**. The first backup of a large machine
can take a while, and later ones are much faster.

**On a machine, check it for real:**

```bash
rfleet status                   # last run and the next scheduled one
rfleet snapshots                # you should see a "files" snapshot and one per database
rfleet restore latest --tag files --target /tmp/restore-test --include /etc/hostname
cat /tmp/restore-test/etc/hostname && rm -rf /tmp/restore-test
```

A backup you have never restored is only a hope, so do this once now and then regularly.

## 6. Save your credentials

> [!IMPORTANT]
> Copy `inventories/<fleet>/credentials/` to a safe place **now**: a password manager, an
> encrypted USB stick, or a private encrypted repository.

It contains:

| File | Why it matters |
|---|---|
| `repo-password/<machine>` | **The only key to that machine's backups.** Lose it together with the machine, and the backups can't be decrypted. |
| `rest-password/<machine>` | The machine's login to rest-server. |
| `report-token/<machine>` | Lets the machine report to the dashboard. |
| `dashboard-admin-password` | Your dashboard login. |
| `ca.crt` | The fleet's CA certificate (public). Import it into your browser to trust the dashboard. The CA's private key stays on the backup server in `/etc/restic-fleet/tls/`. |

The `.gitignore` keeps this folder out of Git on purpose. If you version your inventories, use a
**private** repository and store the credentials encrypted, for example with `ansible-vault` or `sops`.

## Next steps

- Tune paths, schedules and retention: [Configuration](configuration.md).
- Set up alerts if you skipped them: [Configuration → Notifications](configuration.md#notifications).
- Learn the restore paths before you need them: [Restoring](restoring.md).
- Read the [security model](security.md), especially the part about server-side maintenance.
