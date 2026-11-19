.PHONY: help install browsers lint format typecheck test check demo eval serve docker clean

UV ?= uv
REPORT ?= out
REPEAT ?= 3
PROVIDER ?= openai

help:  ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Create .venv and install with dev extras
	$(UV) sync --extra dev

browsers:  ## Download Playwright's Chromium (skip if WTA_CHROMIUM_EXECUTABLE is set)
	$(UV) run playwright install chromium

lint:  ## ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:  ## Auto-format
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck:  ## mypy (strict)
	$(UV) run mypy

test:  ## pytest (includes headless-browser end-to-end tests)
	$(UV) run pytest

check: lint typecheck test  ## Everything CI runs

demo:  ## Run the 10-task suite with the scripted policy (no API key)
	$(UV) run wta run tasks/ --policy scripted --repeat $(REPEAT) --report $(REPORT) --min-success 1.0

eval:  ## Run the suite with an LLM: make eval PROVIDER=anthropic
	$(UV) run wta run tasks/ --policy llm --provider $(PROVIDER) --repeat $(REPEAT) --report $(REPORT)/$(PROVIDER)

serve:  ## Serve the demo sites on http://127.0.0.1:8765
	$(UV) run wta serve --port 8765

docker:  ## Build the Docker image
	docker build -t web-task-agent .

clean:
	rm -rf out .pytest_cache .mypy_cache .ruff_cache
