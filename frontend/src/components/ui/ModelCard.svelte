<script lang="ts">
  import { getModelLogo } from '../../lib/modelLogos';
  import { getModelMetricsUrl, getModelTier } from '../../lib/modelMetrics';

  interface ModelCardProps {
      entry: {
          collection: string;
          slug: string;
          data: {
              title: string;
              description: string;
              devices: string[];
              instanceCount: number;
          };
      };
  }
  export let entry: ModelCardProps["entry"];
  export let chatAppUrl: string;

  const logoUrl = getModelLogo(entry.data.title);
  const metricsUrl = getModelMetricsUrl(entry.data.title);
  const tier = getModelTier(entry.data.title);
  const chatUrl = `${chatAppUrl.replace(/\/$/, "")}/?models=${encodeURIComponent(entry.data.title)}`;

  let copied = false;

  async function copyModelName(e: Event) {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(entry.data.title);
      copied = true;
      setTimeout(() => {
        copied = false;
      }, 1200);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }
</script>

<a
href={chatUrl}
target="_blank"
rel="noopener noreferrer"
class="relative group flex flex-nowrap py-3 px-4 pr-10 rounded-lg border border-black/15 dark:border-white/20 hover:bg-black/5 dark:hover:bg-white/5 hover:text-black dark:hover:text-white transition-colors duration-300 ease-in-out"
>
<div class="flex items-center gap-3 min-w-0">
  <img src={logoUrl} alt="Model logo" class="w-8 h-8 object-contain" />
  <div class="flex flex-col flex-1 min-w-0">
    <div class="font-semibold flex items-center gap-2 min-w-0">
      <span
        on:click={copyModelName}
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
        <span
          class="uptime-badge"
          title="This service is running on CSCS L2 Kubernetes"
        >
          24/7
        </span>
      {:else if tier === "slurm"}
        <span
          class="slurm-badge"
          title="Model-launch Slurm job"
        >
          Slurm
        </span>
      {/if}
      {#if entry.data.instanceCount > 1}
        <span class="instance-count" title="Number of launched instances for higher throughput">
          x{entry.data.instanceCount}
        </span>
      {/if}
    </div>
    <div class="text-sm">on {entry.data.devices.join(', ')}</div>
  </div>
</div>
<svg
  xmlns="http://www.w3.org/2000/svg"
  viewBox="0 0 24 24"
  class="absolute top-1/2 right-2 -translate-y-1/2 size-5 stroke-2 fill-none stroke-current"
>
  <line
    x1="5"
    y1="12"
    x2="19"
    y2="12"
    class="translate-x-3 group-hover:translate-x-0 scale-x-0 group-hover:scale-x-100 transition-transform duration-300 ease-in-out"
  />
  <polyline
    points="12 5 19 12 12 19"
    class="-translate-x-1 group-hover:translate-x-0 transition-transform duration-300 ease-in-out"
  />
</svg>
</a>
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


  @keyframes check-bounce {
    0% {
      transform: scale(1);
    }
    50% {
      transform: scale(1.4);
    }
    100% {
      transform: scale(1);
    }
  }

  :global(.animate-check-bounce) {
    animation: check-bounce 0.6s ease-in-out;
  }

  @keyframes name-flash {
    0% {
      color: inherit;
      transform: scale(1);
    }
    40% {
      color: #4f46e5;
      transform: scale(0.98);
    }
    100% {
      color: inherit;
      transform: scale(1);
    }
  }

  .animate-name-flash {
    animation: name-flash 1s ease-in-out;
  }
</style>