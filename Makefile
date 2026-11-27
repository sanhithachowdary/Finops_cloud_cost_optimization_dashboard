PYTHON ?= python
PIP ?= pip

.PHONY: install install-dev lint format format-check typecheck test test-negative fixtures acceptance evidence cov check run docs help

install: ## Install runtime dependencies
	$(PIP) install -r requirements.txt

install-dev: ## Install development and CI dependencies
	$(PIP) install -r requirements-dev.txt

lint: ## Lint with ruff
	ruff check .

format: ## Auto-format with ruff
	ruff format .

format-check: ## Verify formatting without changing files
	ruff format --check .

typecheck: ## Type-check the core package with mypy
	mypy finops_engine

test: ## Run the test suite
	pytest tests/

test-negative: ## Run the five mandatory negative tests (NT-1..NT-5)
	pytest tests/negative -v

fixtures: ## Regenerate the versioned SYNTHETIC benchmark fixtures (deterministic)
	$(PYTHON) -m scripts.generate_fixtures

acceptance: ## Run the KPI / acceptance harness (writes docs/evidence/kpi-results.json)
	$(PYTHON) -m scripts.acceptance_harness

evidence: ## Test run with JUnit XML + log into docs/evidence
	pytest tests/ -p no:cacheprovider --junitxml=docs/evidence/pytest-junit.xml | tee docs/evidence/pytest-output.txt

cov: ## Run tests with coverage report
	pytest tests/ --cov=finops_engine --cov-report=term-missing

check: lint format-check typecheck test ## Run all checks (CI equivalent)

run: ## Start the API server locally
	uvicorn finops_engine.api.app:app --host 0.0.0.0 --port 8000 --reload

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'
