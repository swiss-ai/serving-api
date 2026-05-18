<script lang="ts">
  import { getModelLogo } from '../../lib/modelLogos';
  import { getModelMetricsUrl, getModelTier } from '../../lib/modelMetrics';

  interface Peer {
    peer_id?: string;
    hostname?: string;
    status?: string;
    device?: string;
    launched_by?: string;
    slurm_job_id?: string;
    started_at?: string;
    expires_at?: string;
    otela_version?: string;
    framework?: string;
    worker_group_id?: string;
    labels?: Record<string, string>;
  }

  interface Replica {
    worker_group_id: string;
    head: Peer;
    followers: Peer[];
    nodesPerReplica: number;
    devices: string[];
  }

  interface ModelCardProps {
      entry: {
          collection?: string;
          slug?: string;
          data: {
              title: string;
              description: string;
              devices: string[];
              replicas: Replica[];
              replicaCount: number;
              nodeCount: number;
          };
      };
  }
  export let entry: ModelCardProps["entry"];
  export let chatAppUrl: string;

  const logoUrl = getModelLogo(entry.data.title);
  const metricsUrl = getModelMetricsUrl(entry.data.title);
  const tier = getModelTier(entry.data.title);
  const chatUrl = `${chatAppUrl.replace(/\/$/, "")}/?models=${encodeURIComponent(entry.data.title)}`;

  let expanded = false;
  let copied = false;

  // Aggregated metadata for the headline summary — pull from the first
  // replica's head peer. All replicas of the same model usually share the
  // same launcher/framework, but we render them per-replica below anyway.
  $: firstHead = entry.data.replicas[0]?.head ?? {};
  $: framework = firstHead.framework || "";

  // "2026-05-17T07:00:00Z" → "2026-05-17T07:00:00Z (11 hours ago)".
  // Returns the iso untouched if it doesn't parse — keeps the row useful even
  // if OpenTela emits something we don't understand.
  function withRelative(iso: string | undefined): string {
    if (!iso) return "";
    const t = new Date(iso).getTime();
    if (isNaN(t)) return iso;
    const diffMs = t - Date.now();
    const abs = Math.abs(diffMs);
    const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
    let rel: string;
    if (abs < 60_000) rel = rtf.format(Math.round(diffMs / 1000), "second");
    else if (abs < 3_600_000) rel = rtf.format(Math.round(diffMs / 60_000), "minute");
    else if (abs < 86_400_000) rel = rtf.format(Math.round(diffMs / 3_600_000), "hour");
    else rel = rtf.format(Math.round(diffMs / 86_400_000), "day");
    return `${iso} (${rel})`;
  }

  // Multi-node topology string: "2 nodes × 4xGH200" for an 8-GPU TP replica.
  function topologyString(r: Replica): string {
    const dev = r.devices[0] || "?";
    if (r.nodesPerReplica === 1) return dev;
    return `${r.nodesPerReplica} nodes × ${dev}`;
  }

  async function copyModelName(e: Event) {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(entry.data.title);
      copied = true;
      setTimeout(() => { copied = false; }, 1200);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }

  function toggleExpand() {
    expanded = !expanded;
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleExpand();
    }
  }
</script>

<div
  role="button"
  tabindex="0"
  aria-expanded={expanded}
  on:click={toggleExpand}
  on:keydown={onKeyDown}
  class="relative group flex flex-col py-3 px-4 rounded-lg border border-black/15 dark:border-white/20 hover:bg-black/5 dark:hover:bg-white/5 hover:text-black dark:hover:text-white transition-colors duration-300 ease-in-out cursor-pointer"
>
  <div class="flex items-center gap-3 min-w-0">
    <img src={logoUrl} alt="Model logo" class="w-8 h-8 object-contain" />
    <div class="flex flex-col flex-1 min-w-0">
      <div class="font-semibold flex items-center gap-2 min-w-0">
        <span
          on:click={copyModelName}
          on:keydown={(e) => { if (e.key === "Enter") copyModelName(e); }}
          role="button"
          tabindex="0"
          class="inline-block cursor-pointer break-all font-mono {copied ? 'animate-name-flash' : ''}"
          title="Click to copy model name"
        >
          {entry.data.title}
        </span>
        <button
          on:click={copyModelName}
          title="Copy model name"
          class="inline-flex items-center justify-center p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors {copied ? 'animate-check-bounce' : ''} flex-shrink-0"
        >
          {#if copied}
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-green-500">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          {:else}
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
          {/if}
        </button>
        {#if metricsUrl}
          <a
            href={metricsUrl}
            target="_blank"
            rel="noopener noreferrer"
            on:click|stopPropagation
            class="metrics-badge"
            title="View metrics dashboard"
          >
            Metrics
          </a>
        {/if}
        {#if tier === "L2"}
          <span class="uptime-badge" title="This service is running on CSCS L2 Kubernetes">24/7</span>
        {:else if tier === "slurm"}
          <span class="slurm-badge" title="Model-launch Slurm job">Slurm</span>
        {/if}
        {#if entry.data.replicaCount > 1}
          <span class="instance-count" title="Replicas of this model (separately-launched instances)">
            x{entry.data.replicaCount}
          </span>
        {/if}
      </div>
      <div class="text-sm">on {entry.data.devices.join(', ') || 'unknown'}</div>
    </div>

    <!-- Chevron indicating expand state -->
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      class="size-5 stroke-2 fill-none stroke-current transition-transform duration-200"
      class:rotate-180={expanded}
      aria-hidden="true"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  </div>

  {#if expanded}
    <div
      class="mt-4 space-y-3"
      on:click|stopPropagation
      on:keydown|stopPropagation
      role="region"
    >
      <!-- Open in OpenWebUI button (what clicking the card used to do) -->
      <a
        href={chatUrl}
        target="_blank"
        rel="noopener noreferrer"
        class="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium transition-colors"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
          <polyline points="15 3 21 3 21 9"></polyline>
          <line x1="10" y1="14" x2="21" y2="3"></line>
        </svg>
        Open in OpenWebUI
      </a>

      <!-- Per-replica detail blocks -->
      {#each entry.data.replicas as replica, idx (replica.worker_group_id)}
        {@const head = replica.head}
        {@const hasLabels = !!(head.launched_by || head.slurm_job_id || head.started_at || head.expires_at || head.framework || head.otela_version || head.status)}
        {@const peerLine = (p) => {
          const hn = p.hostname;
          const pid = p.peer_id;
          if (hn && pid) return `${hn} (${pid})`;
          return hn || pid || "unknown";
        }}
        {@const rows = [
          ["model", entry.data.title],
          ["launched_by", head.launched_by],
          ["slurm_job_id", head.slurm_job_id],
          ["started_at", withRelative(head.started_at)],
          ["expires_at", withRelative(head.expires_at)],
          ["framework", head.framework],
          ["otela_version", head.otela_version],
          // worker_group_id is omitted when it's a synthesised legacy-N fallback —
          // it's just noise in that case.
          ["worker_group_id", replica.worker_group_id.startsWith("legacy-") ? "" : replica.worker_group_id],
          ["head", peerLine(head)],
          ...replica.followers.map((f, i) => [`follower_${i + 1}`, peerLine(f)]),
        ].filter(([, v]) => v && v !== "unknown" || v === peerLine(head) || (typeof v === "string" && v.includes("(")))}
        <div class="border border-black/10 dark:border-white/15 rounded-md p-3 bg-black/[0.02] dark:bg-white/[0.03]">
          <div class="text-xs text-slate-500 dark:text-slate-400 mb-2 flex items-center gap-2">
            <span class="font-semibold">Replica {idx + 1}{entry.data.replicaCount > 1 ? ` / ${entry.data.replicaCount}` : ""}</span>
            <span>·</span>
            <span>{topologyString(replica)}</span>
            {#if head.status}
              <span class="status-pill" data-status={head.status}>{head.status}</span>
            {/if}
          </div>

          <!-- Launch metadata: monospace, key/value rendered as one block.
               Empty fields hidden so the legacy / pre-v0.0.6 case shows
               just what's actually known (peer ids + model). -->
          <pre class="code-block">{rows
            .filter(([, v]) => v)
            .map(([k, v]) => `${k.padEnd(18)} ${v}`)
            .join("\n")}</pre>

          {#if !hasLabels}
            <p class="text-xs text-amber-700 dark:text-amber-400 mt-2">
              Launch metadata (launched_by, slurm_job_id, framework, started_at, expires_at…) requires OpenTela v0.0.6+ on the serving node.
            </p>
          {/if}

          <!-- Topology / extra labels block: framework_args, etc. -->
          {#if head.labels && Object.keys(head.labels).length > 0}
            {@const extra = Object.entries(head.labels).filter(([k]) =>
              !["launched_by","slurm_job_id","worker_group_id","framework","started_at","expires_at","slurm_partition","served_model_name"].includes(k)
            )}
            {#if extra.length > 0}
              <div class="text-xs text-slate-500 dark:text-slate-400 mt-2 mb-1">Extra labels</div>
              <pre class="code-block">{extra.map(([k, v]) => `${k.padEnd(18)} ${v}`).join("\n")}</pre>
            {/if}
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .instance-count {
    background-color: red;
    color: white;
    font-weight: bold;
    padding: 0 6px;
    border-radius: 4px;
  }

  .metrics-badge {
    background-color: #16a34a;
    color: white;
    font-weight: bold;
    font-size: 0.75em;
    padding: 0 6px;
    border-radius: 4px;
    text-decoration: none;
    flex-shrink: 0;
  }

  .metrics-badge:hover {
    background-color: #15803d;
  }

  .uptime-badge {
    background-color: #2563eb;
    color: white;
    font-weight: bold;
    font-size: 0.75em;
    padding: 0 6px;
    border-radius: 4px;
    flex-shrink: 0;
    cursor: help;
  }

  .slurm-badge {
    background-color: #9333ea;
    color: white;
    font-weight: bold;
    font-size: 0.75em;
    padding: 0 6px;
    border-radius: 4px;
    flex-shrink: 0;
    cursor: help;
  }

  .status-pill {
    display: inline-block;
    padding: 0.05em 0.45em;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    background-color: rgba(100, 116, 139, 0.2);
    color: inherit;
  }
  .status-pill[data-status="ready"] {
    background-color: rgba(16, 185, 129, 0.2);
    color: #047857;
  }
  :global(.dark) .status-pill[data-status="ready"] {
    color: #6ee7b7;
  }
  .status-pill[data-status="pending"] {
    background-color: rgba(234, 179, 8, 0.2);
    color: #a16207;
  }
  :global(.dark) .status-pill[data-status="pending"] {
    color: #fde68a;
  }

  .code-block {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.78rem;
    white-space: pre;
    overflow-x: auto;
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
    background-color: rgba(0, 0, 0, 0.05);
    color: inherit;
  }
  :global(.dark) .code-block {
    background-color: rgba(255, 255, 255, 0.05);
  }

  @keyframes check-bounce {
    0% { transform: scale(1); }
    50% { transform: scale(1.4); }
    100% { transform: scale(1); }
  }
  :global(.animate-check-bounce) {
    animation: check-bounce 0.6s ease-in-out;
  }

  @keyframes name-flash {
    0% { color: inherit; transform: scale(1); }
    40% { color: #4f46e5; transform: scale(0.98); }
    100% { color: inherit; transform: scale(1); }
  }
  .animate-name-flash {
    animation: name-flash 1s ease-in-out;
  }
</style>
