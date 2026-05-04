.PHONY: test install
install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest -q
