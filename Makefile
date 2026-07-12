# Geno Makefile
# ==================
#
# Common development and experiment tasks.

.PHONY: all install dev test lint format clean docs experiment analyze release-check release-gate-templates release-gate-vscode release-gate-apps validate-builtin-parity validate-dependencies validate-supported-targets optional-test-collection sandbox-regression dependency-audit local-ci local-ci-release security security-bounty

PYTHON ?= python3

# Default target
all: test

# Install the package
install:
	pip install -e .

# Install with development dependencies
dev:
	pip install -e ".[dev]"

# Install with LLM API support
llm:
	pip install -e ".[llm]"

# Run all tests
test:
	$(PYTHON) -m pytest geno/tests/ -v

# Run tests with coverage
coverage:
	$(PYTHON) -m pytest geno/tests/ -v --tb=short --cov=geno --cov-report=term --cov-report=html --cov-fail-under=80 --timeout=60

# Lint code
lint:
	$(PYTHON) -m ruff check geno/ benchmark/ experiment/ analysis/
	$(PYTHON) -m ruff format --check geno/ benchmark/ experiment/ analysis/
	$(PYTHON) -m mypy geno/ --ignore-missing-imports --no-error-summary

# Format code
format:
	$(PYTHON) -m ruff check --fix geno/ benchmark/ experiment/ analysis/
	$(PYTHON) -m ruff format geno/ benchmark/ experiment/ analysis/

# Validate benchmark
validate:
	$(PYTHON) scripts/validate_benchmark.py --strict-budgets

validate-supported-targets:
	$(PYTHON) scripts/validate_supported_targets.py

validate-builtin-parity:
	$(PYTHON) scripts/validate_builtin_parity.py

validate-dependencies:
	$(PYTHON) scripts/validate_dependencies.py

optional-test-collection:
	$(PYTHON) -m pytest --collect-only geno/tests/test_backend_parity.py geno/tests/test_fuzzing.py geno/tests/test_property_based.py geno/tests/test_differential_fuzzing.py -q

sandbox-regression:
	$(PYTHON) -m pytest geno/tests/test_cli.py::TestGenoRun::test_run_simple_program geno/tests/test_compiler.py::TestCompilerCollectionSizeLimits::test_compiled_literal_results_honor_process_collection_limit geno/tests/test_server.py::TestPostRun::test_valid_source -q

dependency-audit:
	$(PYTHON) -m pip_audit --require-hashes -r requirements.lock --strict --progress-spinner off
	$(PYTHON) -m pip_audit --require-hashes -r requirements-dev.lock --strict --progress-spinner off

# Validate init templates (scaffold + check + test)
release-gate-templates:
	PYTHON=$(PYTHON) bash scripts/release-gate-templates.sh

# Validate VS Code extension packaging
release-gate-vscode:
	PYTHON=$(PYTHON) bash scripts/release-gate-vscode.sh

# Validate shipped example apps
release-gate-apps:
	$(PYTHON) scripts/release_gate_apps.py

# Release readiness checks
release-check:
	$(PYTHON) scripts/check_version_alignment.py
	$(PYTHON) scripts/validate_dependencies.py --check-installs
	PYTHON=$(PYTHON) bash scripts/release-gate-templates.sh
	PYTHON=$(PYTHON) bash scripts/release-gate-vscode.sh
	$(PYTHON) scripts/release_gate_apps.py
	$(PYTHON) scripts/validate_builtin_parity.py
	$(PYTHON) scripts/validate_spec.py
	$(PYTHON) scripts/validate_supported_targets.py
	$(PYTHON) -m ruff check geno/ benchmark/ experiment/ analysis/
	$(PYTHON) -m ruff format --check geno/ benchmark/ experiment/ analysis/
	$(PYTHON) -m mypy geno/ --ignore-missing-imports --no-error-summary
	$(PYTHON) -m ruff check geno/ --select S --ignore S101
	$(PYTHON) -m pytest geno/tests/ -v --tb=short --cov=geno --cov-report=term --cov-fail-under=80 --timeout=60
	$(PYTHON) scripts/check_selfhost_parity.py
	$(PYTHON) scripts/validate_benchmark.py --strict-budgets

# Local CI wrapper for reproducing hosted checks before pushing
local-ci:
	$(PYTHON) scripts/local_ci.py full

# Release-sensitive local CI workflow
local-ci-release:
	$(PYTHON) scripts/local_ci.py release

# Run sandbox escape bounty tests
security-bounty:
	$(PYTHON) scripts/security_bounty.py -v

# Run all security tests
security:
	$(PYTHON) -m pytest geno/tests/test_security.py geno/tests/test_security_attacks.py geno/tests/test_security_audit.py geno/tests/test_security_corpus.py -v
	$(PYTHON) scripts/security_bounty.py

# Run REPL
repl:
	$(PYTHON) -m geno repl

EXPERIMENT_CONFIG ?= experiment/config.example.yaml

# Run experiment (requires LLM API keys)
experiment:
	$(PYTHON) scripts/run_experiment.py --config $(EXPERIMENT_CONFIG)

# Analyze results
analyze:
	$(PYTHON) scripts/analyze_results.py --input results.json --output report.md

# Clean build artifacts
clean:
	rm -rf build/ dist/ *.egg-info/
	rm -rf .pytest_cache/ .mypy_cache/ .coverage htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Full clean
clean-all: clean

# Help
help:
	@echo "Geno Makefile targets:"
	@echo ""
	@echo "  install    - Install the package"
	@echo "  dev        - Install with development dependencies"
	@echo "  llm        - Install with LLM API support"
	@echo "  test       - Run all tests"
	@echo "  coverage   - Run tests with coverage report"
	@echo "  lint       - Check code style"
	@echo "  format     - Auto-format code"
	@echo "  validate   - Validate benchmark problems"
	@echo "  validate-dependencies - Validate dependency metadata and lockfiles"
	@echo "  validate-supported-targets - Validate target docs against targets.toml"
	@echo "  validate-builtin-parity - Validate builtin manifest/runtime parity"
	@echo "  optional-test-collection - Collect optional tests without Hypothesis"
	@echo "  sandbox-regression - Run focused compiled sandbox regression tests"
	@echo "  dependency-audit - Audit Python dependencies for known vulnerabilities"
	@echo "  release-gate-templates - Validate init templates (scaffold + check + test)"
	@echo "  release-gate-vscode - Build and package the VS Code extension"
	@echo "  release-gate-apps - Validate example apps"
	@echo "  release-check - Run production readiness release gates"
	@echo "  local-ci   - Run the full local CI workflow"
	@echo "  local-ci-release - Run the release local CI workflow"
	@echo "  security   - Run all security tests + bounty"
	@echo "  security-bounty - Run sandbox escape bounty tests"
	@echo "  repl       - Start interactive REPL"
	@echo "  experiment - Run the full experiment"
	@echo "  analyze    - Analyze experiment results"
	@echo "  clean      - Remove build artifacts"
	@echo "  clean-all  - Remove all generated files"
