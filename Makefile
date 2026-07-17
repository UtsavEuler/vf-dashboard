.DEFAULT_GOAL := help
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PORT ?= 9000

.PHONY: help venv install install-dev run test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

$(VENV): ## Create the virtualenv
	python3 -m venv $(VENV)

venv: $(VENV) ## Alias for creating the virtualenv

install: $(VENV) ## Install runtime dependencies
	$(PIP) install -q -r requirements.txt

install-dev: $(VENV) ## Install runtime + test dependencies
	$(PIP) install -q -r requirements-dev.txt

run: install ## Run the dashboard locally (loads .env; PORT=9000 by default)
	PORT=$(PORT) $(PY) server.py

test: install-dev ## Run the test suite
	$(VENV)/bin/pytest

clean: ## Remove the virtualenv and caches
	rm -rf $(VENV) .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
