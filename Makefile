.PHONY: test install lint typecheck
# Prefer python3 locally; CI overrides with PYTHON=python from setup-python.
PYTHON ?= $(shell command -v python3 2>/dev/null || command -v python)

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src/
