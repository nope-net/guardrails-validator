.PHONY: install dev test smoke lint format build clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest tests/test_validator.py -v

# Hits the real API; requires NOPE_API_KEY (costs ~$0.003/call).
smoke:
	pytest tests/test_live_smoke.py -v

lint:
	ruff check nope_crisis_screen/ tests/

format:
	ruff format nope_crisis_screen/ tests/

build:
	python -m build

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ __pycache__/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
