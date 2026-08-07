<script>
  import { getModelLogo } from '@lib/modelLogos';
  import { onMount } from 'svelte';
  import { getApiUrl } from '../lib/config';
  
  export let elosData = {}; // Initial data: {model: {input, output, total}}
  export let enableDateFilter = false;
  
  let timeRange = 30; // Default to 30 days
  let loading = false;
  let currentData = elosData;

  // Calendar-day windows against usage_daily (days=1 is "today"), matching
  // the My Usage page — same store, same semantics, same numbers.
  const timeRanges = [
    { label: 'Today', value: 1 },
    { label: 'Last 7 Days', value: 7 },
    { label: 'Last 30 Days', value: 30 },
    { label: 'Last 60 Days', value: 60 },
  ];

  // If initial data is provided, use it. But better to rely on fetching for consistency 
  // if we want to ensure the "Last X Days" logic is strictly followed from the client's 'now'.
  // However, for SSR, we accept elosData.
  
  $: sortedRankings = Object.entries(currentData)
    .filter(([model, tokens]) => {
      return tokens.total > 0 &&
             !model.startsWith('/') &&
             !model.startsWith('[') &&
             !model.startsWith('@');
    })
    .sort(([,a], [,b]) => b.total - a.total)
    .map(([model, tokens], index) => ({
      rank: index + 1,
      model,
      tokens,
      displayName: getDisplayName(model)
    }));
  
  function getDisplayName(modelName) {
    return modelName.replace('-responses', '').replace(/apertus3-/, '');
  }
  
  function getRankIcon(rank) {
    switch(rank) {
      case 1: return '🥇';
      case 2: return '🥈'; 
      case 3: return '🥉';
      default: return '';
    }
  }
  
  function getRankColor(rank) {
    switch(rank) {
      case 1: return 'text-yellow-600 dark:text-yellow-400';
      case 2: return 'text-gray-500 dark:text-gray-400';
      case 3: return 'text-amber-600 dark:text-amber-400';
      default: return 'text-gray-700 dark:text-gray-300';
    }
  }

  function formatNumber(num) {
    if (num === undefined || num === null) return '0';
    if (num >= 1_000_000_000) {
      return (num / 1_000_000_000).toFixed(1) + 'B';
    }
    if (num >= 1_000_000) {
      return (num / 1_000_000).toFixed(1) + 'M';
    }
    if (num >= 1_000) {
      return (num / 1_000).toFixed(1) + 'K';
    }
    return num.toString();
  }

  async function fetchData(days) {
    loading = true;
    try {
      const response = await fetch(`${getApiUrl()}/v1/leaderboard?days=${days}`);
      if (!response.ok) throw new Error('Network response was not ok');
      const result = await response.json();

      let newData = {};
      for (const m of result.models || []) {
        if (m.model) newData[m.model] = {
          input: m.prompt_tokens || 0,
          output: m.completion_tokens || 0,
          total: m.total_tokens || 0,
        };
      }
      currentData = newData;
    } catch (error) {
      console.error("Failed to fetch data:", error);
    } finally {
      loading = false;
    }
  }

  function handleTimeRangeChange(days) {
    timeRange = days;
    fetchData(days);
  }
</script>

<div class="w-full space-y-4">
  {#if enableDateFilter}
    <div class="flex flex-wrap gap-2 justify-center sm:justify-end">
      {#each timeRanges as range}
        <button
          class="px-3 py-1.5 text-sm font-medium rounded-lg transition-colors
            {timeRange === range.value 
              ? 'bg-blue-600 text-white shadow-sm' 
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'}"
          on:click={() => handleTimeRangeChange(range.value)}
          disabled={loading}
        >
          {range.label}
        </button>
      {/each}
    </div>
  {/if}

  <div class="relative min-h-[200px] {loading ? 'opacity-50 pointer-events-none' : ''} transition-opacity duration-200">
      {#if loading}
        <div class="absolute inset-0 flex items-center justify-center z-10">
          <div class="px-4 py-2 bg-white/80 dark:bg-slate-900/80 rounded-full shadow-lg text-sm font-medium text-blue-600 dark:text-blue-400">
            Updating...
          </div>
        </div>
      {/if}

      {#if sortedRankings.length > 0}
        <div class="overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr class="border-b border-gray-200 dark:border-gray-700">
                <th class="text-left py-3 px-2 font-semibold text-gray-700 dark:text-gray-300">Rank</th>
                <th class="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Model</th>
                <th class="text-right py-3 px-2 font-semibold text-gray-700 dark:text-gray-300">Input Tokens</th>
                <th class="text-right py-3 px-2 font-semibold text-gray-700 dark:text-gray-300">Output Tokens</th>
                <th class="text-right py-3 px-2 font-semibold text-gray-700 dark:text-gray-300">Total Tokens</th>
              </tr>
            </thead>
            <tbody>
              {#each sortedRankings as {rank, model, tokens, displayName}}
                <tr class="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                  <td class="py-3 px-2">
                    <div class="flex items-center space-x-2">
                      <span class="font-bold {getRankColor(rank)} text-lg">
                        #{rank}
                      </span>
                      <span class="text-lg">
                        {getRankIcon(rank)}
                      </span>
                    </div>
                  </td>
                  <td class="py-3 px-4">
                    <div class="flex items-center space-x-3">
                      {#if getModelLogo(model)}
                        <img src={getModelLogo(model)} alt="" class="w-6 h-6 rounded-sm flex-shrink-0" />
                      {/if}
                      <div>
                        <div class="font-medium text-gray-900 dark:text-gray-100">{displayName}</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400">{model}</div>
                      </div>
                    </div>
                  </td>
                  <td class="py-3 px-2 text-right">
                    <div class="font-mono text-gray-700 dark:text-gray-300" title={tokens.input.toLocaleString()}>
                      {formatNumber(tokens.input)}
                    </div>
                  </td>
                  <td class="py-3 px-2 text-right">
                    <div class="font-mono text-gray-700 dark:text-gray-300" title={tokens.output.toLocaleString()}>
                      {formatNumber(tokens.output)}
                    </div>
                  </td>
                  <td class="py-3 px-2 text-right">
                    <div class="font-mono font-semibold text-gray-900 dark:text-gray-100" title={tokens.total.toLocaleString()}>
                      {formatNumber(tokens.total)}
                    </div>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else if !loading}
        <div class="text-center py-8 text-gray-500 dark:text-gray-400">
          No data available for this time range.
        </div>
      {/if}
  </div>
</div> 