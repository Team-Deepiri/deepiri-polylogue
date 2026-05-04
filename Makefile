.PHONY: test install lint typecheck
install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest -q

lint:
	ruff check .

typecheck:
	mypy src/
