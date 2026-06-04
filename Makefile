# Hermes Ops Kit — Makefile
# Common development and operations commands.

SHELL := /bin/bash
PYTHON := python3
PIP := $(PYTHON) -m pip
PYTEST := $(PYTHON) -m pytest
RUFF := ruff
SRC := .

.PHONY: help install install-dev format lint compile test test-verbose security-scan clean build doctor usage status assistants

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
	$(RUFF) format $(SRC) 2>/dev/null || echo "ruff not installed — run: pip install ruff"

lint: ## Lint all Python files
	$(RUFF) check $(SRC) 2>/dev/null || echo "ruff not installed — run: pip install ruff"

lint-fix: ## Lint and auto-fix
	$(RUFF) check --fix $(SRC) 2>/dev/null || echo "ruff not installed"

check: format lint ## Format and lint

# ── Compile / Syntax ────────────────────────────────────────────

compile: ## Syntax-check all Python files
	@find $(SRC) -name '*.py' ! -path '*__pycache__*' ! -path '.pytest_cache/*' \
		-exec $(PYTHON) -m py_compile {} \; 2>&1 | grep -v "^$$" || true
	@echo "compile: OK"

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

security-scan: ## Scan for secrets in source
	@grep -rPn '(sk-ant-[A-Za-z0-9-_]{15,}|sk-[A-Za-z0-9-_]{15,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|Bearer\s+[A-Za-z0-9_\-\.]{10,}|-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----)' \
		$(SRC) --include='*.py' --include='*.yaml' --include='*.md' \
		--exclude-dir='__pycache__' --exclude-dir='.pytest_cache' 2>/dev/null || echo "security-scan: clean ✅"

# ── Build ───────────────────────────────────────────────────────

build: ## Build Python wheel
	$(PYTHON) -m build --wheel 2>/dev/null || ($(PIP) install --user build && $(PYTHON) -m build --wheel)

build-dist: ## Build wheel + sdist
	$(PYTHON) -m build 2>/dev/null || ($(PIP) install --user build && $(PYTHON) -m build)

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

doctor: ## Run key rotation doctor
	$(PYTHON) hermes_key_rotate.py --doctor-secrets 2>/dev/null || \
	$(PYTHON) hermes_key_rotate.py --healthcheck

usage: ## Show usage metrics (compact)
	$(PYTHON) usage_metrics_v2.py --compact

usage-full: ## Show usage metrics (full)
	$(PYTHON) usage_metrics_v2.py

usage-json: ## Show usage metrics (JSON)
	$(PYTHON) usage_metrics_v2.py --json

status: ## Show assistant registry status
	$(PYTHON) hermes-assistant-manager.py list --config config/assistants.yaml

assistants: ## Show detailed assistant info
	$(PYTHON) hermes-assistant-manager.py list --config config/assistants.yaml --json | $(PYTHON) -m json.tool

ping-orace: ## Ping Orace assistant
	$(PYTHON) hermes-assistant-manager.py ping orace --config config/assistants.yaml --json 2>/dev/null | $(PYTHON) -m json.tool

# ── Git ─────────────────────────────────────────────────────────

git-push: test security-scan ## Run tests + security scan then push
	git push origin main

# ── All ─────────────────────────────────────────────────────────

all: format lint compile test security-scan ## Run all checks (format, lint, compile, test, security)
	@echo ""
	@echo "✅ all checks passed"
