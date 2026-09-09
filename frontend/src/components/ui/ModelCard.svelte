<script lang="ts">
  import { getApiUrl } from '../../lib/config';
  import { getModelLogo } from '../../lib/modelLogos';
  import { getModelMetricsUrl, getTierFromLaunchedBy, isPassthroughLauncher } from '../../lib/modelMetrics';

  interface Peer {
    peer_id?: string;
    hostname?: string;
    status?: string;
    device?: string;
    launched_by?: string;
    // The launcher as a person, resolved backend-side. The catalogue never
    // ships their email address — see identity_service.py for why the
    // translation cannot happen here.
    launched_by_name?: string;
    launch_id?: string;
    // The effective policy, as names: "public", or who may use the model.
    authorization?: string;
    // Whether THIS viewer may change the model's access (owner or admin),
    // decided by the backend so the card needs neither address to compare.
    can_manage_access?: boolean;
    // The gateway is refusing to route this model for everyone: launches
    // under one name disagree about who may use it.
    authorization_conflict?: boolean;
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
  // The viewer's API key, used to call the access endpoints. Whether they
  // may manage a given model comes from that model's own entry
  // (`can_manage_access`), so no identity has to be passed down here.
  export let viewerApiKey: string | null = null;

  const logoUrl = getModelLogo(entry.data.title);
  // Tier follows the peer's launched_by label: "k8s" or "cscs_L1" → 24/7,
  // anything else (a username from model-launch, or no label) → Slurm.
  const headLaunchedBy = entry.data.replicas[0]?.head?.launched_by;
  const isL1Model = isPassthroughLauncher(headLaunchedBy);
  // Passthrough models (CSCS-Inference, RCP-AIaaS) run on the provider's
  // infrastructure — our Grafana has no panels for them, so no button.
  const metricsUrl = isL1Model ? null : getModelMetricsUrl(entry.data.title);
  const tier = getTierFromLaunchedBy(headLaunchedBy);
  const chatUrl = `${chatAppUrl.replace(/\/$/, "")}/?models=${encodeURIComponent(entry.data.title)}`;

  let expanded = false;
  let copied = false;

  // Aggregated metadata for the headline summary — pull from the first
  // replica's head peer. All replicas of the same model usually share the
  // same launcher/framework, but we render them per-replica below anyway.
  $: firstHead = entry.data.replicas[0]?.head ?? {};
  $: framework = firstHead.framework || "";

  // The listing reports each entry's EFFECTIVE policy (its override where
  // it has one, else its launch label) as a top-level field, translated to
  // display names. "public" means anyone can use the model; a name list is
  // an allowlist — the entry only reached us because the backend authorized
  // this viewer, so badge it to explain the model isn't generally visible.
  // The panel's own answer still wins while it is open, since it is fetched
  // live and this one is as old as the page load.
  $: effectiveAuth =
    accessState?.effective_authorization ?? firstHead.authorization ?? "";
  $: isRestricted = !!effectiveAuth && effectiveAuth !== "public";

  // ── post-launch access management ──────────────────────────────────────
  //
  // A model's `authorization` label is fixed for the life of its Slurm job,
  // so changing who may use a running model means storing an override
  // against the launch instead. `launch_id` is what that override attaches
  // to; a model launched before SML stamped one cannot be managed here, and
  // says so rather than offering a button that would 409.
  // Owner-or-admin is decided by the backend, per entry: the comparison it
  // replaces needed the launcher's email address on the wire, which is
  // exactly what the catalogue no longer publishes. Presentation only —
  // /v1/model-access re-checks on every read and write.
  $: canManageAccess = !!viewerApiKey && !!firstHead.can_manage_access;
  $: hasLaunchId = entry.data.replicas
    .flatMap(r => [r.head, ...(r.followers ?? [])])
    .filter(Boolean)
    .every(p => !!p.launch_id);

  let accessOpen = false;
  let accessLoading = false;
  let accessSaving = false;
  let accessError: string | null = null;
  let accessState: any = null;
  // The textarea's working copy. Kept separate from accessState so an
  // in-progress edit survives a refresh of the server state.
  let accessDraft = "";
  let accessMode: "public" | "restricted" = "public";

  // Every HTTPException the gateway raises is rewritten into the OpenAI
  // error envelope by main.py's exception handler, so the useful text is at
  // error.message — `detail` only survives when a route is mounted without
  // that handler. Read both, and fall back to the status so a failure is
  // never reported as a blank message.
  async function errorMessage(res: Response, fallback: string): Promise<string> {
    try {
      const body = await res.json();
      return body?.error?.message || body?.detail || `${fallback} (HTTP ${res.status})`;
    } catch {
      return `${fallback} (HTTP ${res.status})`;
    }
  }

  function accessUrl(): string {
    return `${getApiUrl()}/v1/model-access/${entry.data.title
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`;
  }

  function applyState(state: any) {
    accessState = state;
    const effective = state?.effective_authorization ?? "public";
    accessMode = effective === "public" ? "public" : "restricted";
    accessDraft = effective === "public" ? "" : effective.split(",").join("\n");
  }

  async function openAccess() {
    accessOpen = !accessOpen;
    // Refetch every time it opens rather than caching the first answer: an
    // admin or a second tab may have changed the policy since, and showing a
    // stale one here invites overwriting their change.
    if (!accessOpen) return;
    accessLoading = true;
    accessError = null;
    try {
      const res = await fetch(accessUrl(), {
        headers: { Authorization: `Bearer ${viewerApiKey}` },
      });
      if (!res.ok) throw new Error(await errorMessage(res, "Could not load access settings."));
      applyState(await res.json());
    } catch (e: any) {
      accessError = e?.message || "Could not load access settings.";
    } finally {
      accessLoading = false;
    }
  }

  // The API takes the same grammar as the launch flag: "public", or a
  // comma-separated email list. The textarea accepts newlines because
  // pasting a list of collaborators one-per-line is the common case.
  function draftAsPolicy(): string {
    if (accessMode === "public") return "public";
    return accessDraft
      .split(/[\n,]/)
      .map(s => s.trim())
      .filter(Boolean)
      .join(",");
  }

  async function saveAccess() {
    const policy = draftAsPolicy();
    if (accessMode === "restricted" && !policy) {
      accessError = "List at least one email, or choose Public.";
      return;
    }
    accessSaving = true;
    accessError = null;
    try {
      const res = await fetch(accessUrl(), {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${viewerApiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ authorization: policy }),
      });
      if (!res.ok) throw new Error(await errorMessage(res, "Could not save access settings."));
      applyState(await res.json());
    } catch (e: any) {
      accessError = e?.message || "Could not save access settings.";
    } finally {
      accessSaving = false;
    }
  }

  // Reset drops the override so the model's launch-time label decides
  // again — which is why the label is never rewritten by a save.
  async function resetAccess() {
    accessSaving = true;
    accessError = null;
    try {
      const res = await fetch(accessUrl(), {
        method: "DELETE",
        headers: { Authorization: `Bearer ${viewerApiKey}` },
      });
      if (!res.ok) throw new Error(await errorMessage(res, "Could not reset access settings."));
      applyState(await res.json());
    } catch (e: any) {
      accessError = e?.message || "Could not reset access settings.";
    } finally {
      accessSaving = false;
    }
  }

  // Independent launches squatting one served name with different policies:
  // OpenTela load-balances the name across all of them, so the gateway
  // refuses to route it for EVERYONE (403) until the collision is resolved.
  // The model is effectively down, and the card says so below.
  //
  // Taken from the backend rather than compared here: it decides this over
  // every launch serving the name, including the ones this listing does not
  // contain — the colliding launch is usually restricted, so its entry is
  // filtered out of the very payload a client-side comparison would read.
  $: hasAuthConflict =
    accessState?.conflict ?? firstHead.authorization_conflict === true;

  // Aggregated status across all replicas:
  //   "blocked" — the gateway refuses to route the model (auth conflict)
  //   "ready"   — every replica's head is ready
  //   "pending" — at least one replica is still booting
  //   "unknown" — no status info at all (legacy binary)
  // Used by both the traffic-light dot and the greyed-tile styling so
  // we can compare which signal reads better at a glance.
  //
  // "blocked" outranks the replica states on purpose: healthy replicas are
  // exactly what makes this case dangerous to report as ready, since every
  // request to them is refused before it gets that far.
  $: aggregateStatus = (() => {
    if (hasAuthConflict) return "blocked";
    const statuses = entry.data.replicas.map(r => r.head?.status).filter(Boolean);
    if (statuses.length === 0) return "unknown";
    if (statuses.some(s => s === "pending")) return "pending";
    if (statuses.every(s => s === "ready")) return "ready";
    return "unknown";
  })();
  $: isPending = aggregateStatus === "pending";

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

  // Header summary across all replicas of this model. If every replica has
  // the same per-replica topology (almost always true: a model is launched
  // with one shape), show it with the replica multiplier prefixed when
  // there's more than one. Otherwise admit ambiguity rather than pick one
  // to display.
  //
  //   1 replica, 1 node              → "4x NVIDIA GH200 120GB"
  //   1 replica, 4 nodes             → "4 nodes × 4x NVIDIA GH200 120GB"
  //   2 replicas, 4 nodes each       → "2 replicas × 4 nodes × 4x NVIDIA GH200 120GB"
  //   replicas with differing shapes → "Various"
  function topologySummary(replicas: Replica[]): string {
    if (replicas.length === 0) return "unknown";
    const distinct = new Set(replicas.map(topologyString));
    if (distinct.size !== 1) return "Various";
    const perReplica = [...distinct][0];
    if (replicas.length === 1) return perReplica;
    return `${replicas.length} replicas × ${perReplica}`;
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
  class:tile-pending={isPending}
  class:tile-blocked={hasAuthConflict}
  class="relative group flex flex-col py-3 px-4 rounded-lg border border-black/15 dark:border-white/20 hover:bg-black/5 dark:hover:bg-white/5 hover:text-black dark:hover:text-white transition-colors duration-300 ease-in-out cursor-pointer"
>
  <div class="flex items-center gap-3 min-w-0">
    <img src={logoUrl} alt="Model logo" class="w-8 h-8 object-contain" />
    <div class="flex flex-col flex-1 min-w-0">
      <div class="font-semibold flex items-center gap-2 min-w-0">
        <!-- Traffic-light dot reflects aggregated replica status.
             Visible whether the tile is expanded or not. -->
        <span
          class="status-dot status-dot-{aggregateStatus}"
          title={aggregateStatus === "blocked" ? "Out of service: the gateway refuses every request for this model"
               : aggregateStatus === "ready" ? "All replicas ready"
               : aggregateStatus === "pending" ? "At least one replica is still starting up"
               : "Status unknown"}
          aria-label="status: {aggregateStatus}"
        ></span>
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
        {#if tier === "L2"}
          <span class="uptime-badge" title="This service is running on CSCS L2 Kubernetes">24/7</span>
        {:else if tier === "slurm"}
          <span class="slurm-badge" title="Model-launch Slurm job">Slurm</span>
        {/if}
        {#if hasAuthConflict}
          <span class="auth-conflict-badge" title="Independent launches are serving this model name with different authorization settings. The API refuses to route requests for it until the conflict is resolved (relaunch under a unique name or with matching authorization).">Out of service</span>
        {:else if isRestricted}
          <span class="restricted-badge" title="Restricted model: only users on its authorization list can see and use it">Restricted</span>
        {/if}
        {#if entry.data.replicaCount > 1}
          <span class="instance-count" title="Replicas of this model (separately-launched instances)">
            x{entry.data.replicaCount}
          </span>
        {/if}
      </div>
      <div class="text-sm">on {topologySummary(entry.data.replicas)}</div>
      <!-- Stated in the collapsed header, not behind the chevron: the model
           looks alive (its replicas are up and its tier badge is normal) and
           every request to it fails, so "why" has to be on the tile. -->
      {#if hasAuthConflict}
        <div class="text-sm out-of-service-note">
          Unavailable — this name is served by separate launches that disagree
          about who may use it, so the API refuses every request for it until
          one of them is relaunched under a different name or with matching
          access.
        </div>
      {/if}
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
      <!-- Action buttons: Chat (primary) + Metrics, left-aligned.
           Chat is inert while the model is refused: following it would open
           the chat app on a model whose every completion 403s. Metrics and
           Manage access stay live — the first still has data, and the second
           is how the owner FIXES this. -->
      <div class="flex flex-wrap gap-2">
        {#if hasAuthConflict}
          <span
            class="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-neutral-400 dark:bg-neutral-600 text-white text-sm font-medium cursor-not-allowed"
            title="This model is out of service: the API refuses every request for it while its launches disagree about who may use it."
            aria-disabled="true"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
            </svg>
            Chat unavailable
          </span>
        {:else}
        <a
          href={chatUrl}
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-black hover:bg-neutral-800 text-white text-sm font-medium transition-colors"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
            <polyline points="15 3 21 3 21 9"></polyline>
            <line x1="10" y1="14" x2="21" y2="3"></line>
          </svg>
          Chat
        </a>
        {/if}
        {#if metricsUrl}
          <a
            href={metricsUrl}
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 3v18h18"></path>
              <path d="M7 15l4-4 4 4 5-5"></path>
            </svg>
            Metrics
          </a>
        {/if}
        {#if canManageAccess}
          <button
            on:click={openAccess}
            class="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-slate-600 hover:bg-slate-700 text-white text-sm font-medium transition-colors"
            aria-expanded={accessOpen}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
              <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
            </svg>
            Manage access
          </button>
        {/if}
      </div>

      {#if canManageAccess && accessOpen}
        <div class="access-panel">
          {#if !hasLaunchId}
            <p class="access-note">
              This model was launched by a version of SML that does not stamp a
              launch id, so its access can only be changed by relaunching it
              with <code>--authorization</code>.
            </p>
          {:else if accessLoading}
            <p class="access-note">Loading access settings…</p>
          {:else}
            <div class="access-row">
              <label>
                <input type="radio" bind:group={accessMode} value="public" />
                Public — anyone on the platform can list and use this model
              </label>
            </div>
            <div class="access-row">
              <label>
                <input type="radio" bind:group={accessMode} value="restricted" />
                Restricted — only the people listed below
              </label>
            </div>
            {#if accessMode === "restricted"}
              <textarea
                bind:value={accessDraft}
                rows="4"
                spellcheck="false"
                placeholder={"alice@epfl.ch\nbob@ethz.ch"}
                class="access-emails"
              ></textarea>
              <p class="access-hint">One email per line (or comma-separated).</p>
            {/if}

            {#if accessState?.is_overridden}
              <p class="access-hint">
                Changed after launch.
                {#if accessState.label_authorization}
                  It was launched as
                  <code>{accessState.label_authorization}</code> — Reset puts it
                  back.
                {:else}
                  <!-- Null when this name's launches were started with
                       different labels; naming one of them would be a lie. -->
                  Reset returns each launch to the policy it started with.
                {/if}
              </p>
            {/if}

            <div class="access-actions">
              <button
                on:click={saveAccess}
                disabled={accessSaving}
                class="access-save"
              >
                {accessSaving ? "Saving…" : "Save"}
              </button>
              <button
                on:click={resetAccess}
                disabled={accessSaving || !accessState?.is_overridden}
                class="access-reset"
                title="Discard the change and go back to the model's launch-time authorization"
              >
                Reset
              </button>
            </div>

            {#if accessError}
              <p class="access-error">{accessError}</p>
            {/if}
          {/if}
        </div>
      {/if}

      <!-- Per-replica detail blocks -->
      {#each entry.data.replicas as replica, idx (replica.worker_group_id)}
        {@const head = replica.head}
        {@const isL1 = isPassthroughLauncher(head.launched_by)}
        {@const hasLabels = !!(head.launched_by || head.slurm_job_id || head.started_at || head.expires_at || head.framework || head.otela_version || head.status)}
        {@const peerLine = (p) => {
          const hn = p.hostname;
          const pid = p.peer_id;
          if (hn && pid) return `${hn} (${pid})`;
          return hn || pid || "unknown";
        }}
        <!-- For CSCS L1 passthrough models we don't run the pod, so the
             slurm/started/expires/peer fields don't exist. Show the
             minimal "what is this" block instead. -->
        {@const rows = isL1
          ? [
              ["model", entry.data.title],
              ["launched_by", head.launched_by],
              ["owner", head.launched_by_name],
              ["framework", head.framework],
            ]
          : [
              ["model", entry.data.title],
              ["launched_by", head.launched_by],
              ["owner", head.launched_by_name],
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
            {#if head.status && !isL1}
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

          {#if !hasLabels && !isL1}
            <p class="text-xs text-amber-700 dark:text-amber-400 mt-2">
              Launch metadata (launched_by, slurm_job_id, framework, started_at, expires_at…) requires OpenTela v0.0.6+ on the serving node.
            </p>
          {/if}

          <!-- Topology / extra labels block: framework_args, etc.
               Skipped for L1 — the upstream service doesn't surface any
               extra labels worth showing. -->
          {#if !isL1 && head.labels && Object.keys(head.labels).length > 0}
            <!-- launched_by_email / authorization are stripped from labels by
                 the backend (identity_service.py) so they can't be scraped off
                 a public page; excluded here too, because a frontend deploy
                 can run against an older gateway that still sends them. -->
            {@const extra = Object.entries(head.labels).filter(([k]) =>
              !["launched_by","launched_by_email","authorization","slurm_job_id","worker_group_id","framework","started_at","expires_at","slurm_partition","served_model_name"].includes(k)
            )}
            {#if extra.length > 0}
              {@const pad = Math.max(...extra.map(([k]) => k.length)) + 1}
              <div class="text-xs text-slate-500 dark:text-slate-400 mt-2 mb-1">Extra labels</div>
              <pre class="code-block">{extra.map(([k, v]) => `${k.padEnd(pad)} ${v}`).join("\n")}</pre>
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

  /* Traffic-light dot — small filled circle in front of the model name. */
  .status-dot {
    display: inline-block;
    width: 0.6rem;
    height: 0.6rem;
    border-radius: 9999px;
    flex-shrink: 0;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.1) inset;
  }
  .status-dot-ready {
    background-color: #10b981; /* emerald-500 */
  }
  .status-dot-pending {
    background-color: #f59e0b; /* amber-500 */
    animation: status-pulse 1.5s ease-in-out infinite;
  }
  .status-dot-unknown {
    background-color: #9ca3af; /* gray-400 */
  }
  /* Red rather than amber, and pulsing like pending does: this is not a
     model that is coming up, it is one nobody can call. */
  .status-dot-blocked {
    background-color: #dc2626; /* red-600 */
    animation: status-pulse 1.5s ease-in-out infinite;
  }
  @keyframes status-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }

  /* Greyed-tile treatment — applied to the outer card when any replica
     is pending. Mutes text/icons via `color` (inherited by SVGs using
     currentColor) and grayscales the logo + colored badges. The
     status-dot opts out by using its own background-color, so the
     amber pending signal stays vivid against the otherwise-grey card. */
  .tile-pending {
    background-color: rgba(0, 0, 0, 0.04);
    color: #6b7280; /* gray-500 */
  }
  :global(.dark) .tile-pending {
    background-color: rgba(255, 255, 255, 0.04);
    color: #9ca3af; /* gray-400 */
  }
  .tile-pending img,
  .tile-pending .uptime-badge,
  .tile-pending .slurm-badge,
  .tile-pending .restricted-badge,
  .tile-pending .instance-count {
    filter: grayscale(1);
  }

  /* Out-of-service treatment: the same muting as a pending tile — the model
     is not usable, so it should not read as one of the live ones — over a
     red ground and edge stripe, because unlike pending this does not resolve
     on its own. The conflict badge and status dot keep their colour in both
     states (they are the signal), and the logo is greyed here too so the
     tile cannot be mistaken for a healthy card at a glance.

     The stripe is an inset shadow rather than a border colour: the tile's
     border comes from a Tailwind utility on the same element, and which of
     the two wins would depend on stylesheet order. */
  .tile-blocked {
    background-color: rgba(220, 38, 38, 0.06);
    box-shadow: inset 3px 0 0 #dc2626; /* red-600 */
    color: #6b7280; /* gray-500 */
  }
  :global(.dark) .tile-blocked {
    background-color: rgba(220, 38, 38, 0.12);
    box-shadow: inset 3px 0 0 #f87171; /* red-400 */
    color: #9ca3af; /* gray-400 */
  }
  .tile-blocked img,
  .tile-blocked .uptime-badge,
  .tile-blocked .slurm-badge,
  .tile-blocked .restricted-badge,
  .tile-blocked .instance-count {
    filter: grayscale(1);
  }

  /* The one line on the collapsed tile that says what is wrong. Kept at
     text weight rather than a full alert box: it sits inside a list of
     cards, and a banner per card would shout down the list itself. */
  .out-of-service-note {
    color: #b91c1c; /* red-700 */
    font-weight: 500;
    margin-top: 0.125rem;
  }
  :global(.dark) .out-of-service-note {
    color: #fca5a5; /* red-300 */
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

  /* Access management panel. Colours come from currentColor and the two
     neutral tokens below so the panel inherits the card's light/dark
     treatment instead of pinning its own palette. */
  .access-panel {
    border: 1px solid rgb(148 163 184 / 0.4);
    border-radius: 6px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: 0.875rem;
  }

  .access-row label {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
  }

  .access-emails {
    width: 100%;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8125rem;
    padding: 6px 8px;
    border: 1px solid rgb(148 163 184 / 0.5);
    border-radius: 4px;
    background: transparent;
    color: inherit;
    resize: vertical;
  }

  .access-note,
  .access-hint {
    opacity: 0.75;
    margin: 0;
  }

  .access-error {
    color: #dc2626; /* red-600 */
    margin: 0;
  }

  .access-actions {
    display: flex;
    gap: 8px;
  }

  .access-save,
  .access-reset {
    padding: 6px 14px;
    border-radius: 6px;
    font-weight: 500;
    transition: background-color 0.15s;
  }

  .access-save {
    background-color: #0f172a; /* slate-900 */
    color: white;
  }

  .access-save:hover:not(:disabled) {
    background-color: #1e293b; /* slate-800 */
  }

  .access-reset {
    border: 1px solid rgb(148 163 184 / 0.6);
  }

  .access-reset:hover:not(:disabled) {
    background-color: rgb(148 163 184 / 0.15);
  }

  .access-save:disabled,
  .access-reset:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .restricted-badge {
    background-color: #d97706; /* amber-600 */
    color: white;
    font-weight: bold;
    font-size: 0.75em;
    padding: 0 6px;
    border-radius: 4px;
    flex-shrink: 0;
    cursor: help;
  }

  .auth-conflict-badge {
    background-color: #dc2626; /* red-600 */
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
