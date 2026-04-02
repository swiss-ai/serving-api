<script>
    import { onMount } from "svelte";
    import ModelCard from "./ModelCard.svelte";
    import { getApiUrl } from "../../lib/config";

    let models = [];
    let modelCount = 0;
    let loading = true;
    let error = null;
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
    {#if loading}
        <div class="loading">Loading...</div>
    {:else if error}
    <div class="error">
      Error loading: {error}
    </div>
    {:else}
    <div class="model-list space-y-2">
        {#each models as model}
            <ModelCard entry={model} />
        {/each}
    </div>
    {/if}
</div>

<style>
/* Optional styling */
</style>