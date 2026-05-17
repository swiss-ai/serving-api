.PHONY: install install-dev format check test run dummy-run db-up db-down migrate _ensure-env _ensure-frontend-env

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
		echo "DATABASE_URL=$(DATABASE_URL)" > .env; \
		echo "wrote default .env (DATABASE_URL -> local docker postgres on :$(PG_PORT))"; \
	fi

_ensure-frontend-env:
	@if [ ! -f frontend/.env ]; then \
		secret=$$(openssl rand -hex 32); \
		{ \
			echo "AUTH_SECRET=$$secret"; \
			echo "AUTH_TRUST_HOST=true"; \
			echo "AUTH0_CLIENT_ID="; \
			echo "AUTH0_CLIENT_SECRET="; \
			echo "AUTH0_ISSUER="; \
		} > frontend/.env; \
		echo "wrote default frontend/.env (AUTH_SECRET generated; fill in AUTH0_* to enable login)"; \
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
				postgres:16 > /dev/null; \
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

migrate: _ensure-env db-up
	alembic upgrade head

run: _ensure-env _ensure-frontend-env db-up migrate
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 & \
	cd frontend && npm run dev & \
	wait

# Same as `run` but forces the model list to come from the synthesised
# upgraded fixture instead of the live OpenTela endpoint. Useful for
# iterating on the model-card UI without depending on prod state.
dummy-run: _ensure-env _ensure-frontend-env db-up migrate
	OTELA_FIXTURE_PATH=$(PWD)/backend/tests/fixtures/dnt_table_upgraded.json \
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 & \
	cd frontend && npm run dev & \
	wait
