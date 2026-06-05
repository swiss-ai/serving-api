---
title: "Run Open WebUI locally with our API 🖥️"
description: "Spin up a local ChatGPT-style interface that talks to our model stacks via your API key"
date: "June 4 2026"
---

## Use our models from your own Open WebUI

[**Open WebUI**](https://github.com/open-webui/open-webui) is a self-hosted, ChatGPT-style interface. Because our API is **OpenAI-compatible**, you can point a local Open WebUI instance at our endpoint and chat with our model stacks straight from your laptop — no cluster access required. This is ideal for external collaborators who just want a friendly UI on top of the models.

### 1. Get your API key

Grab a key from the [API key page](/api_key). You'll use it as the OpenAI API key in the steps below.

```bash
export CSCS_SERVING_API="sk-rc-..."   # your key from the API key page
```

### 2. Run Open WebUI with Docker

The quickest path is to bake the connection details straight into the `docker run` command:

```bash
docker run -d \
  -p 3000:8080 \
  -e OPENAI_API_BASE_URL=https://api.swissai.svc.cscs.ch/v1 \
  -e OPENAI_API_KEY=$CSCS_SERVING_API \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

- `-p 3000:8080` exposes the UI on [http://localhost:3000](http://localhost:3000).
- `OPENAI_API_BASE_URL` points Open WebUI at our OpenAI-compatible endpoint.
- `OPENAI_API_KEY` authenticates every request with your key.
- `-v open-webui:/app/backend/data` keeps your accounts, chats, and settings across restarts.

Then open [http://localhost:3000](http://localhost:3000), create a local admin account (this stays on your machine), and start chatting.

### 3. (Optional) Configure the connection from the UI instead

If you'd rather not put the key in the `docker run` command, start the container without the two `-e` flags and add the connection from the UI:

1. Go to **Settings → Admin Settings → Connections**.
2. Under **OpenAI API**, set:
   - **API Base URL**: `https://api.swissai.svc.cscs.ch/v1`
   - **API Key**: your `sk-rc-...` key
3. Save. Our models will now appear in the model picker.

### 4. Pick a model

Open WebUI auto-discovers everything served at `GET /v1/models`. Select one from the dropdown, for example:

- `swiss-ai/Apertus-70B-Instruct-2509`
- `swiss-ai/Apertus-8B-Instruct-2509`

The available list changes over time — the model picker always reflects what's live. You can also see the full list any time with:

```bash
curl https://api.swissai.svc.cscs.ch/v1/models \
  -H "Authorization: Bearer $CSCS_SERVING_API"
```

### Verify the endpoint directly

If something isn't connecting, confirm the API works on its own before debugging Open WebUI:

```bash
curl -X POST https://api.swissai.svc.cscs.ch/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $CSCS_SERVING_API" \
  -d '{
    "model": "swiss-ai/Apertus-8B-Instruct-2509",
    "messages": [{"role": "user", "content": "Who is Pablo Picasso?"}],
    "stream": false
  }'
```

That's it — a fully local UI, our models, your key. 🎉
