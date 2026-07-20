#!/usr/bin/env python3
"""Validate authentik/apply.py against the local test Authentik.

Checks the propagation path end to end: apply.py refuses when a secret is
missing (fail-closed), a full apply reports successful, the secret substituted
into the content actually lands on the object, and a second apply is idempotent.
Assumes the base stack (no blueprint mount) is up — run via run.sh --apply.
Exits non-zero on any failure.
"""
import os
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
APPLY = os.path.join(HERE, "..", "apply.py")
PY = sys.executable
BASE = "http://localhost:9000"
TOKEN = "local-test-bootstrap-token-0123456789"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {TOKEN}"})
DUMMY_PROD = "dummy-prod-secret-value-12345"
fails = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def run_apply(env_extra):
    env = {**os.environ, "AUTHENTIK_URL": BASE, "AUTHENTIK_API_TOKEN": TOKEN, **env_extra}
    return subprocess.run([PY, APPLY], env=env, capture_output=True, text=True, timeout=300)


def last_line(p):
    out = (p.stdout + p.stderr).strip().splitlines()
    return out[-1] if out else ""


def wait_ready(timeout=240):
    """Wait until Authentik has finished booting — the health endpoint can go
    green before default blueprints apply and the create endpoint is ready
    (a fresh boot briefly 405s), so gate on a default blueprint being applied.
    """
    print(f"Waiting for Authentik to finish initializing (timeout {timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = S.get(f"{BASE}/api/v3/managed/blueprints/", params={"page_size": 200}, timeout=15)
            if r.status_code == 200 and any(
                b["name"] == "authentik Bootstrap" and b.get("status") == "successful"
                for b in r.json()["results"]
            ):
                print("  ready")
                return
        except requests.RequestException:
            pass
        time.sleep(5)
    print("  WARNING: readiness gate timed out; proceeding anyway")


def prod_client_secret():
    lst = S.get(f"{BASE}/api/v3/providers/oauth2/", params={"search": "serving-api-prod"}, timeout=30).json()["results"]
    prov = next((x for x in lst if x["name"] == "serving-api-prod"), None)
    if not prov:
        return None
    # client_secret is only on the detail serializer, not the list.
    return S.get(f"{BASE}/api/v3/providers/oauth2/{prov['pk']}/", timeout=30).json().get("client_secret")


def main():
    wait_ready()

    # 1) fail-closed: no secrets -> must refuse.
    p = run_apply({})
    check("refuses when secrets unset (fail-closed)", p.returncode != 0, last_line(p))

    secrets = {
        "AUTHENTIK_CILOGON_CLIENT_SECRET": "dummy-cilogon",
        "AUTHENTIK_SERVING_PROD_CLIENT_SECRET": DUMMY_PROD,
        "AUTHENTIK_SERVING_DEV_CLIENT_SECRET": "dummy-dev",
    }
    # 2) full apply with secrets present.
    p = run_apply(secrets)
    check("apply succeeds with secrets present", p.returncode == 0, last_line(p) if p.returncode else "")

    # 3) the substituted secret must land on the object.
    time.sleep(2)
    check("prod client_secret substituted onto provider", prod_client_secret() == DUMMY_PROD,
          "matches" if prod_client_secret() == DUMMY_PROD else repr(prod_client_secret())[:40])

    # 4) idempotent second apply.
    p = run_apply(secrets)
    check("second apply idempotent (successful)",
          p.returncode == 0 and "Final status: successful" in p.stdout, last_line(p))

    print("\n" + ("PASS: apply.py verified" if not fails else f"FAIL: {fails}"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
