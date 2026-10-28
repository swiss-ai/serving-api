.PHONY: install install-dev lock lock-check format check test test-e2e run stop reset dummy-run seed-usage idp-up idp-down db-up db-down migrate

UV_EXTRA ?=

# Explicit file: legacy host .env files and Compose overrides do not configure
# the local application. Only the LOCAL_* options in docker-compose.yml do.
COMPOSE := docker compose -f docker-compose.yml

install:
	uv pip install $(UV_EXTRA) -r backend/requirements.txt

install-dev:
	uv pip install $(UV_EXTRA) -r backend/requirements-dev.txt

# requirements*.txt are generated lockfiles: edit the .in files, then run
# `make lock`. Resolved for the Docker image's Python (3.13) and universal
# across platforms, so macOS dev, CI and the linux image install the same
# versions. `make lock` keeps existing pins; bump one package with
# `make lock LOCK_ARGS="--upgrade-package sqlmodel"` or all with
# `make lock LOCK_ARGS=--upgrade`.
LOCK_ARGS ?=
UV_COMPILE := uv pip compile --quiet --universal --python-version 3.13

lock:
	cd backend && $(UV_COMPILE) $(LOCK_ARGS) requirements.in -o requirements.txt
	cd backend && $(UV_COMPILE) $(LOCK_ARGS) requirements-dev.in -o requirements-dev.txt

# CI: fail if a .in file changed without re-locking. Compiles into a temp
# dir seeded with the committed locks, so pins are kept and nothing in the
# working tree is touched.
lock-check:
	@tmp=$$(mktemp -d); cp backend/*.in backend/requirements*.txt $$tmp/; \
	(cd $$tmp && $(UV_COMPILE) requirements.in -o requirements.txt \
	          && $(UV_COMPILE) requirements-dev.in -o requirements-dev.txt) || exit 1; \
	for f in requirements.txt requirements-dev.txt; do \
		diff -u backend/$$f $$tmp/$$f \
			|| { echo "backend/$$f is stale: run 'make lock' and commit it."; exit 1; }; \
	done; echo "lockfiles up to date"

format:
	ruff check --fix backend/
	ruff format backend/

check:
	ruff check backend/
	ruff format --check backend/

test:
	pytest backend/tests/ -v -m "not e2e"

# Real sign-up flow: Postgres + Authentik (+ the backend image when
# E2E_BACKEND_IMAGE is set) in testcontainers. Needs Docker; ~1-2 min.
test-e2e:
	pytest backend/tests/ -v -m e2e

# Docker/Compose is the only runtime prerequisite. Completed bootstrap is a
# dependency of the app, so migrations and seeds finish before it accepts users.
run:
	$(COMPOSE) up --build --wait --wait-timeout 600 || { $(COMPOSE) logs --tail=30 bootstrap; exit 1; }
	@echo "Website: http://localhost:4321  API: http://localhost:8080  Chat: http://localhost:8081"
	@echo "Log in as admin@localhost / admin or user@localhost / user. Stop with make stop."

# Named volumes survive stop. Reset explicitly discards this local stack's data.
stop:
	$(COMPOSE) down

reset:
	$(COMPOSE) down --volumes

db-up:
	$(COMPOSE) up -d --wait postgres

db-down:
	$(COMPOSE) stop postgres

migrate: db-up
	$(COMPOSE) run --build --rm --no-deps --entrypoint alembic bootstrap upgrade head

idp-up: db-up
	$(COMPOSE) up -d --build --wait authentik-server authentik-worker
	$(COMPOSE) run --build --rm --no-deps bootstrap

idp-down:
	$(COMPOSE) stop authentik-server authentik-worker authentik-db authentik-redis

# Model fixtures are now the default; retain the existing alias for callers.
dummy-run: run
	$(MAKE) seed-usage

# Optional demo data for the leaderboard / My Usage pages.
seed-usage: migrate
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U serving -d serving -c "\
	  INSERT INTO usage_daily (day, owner_email, model, requests, prompt_tokens, completion_tokens, updated_at) VALUES \
	    (CURRENT_DATE,                       'user@localhost', 'swissai/apertus3-70b', 120, 1500000, 350000, now()), \
	    (CURRENT_DATE,                       'user@localhost', 'swissai/apertus3-8b',   60,  400000,  90000, now()), \
	    (CURRENT_DATE - INTERVAL '3 days',   'user@localhost', 'swissai/apertus3-70b',  80,  900000, 210000, now()), \
	    (CURRENT_DATE - INTERVAL '10 days',  'user@localhost', 'swissai/apertus3-8b',  200, 2200000, 480000, now()), \
	    (CURRENT_DATE - INTERVAL '45 days',  'user@localhost', 'swissai/apertus3-70b', 300, 5000000, 950000, now()) \
	  ON CONFLICT DO NOTHING;"
