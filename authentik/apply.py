#!/usr/bin/env python3
"""Push authentik/blueprints/serving-api.yaml to Authentik and apply it.

This is the propagation step: it creates (or updates) a content-based blueprint
instance over the REST API and triggers an apply, so a change committed to the
blueprint converges on the live instance. Run it by hand or from CI.

Only serving-api.yaml is pushed. It references the shared CILogon source with
!Find and does not manage it, so this script never touches that source or its
write-only secret (see authentik/blueprints/cilogon-source.yaml).

The blueprint keeps the two provider client secrets as !Env references. The
shared CSCS worker does not have those env vars, so this script substitutes the
real values into the content before pushing (Authentik resolves !Env only from
the worker's own environment). Both must be present in this script's environment
or it refuses to run — a missing secret must never be applied as an empty string
over a live one.

Usage:
    export AUTHENTIK_SERVING_PROD_CLIENT_SECRET=...
    export AUTHENTIK_SERVING_DEV_CLIENT_SECRET=...
    # token from authentik/.token or $AUTHENTIK_API_TOKEN
    python authentik/apply.py            # create/update, apply, wait
    python authentik/apply.py --dry-run  # push disabled + validate, do not apply
"""
import json
import os
import re
import sys
import time

import requests

BASE = os.environ.get("AUTHENTIK_URL", "https://auth.swissai.svc.cscs.ch").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
BLUEPRINT_FILE = os.path.join(HERE, "blueprints", "serving-api.yaml")
INSTANCE_NAME = "serving-api"  # metadata.name in the blueprint

# Secret !Env variables serving-api.yaml references. All are required.
SECRET_ENV_VARS = [
    "AUTHENTIK_SERVING_PROD_CLIENT_SECRET",
    "AUTHENTIK_SERVING_DEV_CLIENT_SECRET",
]


def token() -> str:
    tok = os.environ.get("AUTHENTIK_API_TOKEN", "").strip()
    if not tok:
        tok_file = os.path.join(HERE, ".token")
        if os.path.isfile(tok_file):
            tok = open(tok_file).read().strip()
    if not tok:
        sys.exit("No token: set AUTHENTIK_API_TOKEN or create authentik/.token")
    return tok


def substitute_secrets(content: str) -> str:
    """Replace each `!Env [VAR, ...]` for a known secret with its real value.

    Fail closed: abort if any secret env var is unset/empty, and abort if any
    reference is left unsubstituted — never push a blank or a live !Env secret.
    """
    missing = [v for v in SECRET_ENV_VARS if not os.environ.get(v, "").strip()]
    if missing:
        sys.exit(
            "Refusing to apply — these secrets are unset (an empty value would "
            "clobber the live secret):\n  " + "\n  ".join(missing)
        )
    for var in SECRET_ENV_VARS:
        value = os.environ[var].strip()
        # matches: !Env [VAR, "default"]  /  !Env [VAR]  /  !Env VAR
        pattern = re.compile(r"!Env\s*(?:\[\s*" + re.escape(var) + r"\s*(?:,[^\]]*)?\]|" + re.escape(var) + r"\b)")
        content, n = pattern.subn(json.dumps(value), content)
        if n == 0:
            sys.exit(f"Blueprint has no !Env reference for {var} — refusing (stale mapping?)")
    # Safety net for any OTHER (unknown) secret !Env tag. Ignore comments, where
    # the header legitimately mentions "!Env" in prose.
    uncommented = "\n".join(re.sub(r"#.*$", "", ln) for ln in content.splitlines())
    leftover = re.findall(r"!Env\b", uncommented)
    if leftover:
        sys.exit(f"Unsubstituted !Env reference(s) remain: {len(leftover)} — refusing")
    return content


def main() -> None:
    dry_run = "--dry-run" in sys.argv[1:]
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token()}", "Accept": "application/json"})

    content = substitute_secrets(open(BLUEPRINT_FILE).read())
    print(f"Authentik: {BASE}")
    print(f"Blueprint: {BLUEPRINT_FILE} (secrets substituted, {len(content)} bytes)")

    existing = None
    for bp in s.get(f"{BASE}/api/v3/managed/blueprints/", params={"page_size": 200}, timeout=30).json()["results"]:
        if bp["name"] == INSTANCE_NAME:
            existing = bp
            break

    # dry-run pushes disabled so Authentik parses/validates without applying.
    payload = {"name": INSTANCE_NAME, "content": content, "enabled": not dry_run}
    if existing:
        pk = existing["pk"]
        print(f"Updating blueprint instance {pk} (was status={existing.get('status')})")
        r = s.patch(f"{BASE}/api/v3/managed/blueprints/{pk}/", json=payload, timeout=60)
    else:
        print("Creating new blueprint instance")
        r = s.post(f"{BASE}/api/v3/managed/blueprints/", json=payload, timeout=60)
    if r.status_code not in (200, 201):
        sys.exit(f"Push failed: HTTP {r.status_code} {r.text[:500]}")
    pk = r.json()["pk"]

    if dry_run:
        print("Dry-run: content pushed with enabled=false (parsed/validated, not applied).")
        print("Review + enable in the UI, or run without --dry-run to apply.")
        return

    print("Applying...")
    a = s.post(f"{BASE}/api/v3/managed/blueprints/{pk}/apply/", timeout=120)
    if a.status_code not in (200, 202):
        sys.exit(f"Apply request failed: HTTP {a.status_code} {a.text[:500]}")

    status = None
    for _ in range(30):
        time.sleep(3)
        inst = s.get(f"{BASE}/api/v3/managed/blueprints/{pk}/", timeout=30).json()
        status = inst.get("status")
        if status in ("successful", "error"):
            break
        print(f"  ...status={status}")
    print(f"Final status: {status}")
    if status != "successful":
        sys.exit(1)
    print("Applied. The worker will keep re-applying on its schedule to stay converged.")


if __name__ == "__main__":
    main()
