"""Synthetic sign-up check against a deployed serving-api.

Does what a new user does, with a dedicated test account:
  1. get an access token from Authentik (client_credentials + app password)
  2. GET /v1/profile -> API key is issued (or returned)
  3. the key passes require_auth (GET /v1/mcp)
  4. optionally, a 1-token chat completion (SYNTHETIC_MODEL)

Exits non-zero with the failing step on any problem. Standard library only,
so it runs anywhere without installing the backend.

Env:
  SYNTHETIC_API_URL        e.g. https://api.swissai.svc.cscs.ch
  SYNTHETIC_TOKEN_URL      e.g. https://<authentik>/application/o/token/
  SYNTHETIC_CLIENT_ID      OAuth client allowed the client_credentials grant
  SYNTHETIC_CLIENT_SECRET
  SYNTHETIC_USERNAME       Authentik user of the test account
  SYNTHETIC_APP_PASSWORD   app password token for that user
  SYNTHETIC_MODEL          optional; model id for the completion step
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 30


def _env(name, required=True):
    value = os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"missing env var {name}")
    return value


def _request(method, url, *, headers=None, form=None, body=None):
    data = None
    headers = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace")


def step(name, fn):
    start = time.monotonic()
    try:
        result = fn()
    except Exception as exc:
        print(f"FAIL  {name}: {exc}")
        sys.exit(1)
    print(f"ok    {name} ({time.monotonic() - start:.1f}s)")
    return result


def main():
    api = _env("SYNTHETIC_API_URL").rstrip("/")
    model = _env("SYNTHETIC_MODEL", required=False)

    def get_token():
        status, text = _request(
            "POST",
            _env("SYNTHETIC_TOKEN_URL"),
            form={
                "grant_type": "client_credentials",
                "client_id": _env("SYNTHETIC_CLIENT_ID"),
                "client_secret": _env("SYNTHETIC_CLIENT_SECRET"),
                "username": _env("SYNTHETIC_USERNAME"),
                "password": _env("SYNTHETIC_APP_PASSWORD"),
                "scope": "openid email profile",
            },
        )
        if status != 200:
            raise RuntimeError(f"token endpoint {status}: {text[:300]}")
        return json.loads(text)["access_token"]

    token = step("Authentik issues an access token", get_token)

    def get_key():
        status, text = _request(
            "GET", f"{api}/v1/profile", headers={"Authorization": f"Bearer {token}"}
        )
        if status != 200:
            raise RuntimeError(f"/v1/profile {status}: {text[:300]}")
        key = json.loads(text).get("api_key", "")
        if not key.startswith("sk-rc-"):
            raise RuntimeError("/v1/profile returned no API key")
        return key

    key = step("/v1/profile returns an API key", get_key)
    auth = {"Authorization": f"Bearer {key}"}

    def key_works():
        status, text = _request("GET", f"{api}/v1/mcp", headers=auth)
        if status != 200:
            raise RuntimeError(f"/v1/mcp {status}: {text[:300]}")

    step("API key passes auth", key_works)

    if model:

        def completion():
            status, text = _request(
                "POST",
                f"{api}/v1/chat/completions",
                headers=auth,
                body={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            if status != 200:
                raise RuntimeError(f"/v1/chat/completions {status}: {text[:300]}")

        step(f"1-token completion on {model}", completion)


if __name__ == "__main__":
    main()
