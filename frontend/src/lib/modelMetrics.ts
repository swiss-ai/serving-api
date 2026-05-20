const METRICS_BASE = "https://metrics.swissai.svc.cscs.ch/d/inference-unified/inference-monitoring-vllm-2b-sglang?orgId=1&from=now-15m&to=now&timezone=browser&var-datasource=PBFA97CFB590B2093&refresh=30s&var-model_name=";

export type HostingTier = "L2" | "slurm";

type ModelConfig = {
  metrics?: boolean;
};

// Per-model overrides for the Grafana metrics dashboard URL. Add an entry
// with `metrics: false` for models that have no panel — clicking through
// to a blank dashboard is worse than hiding the button.
const models: Record<string, ModelConfig> = {};

export function getModelMetricsUrl(modelName: string): string | null {
  if (models[modelName]?.metrics === false) return null;
  return `${METRICS_BASE}${encodeURIComponent(modelName)}`;
}

// Tier is now driven by the peer's `launched_by` label instead of a
// hardcoded model list. Persistent infra launchers ("k8s", "cscs_L1") map
// to the 24/7 badge; anything else (a username from model-launch, or an
// older OpenTela binary that doesn't emit the label) is a Slurm job.
const PERSISTENT_LAUNCHERS = new Set(["k8s", "cscs_L1"]);

export function getTierFromLaunchedBy(launched_by: string | undefined): HostingTier {
  return launched_by && PERSISTENT_LAUNCHERS.has(launched_by) ? "L2" : "slurm";
}
