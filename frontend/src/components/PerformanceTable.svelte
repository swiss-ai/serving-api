<script>
  import { onMount } from 'svelte';
  import { getModelLogo } from '@lib/modelLogos';
  import { getApiUrl } from '../lib/config';

  let data = [];
  let loading = true;
  let error = null;

  let sortBy = 'avg_throughput';
  let sortDesc = true;
  
  let filterHardware = 'All';
  let filterModel = 'All';

  $: uniqueHardware = ['All', ...new Set(data.map(d => d.hardware).filter(Boolean))].sort();
  $: uniqueModels = ['All', ...new Set(data.map(d => d.model).filter(Boolean))].sort();

  $: filteredData = data.filter(item => {
    const hwMatch = filterHardware === 'All' || item.hardware === filterHardware;
    const modelMatch = filterModel === 'All' || item.model === filterModel;
    return hwMatch && modelMatch;
  });

  $: sortedData = [...filteredData].sort((a, b) => {
    const valA = a[sortBy];
    const valB = b[sortBy];
    
    if (typeof valA === 'string') {
       return sortDesc ? valB.localeCompare(valA) : valA.localeCompare(valB);
    }
    return sortDesc ? valB - valA : valA - valB;
  });

  $: maxThroughput = Math.max(...data.map(d => d.avg_throughput || 0), 100);
  $: maxLatency = Math.max(...data.map(d => d.avg_latency || 0), 5);
  $: maxTTFT = Math.max(...data.map(d => d.avg_ttft || 0), 5);


  function toggleSort(field) {
    if (sortBy === field) {
      sortDesc = !sortDesc;
    } else {
      sortBy = field;
      sortDesc = true;
    }
  }

  function getDisplayName(modelName) {
    return modelName.replace('-responses', '').replace(/apertus3-/, '');
  }

  onMount(async () => {
    try {
      const response = await fetch(`${getApiUrl()}/v1/perf`);
      if (!response.ok) throw new Error('Failed to fetch performance data');
      const json = await response.json();
      data = json.data || [];
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  });

  function formatNumber(num, decimals = 2) {
      if (num === undefined || num === null) return '-';
      return num.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

</script>

<div class="space-y-4">
  <!-- Filters -->
  <div class="flex flex-col sm:flex-row gap-4 p-4 bg-white dark:bg-slate-900 rounded-xl border border-gray-100 dark:border-gray-800 shadow-sm">
      <div class="flex-1">
          <label for="hardware-filter" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Hardware</label>
          <select 
            id="hardware-filter" 
            bind:value={filterHardware}
            class="w-full px-3 py-2 bg-gray-50 dark:bg-slate-800 border border-gray-200 dark:border-gray-700 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
          >
              {#each uniqueHardware as hw}
                  <option value={hw}>{hw}</option>
              {/each}
          </select>
      </div>
      <div class="flex-1">
          <label for="model-filter" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
          <select 
            id="model-filter" 
            bind:value={filterModel}
            class="w-full px-3 py-2 bg-gray-50 dark:bg-slate-800 border border-gray-200 dark:border-gray-700 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
          >
              {#each uniqueModels as mod}
                  <option value={mod}>{getDisplayName(mod)}</option>
              {/each}
          </select>
      </div>
  </div>

  <div class="relative min-h-[300px] bg-white dark:bg-slate-900 rounded-xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
        {#if loading}
            <div class="absolute inset-0 flex items-center justify-center z-10 bg-white/50 dark:bg-slate-900/50">
                <div class="flex items-center space-x-2 text-indigo-600 dark:text-indigo-400">
                    <svg class="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    <span class="font-medium">Loading metrics...</span>
                </div>
            </div>
        {/if}

        {#if error}
             <div class="p-8 text-center text-red-500">
                Error: {error}
             </div>
        {:else}
        <!-- Desktop Table View -->
        <div class="hidden md:block overflow-x-auto">
            <table class="w-full text-sm text-left">
                <thead class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-slate-800 dark:text-gray-400 border-b border-gray-100 dark:border-gray-700">
                    <tr>
                        <th class="px-6 py-4 font-semibold cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-700 transition" on:click={() => toggleSort('model')}>
                            Model {sortBy === 'model' ? (sortDesc ? '↓' : '↑') : ''}
                        </th>
                        <th class="px-6 py-4 font-semibold cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-700 transition" on:click={() => toggleSort('hardware')}>
                            Hardware {sortBy === 'hardware' ? (sortDesc ? '↓' : '↑') : ''}
                        </th>
                        <th class="px-6 py-4 font-semibold cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-700 transition" on:click={() => toggleSort('concurrency')}>
                            Concurrency {sortBy === 'concurrency' ? (sortDesc ? '↓' : '↑') : ''}
                        </th>
                         <th class="px-6 py-4 font-semibold cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-700 transition text-right w-40" on:click={() => toggleSort('avg_throughput')}>
                            Throughput (tok/s) {sortBy === 'avg_throughput' ? (sortDesc ? '↓' : '↑') : ''}
                        </th>
                         <th class="px-6 py-4 font-semibold cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-700 transition text-right w-32" on:click={() => toggleSort('avg_latency')}>
                            Latency (s) {sortBy === 'avg_latency' ? (sortDesc ? '↓' : '↑') : ''}
                        </th>
                         <th class="px-6 py-4 font-semibold cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-700 transition text-right w-32" on:click={() => toggleSort('avg_ttft')}>
                            TTFT (s) {sortBy === 'avg_ttft' ? (sortDesc ? '↓' : '↑') : ''}
                        </th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                    {#each sortedData as row}
                        <tr class="bg-white dark:bg-slate-900 hover:bg-gray-50 dark:hover:bg-slate-800/50 transition-colors">
                            <td class="px-6 py-4 font-medium text-gray-900 dark:text-white whitespace-nowrap">
                                <div class="flex items-center space-x-3">
                                    {#if getModelLogo(row.model)}
                                        <img src={getModelLogo(row.model)} alt="" class="w-6 h-6 rounded-sm flex-shrink-0" />
                                    {/if}
                                    <span title={row.model}>{getDisplayName(row.model)}</span>
                                </div>
                            </td>
                             <td class="px-6 py-4 text-gray-600 dark:text-gray-400">
                                {row.hardware}
                            </td>
                             <td class="px-6 py-4 text-gray-600 dark:text-gray-400">
                                {row.concurrency}
                            </td>
                            <td class="px-6 py-4 text-right">
                                <div class="flex flex-col items-end">
                                    <span class="font-mono font-medium text-emerald-600 dark:text-emerald-400">
                                        {formatNumber(row.avg_throughput)}
                                    </span>
                                    <div class="h-1.5 w-24 bg-gray-100 dark:bg-gray-700 rounded-full mt-1 overflow-hidden">
                                        <div 
                                          class="h-full bg-emerald-500 rounded-full" 
                                          style="width: {(row.avg_throughput / maxThroughput) * 100}%"
                                        ></div>
                                    </div>
                                </div>
                            </td>
                             <td class="px-6 py-4 text-right">
                                <div class="flex flex-col items-end">
                                    <span class="font-mono font-medium text-gray-900 dark:text-white">
                                        {formatNumber(row.avg_latency)}
                                    </span>
                                     <div class="h-1.5 w-20 bg-gray-100 dark:bg-gray-700 rounded-full mt-1 overflow-hidden">
                                        <div 
                                          class="h-full bg-indigo-500 rounded-full" 
                                          style="width: {(row.avg_latency / maxLatency) * 100}%"
                                        ></div>
                                    </div>
                                </div>
                            </td>
                             <td class="px-6 py-4 text-right">
                                <div class="flex flex-col items-end">
                                     <span class="font-mono font-medium text-gray-900 dark:text-white">
                                        {formatNumber(row.avg_ttft)}
                                    </span>
                                     <div class="h-1.5 w-20 bg-gray-100 dark:bg-gray-700 rounded-full mt-1 overflow-hidden">
                                        <div 
                                          class="h-full bg-blue-500 rounded-full" 
                                          style="width: {(row.avg_ttft / maxTTFT) * 100}%"
                                        ></div>
                                    </div>
                                </div>
                            </td>
                        </tr>
                    {/each}
                     {#if sortedData.length === 0 && !loading}
                        <tr>
                            <td colspan="6" class="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                                No data matches your filters.
                            </td>
                        </tr>
                    {/if}
                </tbody>
            </table>
        </div>

        <!-- Mobile Card View -->
        <div class="md:hidden">
            {#each sortedData as row}
                <div class="p-4 border-b border-gray-100 dark:border-gray-800 last:border-0 hover:bg-gray-50 dark:hover:bg-slate-800/50 transition-colors">
                    <div class="flex items-center justify-between mb-3">
                        <div class="flex items-center space-x-2 font-medium text-gray-900 dark:text-white">
                             {#if getModelLogo(row.model)}
                                <img src={getModelLogo(row.model)} alt="" class="w-5 h-5 rounded-sm flex-shrink-0" />
                            {/if}
                            <span class="text-sm">{getDisplayName(row.model)}</span>
                        </div>
                    </div>
                    
                    <div class="grid grid-cols-2 gap-y-3 text-sm">
                        <div class="text-gray-500 dark:text-gray-400">Hardware:</div>
                        <div class="text-right text-gray-900 dark:text-gray-200 text-xs">{row.hardware}</div>
                        
                        <div class="text-gray-500 dark:text-gray-400">Concurrency:</div>
                        <div class="text-right text-gray-900 dark:text-gray-200">{row.concurrency}</div>

                        <div class="col-span-2 border-t border-gray-100 dark:border-gray-800 my-1"></div>

                        <div class="text-gray-500 dark:text-gray-400 self-center">Throughput:</div>
                        <div class="text-right">
                             <span class="font-mono font-medium text-emerald-600 dark:text-emerald-400">
                                {formatNumber(row.avg_throughput)} tok/s
                            </span>
                             <div class="h-1.5 w-full bg-gray-100 dark:bg-gray-700 rounded-full mt-1 overflow-hidden ml-auto max-w-[100px]">
                                <div class="h-full bg-emerald-500 rounded-full" style="width: {(row.avg_throughput / maxThroughput) * 100}%"></div>
                            </div>
                        </div>

                        <div class="text-gray-500 dark:text-gray-400 self-center">Latency:</div>
                         <div class="text-right">
                             <span class="font-mono font-medium text-gray-900 dark:text-white">
                                {formatNumber(row.avg_latency)} s
                            </span>
                             <div class="h-1.5 w-full bg-gray-100 dark:bg-gray-700 rounded-full mt-1 overflow-hidden ml-auto max-w-[80px]">
                                <div class="h-full bg-indigo-500 rounded-full" style="width: {(row.avg_latency / maxLatency) * 100}%"></div>
                            </div>
                        </div>

                        <div class="text-gray-500 dark:text-gray-400 self-center">TTFT:</div>
                         <div class="text-right">
                             <span class="font-mono font-medium text-gray-900 dark:text-white">
                                {formatNumber(row.avg_ttft)} s
                            </span>
                             <div class="h-1.5 w-full bg-gray-100 dark:bg-gray-700 rounded-full mt-1 overflow-hidden ml-auto max-w-[80px]">
                                <div class="h-full bg-blue-500 rounded-full" style="width: {(row.avg_ttft / maxTTFT) * 100}%"></div>
                            </div>
                        </div>
                    </div>
                </div>
            {/each}
             {#if sortedData.length === 0 && !loading}
                <div class="p-8 text-center text-gray-500 dark:text-gray-400">
                    No data matches your filters.
                </div>
            {/if}
        </div>
        {/if}
  </div>
   <div class="text-xs text-gray-500 dark:text-gray-400 italic text-center">
    Performance metrics are averages from recent observations. Throughput is tokens/query/second (higher is better). Latency and TTFT (Time To First Token) are in seconds (lower is better).
  </div>
</div>
