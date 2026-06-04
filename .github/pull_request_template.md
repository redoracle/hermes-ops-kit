## Description

<!-- What does this PR do? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Security hardening
- [ ] Refactor / cleanup

## Checklist

- [ ] Tests pass: `python3 -m pytest tests/ -v`
- [ ] Simulator scenarios pass: `python3 tests/test_simulator.py --all`
- [ ] No secrets: `make security-scan`
- [ ] Ruff clean: `make check`
- [ ] Docs updated (CLI help, README, or docs/*.md as appropriate)
- [ ] CHANGELOG entry added

## Boundary check

- [ ] This change stays in ops-kit's lane (secrets, keys, metrics, routing
  config, audit, MCP — does not reimplement Hermes core)

## How to verify

<!-- Commands for the reviewer to run -->
