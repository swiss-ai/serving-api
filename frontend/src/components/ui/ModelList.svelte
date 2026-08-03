<script>
    import { onMount } from "svelte";
    import ModelCard from "./ModelCard.svelte";
    import { getApiUrl } from "../../lib/config";
    import { getTierFromLaunchedBy } from "../../lib/modelMetrics";

    export let chatAppUrl;

    let models = [];
    let modelCount = 0;
    let replicaCount = 0;
    let loading = true;
    let error = null;

    let search = "";
    let activeFilter = "all"; // "all" | "24/7" | "slurm"

    // auth-astro mounts its endpoints under the default /api/auth prefix
    // (auth.config.ts sets no override); /session returns the session JSON,
    // or null for anonymous visitors.
    const SESSION_ENDPOINT = "/api/auth/session";

    // Resolve the bearer sent with /v1/models_detailed so the backend can
    // include restricted models this viewer is authorized for. Order: key
    // cached by api_key.astro (localStorage 'apiKey') → exchange the
    // auth-astro session accessToken via /v1/profile (caching under the
    // same keys api_key.astro uses) → anonymous. Every failure degrades to
    // anonymous — the public list must render for logged-out visitors.
    async function resolveApiKey(apiUrl) {
        const stored = localStorage.getItem("apiKey");
        if (stored) return stored;
        try {
            const sessionRes = await fetch(SESSION_ENDPOINT);
            if (!sessionRes.ok) return null;
            const session = await sessionRes.json();
            if (!session?.accessToken) return null;
            const profileRes = await fetch(`${apiUrl}/v1/profile`, {
                headers: { Authorization: `Bearer ${session.accessToken}` },
            });
            if (!profileRes.ok) return null;
            const profile = await profileRes.json();
            if (!profile.api_key) return null;
            localStorage.setItem("apiKey", profile.api_key);
            localStorage.setItem("apiBudget", String(profile.budget));
            return profile.api_key;
        } catch {
            return null;
        }
    }

    function fetchModels(apiUrl, apiKey) {
        return fetch(
            `${apiUrl}/v1/models_detailed`,
            apiKey ? { headers: { Authorization: `Bearer ${apiKey}` } } : undefined,
        );
    }

    onMount(async () => {
        try {
            const apiUrl = getApiUrl();
            const apiKey = await resolveApiKey(apiUrl);
            let response = await fetchModels(apiUrl, apiKey);
            if (response.status === 401 && apiKey) {
                // Stale/rotated stored key — drop the cache (api_key.astro
                // will re-fetch a fresh one on next visit) and retry
                // anonymously so the public models still render.
                localStorage.removeItem("apiKey");
                localStorage.removeItem("apiBudget");
                response = await fetchModels(apiUrl, null);
            }
            const data = await response.json();
            const rawModels = data.data;

            // Map worker_group_id → model_id, so metrics-only follower peers
            // (id="") can be attributed to the right model.
            const wgToModel = new Map();
            for (const m of rawModels) {
                if (m.id && m.worker_group_id) wgToModel.set(m.worker_group_id, m.id);
            }

            // Group: model_id → worker_group_id → list of peer entries.
            const modelsMap = new Map();
            for (const m of rawModels) {
                let modelId = m.id;
                if (!modelId) {
                    modelId = wgToModel.get(m.worker_group_id);
                    if (!modelId) continue; // orphan metrics peer
                }
                if (!modelsMap.has(modelId)) {
                    modelsMap.set(modelId, { id: modelId, replicas: new Map() });
                }
                const model = modelsMap.get(modelId);
                // Fall back to peer_id when worker_group_id is missing
                // (older OpenTela binary) so each peer becomes its own replica.
                const wg = m.worker_group_id || m.peer_id || `legacy-${model.replicas.size}`;
                if (!model.replicas.has(wg)) {
                    model.replicas.set(wg, {
                        worker_group_id: wg,
                        peers: [],
                    });
                }
                model.replicas.get(wg).peers.push(m);
            }

            // Materialize for rendering.
            modelCount = modelsMap.size;
            replicaCount = 0;
            models = Array.from(modelsMap.values()).map(grouped => {
                const replicas = Array.from(grouped.replicas.values()).map(r => {
                    // The head is the peer that owns the serving entry. In a
                    // multi-node TP replica only rank-0 registers `llm`, so
                    // has_service uniquely identifies it; the other peers all
                    // share the same id (from served_model_name) and would
                    // otherwise be indistinguishable.
                    const head =
                        r.peers.find(p => p.has_service) ||
                        r.peers.find(p => p.id === grouped.id) ||
                        r.peers[0];
                    const followers = r.peers.filter(p => p !== head);
                    return {
                        worker_group_id: r.worker_group_id,
                        head,
                        followers,
                        nodesPerReplica: r.peers.length,
                        // device strings are per-peer; collect distinct ones
                        devices: Array.from(new Set(r.peers.map(p => p.device).filter(Boolean))),
                    };
                });
                replicaCount += replicas.length;
                const allDevices = Array.from(new Set(replicas.flatMap(r => r.devices)));
                return {
                    data: {
                        title: grouped.id,
                        description: grouped.id,
                        devices: allDevices,
                        replicas,
                        replicaCount: replicas.length,
                        // Total peers (head + followers) across all replicas.
                        nodeCount: replicas.reduce((s, r) => s + r.nodesPerReplica, 0),
                    },
                };
            });
        } catch (err) {
            console.error("Error fetching models:", err);
            error = err.message;
        }
        finally {
            loading = false;
        }
    });

    $: filteredModels = models.filter((m) => {
        const title = m.data.title;

        if (search.trim()) {
            const q = search.trim().toLowerCase();
            const haystack = (title + " " + m.data.devices.join(" ")).toLowerCase();
            if (!haystack.includes(q)) return false;
        }

        const tier = getTierFromLaunchedBy(m.data.replicas[0]?.head?.launched_by);
        if (activeFilter === "24/7") return tier === "L2";
        if (activeFilter === "slurm") return tier === "slurm";
        return true;
    });
</script>

<div>
    <div class="text-center mb-8">
        <h2 class="text-3xl font-bold text-slate-900 dark:text-white mb-4">
            Available Models
            {#if !loading && !error}
                <span style="display:inline-flex;align-items:center;justify-content:center;font-size:0.65em;font-weight:bold;line-height:1;min-width:2em;padding:0.15em 0.5em;border-radius:4px;background-color:#6366f1;color:#fff;vertical-align:middle;margin-left:0.3em">{modelCount}</span>
                {#if replicaCount !== modelCount}
                    <span class="ml-2" title="Total replicas (separately-launched instances) across all models"><span style="display:inline-flex;align-items:center;justify-content:center;font-size:0.65em;font-weight:bold;line-height:1;min-width:2em;padding:0.15em 0.5em;border-radius:4px;background-color:red;color:#fff;vertical-align:middle;margin-left:0.3em">{replicaCount}</span>
                    </span>
                {/if}
            {/if}
        </h2>
        <p class="text-lg text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
            Access state-of-the-art language models from leading AI research organizations
        </p>
    </div>

    {#if !loading && !error}
    <div class="mb-6 flex flex-col sm:flex-row gap-3 sm:items-center sm:justify-between">
        <div class="flex flex-wrap gap-2">
            <button
                class="pill"
                class:active={activeFilter === "all"}
                on:click={() => (activeFilter = "all")}
            >All</button>
            <button
                class="pill"
                class:active={activeFilter === "24/7"}
                on:click={() => (activeFilter = "24/7")}
            >24/7</button>
            <button
                class="pill"
                class:active={activeFilter === "slurm"}
                on:click={() => (activeFilter = "slurm")}
            >Slurm</button>
        </div>
        <input
            type="text"
            bind:value={search}
            placeholder="Search by model name or GPU..."
            class="search-input"
        />
    </div>
    {/if}

    {#if loading}
        <div class="loading">Loading...</div>
    {:else if error}
    <div class="error">
      Error loading: {error}
    </div>
    {:else}
    <div class="model-list space-y-2">
        {#each filteredModels as model (model.data.title)}
            <ModelCard entry={model} {chatAppUrl} />
        {/each}
        {#if filteredModels.length === 0}
            <div class="text-center text-slate-500 dark:text-slate-400 py-6">
                No models match your filters.
            </div>
        {/if}
    </div>
    {/if}
</div>

<style>
    .search-input {
        width: 100%;
        max-width: 420px;
        padding: 0.5rem 0.75rem;
        border-radius: 6px;
        border: 1px solid rgba(0, 0, 0, 0.15);
        background-color: transparent;
        color: inherit;
        font-size: 0.875rem;
        outline: none;
        transition: border-color 0.15s ease;
    }
    :global(.dark) .search-input {
        border-color: rgba(255, 255, 255, 0.2);
    }
    .search-input:focus {
        border-color: #6366f1;
    }

    .pill {
        padding: 0.3rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 600;
        border: 1px solid rgba(0, 0, 0, 0.15);
        background-color: transparent;
        color: inherit;
        cursor: pointer;
        transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
    }
    :global(.dark) .pill {
        border-color: rgba(255, 255, 255, 0.2);
    }
    .pill:hover {
        background-color: rgba(0, 0, 0, 0.05);
    }
    :global(.dark) .pill:hover {
        background-color: rgba(255, 255, 255, 0.08);
    }
    .pill.active {
        background-color: #6366f1;
        border-color: #6366f1;
        color: white;
    }
</style>
