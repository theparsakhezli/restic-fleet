## What and why

<!-- What does this change, and what problem does it solve? -->

## Checklist

- [ ] `python -m unittest discover -s tests/unit` passes
- [ ] `ansible-lint site.yml` and ShellCheck are clean
- [ ] `tests/e2e/run.sh` passes (for changes to roles or scripts)
- [ ] A second deploy still reports `changed=0`
- [ ] Docs and CHANGELOG updated for user-facing changes
- [ ] No secret can reach a command line or a log
