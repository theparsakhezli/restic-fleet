# Restoring

Pick the situation you're in:

| Situation | Go to |
|---|---|
| I deleted or broke a file or folder | [Restore files](#restore-files) |
| I need a database back | [Restore a database](#restore-a-database) |
| I want to look around first | [Browse snapshots](#browse-snapshots) |
| The machine is gone and I'm rebuilding it | [Restore a whole machine](#restore-a-whole-machine) |
| I need the data somewhere else | [Restore from another computer](#restore-from-another-computer) |
| The backup server is gone | [If the backup server is lost](#if-the-backup-server-is-lost) |

Every machine has `rfleet`, which is `restic` with this machine's repository, password and CA
already set. Any restic command works: `rfleet <command>`. It runs as root through sudo.

> [!TIP]
> Practise before you need it. Once a month, restore one folder and one database to `/tmp` and
> check them. The dashboard's restore panel gives you the exact commands for each machine.

## Find the right snapshot

```bash
rfleet snapshots                          # everything, newest last
rfleet snapshots --tag files              # file backups only
rfleet snapshots --tag app                # dumps of database job "app"
rfleet snapshots --tag files --latest 5   # the last 5
```

```
ID        Time                 Host  Tags      Paths
---------------------------------------------------------------------
4f2a9c1e  2026-09-25 02:41:07  web1  files     /etc, /srv/app
9b1d07aa  2026-09-25 02:41:52  web1  db,app    /app.sql
```

Each job has its own snapshots. **`latest` always needs a tag** (`--tag files` or `--tag <db>`);
without one, "latest" might be a database dump instead of your files.

## Restore files

```bash
# one folder, from the latest file backup, into /tmp/restore
rfleet restore latest --tag files --target /tmp/restore --include /etc/nginx

# a single file from a specific snapshot
rfleet restore 4f2a9c1e --target /tmp/restore --include /srv/app/config.yml

# find which snapshots have a file
rfleet find --tag files '/srv/app/config.yml'

# print a file straight to the terminal
rfleet dump latest --tag files /etc/nginx/nginx.conf
```

Files are restored under the target with their full path (`/tmp/restore/etc/nginx/...`), with
their owners, permissions and timestamps. Compare, then copy back:

```bash
diff -r /tmp/restore/etc/nginx /etc/nginx
rsync -a /tmp/restore/etc/nginx/ /etc/nginx/
```

Restoring straight over the live path (`--target /`) works, but use it only when you're sure. It
overwrites files without asking.

## Restore a database

```bash
rfleet db app > app.sql              # latest dump of job "app"
rfleet db app 9b1d07aa > app.sql     # a specific snapshot
```

Then load it with the database's tool. Step-by-step commands for PostgreSQL, MySQL/MariaDB,
MongoDB and Redis are in [Databases → Restoring](databases.md#restoring-a-database).

## Browse snapshots

Mount every snapshot as a read-only folder (needs FUSE: `apt install fuse3`):

```bash
mkdir -p /mnt/backup
rfleet mount /mnt/backup &
ls /mnt/backup/tags/files/latest/srv/app
cp /mnt/backup/snapshots/2026-09-20T02:40:13+02:00/etc/hosts /tmp/
fusermount -u /mnt/backup
```

Or list without mounting: `rfleet ls latest --tag files /srv/app`.

## Restore a whole machine

The machine died, and you have a fresh server with the same name in the inventory.

1. **Reinstall it with restic-fleet.** Point `ansible_host` at the new server and deploy:

   ```bash
   ./setup.sh deploy prod --limit web1
   ```

   It gets the *same* repository and password, because both come from `credentials/`. The dashboard
   now shows the old machine's history.

2. **Stop the timer** so a new, nearly empty backup doesn't become "latest" while you restore:

   ```bash
   systemctl stop restic-fleet-backup.timer
   ```

3. **Restore files**, reinstall your software, and **restore databases**:

   ```bash
   rfleet restore latest --tag files --target / --include /etc/myapp --include /srv/app
   rfleet db app > /tmp/app.sql     # then load it; see databases.md
   ```

   Restore application data and config, not the whole system. Reinstall the OS and packages
   normally, then put your data back. Restoring all of `/etc` over a different OS version causes
   more trouble than it solves.

4. **Start backups again:** `systemctl start restic-fleet-backup.timer`, then `rfleet run`.

## Restore from another computer

You can read a repository from any computer with restic and the credentials, for example your
laptop:

```bash
F=inventories/prod/credentials
export RESTIC_REPOSITORY=rest:https://203.0.113.10:8000/web1/
export RESTIC_REST_USERNAME=web1
export RESTIC_REST_PASSWORD="$(cat $F/rest-password/web1)"
export RESTIC_PASSWORD_FILE=$F/repo-password/web1
export RESTIC_CACERT=$F/ca.crt

restic snapshots
restic restore latest --tag files --target ./web1-restore --include /srv/app
```

This works through the append-only server: reading is always allowed. The server's firewall
(ufw) must let your address reach port 8000.

**On the backup server itself** (as root), repositories are ordinary folders:

```bash
restic -r /srv/restic-fleet/web1 --password-file /etc/restic-fleet/repos/web1.pass snapshots
```

`/etc/restic-fleet/repos/` exists only with `fleet_server_maintenance: true`. Otherwise use
`--password-file` with the file from your `credentials/`.

## If the backup server is lost

The machines are unaffected and keep their data. Their backups fail until a server is back.

- **If you have an [off-site copy](operations.md#off-site-copy)**, restore the repositories into
  the new server's `fleet_data_dir`, keep the same server name and address (or update
  `fleet_public_address`), and deploy. Existing repositories are detected, not re-created.
- **If you have no copy**, the old backups are gone. Deploy a new server: the machines get new,
  empty repositories and start backing up again that night.

The CA is created on the backup server, so a new server gets a new CA. Deploy **all** machines
(`./setup.sh deploy prod`) so they trust it, and re-import `credentials/ca.crt` into your browser.

## Restore checklist

- [ ] Found the right snapshot (`rfleet snapshots --tag …`), and checked the time
- [ ] Restored into a **separate** location first (`/tmp/restore`, a new database)
- [ ] Checked the result: files open, the database has the expected rows
- [ ] Swapped it in, and restarted the service
- [ ] Made sure backups are running again (`rfleet status`, dashboard is green)
