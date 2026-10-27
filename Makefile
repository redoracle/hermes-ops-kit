# Hermes Ops Kit — Makefile
# Common development and operations commands.

SHELL := /bin/bash
PYTHON := python3
OPS_PY := PYTHONPATH=$(CURDIR) $(PYTHON) -P -m
PIP := $(PYTHON) -m pip
PYTEST := $(PYTHON) -m pytest
RUFF := ruff
SRC := .

.PHONY: help install install-dev format lint compile test test-verbose security-scan clean build doctor usage status assistants check-python

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[1;34m%-20s\033[0m %s\n", $$1, $$2}'

# ── Install ─────────────────────────────────────────────────────

install: ## Install for current user (editable)
	$(PIP) install --user -e .

install-dev: ## Install with dev dependencies
	$(PIP) install --user -e ".[dev]"

install-pipx: ## Install via pipx (standalone)
	pipx install -e .

# ── Format & Lint ───────────────────────────────────────────────

format: ## Format all Python files
	$(RUFF) format $(SRC)

lint: ## Lint all Python files
	$(RUFF) check $(SRC)

lint-fix: ## Lint and auto-fix
	$(RUFF) check --fix $(SRC)

check: lint compile test ## Run lint, syntax checks, and tests

# ── Compile / Syntax ────────────────────────────────────────────

compile: ## Syntax-check all Python files
	$(PYTHON) -m compileall -q -x '(^|/)([.]venv|venv|[.]git|__pycache__)/' $(SRC)

# ── Test ────────────────────────────────────────────────────────

test: ## Run test suite
	$(PYTEST) tests/ -v

test-quiet: ## Run tests (quiet)
	$(PYTEST) tests/ -q

test-security: ## Run security tests only
	$(PYTEST) tests/test_security.py -v

test-snapshots: ## Run snapshot tests
	$(PYTEST) tests/test_snapshots.py -v

simulate: ## Run all simulators
	$(PYTHON) tests/test_simulator.py --all

simulate-failure: ## Simulate provider failure
	$(PYTHON) tests/test_simulator.py --scenario provider-offline

simulate-leak: ## Simulate secret leak
	$(PYTHON) tests/test_simulator.py --scenario secret-leak

# ── Security ────────────────────────────────────────────────────

SECURITY_SCANNER ?= gitleaks
security-scan: ## Scan working-tree secrets (requires gitleaks; existing allowlist applies)
	$(SECURITY_SCANNER) detect --source $(SRC) --no-git --redact --config "$(CURDIR)/.gitleaks.toml"

# ── Build ───────────────────────────────────────────────────────

build: ## Build Python wheel
	$(PYTHON) -m build --wheel

build-dist: ## Build wheel + sdist
	$(PYTHON) -m build

# ── Clean ───────────────────────────────────────────────────────

clean: ## Remove build artifacts
	@rm -rf dist/ build/ *.egg-info/ .pytest_cache/ __pycache__/
	@find $(SRC) -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	@find $(SRC) -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "clean: done"

clean-all: clean ## Remove all artifacts including .mypy_cache
	@rm -rf .mypy_cache/ .ruff_cache/
	@echo "clean-all: done"

# ── Doctor / Status ─────────────────────────────────────────────

check-python: ## Verify Python >= 3.11 (required for -P flag)
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || \
		{ echo "FAIL: Python 3.11+ required (-P flag)"; exit 1; }

doctor: check-python ## Run key rotation doctor
	$(OPS_PY) hermes_ops_kit.hermes_key_rotate --doctor-secrets 2>/dev/null || \
	$(OPS_PY) hermes_ops_kit.hermes_key_rotate --healthcheck

usage: check-python ## Show usage metrics (compact)
	$(OPS_PY) hermes_ops_kit.usage_metrics_v2 --compact

usage-full: check-python ## Show usage metrics (full)
	$(OPS_PY) hermes_ops_kit.usage_metrics_v2

usage-json: check-python ## Show usage metrics (JSON)
	$(OPS_PY) hermes_ops_kit.usage_metrics_v2 --json

status: check-python ## Show assistant registry status
	$(OPS_PY) hermes_ops_kit.hermes_assistant_manager list --config hermes_ops_kit/config/assistants.yaml

assistants: check-python ## Show detailed assistant info
	$(OPS_PY) hermes_ops_kit.hermes_assistant_manager list --config hermes_ops_kit/config/assistants.yaml --json | $(PYTHON) -m json.tool

ping-orace: check-python ## Ping Orace assistant
	$(OPS_PY) hermes_ops_kit.hermes_assistant_manager ping orace --config hermes_ops_kit/config/assistants.yaml --json 2>/dev/null | $(PYTHON) -m json.tool

# ── Git ─────────────────────────────────────────────────────────

git-push: test security-scan ## Run tests + security scan then push
	git push origin main

# ── All ─────────────────────────────────────────────────────────

all: lint compile test security-scan ## Run all checks (format, lint, compile, test, security)
	@echo ""
	@echo "✅ all checks passed"
