.PHONY: install install-dev format check test run auth-run admin-run dummy-run idp-up idp-down db-up db-down migrate _ensure-env _ensure-frontend-env _guard-local-db _guard-local-api

UV_EXTRA ?=

PG_CONTAINER := serving-api-pg
PG_PORT := 5433
PG_USER := serving
PG_PASS := serving
PG_DB := serving
DATABASE_URL := postgresql://$(PG_USER):$(PG_PASS)@localhost:$(PG_PORT)/$(PG_DB)

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

_ensure-env:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "copied .env.example -> .env"; \
	fi

_ensure-frontend-env:
	@if [ ! -f frontend/.env ]; then \
		cp frontend/.env.example frontend/.env; \
		echo "copied frontend/.env.example -> frontend/.env (fill in AUTH0_* to enable login)"; \
	fi

db-up:
	@if [ -z "$$(docker ps -q -f name=^/$(PG_CONTAINER)$$)" ]; then \
		if [ -n "$$(docker ps -aq -f name=^/$(PG_CONTAINER)$$)" ]; then \
			echo "starting existing $(PG_CONTAINER) container"; \
			docker start $(PG_CONTAINER) > /dev/null; \
		else \
			echo "creating $(PG_CONTAINER) container on :$(PG_PORT)"; \
			docker run -d --name $(PG_CONTAINER) \
				-e POSTGRES_USER=$(PG_USER) \
				-e POSTGRES_PASSWORD=$(PG_PASS) \
				-e POSTGRES_DB=$(PG_DB) \
				-p $(PG_PORT):5432 \
				postgres:17 > /dev/null; \
		fi; \
	fi
	@printf "waiting for postgres"; \
	for i in $$(seq 1 30); do \
		if docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -d $(PG_DB) > /dev/null 2>&1; then \
			echo " ready"; exit 0; \
		fi; \
		printf "."; sleep 1; \
	done; \
	echo " timed out"; exit 1

db-down:
	-docker stop $(PG_CONTAINER) > /dev/null 2>&1
	-docker rm $(PG_CONTAINER) > /dev/null 2>&1

# Local Authentik so `make run` can do a real OIDC login now that Auth0 is
# gone. Sign in as admin@localhost / admin. Seeds the matching apikey row
# (with is_admin) so the account has a budget and the admin UI works —
# localhost is not a swiss_domains entry, so an unseeded first login would
# get budget -1 and every API call rejected.
idp-up: db-up
	docker compose up -d --wait authentik-server authentik-worker
	@docker exec $(PG_CONTAINER) psql -q -U $(PG_USER) -d $(PG_DB) -c "\
	  INSERT INTO apikey (key, budget, created_at, updated_at, owner_email, is_admin) \
	  SELECT 'sk-rc-local-admin', 1000, now(), now(), 'admin@localhost', true \
	  WHERE NOT EXISTS (SELECT 1 FROM apikey WHERE owner_email = 'admin@localhost'); \
	  UPDATE apikey SET is_admin = true WHERE owner_email = 'admin@localhost';" 2>/dev/null || true
	@echo "Authentik: http://localhost:9000  (admin@localhost / admin)"

idp-down:
	docker compose stop authentik-server authentik-worker authentik-db authentik-redis

# Refuse to run any DB-touching target if .env points at a non-local host.
# We never want `make run` / `make migrate` to accidentally apply migrations
# or open connections against a remote (prod/staging) database — the local
# Postgres container is the only acceptable target for dev commands.
_guard-local-db: _ensure-env
	@url=$$(grep -E '^DATABASE_URL=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"); \
	host=$$(echo "$$url" | sed -E 's|^[^:]+://[^@]*@([^:/?]+).*|\1|'); \
	case "$$host" in \
		localhost|127.0.0.1|::1|"") ;; \
		*) echo "REFUSING: .env DATABASE_URL host '$$host' is not local."; \
		   echo "Local dev must not run against prod/staging. Set DATABASE_URL=$(DATABASE_URL) in .env."; \
		   exit 1;; \
	esac

# Same guard for the frontend — VITE_API_URL is what `npm run dev` reads,
# so a prod URL there silently makes the local UI hit prod even when the
# local backend is running fine. That's exactly what tripped up dummy-run
# the first time around. Empty / unset is fine (frontend defaults apply).
_guard-local-api: _ensure-frontend-env
	@url=$$(grep -E '^VITE_API_URL=' frontend/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"); \
	host=$$(echo "$$url" | sed -E 's|^[^:]+://([^:/?]+).*|\1|'); \
	case "$$host" in \
		localhost|127.0.0.1|::1|"") ;; \
		*) echo "REFUSING: frontend/.env VITE_API_URL host '$$host' is not local."; \
		   echo "Local dev must not call prod/staging API. Set VITE_API_URL=http://localhost:8080 in frontend/.env."; \
		   exit 1;; \
	esac

migrate: _ensure-env _guard-local-db db-up
	alembic upgrade head

# `run` uses the real OIDC flow, so it brings the local Authentik up too
# (idp-up is idempotent — an already-running stack is a no-op). The bypass
# targets (auth-run/admin-run) skip it: no IdP involved, faster start.
run: _ensure-env _ensure-frontend-env _guard-local-api idp-up migrate
	@trap 'kill 0 2>/dev/null; sleep 1; lsof -ti :8080 -ti :4321 -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null; true' EXIT INT TERM; \
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 & \
	cd frontend && npm run dev & \
	wait

# Like `run`, but enables the local dev auth bypass so the frontend's dev
# session (api_key.astro) is accepted without a real Auth0 login. Use this
# for local work when you don't have Auth0 configured. LOCAL ONLY — the
# bypass must never be enabled in a deployment.
auth-run: _ensure-env _ensure-frontend-env _guard-local-api db-up migrate
	@trap 'kill 0 2>/dev/null; sleep 1; lsof -ti :8080 -ti :4321 -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null; true' EXIT INT TERM; \
	DEV_AUTH_BYPASS=true \
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 & \
	cd frontend && VITE_DEV_AUTH_BYPASS=true npm run dev & \
	wait

# Like `auth-run`, but the dev session is admin@localhost with is_admin set,
# so the Admin dropdown and /users page are exercisable locally. The row is
# seeded idempotently before the servers start. LOCAL ONLY, same as auth-run.
admin-run: _ensure-env _ensure-frontend-env _guard-local-api db-up migrate
	@docker exec $(PG_CONTAINER) psql -q -U $(PG_USER) -d $(PG_DB) -c "\
	  INSERT INTO apikey (key, budget, created_at, updated_at, owner_email, is_admin) \
	  SELECT 'sk-rc-local-admin', 1000, now(), now(), 'admin@localhost', true \
	  WHERE NOT EXISTS (SELECT 1 FROM apikey WHERE owner_email = 'admin@localhost'); \
	  UPDATE apikey SET is_admin = true WHERE owner_email = 'admin@localhost';"
	@trap 'kill 0 2>/dev/null; sleep 1; lsof -ti :8080 -ti :4321 -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null; true' EXIT INT TERM; \
	DEV_AUTH_BYPASS=true DEV_AUTH_EMAIL=admin@localhost \
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 & \
	cd frontend && VITE_DEV_AUTH_BYPASS=true npm run dev & \
	wait

# Same as `run` but forces the model list to come from the synthesised
# upgraded fixture instead of the live OpenTela endpoint. Useful for
# iterating on the model-card UI without depending on prod state.
dummy-run: _ensure-env _ensure-frontend-env _guard-local-api db-up migrate
	@trap 'kill 0 2>/dev/null; sleep 1; lsof -ti :8080 -ti :4321 -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null; true' EXIT INT TERM; \
	OTELA_FIXTURE_PATH=$(PWD)/backend/tests/fixtures/dnt_table_dev_live.json \
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 & \
	cd frontend && npm run dev & \
	wait
