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

## Model Naming

**Username-namespaced served names.** SML launches are served under
`<username>/<vendor>/<model>` (e.g. `alice/swiss-ai/Apertus-70B`), where the
username is the cluster account that submitted the SLURM job — the same value
the peer advertises as its `launched_by` label. The gateway cross-checks the
two: a peer serving `alice/...` from a job that ran as someone else is dropped
from `/v1/models*`, and the id 403s for everyone, because OpenTela balances the
name across every peer advertising it and the gateway cannot keep a request off
the squatting peer. Ids with fewer than three segments carry no username and
are left unchecked, so pre-namespacing launches and passthrough-provider ids
keep working. See [ADR-0002](docs/adrs/0002-username-namespaced-served-model-names.md).

## Dev Quick Start

```bash
make install      # install backend dependencies
make run          # start backend on :8080
```
