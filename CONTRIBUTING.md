# Contributing to Hermes Ops Kit

Thanks for contributing! Hermes Ops Kit is an operational/security plugin for
the [Hermes Agent](https://github.com/NousResearch/hermes-agent). It extends
Hermes with secret management, key rotation, provider health checks, route
diagnostics, MCP auditing, and usage/cost observability.

## Development Setup

```bash
git clone https://github.com/redoracle/hermes-ops-kit.git
cd hermes-ops-kit
pip install -e ".[dev]"
```

Requires Python 3.11+. Dependencies are minimal: `requests`, `PyYAML`, `Pillow`.

## Code Style

- **Linter:** ruff (`ruff check .`)
- **Formatter:** ruff format (`ruff format .`)
- Python 3.11+ with `from __future__ import annotations`
- Type hints for public interfaces (checked by Pyright)
- Docstrings for modules and public functions
- Keep it simple — no unnecessary abstractions

Run format and lint before committing:

```bash
make format
make lint
```

## Running Tests

```bash
# Full test suite
python3 -m pytest tests/ -v

# Simulator scenarios (no real API calls)
python3 tests/test_simulator.py --all

# Security tests
python3 -m pytest tests/test_security.py -v

# CLI integration tests
python3 -m pytest tests/cli/ -v
```

The real-core contract tests (`tests/test_hermes_contract.py`) skip unless you point them
at a Hermes checkout and its committed interpreter:

```bash
export HERMES_AGENT_SOURCE="$HOME/.hermes/hermes-agent"
# the environment Hermes itself runs, not an arbitrary venv under ~/.hermes/installs:
export HERMES_RUNTIME_PYTHON="$(python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["HERMES_AGENT_SOURCE"])
from pathlib import Path
from pm.environments import committed_venv, venv_python
print(venv_python(committed_venv(Path(os.environ["HERMES_AGENT_SOURCE"]))))
PY
)"
```

Tests use a temporary `HERMES_HOME`; one that resolves the real `~/.hermes` fails by design.

## Architecture

See `docs/architecture.md` for the module map and data flow. Key conventions:

- **Subprocess-based adapters.** `bridge.py` invokes provider adapters as
  standalone scripts communicating via JSON on stdout.
- **Shared redaction.** All modules import `redact()` from `security/redaction.py`.
- **SecretBackend Protocol.** Provider rotators depend ONLY on `SecretBackend` —
  never on Bitwarden/Vaultwarden internals.
- **No raw secrets in any output path.** Every write is gated by
  `secret_scanner.assert_clean()`.

## Submitting Changes

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Run `make check` (format + lint + test)
6. Verify `make security-scan` passes
7. Submit a pull request

### PR Checklist

- [ ] Tests pass: `python3 -m pytest tests/ -v`
- [ ] No secrets in code: `make security-scan`
- [ ] Ruff clean: `make check`
- [ ] Docs updated if you changed CLI behavior or config formats
- [ ] Relevant CHANGELOG entry added

Dependabot PRs are integrated by a maintainer; see the *Dependency updates* section of
`docs/Operations Runbook.md`. Pull requests that change `package.json`, `package-lock.json` or
`.releaserc.json` also run the `release-config` check (`scripts/check-release-notes.mjs`); see
*Release approval* in `docs/Operations Runbook.md`.

## Boundaries

Hermes Ops Kit is an **operational/security plugin** — it does NOT replace:
- Hermes runtime routing
- Model invocation
- Memory
- Skills
- Scheduler
- Gateway
- Auxiliary route resolution

If your change overlaps with Hermes core functionality, it belongs upstream
in [Hermes Agent](https://github.com/NousResearch/hermes-agent) instead.

## License

By contributing, you agree your contributions will be licensed under the
MIT License.
