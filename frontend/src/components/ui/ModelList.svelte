<script>
    import { onMount } from "svelte";
    import ModelCard from "./ModelCard.svelte";
    import { getApiUrl } from "../../lib/config";
    import { getModelTier } from "../../lib/modelMetrics";

    export let chatAppUrl;

    let models = [];
    let modelCount = 0;
    let loading = true;
    let error = null;

    let search = "";
    let activeFilter = "all"; // "all" | "24/7" | "slurm"

    onMount(async () => {
        try {
            const response = await fetch(`${getApiUrl()}/v1/models_detailed`);
            const data = await response.json();
            const rawModels = data.data;

            const modelsMap = new Map();

            for (const model of rawModels) {
                if (!modelsMap.has(model.id)) {
                    modelsMap.set(model.id, {
                        id: model.id,
                        devices: new Set(),
                        count: 0,
                    });
                }
                const existing = modelsMap.get(model.id);
                existing.devices.add(model.device);
                existing.count++;
            }

            modelCount = modelsMap.size;
            models = Array.from(modelsMap.values()).map(groupedModel => ({
                data: {
                    title: groupedModel.id,
                    description: groupedModel.id,
                    devices: Array.from(groupedModel.devices),
                    instanceCount: groupedModel.count,
                },
            }));
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

        if (activeFilter === "24/7") return getModelTier(title) === "L2";
        if (activeFilter === "slurm") return getModelTier(title) === "slurm";
        return true;
    });
</script>

<div>
    <div class="text-center mb-8">
        <h2 class="text-3xl font-bold text-slate-900 dark:text-white mb-4">
            Available Models
            {#if !loading && !error}
                <span style="display:inline-flex;align-items:center;justify-content:center;font-size:0.65em;font-weight:bold;line-height:1;padding:0.15em 0.5em;border-radius:4px;background-color:#6366f1;color:#fff;vertical-align:middle;margin-left:0.3em">{modelCount}</span>
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
