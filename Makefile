SHELL := /bin/bash
.DEFAULT_GOAL := help
PYTHON ?= python3.12
VENV ?= .venv
VPYTHON := $(VENV)/bin/python
JOBS ?= 2
HOST ?= 127.0.0.1
PORT ?= 8787
STURGIS_ROOT ?= $(CURDIR)/.data/sturgis

.PHONY: help bootstrap browsers lint lint-python lint-web lint-native typecheck format-check format-debt test test-python test-web test-tooling qualify docs docs-check build build-native build-web setup dev dev-web dev-docs-users dev-docs-developers

help: ## List common local development commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Install pinned Python, frontend and documentation development dependencies
	$(PYTHON) -m venv $(VENV)
	$(VPYTHON) -m pip install -e '.[test,dev]' -r documentation/requirements.txt
	npm ci
	npm --prefix documentation ci

browsers: ## Install Chromium for browser tests (system libraries must already be available)
	npx playwright install chromium

lint: lint-python lint-web lint-native ## Run Python/JavaScript correctness checks and C++ warnings as errors

lint-python:
	$(VPYTHON) -m ruff check agent consumer model_gateway scripts tests documentation

lint-web:
	npm run lint:web

lint-native:
	cmake -S . -B .build/lint-native -DSTURGIS_COVERAGE=OFF -DCMAKE_CXX_FLAGS=-Werror
	cmake --build .build/lint-native --parallel $(JOBS)

typecheck: ## Check all authored frontend production TypeScript with strict compiler settings
	npm run typecheck:web

format-check: ## Enforce formatting for new/changed files; report exact unchanged legacy debt
	$(VPYTHON) scripts/check_format.py

format-debt: ## List every remaining legacy formatting deviation and formatter
	$(VPYTHON) scripts/check_format.py --show-debt

test: test-python test-web ## Run the full Python suite and frontend component tests (not qualification)

test-python: build-native
	$(VPYTHON) -m pytest -q

test-web:
	npm run test:components

test-tooling: ## Test repository tooling and enforce 90-percent executable-line coverage
	COVERAGE_FILE=.build/coverage-repository-tooling $(VPYTHON) -m pytest -q tests/test_repository_tooling.py --cov=scripts.check_format --cov-branch --cov-report=json:.build/repository-tooling-coverage.json --cov-report=term-missing
	$(VPYTHON) -c 'import json; totals = json.load(open(".build/repository-tooling-coverage.json"))["totals"]; covered, total = totals["covered_lines"], totals["num_statements"]; print(f"Repository tooling executable-line coverage: {covered}/{total} ({100 * covered / total if total else 0:.2f}%)"); raise SystemExit(0 if total and 100 * covered >= 90 * total else "Repository tooling coverage is below 90 percent")'

qualify: ## Run the existing full product/browser gates with separate 90-percent language floors
	$(VPYTHON) scripts/qualify_gateway.py

docs: ## Build both the user and developer documentation sites
	npm --prefix documentation run build

docs-check: ## Run the full documentation content, source, examples and browser gate
	$(VPYTHON) scripts/docs_gate.py full

build: build-native build-web ## Build the native memory worker and production web clients

build-native:
	cmake -S . -B .build/native -DSTURGIS_COVERAGE=ON
	cmake --build .build/native --parallel $(JOBS)

build-web:
	npm run build:web

setup: build ## Save checkout-local client/server configuration without starting a server
	$(VENV)/bin/sturgis --config-dir "$(STURGIS_ROOT)/client" setup --root "$(STURGIS_ROOT)" --source-root "$(CURDIR)" --native-binary "$(CURDIR)/.build/native/sturgis-memory"

dev: build ## Serve Sturgis locally; defaults to http://127.0.0.1:8787 and .data/sturgis
	$(VENV)/bin/sturgis --config-dir "$(STURGIS_ROOT)/client" serve --root "$(STURGIS_ROOT)" --source-root "$(CURDIR)" --native-binary "$(CURDIR)/.build/native/sturgis-memory" --host "$(HOST)" --port "$(PORT)"

dev-web: ## Run the Vite frontend with reload; start make dev separately for the API
	npm run dev:web

dev-docs-users: ## Serve the user manual locally on port 4321
	npm --prefix documentation run dev:users

dev-docs-developers: ## Serve the developer manual locally on port 4322
	npm --prefix documentation run dev:developers
