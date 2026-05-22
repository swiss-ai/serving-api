#!/usr/bin/env bash
# Fetches the MCP server catalog from the serving-api, then hits each MCP
# server's public URL directly with an `initialize` JSON-RPC call to confirm
# ingress + DNS + cert + MCP protocol all line up.
#
# Usage:
#   API_TOKEN=... ./scripts/test_mcp_tools.sh
#   API_URL=https://apidev.swissai.svc.cscs.ch API_TOKEN=... ./scripts/test_mcp_tools.sh

set -uo pipefail

API_URL="${API_URL:-https://apidev.swissai.svc.cscs.ch}"
API_TOKEN="${API_TOKEN:-}"

if [[ -z "$API_TOKEN" ]]; then
    echo "ERROR: API_TOKEN env var is required" >&2
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required (brew install jq)" >&2
    exit 1
fi

echo "Catalog: $API_URL/v1/mcp"
catalog="$(curl -s -H "Authorization: Bearer $API_TOKEN" "$API_URL/v1/mcp")"
if ! echo "$catalog" | jq -e '.servers' >/dev/null 2>&1; then
    echo "ERROR: catalog endpoint did not return {servers: ...}" >&2
    echo "$catalog" | head -c 400 >&2
    exit 1
fi

count="$(echo "$catalog" | jq -r '.servers | length')"
echo "Found $count servers in catalog"
echo

INIT_PAYLOAD='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test_mcp_tools","version":"0.0.1"}}}'

GREEN='\033[0;32m'
RED='\033[0;31m'
RESET='\033[0m'

pass=0
fail=0

printf "%-46s %-7s %-30s %s\n" "SERVER" "STATUS" "SERVERINFO" "NOTE"
printf "%-46s %-7s %-30s %s\n" "----------------------------------------------" "------" "------------------------------" "----"

while IFS=$'\t' read -r slug url; do
    tmp="$(mktemp)"
    status="$(curl -s -o "$tmp" -w '%{http_code}' --max-time 10 \
        -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        --data "$INIT_PAYLOAD" 2>/dev/null)"
    [[ -z "$status" ]] && status="000"

    body="$(cat "$tmp")"
    rm -f "$tmp"

    # Streamable-HTTP returns SSE: `data: {json}` lines.
    json="$(printf '%s' "$body" | sed -n 's/^data: //p' | head -1)"
    if [[ -z "$json" ]]; then
        # Some servers may return plain JSON.
        json="$body"
    fi

    info="-"
    note=""
    if [[ "$status" == "200" ]]; then
        name="$(printf '%s' "$json" | jq -r '.result.serverInfo.name // empty' 2>/dev/null)"
        ver="$(printf '%s' "$json" | jq -r '.result.serverInfo.version // empty' 2>/dev/null)"
        if [[ -n "$name" ]]; then
            info="$name${ver:+ v$ver}"
            color="$GREEN"
            pass=$((pass + 1))
        else
            info="(no serverInfo)"
            note="$(printf '%s' "$body" | head -c 120 | tr -d '\n')"
            color="$RED"
            fail=$((fail + 1))
        fi
    else
        color="$RED"
        note="$(printf '%s' "$body" | head -c 120 | tr -d '\n')"
        fail=$((fail + 1))
    fi

    printf "${color}%-46s %-7s %-30s${RESET} %s\n" "$slug" "$status" "$info" "$note"
done < <(echo "$catalog" | jq -r '.servers[] | "\(.owner)/\(.repo)\t\(.url)"')

echo
echo -e "${GREEN}pass: $pass${RESET}  ${RED}fail: $fail${RESET}  total: $count"
[[ $fail -eq 0 ]] || exit 1
