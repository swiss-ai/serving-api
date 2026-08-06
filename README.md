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

SML's `--authorization private` (the default) never reaches OpenTela: SML
resolves it to the launcher's own email before submission via
`GET /v1/whoami` with the user's API key (`Authorization: Bearer sk-rc-...`),
which returns `{"email": "<owner_email>"}` (401 on an unknown key).

`/v1/models` and `/v1/models_detailed` accept an *optional* bearer API key:
anonymous callers see only public entries (public/missing label, plus the
synthetic passthrough-provider entries, which are always public); a valid key
additionally reveals models whose email list contains the key's owner; a
present-but-unknown key gets 401. Every inference route enforces the same
rule before proxying — an unauthorized caller gets a 403 `permission_error`.

**Served-name collisions.** Independent launches may advertise the same
served model name with *different* authorization labels (label strings are
compared as normalized policies, so reordered/re-cased email lists or
`public` vs a missing label are not a conflict). Because OpenTela
load-balances a model name across every peer advertising it, the gateway
cannot keep a request off the colliding launch's replica — so on a real
policy conflict it refuses to route the model for **everyone** (403 naming
the conflict) until one side is relaunched under a unique name or with a
matching label. See ADR-0001 for the reasoning.

**Username-namespaced served names.** SML launches are served under
`<username>/<vendor>/<model>` (e.g. `alice/swiss-ai/Apertus-70B`), where the
username is the cluster account that submitted the SLURM job — the same value
the peer advertises as its `launched_by` label. The gateway cross-checks the
two: a peer serving `alice/...` from a job that ran as someone else is
dropped from `/v1/models*`, and the id 403s for everyone (same reasoning as a
policy conflict — OpenTela balances the name across every peer advertising
it). Ids with fewer than three segments carry no username and are left
unchecked, so pre-namespacing launches and passthrough-provider ids keep
working. See ADR-0002.

## Dev Quick Start

```bash
make install      # install backend dependencies
make run          # start backend on :8080
```
