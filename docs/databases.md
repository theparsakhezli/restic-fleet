# Databases

Copying a live database's files usually gives you a backup that won't start. restic-fleet
instead takes a proper dump with the database's own tool and **streams it straight into restic**:

```
pg_dump ──stdout──► restic backup --stdin-from-command ──► encrypted snapshot
```

- **No temporary files.** You don't need free disk space the size of your database.
- **No silent corruption.** If the dump command exits with an error, restic discards the snapshot
  and the job is reported as **Failed**. A half-written dump is never stored as a success.
- **Its own history.** Each database is a separate job with its own snapshot, tag and retention.
- **Good deduplication.** Plain-SQL dumps change little from day to day, so each new dump adds
  only a fraction of its size.

- [Adding a database](#adding-a-database)
- [PostgreSQL](#postgresql)
- [MySQL and MariaDB](#mysql-and-mariadb)
- [MongoDB](#mongodb)
- [Anything else (`command`)](#anything-else-command)
- [Restoring a database](#restoring-a-database)
- [Checking a dump job](#checking-a-dump-job)

## Adding a database

Answer **y** to "Back up a database?" in the wizard, or add entries under the machine in
`hosts.yml` and deploy:

```yaml
    db1:
      backup_databases:
        - name: app           # the job name: letters, digits, . _ -
          type: postgres      # postgres | mysql | mongodb | command
          container: app-db   # omit for a database installed directly on the machine
          database: app       # omit or "all" for every database
```

Common options:

| Option | Types | Default | Description |
|---|---|---|---|
| `name` | all | *required* | Job name, shown on the dashboard as `db-<name>` and used in `rfleet db <name>`. |
| `type` | all | *required* | `postgres`, `mysql`, `mongodb` or `command`. |
| `container` | postgres, mysql, mongodb | — | Docker container to run the dump tool in (`docker exec`). |
| `database` | postgres, mysql, mongodb | `all` | One database, or `all`. |
| `user` | postgres, mysql | `postgres` / `root` | Database user. |
| `options` | postgres, mysql, mongodb | `""` | Extra flags for the dump tool. |
| `defaults_file` | mysql (native) | `/etc/mysql/debian.cnf` | MySQL option file with the login. |
| `command` | command | *required* | Shell command that writes the backup to stdout. |
| `filename` | all | `<name>.sql` / `.archive` / `.dump` | File name of the dump inside the snapshot. |

Remove an entry and deploy, and its dump script is removed from the machine. Its old snapshots stay
in the repository until retention removes them.

## PostgreSQL

```yaml
- name: app                 # one database, in Docker
  type: postgres
  container: app-db
  database: app
  user: postgres

- name: pg-all              # every database + roles, installed on the machine
  type: postgres
```

| | One database | `all` |
|---|---|---|
| Tool | `pg_dump` | `pg_dumpall` (includes roles and tablespaces) |
| Format | plain SQL | plain SQL |
| Docker | `docker exec <container> pg_dump -U <user> <db>` | same with `pg_dumpall` |
| Native | runs as the OS user `<user>` (`runuser -u postgres`) through the local socket | same |

For large databases you can use the custom format, which restores in parallel:
`options: "-Fc"` together with `filename: app.dump`.

## MySQL and MariaDB

```yaml
- name: shop                # in Docker
  type: mysql
  container: shop-db
  database: shop

- name: mysql-all           # installed on the machine
  type: mysql
  defaults_file: /etc/mysql/debian.cnf
```

- Uses `mariadb-dump` when available, otherwise `mysqldump`.
- Flags: `--single-transaction --quick --routines --events --triggers --hex-blob`. InnoDB tables
  are dumped consistently **without locking**. MyISAM tables are not transactional; stop writers
  with a pre-hook if you rely on them.
- **Password in Docker:** taken from the container's own `MYSQL_ROOT_PASSWORD` or
  `MARIADB_ROOT_PASSWORD` environment variable, so it never appears in your inventory or on a
  command line.
- **Password natively:** read from an option file (`defaults_file`, default
  `/etc/mysql/debian.cnf`). To use a dedicated backup user, create one:

  ```sql
  CREATE USER 'backup'@'localhost' IDENTIFIED BY '…';
  GRANT SELECT, SHOW VIEW, TRIGGER, EVENT, LOCK TABLES, RELOAD, PROCESS ON *.* TO 'backup'@'localhost';
  ```

  ```ini
  # /root/.backup.cnf  (chmod 600)
  [client]
  user=backup
  password=…
  ```

  Then set `defaults_file: /root/.backup.cnf`.

## MongoDB

```yaml
- name: mongo
  type: mongodb
  container: mongo          # or omit for a native install
  # database: app           # default: all databases
  # options: "--uri=mongodb://backup:…@localhost:27017/?authSource=admin"
```

Runs `mongodump --archive`, one stream stored as `<name>.archive`. On a replica set, add
`options: "--oplog"` for a point-in-time consistent dump.

## Anything else (`command`)

Any command that writes the backup to **stdout** and exits non-zero on failure:

```yaml
- name: redis
  type: command
  command: "redis-cli --rdb -"
  filename: dump.rdb

- name: sqlite
  type: command
  command: "sqlite3 /srv/app/app.db .dump"
  filename: app.sql

- name: vault-config
  type: command
  command: "tar -C /srv/vault -cf - config"
  filename: vault-config.tar
```

The command runs as root under `sh -c` on the machine. If it's a pipeline, start it with
`set -o pipefail;` (bash) or make sure the last command fails when an earlier one does. Otherwise
a failing first step can go unnoticed.

## Restoring a database

`rfleet db <name>` writes the latest dump to stdout. Add a snapshot ID for an older one:

```bash
rfleet snapshots --tag app                   # list the dumps of job "app"
rfleet db app > app.sql                      # latest
rfleet db app 4f2a9c1e > app-tuesday.sql     # a specific snapshot
```

Then load it with the database's own tool:

```bash
# PostgreSQL (one database): into a fresh database
createdb -U postgres app_restored
psql -U postgres -d app_restored -f app.sql
#   in Docker:  docker exec -i app-db psql -U postgres -d app_restored < app.sql
#   custom format (-Fc):  pg_restore -U postgres -d app_restored -j 4 app.dump

# PostgreSQL (all): into an empty cluster
psql -U postgres -f pg-all.sql postgres

# MySQL / MariaDB
mysql < shop.sql                               # the dump contains CREATE DATABASE
#   in Docker:  docker exec -i shop-db sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"' < shop.sql

# MongoDB
mongorestore --archive=mongo.archive --drop
#   in Docker:  docker exec -i mongo mongorestore --archive --drop < mongo.archive

# Redis
systemctl stop redis && cp dump.rdb /var/lib/redis/dump.rdb && chown redis: /var/lib/redis/dump.rdb && systemctl start redis
```

Big dumps can go straight into the database without a file:

```bash
rfleet db app | docker exec -i app-db psql -U postgres -d app_restored
```

Restore into a **new** database first, check it, then switch over. Don't overwrite production
before you know the dump is the one you want.

## Checking a dump job

```bash
sudo /etc/restic-fleet/dumps/app.sh | head      # does the dump command work?
rfleet run && rfleet logs -f                     # run all jobs now and watch
rfleet snapshots --tag app                       # is there a new snapshot?
rfleet db app | tail -5                          # does it end cleanly? (pg_dump ends with "PostgreSQL database dump complete")
```
