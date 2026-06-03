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

// `launched_by` values for OpenAI-compatible passthrough providers we
// forward to (CSCS L1, EPFL RCP, ...). These mirror the provider `name`
// values in backend/services/passthrough_service.py. The model card
// renders these in a compact branch (no pod metadata, since we don't run
// the pod) — see isPassthroughLauncher usage in ModelCard.svelte.
const PASSTHROUGH_LAUNCHERS = new Set(["cscs_L1", "rcp"]);

export function isPassthroughLauncher(launched_by: string | undefined): boolean {
  return !!launched_by && PASSTHROUGH_LAUNCHERS.has(launched_by);
}

// Tier is now driven by the peer's `launched_by` label instead of a
// hardcoded model list. Persistent infra launchers ("k8s" plus the
// passthrough providers) map to the 24/7 badge; anything else (a username
// from model-launch, or an older OpenTela binary that doesn't emit the
// label) is a Slurm job.
const PERSISTENT_LAUNCHERS = new Set(["k8s", ...PASSTHROUGH_LAUNCHERS]);

export function getTierFromLaunchedBy(launched_by: string | undefined): HostingTier {
  return launched_by && PERSISTENT_LAUNCHERS.has(launched_by) ? "L2" : "slurm";
}
