# serving-api

Frontend and backend API proxy for SwissAI LLM serving. For examples on how to launch models, see [model-launch](https://github.com/swiss-ai/model-launch) repo.

**Live at:** 
- Prod: [serving.swissai.svc.cscs.ch](https://serving.swissai.svc.cscs.ch)
- Dev: [servingdev.swissai.svc.cscs.ch](https://servingdev.swissai.svc.cscs.ch)
- Local: with `make run`

## Architecture

```
                              o
        ┌─────────────────┐  /|\   curl / python SDK
        │    OpenWebUI    │  / \ 
        └────────┬────────┘   |
                 │            │
                 │  POST /v1/chat/completions
                 │            │
                 ▼            ▼
        ┌─────────────────────────┐
        │       serving-api       │  auth + proxy (this repo)
        └─────────────────────────┘
                 │
                 │
                 ▼
        ┌─────────────────┐
        │     OpenTela    │  P2P routing → model=apertus-...
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │   vllm/sglang   │  model inference (GPU)
        └─────────────────┘
```

## Model Namespaces & Passthrough Providers

Model ids are namespaced by their **first path segment**, which selects
who serves the model:

| First segment | Serves | Example |
| :- | :- | :- |
| `SwissAI-Research/` | Models we serve on our Kubernetes cluster (via OpenTela) | `SwissAI-Research/swiss-ai/Apertus-70B-Instruct-2509` |
| `{username}/` | Models launched by users through model-launch | `{username}/Qwen/Qwen2.5-72B-Instruct` |
| `CSCS-Inference/` | CSCS L1 inference service (`api.inference.cscs.ch`) | `CSCS-Inference/swiss-ai/Apertus-70B-Instruct-2509` |
| `RCP-AIaaS/` | EPFL RCP AIaaS (`inference-rcp.epfl.ch`) | `RCP-AIaaS/swiss-ai/Apertus-8B-Instruct-2509` |

The model list only advertises ids of exactly three non-empty segments
(`{owner}/{hf_org}/{hf_model}`); user launches additionally need OpenTela
`sai-v0.0.6` or newer. This is listing-only curation — anything running
stays routable by its id (admins see the full unfiltered set, with the
reason each hidden entry is dropped, on `/all_models`).

Passthrough requests are forwarded with the provider prefix stripped
(the upstream only knows its own id) and responses — including streamed
chunks — are rewritten back to the prefixed id. Platform ids
(`SwissAI-Research/...`, `{username}/...`) are forwarded verbatim: the
full prefixed id IS the served name on the OpenTela side. The same upstream model on two
providers is two distinct, individually-routable entries; a prefixed id
can never collide with (or shadow) a local model. The
`SwissAI-Research`, `CSCS-Inference` and `RCP-AIaaS` prefixes are
reserved names: never launch a local model whose id starts with one.

**Passthrough curation.** What each provider surfaces is governed by its
`hidden_id_suffixes` in `backend/services/passthrough_service.py`:

- **CSCS L1** — unrestricted: everything its `/models` endpoint
  advertises is listed and routable (tracked live, ~30 s discovery TTL).
- **EPFL RCP** — everything it advertises, minus alias/quant variants of
  the same weights (`-bfloat16`, `-float32`, `-fp8`, `-int4` suffixes,
  case-insensitive): one row per model is enough.

**Rate limiting** applies only to passthrough traffic: external
providers are a shared, platform-accountable resource, while
OpenTela-served models run on the caller's own GPU allocation. See
`backend/services/rate_limit_service.py`. To give one user a different
limit, an admin calls `PUT /v1/admin/rate-limits/{email}` with
`{"rpm": 500}` (`0` = unlimited; `DELETE` reverts to the default). It is
stored on their API key row, so it survives Redis restarts and key
rotation.

**Back-compat.** Un-prefixed passthrough ids (the historical form) still
route during a deprecation window — logged, first provider in
registration order wins — and responses already return the prefixed id
to advertise the migration target.

Planned (not yet implemented): per-model health status for passthrough
entries.

## Repo Structure

```
backend/         # Python API proxy (FastAPI) — auth, caching, routing
frontend/        # web UI (Astro + Svelte)
meta/            # example Dockerfiles, example k8s manifests, build scripts
```

OpenTela (formerly OCF / "Open Compute Framework") is maintained upstream at [eth-easl/OpenTela](https://github.com/eth-easl/OpenTela). We maintain a fork at [swiss-ai/OpenTela](https://github.com/swiss-ai/opentela) to control deployments to dev+prod.

## Model Authorization

Models launched via [SML](https://github.com/swiss-ai/model-launch) carry an
OpenTela peer label `authorization` that controls who can see and use them:

- `public` — anyone may list and use the model.
- a comma-separated email list (e.g. `user1@epfl.ch,user2@ethz.ch`) — only
  those users. Emails are normalized (strip, lowercase) by SML before launch;
  the backend also compares case-insensitively as defense in depth.
- missing/empty label — treated as `public`, so every model launched before
  this feature keeps working and stays visible.

A launch is `public` unless it says otherwise — SML's `--authorization` defaults
to it, and so does a missing label.

SML's `--authorization private` never reaches OpenTela: SML resolves it to the
launcher's own email before submission via `GET /v1/whoami` with the user's API
key (`Authorization: Bearer sk-rc-...`), which returns `{"email":
"<owner_email>"}` (401 on an unknown key).

`/v1/models` and `/v1/models_detailed` accept an *optional* bearer API key:
anonymous callers see only public entries (public/missing label, plus the
synthetic passthrough-provider entries, which are always public); a valid key
additionally reveals models whose email list contains the key's owner; a
present-but-unknown key gets 401. Every inference route enforces the same
rule before proxying — an unauthorized caller gets a 403 `permission_error`.

**No email addresses are published here.** Both endpoints are anonymously
readable, so an address in the payload is a harvestable one. Each entry
instead carries:

- `launched_by_name` — the launcher as a person (`apikey.owner_name`, taken
  from the IdP's `name` claim on profile load; derived from the address' local
  part when we have never seen that user, so the domain is dropped either way).
- `authorization` — the model's *effective* policy (override if it has one,
  else the launch label), rendered as `public` or a list of those same display
  names.
- `can_manage_access` — whether **this** caller may change the model's access,
  so the UI needs no identity to compare against a launcher's.
- `authorization_conflict` — the gateway is refusing to route this model for
  everyone (see below). Computed over every launch serving the name, before
  the listing filters run, so it is still reported when the colliding launch
  is one this caller cannot see.

`launched_by_email` and the raw `authorization` label are stripped from the
entry and from its `labels` dict. Raw addresses stay on the endpoints that
need them and already gate on identity: `/v1/model-access/{model_id}` (owner
or admin) and `/v1/profile` (your own). See ADR-0001.

**Served-name collisions.** Independent launches may advertise the same
served model name with *different* authorization labels (label strings are
compared as normalized policies, so reordered/re-cased email lists or
`public` vs a missing label are not a conflict). Because OpenTela
load-balances a model name across every peer advertising it, the gateway
cannot keep a request off the colliding launch's replica — so on a real
policy conflict it refuses to route the model for **everyone** (403 naming
the conflict) until one side is relaunched under a unique name or with a
matching label. Such a model is listed with `authorization_conflict: true`,
and the web UI marks its card **Out of service** — red status dot, disabled
Chat button, and a line saying why — rather than advertising a model whose
every request fails. See ADR-0001 for the reasoning.

### Changing access after launch

An OpenTela label is fixed for the life of the SLURM job, so the label above
is only the policy a model **launched with**. To change who may use a model
that is already running, the owner (or an admin) sets an *override*, which
replaces the label until it is reset:

```
GET    /v1/model-access/{model_id}    # effective policy, launch-time label, can_edit
PUT    /v1/model-access/{model_id}    # {"authorization": "public" | "a@epfl.ch,b@ethz.ch"}
DELETE /v1/model-access/{model_id}    # reset: the launch-time label decides again
```

The bearer may be a serving API key or an IdP access token. The web UI exposes
this as a **Manage access** menu on the model card, with a **Reset** button.

Overrides are keyed by the launch's `launch_id` label rather than by model
name, so one can never outlive its job and re-apply to a different launch that
later takes the same name — and consequently an override does not survive a
relaunch. Who may edit is decided by the `launched_by_email` label (the
`launched_by` label next to it is a cluster shell account, which matches no
platform identity); a model launched without one is admin-managed. Both labels
need a current SML. The listing reports the *outcome* of that check per entry
(`can_manage_access`) rather than the label it was made from.

Because the collision rule above compares *effective* policies, a change is
applied to every launch serving the name at once, and is refused unless the
caller may edit all of them. See ADR-0002.

## Dev Quick Start

```bash
make run          # build and start the complete local stack with Docker Compose
```

Docker with Compose v2.20+ and `make` are the only prerequisites. The first run
builds the application images and downloads the service images; Python, uv,
Node and npm are not needed on the host. Startup waits for database migrations,
idempotent account seeding, the Authentik OIDC provider, and the application
health checks. Re-running `make run` preserves local data and rotated user keys.

- Website: http://localhost:4321
- API / docs: http://localhost:8080/docs
- Chat: http://localhost:8081 (local development session; no separate login)
- Authentik: http://authentik.localhost:9000
- Website logins: `admin@localhost` / `admin`, `user@localhost` / `user`
- Application Postgres: `localhost:5433`, user/password/database `serving`

The local OIDC hostname is shared by browser redirects and Docker DNS. Browsers
resolve `authentik.localhost` to loopback; no hosts-file change is needed.
If port 9000 is occupied, use `LOCAL_AUTHENTIK_PORT=9002 make run`; the issuer,
container listener and published port change together.

Compose owns these local settings. Existing root/frontend `.env` files are not
loaded into the application containers or copied into the images, and the
local stack uses its own `serving-api-local` Compose project and named volumes.
The fixed credentials are for local development only. `COMPOSE_PROJECT_NAME`
can select a separate instance when needed.

```bash
make stop         # stop/remove local containers; preserve database and chat data
make reset        # stop and delete this local stack's data volumes
```

Models are listed from the checked-in OpenTela snapshot by default, so browsing
the site and local chat's model picker does not require the shared dev network.
The snapshot supplies discovery metadata, not a running LLM. To use real models,
opt into a reachable OpenTela head (and launch models there):

```bash
LOCAL_OTELA_FIXTURE_PATH= LOCAL_OTELA_HEAD_ADDR=http://<reachable-head>:8092 make run
```

The upstream must be reachable from the backend container. The local chat is
already connected to the backend with its own seeded API key. `make dummy-run`
remains an alias for fixture startup plus optional demo usage data; `make
seed-usage` also seeds that data separately.

### Dependencies and tests

`backend/requirements.txt` and `requirements-dev.txt` are lockfiles generated
from the `.in` files. CI, the Docker image and local installs all use them, so
prod runs exactly the versions CI tested. To add or change a dependency, edit
`backend/requirements.in` and run `make lock`. Dependabot opens weekly bump PRs.

```bash
make test         # unit + integration tests (Postgres via testcontainers)
make test-e2e     # real sign-up against Authentik in testcontainers (~1 min)
E2E_BACKEND_IMAGE=<image> make test-e2e   # same, against a built backend image
```
