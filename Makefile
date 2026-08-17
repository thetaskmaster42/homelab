.DEFAULT_GOAL := help
SHELL := /bin/bash

# Fast checks run everywhere. The arm64 and helm-render checks need network and
# are marked `network` so they can be skipped offline but never in CI.
PYTEST ?= uv run --with pytest --with pyyaml pytest

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: validate
validate: lint test-fast test-render ## Everything CI runs

.PHONY: lint
lint: ## yamllint over the declarative tree
	@command -v yamllint >/dev/null || { echo "yamllint not installed; skipping"; exit 0; }
	yamllint -c .yamllint.yaml argocd infra apps clusters

.PHONY: test-fast
test-fast: ## Schema, pinning and secret checks (no network)
	$(PYTEST) tests -q -m "not network"

.PHONY: test-render
test-render: ## Helm/kustomize rendering + arm64 image verification (network)
	$(PYTEST) tests -q -m network

.PHONY: test-cli
test-cli: ## Unit tests for the homelab CLI
	@test -d cli && cd cli && uv run pytest -q || echo "cli/ not present yet"

.PHONY: arm64
arm64: ## Just the arm64 image check — run this before adopting a new chart
	$(PYTEST) tests/test_images_arm64.py -q

.PHONY: clean
clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
