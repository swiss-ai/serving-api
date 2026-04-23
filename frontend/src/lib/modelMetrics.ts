const METRICS_BASE = "https://metrics.swissai.svc.cscs.ch/d/inference-unified/inference-monitoring-vllm-2b-sglang?orgId=1&from=now-15m&to=now&timezone=browser&var-datasource=PBFA97CFB590B2093&refresh=30s&var-model_name=";

export type HostingTier = "L2" | "slurm";

type ModelConfig = {
  metrics?: boolean;
  tier?: HostingTier;
};

const models: Record<string, ModelConfig> = {
  "swiss-ai/Apertus-8B-Instruct-2509": { tier: "L2" },
  "zai-org/GLM-4.7-Flash": { tier: "L2" },
  "Snowflake/snowflake-arctic-embed-l-v2.0": { tier: "L2" },
  "cais/HarmBench-Llama-2-13b-cls": { tier: "L2" },
  "meta-llama/Llama-3.3-70B-Instruct": { tier: "L2" },
  "meta-llama/Llama-Guard-4-12B": { tier: "L2" },
  "swiss-ai/Apertus-70B-Instruct-2509": { tier: "L2" },
  "Qwen/Qwen3.5-27B": { tier: "L2" },
};

export function getModelMetricsUrl(modelName: string): string | null {
  if (models[modelName]?.metrics === false) return null;
  return `${METRICS_BASE}${encodeURIComponent(modelName)}`;
}

export function getModelTier(modelName: string): HostingTier {
  return models[modelName]?.tier ?? "slurm";
}
