---
title: "Launch Models with model-launch 🚀"
description: "Framework-agnostic SLURM job submission for distributed inference"
date: "December 22 2025"
---

## Recommended: Use model-launch

_[**model-launch**](https://github.com/swiss-ai/model-launch) is the recommended tool for getting models on [serving.swissai.cscs.ch](https://serving.swissai.cscs.ch)!_

It provides a framework-agnostic approach to submitting SLURM jobs for distributed inference using SGLang or vLLM. The tool handles single-node and multi-node deployments, automatically integrates with OCF (Open Compute Framework) for service discovery, and makes your models accessible externally from outside the cluster. It includes ready-to-use examples for popular models like Swiss AI Apertus, DeepSeek-V3, Kimi-K2, and many others, with support for advanced features like multi-worker routing, pre-launch commands, and interactive debugging modes.

**Visit the [model-launch repository](https://github.com/swiss-ai/model-launch) for setup instructions, examples, and complete documentation.**
