# Troubleshooting

Start with the logs. They almost always name the problem.

```bash
# on a machine
rfleet status
rfleet logs -n 100

# on the backup server
journalctl -u restic-fleet-rest-server -n 100
journalctl -u restic-fleet-dashboard -n 100
journalctl -u restic-fleet-maintain -n 100
```

- [Setup and deploy](#setup-and-deploy)
- [Backups](#backups)
- [A machine is overdue](#a-machine-is-overdue)
- [Databases](#databases)
- [Dashboard](#dashboard)
- [Maintenance](#maintenance)
- [Still stuck?](#still-stuck)

## Setup and deploy

**`UNREACHABLE! … Permission denied (publickey)`**
The controller can't SSH in. Test it with `ssh <user>@<host>`. Install your key with `ssh-copy-id`,
or set `ansible_ssh_private_key_file` in `hosts.yml`. Then run `./setup.sh check prod`.

**`Missing sudo password`**
Your user needs a password for sudo. Deploy with `-K`: `./setup.sh deploy prod -K`.

**`/usr/bin/python3: not found` on a target**
Ansible needs Python on every machine. Install it: `apt install -y python3` or `dnf install -y python3`.

**`Could not create a virtualenv`**
On Debian/Ubuntu controllers: `apt install python3-venv`, then run `./setup.sh` again.

**`checksum mismatch` while installing restic or rest-server**
The download doesn't match the pinned SHA-256. If you changed the version, copy the checksums from
that release's `SHA256SUMS`. If you didn't, something between you and GitHub altered the file.
Don't work around it.

**A deploy fails halfway**
It's safe to run it again. Every step checks the current state first.

## Backups

**`x509: certificate signed by unknown authority`**
The machine has an old CA, usually after the server was rebuilt or moved. Deploy that machine:
`./setup.sh deploy prod --limit web1`.

**`x509: certificate is valid for 10.0.0.5, not 203.0.113.10`**
The machine connects to a different address than the one in the certificate. Set
`fleet_public_address` on the server host to the address machines really use, then deploy
everything.

**`Fatal: unable to open config file: … 401 Unauthorized`**
The machine's rest-server login doesn't match. Deploy the server and the machine together:
`./setup.sh deploy prod --limit web1,backup`.

**`dial tcp …:8000: i/o timeout` or `connection refused`**
The machine can't reach rest-server. Check the firewall on the server (`ufw status`, cloud security
groups), and that the service runs: `systemctl status restic-fleet-rest-server`. Behind NAT, set
`fleet_source_address` on the machine so the ufw rule matches its real address.

**`repository is already locked`**
Backups can run side by side, so this usually means weekly maintenance (prune) was running at
that moment. The next scheduled backup will work. If it keeps happening, a run was killed hard
(reboot, OOM, `kill -9`) and left a lock behind. Clear it with `rfleet unlock`, which only removes
stale locks. Weekly maintenance does this too.

**Warning: `some files could not be read`**
Files vanished or changed while being read, or access was denied: sockets, temp files, files of
running programs. The backup is still valid. If the same paths show up every time, add them to
`backup_excludes`.

**`warning: could not report 'files' to the dashboard`**
The backup itself worked, but the report didn't arrive. Check the machine can reach
`https://<server>:8443` (firewall, `fleet_dashboard_allow_from`), and that its token matches
(deploy `--limit web1,backup`).

**Backups are slow**
The first backup reads everything, and later ones read only changed files. Make sure caches and
logs are excluded, and the cache (`/var/cache/restic-fleet`) isn't wiped between runs.
`rfleet logs` shows how much data each run added.

## A machine is overdue

Overdue means no successful backup for 1.5 × `backup_interval_hours`. Work through these in order:

1. **Is the machine up?** If it's down, overdue is the correct status.
2. **Is the timer active?** `systemctl list-timers restic-fleet-backup.timer` should show a next run.
   If not: `systemctl enable --now restic-fleet-backup.timer`.
3. **Did it run and fail?** Check `rfleet logs`. Failures also show on the dashboard as *Failed*,
   unless the report couldn't be sent. See the report warning above.
4. **Does the interval match the schedule?** A weekly schedule with `backup_interval_hours: 24` is
   overdue six days a week. Set it to `168`.

## Databases

**`Error response from daemon: No such container: app-db`**
The `container` name doesn't match `docker ps`. Compose projects often prefix names
(`myapp-db-1`). Use the exact name, or set `container_name:` in your compose file.

**PostgreSQL: `role "root" does not exist` / `Peer authentication failed`**
Set `user` to a role that exists (usually `postgres`). Natively, the dump runs as that OS user,
so the OS user and the database role must share a name, or `pg_hba.conf` must allow it.

**PostgreSQL: `server version mismatch`**
A native `pg_dump` is older than the server. Install the matching client version (for example
`postgresql-client-16`). Dumps in Docker use the container's own tools and don't have this problem.

**MySQL: `Access denied for user 'root'`**
In Docker, the container needs `MYSQL_ROOT_PASSWORD` or `MARIADB_ROOT_PASSWORD` in its
environment. Natively, check the `defaults_file`. See [Databases](databases.md#mysql-and-mariadb).

**`mysqldump: Couldn't execute 'FLUSH TABLES'` / lock errors**
The user lacks `RELOAD` or `LOCK TABLES`. Grant the privileges listed in [Databases](databases.md#mysql-and-mariadb).

**Test a dump by hand**
`sudo sh /etc/restic-fleet/dumps/<job>.sh | head` should print the start of a dump.

## Dashboard

**The browser warns about the certificate**
Expected: it's your fleet's own CA. See
[Trusting the certificate](dashboard.md#trusting-the-certificate).

**`cross-site request refused` when signing in**
The request looked like it came from another site. This happens behind a reverse proxy that
doesn't pass the `Host` header. Add `proxy_set_header Host $host;` (nginx). Opening the dashboard
by one address and posting to another has the same effect.

**`Too many attempts` when signing in**
10 failed logins in 15 minutes from one address. Wait 15 minutes. Behind a proxy, set
`fleet_dashboard_trusted_proxy: true`, or every user shares the proxy's address.

**Forgot the password**
Run `./setup.sh info`, or read `credentials/dashboard-admin-password`.

**The page shows "Connection lost"**
The dashboard stopped answering. Check `systemctl status restic-fleet-dashboard` on the server.

**No sizes or snapshot counts on the cards**
They're collected every 10 minutes by `restic-fleet-collect.timer`, so wait a few minutes after the
first backup. Snapshot counts need `fleet_server_maintenance: true`, because the server needs the
repository password to read them. Sizes are always shown.

## Maintenance

**🧹 maintenance failed: `repository is already locked`**
A backup was running during the whole lock-wait window (1 hour). Move `fleet_maintenance_schedule`
away from the backup times. The next run removes stale locks first.

**`check` reports errors**
Take it seriously. Run a full check on the server:
`restic -r /srv/restic-fleet/<m> --password-file /etc/restic-fleet/repos/<m>.pass check --read-data`.
Check the disk (`dmesg`, `smartctl`). restic's
[troubleshooting guide](https://restic.readthedocs.io/en/stable/077_troubleshooting.html) explains
repairs. Keep the next backups running: new snapshots are independent of damaged old packs.

## Still stuck?

Open an issue with:

- what you ran and the full error (**remove passwords, tokens and IP addresses**);
- the OS of the controller, the server and the machine;
- `ansible --version`, and `restic version` on the machine.
