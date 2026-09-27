# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
"Report a vulnerability" (Security → Advisories) on this repository, or email the maintainers.
Include what you found, how to reproduce it and the impact you expect. You'll get an
acknowledgement within a week, and a fix or mitigation plan as soon as it's understood.

## Scope

In scope: the Ansible roles, `setup.sh`, the dashboard, and the scripts installed on servers
(`restic-fleet-backup`, `rfleet`, `restic-fleet-server`, `htpasswd-sync`).

Out of scope: vulnerabilities in restic or rest-server themselves (report those to
[restic](https://github.com/restic/restic/security)), and setups that deliberately weaken the
defaults (for example exposing the dashboard without TLS).

## Design notes

The threat model, what each component can and cannot do if compromised, and the
server-maintenance trade-off are described in [docs/security.md](docs/security.md).
