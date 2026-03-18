.PHONY: install install-dev format check test run

UV_EXTRA ?=

install:
	uv pip install $(UV_EXTRA) -r backend/requirements.txt

install-dev:
	uv pip install $(UV_EXTRA) -r backend/requirements-dev.txt

format:
	ruff check --fix backend/
	ruff format backend/

check:
	ruff check backend/
	ruff format --check backend/

test:
	pytest backend/tests/ -v

run:
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 & \
	cd frontend && npm run dev & \
	wait
