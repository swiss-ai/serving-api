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
who serves the model. Three namespaces exist today:

| First segment | Serves | Example |
| :- | :- | :- |
| `SwissAI-Research/` | **This platform** — models we serve ourselves via OpenTela | `SwissAI-Research/swiss-ai/Apertus-70B-Instruct-2509` |
| `CSCS-Inference/` | CSCS L1 inference service (`api.inference.cscs.ch`) | `CSCS-Inference/swiss-ai/Apertus-70B-Instruct-2509` |
| `RCP-AIaaS/` | EPFL RCP AIaaS (`inference-rcp.epfl.ch`) | `RCP-AIaaS/swiss-ai/Apertus-8B-Instruct-2509` |

Bare `{org}/{model}` ids remain accepted and equivalent to
`SwissAI-Research/{org}/{model}` — the model list currently shows the bare
form; flipping the listing to the prefixed form is a pending decision
(it changes every advertised id, so clients need a migration window).

Requests are forwarded with the prefix stripped (the serving side only
knows its own id) and responses — including streamed chunks — are
rewritten back to the prefixed id. The same upstream model on two
providers is two distinct, individually-routable entries; a prefixed id
can never collide with (or shadow) a local model. All three prefixes are
reserved names: never launch a local model whose id starts with one.

**Curation.** What each provider surfaces is governed by its
`allowed_ids` in `backend/services/passthrough_service.py`:

- **CSCS L1** — unrestricted: everything its `/models` endpoint
  advertises is listed and routable (tracked live, ~30 s discovery TTL).
- **EPFL RCP** — allowlisted to the two Apertus Instruct models only.
  RCP's `/models` advertises many more (quant variants, scale-to-zero
  deployments that cold-start on first request); surfacing them would
  advertise capacity that responds slowly or misleadingly, so the
  allowlist stays deliberately narrow.

**Rate limiting** applies only to passthrough traffic: external
providers are a shared, platform-accountable resource, while
OpenTela-served models run on the caller's own GPU allocation. See
`backend/services/rate_limit_service.py`.

**Back-compat.** Un-prefixed passthrough ids (the historical form) still
route during a deprecation window — logged, first provider in
registration order wins — and responses already return the prefixed id
to advertise the migration target.

Planned (not yet implemented): `$OWNER/{org}/{model}` for private
user/group launches, and per-model health status for passthrough
entries.

## Repo Structure

```
backend/         # Python API proxy (FastAPI) — auth, caching, routing
frontend/        # web UI (Astro + Svelte)
meta/            # example Dockerfiles, example k8s manifests, build scripts
```

OpenTela (formerly OCF / "Open Compute Framework") is maintained upstream at [eth-easl/OpenTela](https://github.com/eth-easl/OpenTela). We maintain a fork at [swiss-ai/OpenTela](https://github.com/swiss-ai/opentela) to control deployments to dev+prod.

## Dev Quick Start

```bash
make install      # install backend dependencies
make run          # start backend on :8080
```
