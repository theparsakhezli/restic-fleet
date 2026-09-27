<p align="center">
  <img src="design/logo/restic-fleet-logo.svg" alt="restic-fleet" width="340">
</p>

<p align="center">
  <b>Encrypted, append-only backups for every Linux server you run: one setup command, one dashboard.</b>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-3350D8"></a>
  <img alt="restic 0.19" src="https://img.shields.io/badge/restic-0.19.1-0F1B2A">
  <img alt="ansible-core 2.16–2.19" src="https://img.shields.io/badge/ansible--core-2.16%E2%80%932.19-0F1B2A">
  <img alt="No dependencies" src="https://img.shields.io/badge/dashboard-Python%20stdlib%20only-13804B">
</p>

<p align="center">
  <a href="docs/getting-started.md">Getting started</a> ·
  <a href="docs/README.md">Documentation</a> ·
  <a href="docs/restoring.md">Restoring</a> ·
  <a href="docs/security.md">Security</a>
</p>

---

restic-fleet turns one machine into a [restic](https://restic.net) backup server and connects all
your other machines to it. An interactive wizard asks a few questions, and Ansible does the rest.
Every machine then backs up its files and databases on a schedule, and a web dashboard shows what
ran, what failed and what is overdue. Alerts go to Telegram, Slack or Discord.

```bash
git clone https://github.com/<your-github-user>/restic-fleet.git
cd restic-fleet
./setup.sh
```

## Why restic-fleet

restic makes excellent backups of one machine. When you run ten, you also need a server to store
them, a login per machine, TLS, schedules, database dumps, retention, integrity checks, and a way
to see at a glance that last night's backups actually ran. restic-fleet provides all of that with
safe defaults, so none of it has to be scripted by hand.

| | |
|---|---|
| 🔒 **Encrypted at the source** | restic encrypts every backup before it leaves the machine. The server stores only ciphertext. |
| 🛡️ **Ransomware-resistant** | The server is **append-only**. A hacked machine can add backups but cannot delete or rewrite its history, and it can't see any other machine's backups. |
| 🗄️ **Databases, done right** | PostgreSQL, MySQL/MariaDB, MongoDB (Docker or native) or any command. Dumps stream straight into restic, with no temp files and no half-written dumps saved as successes. |
| 📊 **A dashboard you can read in 3 seconds** | One sentence says whether you're OK. Problems sort to the top. Each machine gets a 30-day history and restore commands you can copy. |
| 🔔 **Alerts that don't spam** | A job fails, a machine goes quiet, or maintenance fails: you hear about it once, and again when it recovers. |
| 🧹 **Maintenance included** | Retention, prune and a weekly integrity check that reads real data, all run on the server. |
| 🪶 **Boring on purpose** | Pinned, checksum-verified binaries. Nothing runs in a container. The dashboard is one Python file with no dependencies. |

## How it works

```
  your laptop                      backup server                          your machines
 ┌─────────────┐   SSH   ┌──────────────────────────────────┐  HTTPS  ┌───────────────────┐
 │ ./setup.sh  │ ──────► │ rest-server :8000  (append-only)  │ ◄────── │ restic backup      │
 │  + Ansible  │         │ /srv/restic-fleet/<machine>/      │         │  files + databases │
 │             │         │ dashboard   :8443  ◄──────────────┼─report─ │  (systemd timer)   │
 │ credentials │         │ weekly: prune + integrity check   │         │ rfleet (restore)   │
 └─────────────┘         └──────────────────────────────────┘         └───────────────────┘
```

1. **Setup:** the wizard writes an Ansible inventory and deploys it. That creates a private
   certificate authority, a rest-server login, a repository password and a dashboard token per
   machine. Every secret is generated on *your* computer.
2. **Backups:** a systemd timer on each machine backs up its files, then each database, and
   reports every job to the dashboard.
3. **Maintenance:** the server collects repository sizes every 10 minutes and, once a week, applies
   retention, prunes and checks the data.
4. **Restore:** on any machine, `rfleet` is restic with the right repository and keys already set.

Read more in [Architecture](docs/architecture.md).

## Quick start

**You need**

- a computer with `bash` and `python3` to run the setup: Linux, macOS or WSL;
- SSH access with `sudo` to one backup server and to each machine you want to back up;
- targets running Debian 11+, Ubuntu 20.04+ or RHEL/Rocky/Alma 8+, with systemd, on x86_64 or
  arm64.

```bash
./setup.sh
```

The wizard walks you through it:

```
? Fleet name (a folder under inventories/) [prod]
? SSH user on all machines (needs sudo) [root]

Backup server
? IP address or DNS name clients will use  203.0.113.10

Machine to back up
? Name (becomes its repository name)  web1
? Folders to back up (comma-separated, empty for none) [/etc]  /etc,/srv/app
? Back up a database on web1? [y/N]  y
? Database type
    1) postgres  2) mysql  3) mongodb  4) command
...
? Deploy now? [Y/n]

Dashboard
  URL:      https://203.0.113.10:8443
  User:     admin
  Password: ••••••••••••••••
```

> [!IMPORTANT]
> **Save `inventories/<fleet>/credentials/` somewhere safe**, such as a password manager or an
> encrypted USB stick. It holds each repository's password. The encryption is real: without that
> password, nobody can restore the backups, including you.

Next: [Getting started](docs/getting-started.md) covers every question the wizard asks and
how to check your first backup.

## Everyday commands

On your computer:

```bash
./setup.sh add-client     # add a machine
./setup.sh deploy         # apply changes after editing the inventory
./setup.sh info           # dashboard address and login
./setup.sh check          # test SSH access to every machine
```

On any backed-up machine:

```bash
rfleet run                                          # back up now
rfleet logs -f                                      # watch it
rfleet snapshots                                    # list backups
rfleet restore latest --tag files --target /tmp/r --include /etc/nginx
rfleet db app > app.sql                             # latest dump of database job "app"
```

## Documentation

| Guide | What's in it |
|---|---|
| [Getting started](docs/getting-started.md) | Prerequisites, the wizard step by step, your first backup |
| [Configuration](docs/configuration.md) | Every setting: paths, schedules, retention, hooks, alerts, firewall |
| [Databases](docs/databases.md) | PostgreSQL, MySQL/MariaDB, MongoDB, custom commands, and restoring each |
| [The dashboard](docs/dashboard.md) | Statuses, alerts, users, Prometheus, running behind a reverse proxy |
| [Restoring](docs/restoring.md) | A file, a folder, a database, a whole machine, disaster recovery |
| [Security](docs/security.md) | Threat model, the maintenance trust trade-off, hardening, secrets |
| [Operations](docs/operations.md) | Add and remove machines, upgrades, secret rotation, file locations |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and how to fix them |
| [Architecture](docs/architecture.md) | Components, data flow, the report API |
| [Design system](docs/design-system.md) | Logo, colours, status language, components |

## FAQ

**Does it replace restic?** No. It installs and runs restic and rest-server for you. Your backups
are plain restic repositories, so any restic tool can read them, with or without restic-fleet.

**Can I use my existing Ansible setup?** Yes. It's a normal playbook: copy
`inventories/example`, edit it and run `ansible-playbook -i inventories/<fleet> site.yml`.

**Does the backup server need a domain name or public certificate?** No. restic-fleet runs its own
small certificate authority, and each machine trusts only that one.

**What if the backup server dies?** Your machines are fine, but the backups on that server are
gone. For important data, copy the repositories somewhere else too (see
[Operations → Off-site copy](docs/operations.md#off-site-copy)).

**Windows or macOS machines?** Not yet. The backup targets are Linux servers with systemd.

## Contributing

Bug reports, fixes and new database types are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for
the ground rules and how to run the tests (unit tests plus a full end-to-end run in Docker).
Report security problems privately; see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE). restic-fleet is a community project built on [restic](https://github.com/restic/restic)
and [rest-server](https://github.com/restic/rest-server) (BSD-2-Clause) and is not affiliated with
the restic authors.
