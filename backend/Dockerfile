# Digest-pinned so a rebuild of the same commit gets the same base; Dependabot
# bumps it (.github/dependabot.yml).
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

WORKDIR /app

# Install from the pinned lockfile only (no -U): the image must contain the
# exact versions CI tested. Copied before the code so the dependency layer
# is cached across code-only changes. Keep the Python minor version in sync
# with `make lock` (--python-version) and the CI workflows.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --no-deps -r backend/requirements.txt \
    && pip check

COPY backend/ /app/backend

ENV PYTHONPATH=/app
EXPOSE 8080

ENTRYPOINT ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4", "--no-access-log"]
