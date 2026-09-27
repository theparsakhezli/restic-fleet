# Changelog

All notable changes to restic-fleet are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-27

First public release.

### Added

- Interactive `setup.sh` wizard: create a fleet, add machines, deploy, show login, check SSH.
- Ansible roles for a restic backup server (rest-server in append-only, private-repository mode,
  private CA with TLS) and for the machines (systemd timer, files and database jobs, hooks).
- Database dumps streamed into restic for PostgreSQL, MySQL/MariaDB and MongoDB (in Docker or
  native) and for any custom command.
- Server-side collection of repository statistics, and weekly unlock, forget/prune and integrity
  check.
- Dashboard: fleet verdict, machine cards with a 30-day history, details panel with restore
  commands, Telegram and webhook alerts, Prometheus metrics.
- `rfleet` helper on every machine, including `rfleet db <job>` for database restores.
- Logo, design system and documentation.
- Unit tests and an end-to-end test suite in Docker.
