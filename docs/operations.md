# Operations

Everyday tasks once the fleet is running.

- [Add a machine](#add-a-machine)
- [Remove a machine](#remove-a-machine)
- [Change what a machine backs up](#change-what-a-machine-backs-up)
- [Run a backup now](#run-a-backup-now)
- [Upgrade restic-fleet](#upgrade-restic-fleet)
- [Upgrade restic or rest-server](#upgrade-restic-or-rest-server)
- [Rotating secrets](#rotating-secrets)
- [Move the backup server](#move-the-backup-server)
- [Disk space](#disk-space)
- [Off-site copy](#off-site-copy)
- [Where everything lives](#where-everything-lives)
- [Services and timers](#services-and-timers)

## Add a machine

```bash
./setup.sh add-client prod
```

It asks the same questions as for the first machines, adds the machine to `hosts.yml`, updates
the server (new login, firewall rule and dashboard token) and installs the machine. Or add a
host entry by hand and run `./setup.sh deploy prod`.

## Remove a machine

Delete its entry from `hosts.yml` and deploy:

```bash
./setup.sh deploy prod
```

This removes its rest-server login, its dashboard token and (with maintenance on) its repository
password from the server. **Its backups stay** in `/srv/restic-fleet/<machine>/` until you decide
otherwise. To delete them for good:

```bash
ssh backup 'sudo rm -rf /srv/restic-fleet/web1'
```

Keep `credentials/repo-password/web1` for as long as you keep the repository.

The removed machine still has restic-fleet installed. Clean it up with:

```bash
systemctl disable --now restic-fleet-backup.timer
rm -rf /etc/restic-fleet /var/cache/restic-fleet /usr/local/bin/rfleet /usr/local/sbin/restic-fleet-backup \
       /etc/systemd/system/restic-fleet-backup.*
systemctl daemon-reload
```

## Change what a machine backs up

Edit its entry in `hosts.yml` (paths, excludes, databases, schedule, retention) and deploy only
that machine:

```bash
./setup.sh deploy prod --limit web1,backup      # the server too if retention or schedule changed
```

## Run a backup now

On the machine: `rfleet run`, then `rfleet logs -f` to follow it. For the whole fleet from the controller:

```bash
.venv/bin/ansible -i inventories/prod backup_clients -b -m ansible.builtin.systemd -a "name=restic-fleet-backup.service state=started"
```

## Upgrade restic-fleet

```bash
git pull
./setup.sh deploy prod
```

Read the release notes first. Deploys are idempotent, and your inventory and credentials are
untouched by `git pull` because they're git-ignored.

## Upgrade restic or rest-server

Set the new version and its checksums (from the release's `SHA256SUMS`) in `group_vars`, then deploy:

```yaml
fleet_restic_version: "0.19.2"
fleet_restic_sha256:
  amd64: "…"
  arm64: "…"
```

A mismatched checksum fails the deploy before anything is installed. Newer restic versions read
older repositories; check restic's release notes before upgrading across a repository format
change.

## Rotating secrets

| Secret | How to rotate |
|---|---|
| rest-server password or report token of a machine | Delete `credentials/rest-password/<m>` and/or `credentials/report-token/<m>`, then `./setup.sh deploy prod --limit <m>,backup`. New ones are generated and deployed. |
| dashboard admin password | Edit `credentials/dashboard-admin-password` (or delete it for a new random one), then deploy. |
| metrics token | Delete `credentials/metrics-token`, deploy, and update Prometheus. |
| repository password | Can't be swapped by redeploying: the repository is encrypted with it. Use restic's key commands (below). |
| the CA | Remove `/etc/restic-fleet/tls/` on the server, then deploy **every** machine. |

Rotating a repository password:

```bash
# on the machine
NEW=$(openssl rand -base64 36 | tr -dc A-Za-z0-9 | head -c 48)
printf '%s' "$NEW" > /tmp/new.pass
rfleet key add --new-password-file /tmp/new.pass
rfleet key list                   # note the ID of the old key (not marked "*")
# put the new password in inventories/prod/credentials/repo-password/<m> on the controller, deploy,
# then remove the old key:
rfleet key remove <old-key-id>
shred -u /tmp/new.pass
```

## Move the backup server

1. Stop backups everywhere, or pick a quiet hour.
2. Copy the repositories: `rsync -aH --numeric-ids old:/srv/restic-fleet/ new:/srv/restic-fleet/`.
3. Point the server's `ansible_host` (and `fleet_public_address`, if set) at the new machine.
4. Deploy **everything**: `./setup.sh deploy prod`. The new server gets a new CA and certificate,
   and every machine is updated to trust it and use the new address.
5. Run `rfleet run` on one machine, and check the dashboard.
6. Re-import `credentials/ca.crt` into your browser.

The dashboard history (`/var/lib/restic-fleet-dashboard/`) can be copied too, or left behind: it
only holds status data.

## Disk space

- The dashboard shows each repository's size. `restic_fleet_repo_size_bytes` gives the same in Prometheus.
- Space is only freed by **prune**, which runs weekly with `fleet_server_maintenance: true`. After
  you lower retention, run it right away: `systemctl start restic-fleet-maintain.service` on the server.
- Find what's big: `rfleet stats latest --tag files --mode raw-data`, or look for a job whose
  "data added" is large every day. That's often logs, caches or an uncompressed database file
  that belongs in `backup_excludes`.
- `fleet_rest_max_size` caps the total size rest-server accepts. Backups then fail instead of
  filling the disk.

## Off-site copy

One backup server is one copy. For data you can't lose, keep a second copy in another place that
the backup server **can't delete**. Repositories are already encrypted, so any storage will do.

**Option A: pull from somewhere else (recommended).** A machine in another location pulls the
repository files. The backup server has no credentials for it, so even a fully compromised backup
server can't reach the copy.

```bash
# on the off-site machine, e.g. nightly from cron; its user has read access on the backup server
rsync -aH --delete-after backup-reader@backup:/srv/restic-fleet/ /data/restic-fleet-copy/
zfs snapshot tank/restic-fleet-copy@$(date +%F)       # or btrfs/LVM snapshots: keep history
```

**Option B: object storage with retention locks.** Push with `rclone sync` to S3, B2 or Wasabi
using a key that can't delete (or a bucket with object lock / versioning), so a compromised
server can't wipe the copy.

Test a restore from the copy once, with the same passwords from `credentials/`.

## Where everything lives

**On the controller**

| Path | What |
|---|---|
| `inventories/<fleet>/hosts.yml` | machines and per-machine settings |
| `inventories/<fleet>/group_vars/all/` | fleet settings, vault |
| `inventories/<fleet>/credentials/` | generated secrets and `ca.crt`: **back up** |
| `.venv/` | Ansible installed by the wizard |

**On the backup server**

| Path | What |
|---|---|
| `/srv/restic-fleet/<machine>/` | repositories (`fleet_data_dir`) |
| `/etc/restic-fleet/tls/` | CA key and certificate, server certificate |
| `/etc/restic-fleet/rest-server.htpasswd` | machine logins (bcrypt) |
| `/etc/restic-fleet/repos/<machine>.pass` | repository passwords (maintenance on only) |
| `/etc/restic-fleet/server.json` | settings for the collect and maintain tasks |
| `/etc/restic-fleet/dashboard.json` | dashboard settings (token hashes, notifications) |
| `/opt/restic-fleet/` | dashboard program and static files |
| `/var/lib/restic-fleet-dashboard/` | dashboard database and users |
| `/usr/local/bin/restic`, `/usr/local/bin/rest-server` | binaries |
| `/usr/local/sbin/restic-fleet-server` | collect and maintain script |

**On each machine**

| Path | What |
|---|---|
| `/etc/restic-fleet/fleet.env` | repository address and login (0600) |
| `/etc/restic-fleet/repo.pass` | repository password (0600) |
| `/etc/restic-fleet/report.header` | dashboard token (0600) |
| `/etc/restic-fleet/ca.crt` | the fleet CA |
| `/etc/restic-fleet/paths.txt`, `excludes.txt` | what to back up and skip |
| `/etc/restic-fleet/dumps/<job>.sh` | database dump commands |
| `/etc/restic-fleet/hooks/pre`, `post` | hooks |
| `/var/cache/restic-fleet/` | restic cache (safe to delete) |
| `/usr/local/sbin/restic-fleet-backup` | the backup run |
| `/usr/local/bin/rfleet` | restore and admin helper |

## Services and timers

| Unit | Where | What |
|---|---|---|
| `restic-fleet-rest-server.service` | server | the repository server (:8000) |
| `restic-fleet-dashboard.service` | server | the dashboard (:8443) |
| `restic-fleet-collect.timer` | server | repository sizes and snapshot counts, every 10 min |
| `restic-fleet-maintain.timer` | server | unlock, forget/prune, check: weekly |
| `restic-fleet-backup.timer` / `.service` | machines | the scheduled backup |

```bash
systemctl list-timers 'restic-fleet-*'
journalctl -u restic-fleet-maintain.service -n 100
journalctl -u restic-fleet-dashboard.service -f
```
