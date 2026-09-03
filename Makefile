# Day-to-day commands. `make` with no target prints this list.
#
# Everything runs from the repo's own virtualenv (.venv) and web/node_modules,
# so nothing here depends on what is installed globally.

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
WEB     := web

# Password for the offline stub server. Override: make stub DEV_PASSWORD=hunter2
DEV_PASSWORD ?= dev

# make stub REFUSE=1 makes every question refuse, to see the escalation card.
FAKE_SCORE := $(if $(REFUSE),0.50,0.78)

.DEFAULT_GOAL := help

.PHONY: help setup stub web test cov lint lint-py lint-web fmt audit build check compose loadtest clean

help: ## Show this list
	@grep -E '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## One-time: create .venv, install Python and Node dependencies
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install -q -r requirements/dev.txt
	cd $(WEB) && npm install

stub: ## Run the API on :8000 with a fake model and in-memory Mongo (no accounts needed)
	APP_PASSWORD_HASH="$$($(PY) -c "import bcrypt; print(bcrypt.hashpw(b'$(DEV_PASSWORD)', bcrypt.gensalt()).decode())")" \
	FAKE_PASSAGE_SCORE=$(FAKE_SCORE) FAKE_DB_LATENCY_MS=0 \
	$(VENV)/bin/uvicorn scripts.loadtest.server:app --port 8000 --log-level warning

web: ## Run the React app on :5173 with hot reload (proxies /api to :8000)
	cd $(WEB) && npx vite --port 5173 --strictPort

test: ## Run the Python test suite (~1 second, nothing external)
	$(PY) -m pytest

cov: ## Tests with a coverage report; CI fails under 80%
	$(PY) -m pytest --cov --cov-report=term-missing

lint: lint-py lint-web ## Ruff on Python; ESLint and TypeScript on the web app

lint-py: ## Ruff lint and format check (make fmt fixes what it can)
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

lint-web: ## ESLint and TypeScript on the web app
	cd $(WEB) && npm run -s lint && npx tsc -b

fmt: ## Fix lint findings and format the Python code
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

audit: ## Known vulnerabilities in the Python and npm dependency trees
	./scripts/audit.sh

build: ## Production build of the web app
	cd $(WEB) && npm run -s build

check: test lint build ## What the CI workflow runs on every PR (audit runs in Security)

compose: ## Full stack in Docker against the real services in .env
	docker compose up --build

loadtest: ## Throughput measurement against `make stub`; see scripts/loadtest/RESULTS.md
	$(PY) scripts/loadtest/run.py --concurrency 10 20 40 80

clean: ## Remove build and test artifacts
	rm -rf .coverage coverage.xml junit.xml htmlcov .pytest_cache .ruff_cache $(WEB)/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
