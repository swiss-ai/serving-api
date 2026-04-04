const SGLANG_BASE = "https://metrics.swissai.svc.cscs.ch/d/sglang-monitoring/sglang-monitoring?orgId=1&from=now-15m&to=now&timezone=browser&refresh=5s&var-model=";
const VLLM_BASE = "https://metrics.swissai.svc.cscs.ch/d/vllm-master-v2/vllm-monitoring-v2?orgId=1&from=now-15m&to=now&timezone=browser&refresh=5s&var-model_name=";

type Engine = "sglang" | "vllm";

const modelEngines: Record<string, Engine> = {
  "swiss-ai/Apertus-8B-Instruct-2509": "sglang",
  "zai-org/GLM-4.7-Flash": "sglang",
  "Snowflake/snowflake-arctic-embed-l-v2.0": "vllm",
  "cais/HarmBench-Llama-2-13b-cls": "vllm",
  "meta-llama/Llama-3.3-70B-Instruct": "vllm",
  "meta-llama/Llama-Guard-4-12B": "vllm",
  "swiss-ai/Apertus-70B-Instruct-2509": "vllm",
};

/**
 * Get the metrics dashboard URL for a model, or null if none exists
 */
export function getModelMetricsUrl(modelName: string): string | null {
  const engine = modelEngines[modelName];
  if (!engine) return null;

  const encoded = encodeURIComponent(modelName);
  return engine === "sglang"
    ? `${SGLANG_BASE}${encoded}`
    : `${VLLM_BASE}${encoded}`;
}
