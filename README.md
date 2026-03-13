# serving-api

Frontend and backend API proxy for SwissAI LLM serving.

**Live at:** [serving.swissai.cscs.ch](https://serving.swissai.cscs.ch)

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
        │       OCF       │  OpenTela P2P routing → model=apertus-...
        │                 │  github.com/eth-easl/OpenTela
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
meta/            # Dockerfiles, k8s manifests, build scripts
tests/           # integration tests
tools/           # metrics & monitoring utilities
```

OCF (Open Compute Framework) is maintained at [eth-easl/OpenTela](https://github.com/eth-easl/OpenTela).

## Quick Start

### Docker

```bash
docker compose up
```

### Local Development

```bash
make install      # install backend dependencies
make run          # start backend on :8080

# frontend
cd frontend
npm install && npm run dev
```
