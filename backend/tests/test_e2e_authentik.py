"""A new user's sign-up against a real Authentik, in testcontainers.

Nothing is faked: Authentik issues the access token, the backend resolves it
through OIDC discovery + userinfo, creates the API key in migrated Postgres,
and the key then has to pass require_auth.

Two modes:
- default: the backend runs in-process (TestClient) against the conftest
  Postgres.
- E2E_BACKEND_IMAGE=<image>: the backend runs as that Docker image, so CI
  tests the exact artifact (and dependency set) that ships to prod.

Run with `make test-e2e`. Needs Docker; Authentik takes ~1 min to boot.
"""

import contextlib
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT = Path(__file__).parent / "fixtures" / "authentik-e2e-blueprint.yaml"

E2E_EMAIL = "e2e-signup@example.org"
TOKEN_FORM = {
    "grant_type": "client_credentials",
    "client_id": "serving-api-e2e",
    "client_secret": "e2e-client-secret-not-a-secret",
    "username": "e2e-signup",
    "password": "e2e-app-password-not-a-secret",
    "scope": "openid email profile",
}
ISSUER_PATH = "/application/o/serving-api-e2e/"
BOOT_TIMEOUT_S = int(os.environ.get("E2E_BOOT_TIMEOUT", "300"))


def _authentik_image() -> str:
    """The digest-pinned image docker-compose.yml uses, so this test always
    runs the same Authentik as local dev (and the cluster)."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    match = re.search(r"image:\s*(ghcr\.io/goauthentik/server:\S+)", compose)
    assert match, "no goauthentik/server image in docker-compose.yml"
    return match.group(1)


def _wait_for(what, check, timeout, on_timeout=lambda: ""):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception as exc:  # container still booting
            last = exc
        time.sleep(2)
    pytest.fail(f"timed out waiting for {what} (last error: {last})\n{on_timeout()}")


def _logs(container, tail=60) -> str:
    out, err = container.get_logs()
    text = (out + err).decode(errors="replace").splitlines()
    return "\n".join(text[-tail:])


@pytest.fixture(scope="module")
def stack():
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.network import Network

    image = _authentik_image()
    env = {
        "AUTHENTIK_SECRET_KEY": "e2e-secret-key-not-a-secret",
        "AUTHENTIK_POSTGRESQL__HOST": "authentik-db",
        "AUTHENTIK_POSTGRESQL__USER": "authentik",
        "AUTHENTIK_POSTGRESQL__PASSWORD": "authentik",
        "AUTHENTIK_POSTGRESQL__NAME": "authentik",
        "AUTHENTIK_REDIS__HOST": "authentik-redis",
        "AUTHENTIK_ERROR_REPORTING__ENABLED": "false",
        "AUTHENTIK_DISABLE_UPDATE_CHECK": "true",
    }

    with Network() as net, contextlib.ExitStack() as stack:
        stack.enter_context(
            DockerContainer("postgres:17-alpine")
            .with_envs(
                POSTGRES_USER="authentik",
                POSTGRES_PASSWORD="authentik",
                POSTGRES_DB="authentik",
            )
            .with_network(net)
            .with_network_aliases("authentik-db")
        )
        stack.enter_context(
            DockerContainer("redis:8-alpine")
            .with_network(net)
            .with_network_aliases("authentik-redis")
        )
        server = stack.enter_context(
            DockerContainer(image)
            .with_command("server")
            .with_envs(**env)
            .with_network(net)
            .with_network_aliases("authentik-server")
            .with_exposed_ports(9000)
        )
        worker = stack.enter_context(
            DockerContainer(image)
            .with_command("worker")
            .with_envs(**env)
            .with_network(net)
            .with_volume_mapping(
                str(BLUEPRINT), "/blueprints/e2e/serving-api-e2e.yaml", "ro"
            )
        )

        base = (
            f"http://{server.get_container_host_ip()}:{server.get_exposed_port(9000)}"
        )

        # Ready once the worker has applied the blueprint and the server
        # issues tokens for it.
        def issues_token():
            r = httpx.post(f"{base}/application/o/token/", data=TOKEN_FORM, timeout=10)
            return r.status_code == 200 and "access_token" in r.json()

        _wait_for(
            "Authentik to issue an e2e token",
            issues_token,
            BOOT_TIMEOUT_S,
            lambda: f"--- server ---\n{_logs(server)}\n--- worker ---\n{_logs(worker)}",
        )
        yield SimpleNamespace(
            network=net,
            base_url=base,
            issuer=base + ISSUER_PATH,
            internal_issuer="http://authentik-server:9000" + ISSUER_PATH,
        )


@pytest.fixture(scope="module")
def api(stack, request):
    image = os.environ.get("E2E_BACKEND_IMAGE")
    if image:
        yield from _backend_container(stack, image)
    else:
        yield from _backend_in_process(stack, request)


def _backend_in_process(stack, request):
    from backend.config import get_settings

    client = request.getfixturevalue("client")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AUTH_PROVIDER", "authentik")
        mp.setenv("AUTHENTIK_ISSUER", stack.issuer)
        get_settings.cache_clear()
        yield client
    get_settings.cache_clear()


def _backend_container(stack, image):
    from testcontainers.core.container import DockerContainer
    from testcontainers.postgres import PostgresContainer

    from backend.tests.conftest import run_migrations

    with contextlib.ExitStack() as es:
        pg = es.enter_context(
            PostgresContainer(
                "postgres:17-alpine",
                username="serving",
                password="serving",
                dbname="serving",
            )
            .with_network(stack.network)
            .with_network_aliases("serving-db")
        )
        run_migrations(pg.get_connection_url())
        # Real Redis: the image runs 4 uvicorn workers, and key rotation must
        # evict the old key from a cache they all share (as in prod).
        es.enter_context(
            DockerContainer("redis:8-alpine")
            .with_network(stack.network)
            .with_network_aliases("serving-redis")
        )
        backend = es.enter_context(
            DockerContainer(image)
            .with_envs(
                DATABASE_URL="postgresql://serving:serving@serving-db:5432/serving",
                AUTH_PROVIDER="authentik",
                AUTHENTIK_ISSUER=stack.internal_issuer,
                REDIS_HOST="serving-redis",
            )
            .with_network(stack.network)
            .with_exposed_ports(8080)
        )
        base = (
            f"http://{backend.get_container_host_ip()}:{backend.get_exposed_port(8080)}"
        )
        with httpx.Client(base_url=base, timeout=30) as client:
            _wait_for(
                "backend image to serve /openapi.json",
                lambda: client.get("/openapi.json").status_code == 200,
                120,
                lambda: _logs(backend),
            )
            yield client


@pytest.fixture()
def access_token(stack):
    r = httpx.post(
        f"{stack.base_url}/application/o/token/", data=TOKEN_FORM, timeout=10
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _key_status(api, key):
    return api.get("/v1/mcp", headers={"Authorization": f"Bearer {key}"}).status_code


def test_signup_with_real_authentik_token(api, access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    profile = api.get("/v1/profile", headers=headers)
    assert profile.status_code == 200, profile.text
    body = profile.json()
    assert body["email"] == E2E_EMAIL
    assert body["api_key"].startswith("sk-rc-")
    assert body["budget"] > 0

    assert _key_status(api, body["api_key"]) == 200
    assert _key_status(api, "sk-rc-not-a-real-key") == 401

    # Signing in again returns the same key.
    again = api.get("/v1/profile", headers=headers)
    assert again.json()["api_key"] == body["api_key"]


def test_rotate_with_real_authentik_token(api, access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    old = api.get("/v1/profile", headers=headers).json()["api_key"]

    rotated = api.post("/v1/profile/rotate", headers=headers)
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["api_key"]

    assert new != old
    assert _key_status(api, old) == 401
    assert _key_status(api, new) == 200


def test_garbage_token_is_rejected_by_authentik(api):
    r = api.get("/v1/profile", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_synthetic_signup_script(api, stack):
    """Keeps scripts/synthetic_signup.py (the prod monitor) working. Needs a
    real HTTP backend, so only runs in image mode."""
    if not isinstance(api, httpx.Client) or not os.environ.get("E2E_BACKEND_IMAGE"):
        pytest.skip("needs E2E_BACKEND_IMAGE (backend served over HTTP)")
    import subprocess
    import sys

    env = {
        **os.environ,
        "SYNTHETIC_API_URL": str(api.base_url),
        "SYNTHETIC_TOKEN_URL": f"{stack.base_url}/application/o/token/",
        "SYNTHETIC_CLIENT_ID": TOKEN_FORM["client_id"],
        "SYNTHETIC_CLIENT_SECRET": TOKEN_FORM["client_secret"],
        "SYNTHETIC_USERNAME": TOKEN_FORM["username"],
        "SYNTHETIC_APP_PASSWORD": TOKEN_FORM["password"],
        "SYNTHETIC_MODEL": "",
    }
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "synthetic_signup.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("ok ") == 3, result.stdout
