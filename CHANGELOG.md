# Changelog

All notable changes to restic-fleet are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security

- Server maintenance no longer prunes a repository that contains snapshots whose time doesn't
  match when the server received them. A compromised machine could otherwise upload back-dated
  snapshots so that retention deleted its real backups, defeating append-only mode
  (`fleet_maintenance_verify_snapshot_times`, on by default).
- Dashboard: a client that opened a TCP connection and never completed the TLS handshake blocked
  every other connection. Handshakes now run per connection with a timeout, and connections are
  capped at 256.
- Dashboard: behind `fleet_dashboard_trusted_proxy`, the sign-in rate limit used the first
  (client-controlled) `X-Forwarded-For` entry and could be bypassed; it now uses the proxy's hop.
- Dashboard: concurrent scrypt checks are capped (memory exhaustion), password changes end
  existing sessions, and `webhook_url` must be http(s).
- Maintenance and statistics run as a new `restic-maint` user, the only one able to read repository
  passwords. rest-server (network-facing) now runs with `--group-accessible-repos` and can no longer
  read them; existing repositories are shared with the `restic` group on deploy.
- The dashboard's sign-in page and status are only served to `fleet_dashboard_allow_from`, now
  private, VPN and loopback ranges by default (was `any`), and enforced by the dashboard itself, not
  only by ufw. Machine reports are unaffected. The old allow-anyone ufw rule is removed. **If you open
  the dashboard over the internet, set `fleet_dashboard_allow_from` before upgrading.**
- `./setup.sh new` asks whether the server should hold repository passwords (maintenance) and who
  may open the dashboard; every deploy states the maintenance mode.
- CI: actions pinned to commit SHAs, checkout without persisted credentials, new security job
  (gitleaks over full history, bandit, pip-audit), Dependabot for actions.

### Added

- Automatic restore test: every 28 days (`fleet_restore_test_days`), maintenance streams the latest
  snapshot of every job through `restic dump`, decrypting and reassembling every file.

### Fixed

- A path in `backup_paths` that doesn't exist now fails the files job and the systemd unit (it was
  a warning, and systemd reported success).

- Per-machine `backup_retention` now overrides individual keys of `fleet_retention`, as documented.
  It used to replace the whole policy, so `{ daily: 14 }` silently dropped weekly, monthly and
  yearly snapshots.
- Retention settings are validated on deploy and on the server; unknown keys or bad values stop
  instead of changing what is kept.
- Statistics collection crashed (for every machine) on a snapshot time with no fractional seconds.
  A future-dated snapshot no longer hides an overdue machine.
- `restic init` only runs when the repository doesn't exist (exit 10); a wrong password or a TLS
  error now stops the deploy with a clear message.
- Backups wait up to an hour for the weekly prune's lock instead of failing, report restic's exit
  codes in words, and flag empty snapshots (0 bytes) as warnings.
- `rfleet restore` asks for confirmation before restoring over `/`, `/etc`, `/usr` and other system
  directories (`--dry-run` is always allowed).
- Database job `filename` is validated.
- The e2e test reads root-owned credentials through the controller container (fixes CI on Linux).

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
