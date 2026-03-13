---
title: "[ARCHIVED] Spin Up Models on Alps"
description: "Getting Started (Archived)"
date: "May 19 2025"
---

:::caution[ARCHIVED]
**These instructions are no longer recommended.**

Please see the [model-launch repository](https://github.com/swiss-ai/model-launch) for up-to-date instructions and examples.
:::

## Quick Start

1. Download the script:
   ```bash
   wget https://raw.githubusercontent.com/swiss-ai/model-spinning/refs/heads/main/spin-model.py -O spin-model && chmod 755 spin-model && mv spin-model ~/.local/bin/
   ```

2. Check your available SLURM accounts:
   ```bash
   sacctmgr show associations user=$USER format=user,account%20
   ```

3. Launch a model:


   ```bash
   # Launch Mistral 7B with tensor parallelism 2 for 30 minutes
   spin-model --model mistralai/Mistral-7B-Instruct-v0.3 --tp-size 2 --time 30m --account YOUR_ACCOUNT
   ```
 
## Usage

```
usage: spin-model [-h] [--model MODEL] [--time TIME] [--vllm] [--vllm-help]
                     [--sp-help] [--account ACCOUNT] [--env ENV]
                     [--environment ENVIRONMENT]

Launch a model on SLURM

optional arguments:
  -h, --help            show this help message and exit
  --model MODEL         Name of the model to launch
  --time TIME           Time duration for the job. Examples: 2h, 1h30m, 90m,
                        1:30:00
  --vllm                Use vllm instead of sp to serve the model
  --vllm-help           Show available options for **vllm** model server
  --sp-help             Show available options for **sp** model server
  --account ACCOUNT     Slurm account to use for job submission
  --env ENV             Specify environment variables in format KEY=VALUE
  --environment ENVIRONMENT
                        Specify a custom environment file path
```

Additional model-specific arguments can be passed after the main arguments.

## Important Parameters

### Model Serving Options

- **Model Server**: By default, the script uses the `sp` model server. For certain architectures, you can use `--vllm` to switch to the vLLM server.
- **Documentation**: 
  - [Scratchpad (sp) documentation](https://github.com/swiss-ai/model-spinning/blob/main/sp-docs.txt)
  - [vLLM documentation](https://github.com/swiss-ai/model-spinning/blob/main/vllm-docs.txt)
  - View these docs directly with:
    ```bash
    spin-model --sp-help    # For sp server options
    spin-model --vllm-help  # For vllm server options
    ```

### Tensor Parallelism

The `--tp-size` parameter specifies the tensor parallelism size when a model is too large to fit on a single GPU:

- Models < 2B parameters: `--tp-size 1`
- Models < 14B parameters: `--tp-size 2`
- Models < 45B parameters: `--tp-size 3`
- Models < 90B parameters: `--tp-size 4`

### Time Allocation

The `--time` parameter accepts various formats:
- `2h` (2 hours)
- `1h30m` (1 hour and 30 minutes)
- `90m` (90 minutes)
- `1:30:00` (1 hour and 30 minutes in SLURM format)

Note: On Bristen nodes, time is limited to 1 hour maximum, while Clariden nodes allow up to 24 hours.

### Environment Variables

The `--env` parameter allows you to specify custom environment variables for your model server. This is useful for:

- Setting API keys (e.g., Hugging Face tokens)
- Configuring model-specific parameters
- Passing authentication credentials

You can specify multiple environment variables by using `--env` multiple times.
```bash 
spin-model --model CohereLabs/aya-expanse-8b --tensor-parallel-size 2 --time 4h --account YOUR_ACCOUNT --vllm --env HF_TOKEN=hf_abcdef0123456789 --env OPENAI_API_KEY=sk-proj-rniovncziroeuHNOIniuonOIU --env GOOGLE_API_KEY=aoimrewopv_einworcxz
```

### Model launch examples 

```bash
# Apertus 70B - SwissAI model
spin-model --model /a10/swiapertus3ss-alignment/checkpoints/apertus3-70B-iter_90000-tulu3-sft/checkpoint-14000 \
    --served-model-name swissai/apertus3-70b-0425 \
    --account YOUR_ACCOUNT \
    --tp-size 4

# Standard model launches (using sp server)
# Gemma 3 12B - Latest Google model with strong performance
spin-model --model google/gemma-3-12b-it --tp-size 2 --time 4h --account YOUR_ACCOUNT

# Qwen 2.5 7B 
spin-model --model Qwen/Qwen2.5-7B-Instruct --tp-size 2 --time 4h --account YOUR_ACCOUNT

# DeepSeek 14B - Distilled version of Qwen for better efficiency
spin-model --model deepseek-ai/DeepSeek-R1-Distill-Qwen-14B --tp-size 2 --time 4h --account YOUR_ACCOUNT

# vLLM-only architectures (must use --vllm flag)
# Mistral 7B 
spin-model --model mistralai/Mistral-7B-Instruct-v0.3 --tensor-parallel-size 2 --time 4h --account YOUR_ACCOUNT --vllm

# Phi-3 Mini 
spin-model --model microsoft/Phi-3-mini-4k-instruct --tensor-parallel-size 2 --time 4h --account YOUR_ACCOUNT --vllm

# Gemma 2 9B - Previous generation Google model
spin-model --model google/gemma-2-9b-it --tensor-parallel-size 2 --time 4h --account YOUR_ACCOUNT --vllm

# Launch Aya Expanse 8B model with vLLM server with a custom variable
spin-model --model CohereLabs/aya-expanse-8b --tensor-parallel-size 2 --time 4h --account YOUR_ACCOUNT --vllm --env HF_TOKEN=hf_abcdef0123456789
```

### Local Model Launch and Apertus

```bash
spin-model --model /a10/swiapertus3ss-alignment/checkpoints/apertus3-70B-iter_90000-tulu3-sft/checkpoint-14000 \
    --served-model-name swissai/apertus3-70b-0425 \
    --account YOUR_ACCOUNT \
    --tp-size 4
```

The `--model` parameter specifies the actual path to your model checkpoint. Please, make sure the environment (.toml file) has a mount point at `/a10`. 

The `--served-model-name` parameter allows you to specify a user-friendly name for your model when it's served. 

## After Submission

Once your job is submitted, you'll see:
- Job ID
- Commands to check job status and logs 
