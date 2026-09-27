<p align="center"><img src="../design/logo/restic-fleet-logo.svg" alt="restic-fleet" width="280"></p>

# restic-fleet documentation

New here? Start with **[Getting started](getting-started.md)**. It goes from zero to a first
verified backup in about 15 minutes.

## Setting up

- **[Getting started](getting-started.md)**: prerequisites, the wizard step by step, and checking your first backup.
- **[Configuration](configuration.md)**: every setting, including what to back up, schedules, retention, hooks, alerts and the firewall.
- **[Databases](databases.md)**: PostgreSQL, MySQL/MariaDB, MongoDB and custom commands.

## Using it

- **[The dashboard](dashboard.md)**: what each status means, alerts, users, Prometheus, and running it behind a reverse proxy.
- **[Restoring](restoring.md)**: a single file, a database, a whole machine, or a restore after a disaster.
- **[Operations](operations.md)**: adding and removing machines, upgrades, rotating secrets, off-site copies, and where files live.
- **[Troubleshooting](troubleshooting.md)**: common errors and their fixes.

## Understanding it

- **[Architecture](architecture.md)**: the components, how data flows, and the report API.
- **[Security](security.md)**: the threat model, the maintenance trust trade-off, and the hardening in place.
- **[Design system](design-system.md)**: logo, colours, status language and components.

## Words used in these docs

| Word | Meaning |
|---|---|
| **fleet** | One backup server plus the machines that back up to it. It lives in `inventories/<fleet>/`. |
| **backup server** | The machine that stores the repositories and runs rest-server, the dashboard and maintenance. |
| **machine** / **client** | A server that is backed up. Each one has exactly one repository. |
| **repository** | A restic repository, stored on the backup server at `/srv/restic-fleet/<machine>/`. |
| **job** | One thing a machine backs up: `files`, or one database. Each job makes its own snapshot. |
| **snapshot** | One backup of one job at one point in time. |
| **controller** | The computer you run `./setup.sh` / Ansible from. It holds the credentials. |
