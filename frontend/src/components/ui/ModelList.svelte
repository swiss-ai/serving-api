<script>
    import { onMount } from "svelte";
    import ModelCard from "./ModelCard.svelte";
    import { getApiUrl } from "../../lib/config";

    let models = [];
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