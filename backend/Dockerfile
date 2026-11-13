# Digest-pinned so a rebuild of the same commit gets the same base; Dependabot
# bumps it (.github/dependabot.yml).
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

WORKDIR /app

# Install from the pinned lockfile only (no -U): the image must contain the
# exact versions CI tested. Copied before the code so the dependency layer
# is cached across code-only changes. Keep the Python minor version in sync
# with `make lock` (--python-version) and the CI workflows.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --no-deps -r backend/requirements.txt \
    && pip check

COPY backend/ /app/backend
COPY alembic.ini /app/alembic.ini

ENV PYTHONPATH=/app
EXPOSE 8080

ENTRYPOINT ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4", "--no-access-log"]
