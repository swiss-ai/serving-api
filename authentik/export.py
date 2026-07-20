#!/usr/bin/env python3
"""Export the serving-api Authentik configuration from the live instance.

Config-as-code for the shared CSCS Authentik (https://auth.swissai.svc.cscs.ch).
This repo *consumes* that Authentik instance (OIDC), it does not deploy it, so
`ak export_blueprint` (which must run inside the worker pod) is not reachable.
Instead we read the relevant objects over the REST API and dump them, then
hand-assemble a version-1 blueprint under authentik/blueprints/ that mirrors the
SDSC pattern (secrets templated out, objects referenced with !Find).

Usage:
    # token comes from authentik/.token (gitignored) or $AUTHENTIK_API_TOKEN
    python authentik/export.py

The admin API token: Authentik UI -> Directory -> Tokens and App passwords ->
Create, Intent = API. Save it to authentik/.token (git-ignored) or export
AUTHENTIK_API_TOKEN. Raw dumps land in authentik/raw/ (git-ignored); they are an
inspection aid, not the committed artifact.
"""

import json
import os
import sys
from pathlib import Path

import requests

BASE = os.environ.get("AUTHENTIK_URL", "https://auth.swissai.svc.cscs.ch").rstrip("/")
HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
# Which applications to capture. Everything else (providers, mappings, flows,
# sources, entitlements) is discovered from these.
APP_SEARCH = "serving-api"


def _token() -> str:
    tok = os.environ.get("AUTHENTIK_API_TOKEN", "").strip()
    if tok:
        return tok
    tok_file = HERE / ".token"
    if tok_file.is_file():
        return tok_file.read_text().strip()
    sys.exit(
        "No token. Put it in authentik/.token (git-ignored) or set "
        "AUTHENTIK_API_TOKEN. Create one in Authentik: Directory -> Tokens "
        "and App passwords -> Create, Intent = API."
    )


SESSION = requests.Session()
SESSION.headers.update({"Authorization": f"Bearer {_token()}", "Accept": "application/json"})


def api(path: str, **params):
    """GET /api/v3/<path> and return parsed JSON (paginating list endpoints)."""
    url = f"{BASE}/api/v3/{path.lstrip('/')}"
    r = SESSION.get(url, params=params or None, timeout=(5, 30))
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "results" in data and "pagination" in data:
        results = list(data["results"])
        page = data["pagination"]
        while page.get("next"):
            r = SESSION.get(url, params={**(params or {}), "page": page["next"]}, timeout=(5, 30))
            r.raise_for_status()
            data = r.json()
            results.extend(data["results"])
            page = data["pagination"]
        return results
    return data


def dump(name: str, obj) -> None:
    (RAW / f"{name}.json").write_text(json.dumps(obj, indent=2, sort_keys=True))
    print(f"  wrote raw/{name}.json")


def export_flow(slug: str) -> None:
    """Save a flow's full blueprint export verbatim (stages, policies, bindings)."""
    url = f"{BASE}/api/v3/flows/instances/{slug}/export/"
    r = SESSION.get(url, timeout=(5, 30))
    if r.status_code != 200:
        print(f"  ! flow export {slug}: HTTP {r.status_code}")
        return
    (RAW / "flows").mkdir(exist_ok=True)
    (RAW / "flows" / f"{slug}.yaml").write_text(r.text)
    print(f"  wrote raw/flows/{slug}.yaml")


def main() -> None:
    RAW.mkdir(exist_ok=True)
    print(f"Authentik: {BASE}")

    apps = api("core/applications/", search=APP_SEARCH)
    print(f"\nApplications matching '{APP_SEARCH}': {[a['slug'] for a in apps]}")
    dump("applications", apps)

    # Index tables we resolve UUID references against.
    all_mappings = {m["pk"]: m for m in api("propertymappings/all/")}
    flows = {f["pk"]: f for f in api("flows/instances/")}
    certs = {c["pk"]: c for c in api("crypto/certificatekeypairs/")}
    dump("propertymappings_all", list(all_mappings.values()))
    dump("flows", list(flows.values()))

    providers, entitlements, bindings, flow_slugs = [], [], [], set()
    for app in apps:
        slug = app["slug"]
        if app.get("provider"):
            prov = api(f"providers/oauth2/{app['provider']}/")
            providers.append(prov)
            for key in ("authorization_flow", "invalidation_flow", "authentication_flow"):
                fpk = prov.get(key)
                if fpk and fpk in flows:
                    flow_slugs.add(flows[fpk]["slug"])
            # Annotate the provider dump with resolved names for easy assembly.
            prov["_resolved"] = {
                "signing_key": certs.get(prov.get("signing_key"), {}).get("name"),
                "authorization_flow": flows.get(prov.get("authorization_flow"), {}).get("slug"),
                "invalidation_flow": flows.get(prov.get("invalidation_flow"), {}).get("slug"),
                "property_mappings": [
                    all_mappings.get(pk, {}).get("scope_name")
                    or all_mappings.get(pk, {}).get("name")
                    for pk in prov.get("property_mappings", [])
                ],
            }
        ents = api("core/application_entitlements/", app=app["pk"])
        entitlements.extend(ents)
        for ent in ents:
            bindings.extend(api("policies/bindings/", target=ent["pk"]))

    dump("providers_oauth2", providers)
    dump("application_entitlements", entitlements)
    dump("policy_bindings", bindings)

    dump("groups", api("core/groups/"))
    sources = api("sources/oauth/")
    dump("sources_oauth", sources)
    dump("stages_identification", api("stages/identification/"))
    dump("brands", api("core/brands/"))

    # Flows referenced by the captured OAuth sources (enrollment/authentication).
    for src in sources:
        for key in ("authentication_flow", "enrollment_flow"):
            fpk = src.get(key)
            if fpk and fpk in flows:
                flow_slugs.add(flows[fpk]["slug"])

    print("\nExporting referenced flows:")
    for slug in sorted(flow_slugs):
        export_flow(slug)

    print("\nDone. Inspect authentik/raw/, then assemble the blueprint.")


if __name__ == "__main__":
    main()
