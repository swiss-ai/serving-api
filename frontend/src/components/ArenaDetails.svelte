<script>
  import { onMount } from 'svelte';
  import { getModelLogo } from '@lib/modelLogos';
  
  export let elosData = {};

  let detailsData = [];
  let models = [];
  let selectedModel1 = '';
  let selectedModel2 = '';
  let filteredComparisons = [];
  let displayedComparisons = [];
  let currentPage = 0;
  const PAGE_SIZE = 10;
  let loading = false;
  let dataLoading = true;
  let expandedItems = new Set();
  let shuffledIndices = [];
  let isShuffled = false;

  // Cache for filtered results
  let filterCache = new Map();
  
  // Reactive statements
  $: {
    if (elosData && detailsData.length > 0) {
      // Get unique models
      const uniqueModels = new Set();
      Object.keys(elosData).forEach(model => uniqueModels.add(model));
      detailsData.forEach(comp => {
        uniqueModels.add(comp.model1);
        uniqueModels.add(comp.model2);
      });
      models = Array.from(uniqueModels).sort();
    }
  }

  // Watch for filter changes - trigger when either model selection changes or data loads
  $: {
    console.log('Filter trigger - dataLoading:', dataLoading, 'detailsData.length:', detailsData.length);
    if (!dataLoading && detailsData.length > 0) {
      console.log('Filter trigger:', selectedModel1, selectedModel2);
      filterComparisons();
    } else if (!dataLoading && detailsData.length === 0) {
      console.log('No data loaded, setting empty filtered comparisons');
      filteredComparisons = [];
      displayedComparisons = [];
    }
  }

  // Calculate model2 options based on selectedModel1
  $: model2Options = selectedModel1 ? getModel2Options(selectedModel1) : [];

  // Reset model2 when model1 changes to an incompatible selection
  $: {
    if (selectedModel1 && selectedModel2) {
      const availableModel2s = getModel2Options(selectedModel1);
      if (!availableModel2s.includes(selectedModel2)) {
        selectedModel2 = '';
      }
    }
  }

  async function loadDetailsData() {
    try {
      dataLoading = true;
      console.log('Starting to fetch details.json...');
      
      const url = new URL('/data/arena/details.json', window.location.origin);
      const response = await fetch(url);
      console.log('Response received:', response.status, response.statusText);
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      console.log('Starting to parse JSON...');
      const data = await response.json();
      console.log('JSON parsed successfully, entries:', data.length);
      
      detailsData = data;
      // Initialize shuffled indices
      shuffledIndices = Array.from({length: data.length}, (_, i) => i);
      dataLoading = false;
      console.log('Data loaded and processed successfully');
    } catch (error) {
      console.error('Error loading details data:', error);
      console.error('Error details:', {
        name: error.name,
        message: error.message,
        stack: error.stack
      });
      dataLoading = false;
    }
  }

  function getDisplayName(modelName) {
    return modelName.replace('-responses', '').replace(/apertus3-/, '');
  }

  function getModel2Options(model1) {
    if (!model1 || !detailsData.length) return [];
    
    const uniqueModels = new Set();
    detailsData.forEach(comp => {
      if (comp.model1 === model1) {
        uniqueModels.add(comp.model2);
      } else if (comp.model2 === model1) {
        uniqueModels.add(comp.model1);
      }
    });
    
    return Array.from(uniqueModels).sort();
  }

  function shuffleArray(array) {
    const shuffled = [...array];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
  }

  function shuffleEntries() {
    if (!filteredComparisons.length) return;
    
    loading = true;
    isShuffled = true;
    
    // Create shuffled indices for the filtered comparisons
    shuffledIndices = shuffleArray(Array.from({length: filteredComparisons.length}, (_, i) => i));
    
    currentPage = 0;
    updateDisplayedComparisons();
    loading = false;
  }

  function filterComparisons() {
    if (!detailsData.length) return;
    
    loading = true;
    console.log('Filtering with:', selectedModel1, selectedModel2);
    
    // Create cache key
    const cacheKey = `${selectedModel1}|${selectedModel2}`;
    
    // Check cache first
    if (filterCache.has(cacheKey)) {
      console.log('Using cached result for:', cacheKey);
      filteredComparisons = filterCache.get(cacheKey);
      // Reset shuffle state when filtering
      isShuffled = false;
      shuffledIndices = Array.from({length: filteredComparisons.length}, (_, i) => i);
      currentPage = 0;
      updateDisplayedComparisons();
      loading = false;
      return;
    }

    // Filter comparisons
    let filtered;
    if (!selectedModel1) {
      filtered = [...detailsData];
      console.log('No filter applied, showing all entries:', filtered.length);
    } else if (selectedModel1 && !selectedModel2) {
      filtered = detailsData.filter(comp => 
        comp.model1 === selectedModel1 || comp.model2 === selectedModel1
      );
      console.log('Filtered by model1 only:', selectedModel1, 'found:', filtered.length);
    } else {
      filtered = detailsData.filter(comp => 
        (comp.model1 === selectedModel1 && comp.model2 === selectedModel2) ||
        (comp.model1 === selectedModel2 && comp.model2 === selectedModel1)
      );
      console.log('Filtered by both models:', selectedModel1, 'vs', selectedModel2, 'found:', filtered.length);
    }
    
    // Cache the result
    filterCache.set(cacheKey, filtered);
    filteredComparisons = filtered;
    
    // Reset shuffle state when filtering
    isShuffled = false;
    shuffledIndices = Array.from({length: filtered.length}, (_, i) => i);
    
    currentPage = 0;
    updateDisplayedComparisons();
    loading = false;
  }

  function updateDisplayedComparisons() {
    const startIndex = 0;
    const endIndex = (currentPage + 1) * PAGE_SIZE;
    
    // Use shuffled indices if shuffled, otherwise use normal order
    const indicesToShow = shuffledIndices.slice(startIndex, endIndex);
    displayedComparisons = indicesToShow.map(index => filteredComparisons[index]);
    console.log('Updated displayed comparisons:', displayedComparisons.length);
  }

  function loadMore() {
    loading = true;
    currentPage++;
    updateDisplayedComparisons();
    loading = false;
  }

  function resetFilters() {
    selectedModel1 = '';
    selectedModel2 = '';
    currentPage = 0;
    isShuffled = false;
    expandedItems.clear();
    expandedItems = expandedItems; // Trigger reactivity
    // Clear cache to force refresh
    filterCache.clear();
    filterComparisons();
  }

  function getWinnerInfo(comp) {
    const model1Winner = comp.winner === comp.model1;
    const model2Winner = comp.winner === comp.model2;
    const isDraw = comp.winner === 'draw' || (!comp.winner && !comp.loser);
    
    return {
      model1Winner,
      model2Winner,
      isDraw,
      winnerText: isDraw ? 'Draw' : comp.winner
    };
  }

  function formatVotes(votes) {
    if (!votes || Object.keys(votes).length === 0) return '';
    
    return Object.entries(votes).map(([voter, decision]) => {
      if (!decision) return `${voter}: No decision recorded`;
      
      const reasoning = decision.reasoning || 
        (decision.winner === 'draw' ? 'Draw - both responses are considered equal.' : `Winner: ${decision.winner || 'Unknown'}`);
      return `${voter}: ${reasoning}`;
    }).join('\n\n');
  }

  function truncateText(text, maxLength = 200) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
  }

  function toggleExpanded(id) {
    if (expandedItems.has(id)) {
      expandedItems.delete(id);
    } else {
      expandedItems.add(id);
    }
    expandedItems = new Set(expandedItems); // Trigger reactivity
  }

  // Initialize on mount
  onMount(() => {
    loadDetailsData();
  });
</script>

<div class="space-y-6">
  {#if dataLoading}
    <div class="text-center py-12">
      <div class="animate-spin w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full mx-auto mb-4"></div>
      <p class="text-gray-600 dark:text-gray-400 text-lg">Loading arena details...</p>
      <p class="text-gray-500 dark:text-gray-500 text-sm mt-2">This may take a moment as we load the comparison data.</p>
    </div>
  {:else}
    <!-- Filters -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-6">
      <h3 class="text-lg font-semibold text-gray-900 dark:text-white mb-4">Filter Comparisons</h3>
      
      <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div>
          <label for="model1-select" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Model 1
          </label>
          <select 
            id="model1-select"
            bind:value={selectedModel1}
            class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:text-white"
          >
            <option value="">Select Model 1</option>
            {#each models as model}
              <option value={model}>{getDisplayName(model)}</option>
            {/each}
          </select>
        </div>

        <div>
          <label for="model2-select" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Model 2
          </label>
          <select 
            id="model2-select"
            bind:value={selectedModel2}
            disabled={!selectedModel1}
            class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:text-white disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <option value="">Select Model 2</option>
            {#each model2Options as model}
              <option value={model}>{getDisplayName(model)}</option>
            {/each}
          </select>
        </div>

        <div class="flex items-end">
          <button 
            on:click={shuffleEntries}
            disabled={!filteredComparisons.length || loading}
            class="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-medium rounded-md transition-colors flex items-center justify-center space-x-2"
          >
            <i class="fas fa-random"></i>
            <span>Shuffle</span>
          </button>
        </div>

        <div class="flex items-end">
          <button 
            on:click={resetFilters}
            class="w-full px-4 py-2 bg-gray-500 hover:bg-gray-600 text-white font-medium rounded-md transition-colors"
          >
            Reset Filters
          </button>
        </div>
      </div>

      <div class="mt-4 flex items-center justify-between">
        <div class="text-sm text-gray-600 dark:text-gray-400">
          Showing {displayedComparisons.length} of {filteredComparisons.length} comparisons
          {#if isShuffled}
            <span class="ml-2 px-2 py-1 bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-200 text-xs font-medium rounded-full">
              Shuffled
            </span>
          {/if}
        </div>
        {#if loading}
          <div class="flex items-center space-x-2 text-sm text-gray-500">
            <div class="animate-spin w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full"></div>
            <span>Loading...</span>
          </div>
        {/if}
      </div>
    </div>

    <!-- Comparisons -->
    {#if filteredComparisons.length === 0 && !loading}
      <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
        <p class="text-blue-800 dark:text-blue-200">No comparisons found for the selected criteria.</p>
      </div>
    {:else}
      <div class="space-y-6">
        {#each displayedComparisons as comp, index}
          {@const winnerInfo = getWinnerInfo(comp)}
          {@const compId = `${comp.response_idx}-${index}`}
          
          <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
            <!-- Header -->
            <div class="p-6 border-b border-gray-200 dark:border-gray-700">
              <div class="flex items-center justify-between">
                <h4 class="text-lg font-semibold text-gray-900 dark:text-white">
                  Question #{comp.response_idx}
                </h4>
                <div class="flex items-center space-x-2">
                  {#if winnerInfo.isDraw}
                    <span class="px-3 py-1 bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-sm font-medium rounded-full">
                      Draw
                    </span>
                  {:else}
                    <span class="px-3 py-1 bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-200 text-sm font-medium rounded-full">
                      Winner: {getDisplayName(comp.winner)}
                    </span>
                  {/if}
                </div>
              </div>
            </div>

            <!-- Prompt -->
            <div class="p-6 border-b border-gray-200 dark:border-gray-700">
              <h5 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Prompt</h5>
              <div class="text-gray-900 dark:text-gray-100 whitespace-pre-wrap">
                {#if expandedItems.has(`${compId}-prompt`) || comp.prompt.length <= 200}
                  {comp.prompt}
                {:else}
                  {truncateText(comp.prompt)}
                  <button 
                    on:click={() => toggleExpanded(`${compId}-prompt`)}
                    class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                  >
                    [Expand]
                  </button>
                {/if}
                {#if expandedItems.has(`${compId}-prompt`) && comp.prompt.length > 200}
                  <button 
                    on:click={() => toggleExpanded(`${compId}-prompt`)}
                    class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                  >
                    [Collapse]
                  </button>
                {/if}
              </div>
            </div>

            <!-- Responses -->
            <div class="grid grid-cols-1 lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-gray-200 dark:divide-gray-700">
              <!-- Model 1 Response -->
              <div class="p-6 {winnerInfo.model1Winner ? 'bg-green-50 dark:bg-green-900/10' : winnerInfo.isDraw ? 'bg-gray-50 dark:bg-gray-800/50' : 'bg-red-50 dark:bg-red-900/10'}">
                <div class="flex items-center space-x-3 mb-3">
                  {#if getModelLogo(comp.model1)}
                    <img src={getModelLogo(comp.model1)} alt="" class="w-6 h-6 rounded" />
                  {/if}
                  <h6 class="font-semibold text-gray-900 dark:text-white">
                    {getDisplayName(comp.model1)}
                  </h6>
                  {#if winnerInfo.model1Winner}
                    <span class="text-green-600 dark:text-green-400 text-lg">🏆</span>
                  {/if}
                </div>
                <div class="text-gray-900 dark:text-gray-100 text-sm whitespace-pre-wrap">
                  {#if expandedItems.has(`${compId}-resp1`) || comp.response1.length <= 200}
                    {comp.response1}
                  {:else}
                    {truncateText(comp.response1)}
                    <button 
                      on:click={() => toggleExpanded(`${compId}-resp1`)}
                      class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                    >
                      [Expand]
                    </button>
                  {/if}
                  {#if expandedItems.has(`${compId}-resp1`) && comp.response1.length > 200}
                    <button 
                      on:click={() => toggleExpanded(`${compId}-resp1`)}
                      class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                    >
                      [Collapse]
                    </button>
                  {/if}
                </div>
              </div>

              <!-- Model 2 Response -->
              <div class="p-6 {winnerInfo.model2Winner ? 'bg-green-50 dark:bg-green-900/10' : winnerInfo.isDraw ? 'bg-gray-50 dark:bg-gray-800/50' : 'bg-red-50 dark:bg-red-900/10'}">
                <div class="flex items-center space-x-3 mb-3">
                  {#if getModelLogo(comp.model2)}
                    <img src={getModelLogo(comp.model2)} alt="" class="w-6 h-6 rounded" />
                  {/if}
                  <h6 class="font-semibold text-gray-900 dark:text-white">
                    {getDisplayName(comp.model2)}
                  </h6>
                  {#if winnerInfo.model2Winner}
                    <span class="text-green-600 dark:text-green-400 text-lg">🏆</span>
                  {/if}
                </div>
                <div class="text-gray-900 dark:text-gray-100 text-sm whitespace-pre-wrap">
                  {#if expandedItems.has(`${compId}-resp2`) || comp.response2.length <= 200}
                    {comp.response2}
                  {:else}
                    {truncateText(comp.response2)}
                    <button 
                      on:click={() => toggleExpanded(`${compId}-resp2`)}
                      class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                    >
                      [Expand]
                    </button>
                  {/if}
                  {#if expandedItems.has(`${compId}-resp2`) && comp.response2.length > 200}
                    <button 
                      on:click={() => toggleExpanded(`${compId}-resp2`)}
                      class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                    >
                      [Collapse]
                    </button>
                  {/if}
                </div>
              </div>
            </div>

            <!-- Voting Reasoning -->
            {#if comp.votes && Object.keys(comp.votes).length > 0}
              {@const votingText = formatVotes(comp.votes)}
              <div class="p-6 bg-gray-50 dark:bg-gray-800/50 border-t border-gray-200 dark:border-gray-700">
                <h5 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Voter Reasoning</h5>
                <div class="text-gray-900 dark:text-gray-100 text-sm whitespace-pre-wrap">
                  {#if expandedItems.has(`${compId}-votes`) || votingText.length <= 200}
                    {votingText}
                  {:else}
                    {truncateText(votingText)}
                    <button 
                      on:click={() => toggleExpanded(`${compId}-votes`)}
                      class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                    >
                      [Expand]
                    </button>
                  {/if}
                  {#if expandedItems.has(`${compId}-votes`) && votingText.length > 200}
                    <button 
                      on:click={() => toggleExpanded(`${compId}-votes`)}
                      class="ml-2 text-blue-600 dark:text-blue-400 hover:underline text-sm font-medium"
                    >
                      [Collapse]
                    </button>
                  {/if}
                </div>
              </div>
            {/if}
          </div>
        {/each}

        <!-- Load More Button -->
        {#if displayedComparisons.length < filteredComparisons.length}
          <div class="text-center">
            <button 
              on:click={loadMore}
              disabled={loading}
              class="px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-medium rounded-lg transition-colors flex items-center space-x-2 mx-auto"
            >
              {#if loading}
                <div class="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full"></div>
              {/if}
              <span>Load More ({filteredComparisons.length - displayedComparisons.length} remaining)</span>
            </button>
          </div>
        {/if}
      </div>
    {/if}
  {/if}
</div> 