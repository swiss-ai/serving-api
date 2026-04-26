---
title: "[ARCHIVED] Getting Started with SP and Local Serving"
description: "[ARCHIVED] Run models locally with Scratchpad and Ollama"
date: "Mar 18 2024"
---

# [ARCHIVED] Getting Started with SP and Local Serving

## Run your Model Locally

With post-ampere GPU (using [Scratchpad](https://github.com/eth-easl/Scratchpad) as backend):

```bash
docker run --rm --gpus all --runtime=nvidia -v $OCF_MODELS:/models -e HF_MODELS=/models -e HF_TOKEN=$HF_TOKEN ghcr.io/researchcomputer/ocf-scratchpad:latest "sp serve
Qwen/Qwen2.5-7B-Instruct-1M --port 8080 --max-prefill-tokens 8192 --context-length 8192"
```

With pre-ampere GPU (using [Ollama](https://ollama.com/) as backend):

```bash
docker run --rm --gpus all --runtime=nvidia -v $OCF_MODELS:/models -e OLLAMA_MODELS=/models ghcr.io/researchcomputer/ocf-ollama:latest gemma3:1b
```