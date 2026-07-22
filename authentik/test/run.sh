#!/usr/bin/env bash
# Level-1 correctness test: apply the serving-api blueprints to a disposable
# local Authentik and assert every object is created and re-apply is a no-op.
# Zero blast radius — never touches the shared CSCS instance.
#
#   authentik/test/run.sh          # up, wait, assert blueprint (discovery path)
#   authentik/test/run.sh --apply  # also validate apply.py (the push path)
#   authentik/test/run.sh --down   # tear everything down (incl. volumes)
set -euo pipefail
cd "$(dirname "$0")"
PY="$(cd ../.. && echo "$PWD/.venv/bin/python")"

# --apply tests apply.py against the base stack (no mount); default tests the
# discovery path with the blueprint mounted in. --down must clear either.
if [[ "${1:-}" == "--apply" ]]; then
  COMPOSE=(docker compose -f docker-compose.yml)
else
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.discovery.yml)
fi

if [[ "${1:-}" == "--down" ]]; then
  docker compose -f docker-compose.yml -f docker-compose.discovery.yml down -v
  exit 0
fi

echo "== Starting local Authentik 2026.5.0 (pulls images on first run) =="
"${COMPOSE[@]}" up -d

echo "== Waiting for the server API to accept requests =="
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:9000/-/health/ready/ || true)
  [[ "$code" == "200" ]] && { echo "  server ready"; break; }
  [[ "$i" == "60" ]] && { echo "  server never became ready"; "${COMPOSE[@]}" logs --tail=40 server; exit 1; }
  sleep 5
done

if [[ "${1:-}" == "--apply" ]]; then
  echo "== Validating apply.py (the API push/propagation path) =="
  "$PY" apply_test.py
  rc=$?
else
  echo "== Asserting blueprints applied correctly (discovery path) =="
  "$PY" assert.py
  rc=$?
fi

echo
echo "Stack left running (UI: http://localhost:9000  admin@localhost / local-test-admin-password)."
echo "Tear down with: authentik/test/run.sh --down"
exit $rc
