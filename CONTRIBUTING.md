# Contributing

Thanks for helping! Start with [docs/architecture.md](docs/architecture.md) for how the pieces fit
together. A few ground rules keep restic-fleet dependable:

- **Keep it boring.** The dashboard and server tools use the Python standard library only; the
  roles use `ansible.builtin` modules only. Please don't add dependencies without a strong reason.
- **Secrets never touch a command line or a log.** Pass them on stdin or in 0600 files, and mark
  Ansible tasks that handle them `no_log: true`.
- **Deploys must be idempotent.** A second `site.yml` run must report `changed=0`.
- **Test it.** Run before opening a pull request:

  ```bash
  python -m unittest discover -s tests/unit -v
  shellcheck -x setup.sh tests/e2e/run.sh roles/*/files/*[!.py]
  tests/e2e/run.sh          # needs Docker; ~5 minutes
  ```

- New backup types (databases, …) need an end-to-end check that restores them.
- When bumping restic or rest-server, update both the version and the SHA-256 values from the
  release's `SHA256SUMS` in `roles/fleet_common/defaults/main.yml`.
- User-facing changes need a line in [CHANGELOG.md](CHANGELOG.md) and, where it applies, an update
  to the docs in `docs/`. Dashboard UI changes follow the [design system](docs/design-system.md).
