---
title: "API routes by backend"
description: "Which /v1 routes work on CSCS L1, EPFL RCP and Slurm (OpenTela) models"
date: "October 30 2026"
---

## Which backend serves your request

The first part of the model id decides where a request goes:

| Model id starts with | Backend |
| :- | :- |
| `CSCS-Inference/` | CSCS L1 |
| `RCP-AIaaS/` | EPFL RCP |
| `SwissAI-Research/` or `{username}/` | Slurm (OpenTela) |

## Route support

| Route | CSCS L1 | EPFL RCP | Slurm (OpenTela) |
| :- | :- | :- | :- |
| `/v1/chat/completions` | Yes | Yes | Yes |
| `/v1/completions` | Yes | Yes | Yes |
| `/v1/responses` | Yes | No | Yes |
| `/v1/embeddings` | No | Embedding models | Embedding models |
| `/v1/rerank` | No | Reranker models | Reranker models |
| `/v1/score` | No | No | Reranker models |
| `/v1/classify` | No | No | Classifier models |
| `/v1/tokenize` | No | No | Yes |
| `/v1/detokenize` | No | No | Yes |

"No" means the backend doesn't serve that route. You get an error with the backend's status code, usually a 404.

For Slurm models, the embedding, rerank, score and classify routes only work if the model was launched for that task. See [model-launch](/guides/01-getting-started/model-launch) to launch one.

## How requests are forwarded

- **CSCS L1 and EPFL RCP:** we send your request to the same path on the provider, so `/v1/rerank` goes to `/v1/rerank`. We replace your API key with the provider's key and remove the prefix from the model id. These requests are rate limited.
- **Slurm (OpenTela):** we send your request to the vLLM server running the model, with your own API key. The model id stays the same. `/v1/tokenize`, `/v1/detokenize`, `/v1/classify`, `/v1/score` and `/v1/rerank` go to the vLLM paths without `/v1`.

## What we don't forward

We only forward the routes in the table above. Other provider and vLLM routes can't be reached through this API, including:

- vLLM's admin routes, such as sleep and wake, weight updates, cache resets and profiling
- `/pooling` and `/tokenizer_info`
- Cohere-style `/v2/rerank` and `/v2/embed`
- `/v1/load_lora_adapter` and `/v1/unload_lora_adapter`
