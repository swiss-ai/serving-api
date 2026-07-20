#!/usr/bin/env python3
"""Assert the serving-api blueprint applied cleanly to the local test Authentik.

Polls the discovered blueprint instance until it reports `successful`, checks
every expected object exists and that cross-references (e.g. the dev provider's
authentication_flow) resolved, then re-applies and confirms it stays
`successful` (idempotency). Exits non-zero on any failure. Run via ./run.sh.
"""
import sys
import time

import requests

BASE = "http://localhost:9000/api/v3"
TOKEN = "local-test-bootstrap-token-0123456789"  # from docker-compose.yml
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})

BLUEPRINT = "serving-api"  # metadata.name of authentik/blueprints/serving-api.yaml
fails = []


def get(path, **params):
    params.setdefault("page_size", 200)
    r = S.get(f"{BASE}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def blueprint_instance():
    for b in get("managed/blueprints/")["results"]:
        if b["name"] == BLUEPRINT:
            return b
    return None


def wait_blueprint(timeout=300):
    print(f"Waiting for blueprint '{BLUEPRINT}' to apply (timeout {timeout}s)...")
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        try:
            inst = blueprint_instance()
        except Exception:  # server still coming up
            time.sleep(5)
            continue
        status = (inst or {}).get("status")
        if status == "successful":
            print(f"  applied: {status}")
            return inst
        # `error` early in boot is expected (deps not present yet); worker retries.
        print(f"  ...status={status}")
        time.sleep(10)
    print(f"  TIMEOUT (last status={status})")
    return blueprint_instance()


def main():
    inst = wait_blueprint()
    check(f"blueprint '{BLUEPRINT}' status successful", (inst or {}).get("status") == "successful",
          (inst or {}).get("status"))

    print("\nObjects created by the blueprint:")
    apps = {a["slug"] for a in get("core/applications/", search="serving-api")["results"]}
    check("application serving-api-prod", "serving-api-prod" in apps)
    check("application serving-api-dev", "serving-api-dev" in apps)

    provs = {p["name"]: p for p in get("providers/oauth2/", search="serving-api")["results"]}
    check("provider serving-api-prod", "serving-api-prod" in provs)
    check("provider serving-api-dev-provider", "serving-api-dev-provider" in provs)

    scopes = {m["scope_name"]: m for m in get("propertymappings/provider/scope/")["results"]}
    check("scope mapping 'groups' (serving-api-groups)",
          scopes.get("groups", {}).get("name") == "serving-api-groups")

    groups = {g["name"] for g in get("core/groups/")["results"]}
    for g in ("serving-admin", "serving-user", "serving-viewer"):
        check(f"group {g}", g in groups)

    sources = {s["slug"]: s for s in get("sources/oauth/")["results"]}
    check("CILogon oauth source", sources.get("cilogon", {}).get("provider_type") == "openidconnect")

    flows = {f["pk"]: f["slug"] for f in get("flows/instances/")["results"]}
    check("flow cilogon-direct", "cilogon-direct" in flows.values())

    # The interesting cross-reference: dev provider's authentication_flow must
    # resolve to cilogon-direct, and it must carry all 8 scope mappings.
    dev = provs.get("serving-api-dev-provider", {})
    check("dev provider authentication_flow -> cilogon-direct",
          flows.get(dev.get("authentication_flow")) == "cilogon-direct",
          flows.get(dev.get("authentication_flow")))
    check("dev provider has 8 scope mappings", len(dev.get("property_mappings", [])) == 8,
          str(len(dev.get("property_mappings", []))))
    check("prod provider has 7 scope mappings",
          len(provs.get("serving-api-prod", {}).get("property_mappings", [])) == 7,
          str(len(provs.get("serving-api-prod", {}).get("property_mappings", []))))

    print("\nRe-applying to confirm idempotency (proves a no-op re-apply):")
    pk = inst["pk"]
    r = S.post(f"{BASE}/managed/blueprints/{pk}/apply/", timeout=60)
    time.sleep(3)
    st = get(f"managed/blueprints/{pk}/").get("status") if r.status_code in (200, 202) else None
    check("re-apply successful", r.status_code in (200, 202) and st == "successful",
          f"http={r.status_code} status={st}")

    print("\n" + ("PASS: all checks green" if not fails else f"FAIL: {len(fails)} check(s) -> {fails}"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
