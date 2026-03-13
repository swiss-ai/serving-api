<script>
  import { onMount } from 'svelte';
  import { getModelLogo } from '@lib/modelLogos';
  
  export let matrixData = {};
  
  let models = [];
  let matrix = [];
  let hoveredCell = null;
  let containerRef;
  
  $: {
    if (matrixData && Object.keys(matrixData).length > 0) {
      const allModels = Object.keys(matrixData);
      
      // Calculate average win rate for each model
      const modelAvgs = allModels.map(model => {
        const opponents = allModels.filter(m => m !== model);
        const totalWinRate = opponents.reduce((sum, opponent) => {
          const wins = matrixData[model]?.[opponent] || 0;
          const total = wins + (matrixData[opponent]?.[model] || 0);
          const winRate = total > 0 ? wins / total : 0.5;
          return sum + winRate;
        }, 0);
        const avgWinRate = opponents.length > 0 ? totalWinRate / opponents.length : 0.5;
        return { model, avgWinRate };
      });
      
      // Sort by average win rate (descending)
      models = modelAvgs
        .sort((a, b) => b.avgWinRate - a.avgWinRate)
        .map(item => item.model);
      
      // Create the matrix with win rates
      matrix = models.map(model1 => 
        models.map(model2 => {
          if (model1 === model2) return 0.5; // Diagonal cells
          const wins = matrixData[model1]?.[model2] || 0;
          const total = wins + (matrixData[model2]?.[model1] || 0);
          return total > 0 ? wins / total : 0.5;
        })
      );
    }
  }
  
  function getCellColorStyle(winRate) {
    if (winRate === 0.5) return 'background-color: rgb(156 163 175 / 0.3)'; // Diagonal - gray
    
    const intensity = Math.abs(winRate - 0.5) * 2; // 0 to 1
    
    if (winRate > 0.5) {
      // Green gradient for wins
      const alpha = 0.3 + (intensity * 0.7); // 0.3 to 1.0
      return `background-color: rgba(34, 197, 94, ${alpha})`;
    } else {
      // Red gradient for losses  
      const alpha = 0.3 + (intensity * 0.7);
      return `background-color: rgba(239, 68, 68, ${alpha})`;
    }
  }
  
  function formatPercentage(winRate) {
    if (winRate === 0.5) return '—';
    return `${Math.round(winRate * 100)}%`;
  }
  
  function getTotalGames(model1, model2) {
    const wins1 = matrixData[model1]?.[model2] || 0;
    const wins2 = matrixData[model2]?.[model1] || 0;
    return wins1 + wins2;
  }
  
  function getDisplayName(modelName) {
    // Remove '-responses' suffix and clean up names
    return modelName.replace('-responses', '').replace(/apertus3-/, '');
  }

  function handleMouseMove(event, i, j, model1, model2) {
    const rect = event.currentTarget.getBoundingClientRect();
    const tooltipWidth = 250; // Approximate tooltip width
    const tooltipHeight = 80; // Approximate tooltip height
    
    // Calculate position relative to the cell
    let x = event.clientX + 15;
    let y = event.clientY - 10;
    
    // Prevent tooltip from going off-screen
    if (x + tooltipWidth > window.innerWidth) {
      x = event.clientX - tooltipWidth - 15;
    }
    if (y + tooltipHeight > window.innerHeight) {
      y = event.clientY - tooltipHeight - 10;
    }
    if (y < 0) {
      y = event.clientY + 15;
    }
    
    hoveredCell = {
      i, j, model1, model2, 
      winRate: matrix[i][j], 
      totalGames: getTotalGames(model1, model2),
      x: x,
      y: y
    };
  }

  function handleMouseLeave() {
    hoveredCell = null;
  }
</script>

<style>
  .matrix-table {
    border-collapse: separate;
    border-spacing: 2px;
  }
  
  .matrix-cell {
    width: 60px;
    height: 48px;
    text-align: center;
    vertical-align: middle;
    font-weight: bold;
    font-size: 0.75rem;
    border: 1px solid #d1d5db;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
  }
  
  .matrix-cell:hover {
    transform: scale(1.1);
    z-index: 10;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
  }
  
  .row-header {
    text-align: right;
    padding-right: 12px;
    font-weight: 500;
    font-size: 0.75rem;
    width: 140px;
    max-width: 140px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  
  .col-header {
    writing-mode: vertical-rl;
    text-orientation: mixed;
    padding: 8px 4px;
    font-weight: 500;
    font-size: 0.75rem;
    width: 60px;
    height: 120px;
    vertical-align: bottom;
    text-align: center;
  }
  
  :global(.dark) .matrix-cell {
    border-color: #4b5563;
    color: #f9fafb;
  }
  
  :global(.dark) .row-header,
  :global(.dark) .col-header {
    color: #d1d5db;
  }
</style>

<div class="space-y-6">
  {#if models.length > 0}
    <!-- Note -->
    <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
      <div class="text-sm text-blue-800 dark:text-blue-200">
        <strong>Note:</strong> This matrix displays percentage of wins of models against models. Some models may have more total battles but lower win percentages. For example, Model A might have 800 battles with 60% wins, while Model B has 200 battles with 95% wins. By ELO rankings Model A might be winning due to volume, but by win percentage Model B performs better.
      </div>
    </div>

    <!-- Heatmap Table -->
    <div class="relative overflow-x-auto bg-gray-50 dark:bg-gray-900 rounded-lg p-4">
      <table class="matrix-table">
        <thead>
          <tr>
            <th class="w-36"></th>
            {#each models as model}
              <th class="col-header text-gray-700 dark:text-gray-300">
                {getDisplayName(model)}
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each models as model1, i}
            <tr>
              <td class="row-header text-gray-700 dark:text-gray-300">
                {getDisplayName(model1)}
              </td>
              {#each models as model2, j}
                <td 
                  class="matrix-cell text-gray-800 dark:text-gray-100"
                  style={getCellColorStyle(matrix[i][j])}
                  on:mousemove={(e) => handleMouseMove(e, i, j, model1, model2)}
                  on:mouseleave={handleMouseLeave}
                  role="button"
                  tabindex="0"
                >
                  {formatPercentage(matrix[i][j])}
                </td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <!-- Legend -->
    <div class="flex items-center justify-center space-x-8 text-sm text-gray-600 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
      <div class="flex items-center space-x-2">
        <div class="w-6 h-6 rounded border border-gray-300" style="background-color: rgba(34, 197, 94, 0.8)"></div>
        <span>Higher Win Rate</span>
      </div>
      <div class="flex items-center space-x-2">
        <div class="w-6 h-6 rounded border border-gray-300" style="background-color: rgb(156 163 175 / 0.3)"></div>
        <span>Same Model</span>
      </div>
      <div class="flex items-center space-x-2">
        <div class="w-6 h-6 rounded border border-gray-300" style="background-color: rgba(239, 68, 68, 0.8)"></div>
        <span>Lower Win Rate</span>
      </div>
    </div>

    <!-- Statistics -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
        <div class="text-2xl font-bold text-gray-900 dark:text-white">{models.length}</div>
        <div class="text-sm text-gray-600 dark:text-gray-400">Models Compared</div>
      </div>
      <div class="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
        <div class="text-2xl font-bold text-gray-900 dark:text-white">
          {Object.values(matrixData).reduce((total, modelData) => 
            total + Object.values(modelData).reduce((sum, wins) => sum + wins, 0), 0
          )}
        </div>
        <div class="text-sm text-gray-600 dark:text-gray-400">Total Comparisons</div>
      </div>
      <div class="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
        <div class="text-2xl font-bold text-gray-900 dark:text-white">
          {Math.round(models.length * (models.length - 1) / 2)}
        </div>
        <div class="text-sm text-gray-600 dark:text-gray-400">Unique Matchups</div>
      </div>
    </div>
  {:else}
    <div class="text-center py-12 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
      <div class="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full mx-auto mb-4"></div>
      Loading matrix data...
    </div>
  {/if}
</div>

<!-- Tooltip -->
{#if hoveredCell}
  <div class="fixed z-50 px-3 py-2 bg-gray-900 text-white text-sm rounded-lg shadow-xl pointer-events-none transition-all duration-75"
       style="left: {hoveredCell.x}px; top: {hoveredCell.y}px; max-width: 250px;">
    <div class="font-semibold mb-1 text-xs">{getDisplayName(hoveredCell.model1)} vs {getDisplayName(hoveredCell.model2)}</div>
    <div class="text-gray-300 text-xs">Win Rate: <span class="text-white font-medium">{formatPercentage(hoveredCell.winRate)}</span></div>
    <div class="text-gray-300 text-xs">Total Games: <span class="text-white font-medium">{hoveredCell.totalGames}</span></div>
  </div>
{/if}