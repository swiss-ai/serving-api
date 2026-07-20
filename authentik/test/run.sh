#!/usr/bin/env bash
# Level-1 correctness test: apply the serving-api blueprints to a disposable
# local Authentik and assert every object is created and re-apply is a no-op.
# Zero blast radius — never touches the shared CSCS instance.
#
#   authentik/test/run.sh          # up, wait, assert (leaves stack running)
#   authentik/test/run.sh --down   # tear everything down (incl. volumes)
set -euo pipefail
cd "$(dirname "$0")"
COMPOSE=(docker compose -f docker-compose.yml)
PY="$(cd ../.. && echo "$PWD/.venv/bin/python")"

if [[ "${1:-}" == "--down" ]]; then
  "${COMPOSE[@]}" down -v
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

echo "== Asserting blueprints applied correctly =="
"$PY" assert.py
rc=$?

echo
echo "Stack left running (UI: http://localhost:9000  admin@localhost / local-test-admin-password)."
echo "Tear down with: authentik/test/run.sh --down"
exit $rc
