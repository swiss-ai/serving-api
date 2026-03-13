<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { StatisticsResponse } from '../types/statistics';
  import { Chart } from 'chart.js/auto';
  import { getApiUrl } from '../lib/config';

  let chartInstance: Chart | null = null;
  let statistics: StatisticsResponse | null = null;
  let error: string | null = null;
  let loading = true;

  // Function to get API key from storage
  function getApiKey(): string | null {
    return localStorage.getItem('apiKey');
  }

  async function fetchStatistics() {
    try {
      console.log('Fetching statistics...');
      const apiKey = getApiKey();
      
      if (!apiKey) {
        throw new Error('No API key found. Please log in and get your API key first.');
      }

      const response = await fetch(`${getApiUrl()}/v1/statistics`, {
        headers: {
          'Authorization': `Bearer ${apiKey}`
        }
      });
      
      if (!response.ok) {
        const errorText = await response.text();
        console.error('API Error:', response.status, errorText);
        throw new Error(`Failed to fetch statistics: ${response.status} ${errorText}`);
      }
      
      try {
        statistics = await response.json();
        console.log('Received statistics:', statistics);
      } catch (parseError) {
        console.error('Failed to parse API response:', parseError);
        throw new Error('Failed to parse statistics data');
      }
      
      if (!statistics.data || statistics.data.length === 0) {
        throw new Error('No statistics data available');
      }
      
      // Wait for the next tick to ensure the canvas is rendered (100ms is more reliable)
      await new Promise(resolve => setTimeout(resolve, 100));
      renderChart();
    } catch (e) {
      console.error('Error fetching statistics:', e);
      error = e instanceof Error ? e.message : 'An error occurred';
    } finally {
      loading = false;
    }
  }

  function renderChart() {
    if (!statistics || !statistics.data.length) {
      console.error('No data available for chart');
      return;
    }

    const ctx = document.getElementById('statisticsChart') as HTMLCanvasElement;
    if (!ctx) {
      console.error('Canvas element not found');
      return;
    }

    console.log('Rendering chart with data:', statistics.data);

    // Destroy existing chart if it exists
    if (chartInstance) {
      chartInstance.destroy();
    }

    const dates = statistics.data.map(d => d.date);
    const costs = statistics.data.map(d => d.totalCost);
    const traces = statistics.data.map(d => d.countTraces);

    console.log('Chart data:', { dates, costs, traces });

    chartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: dates,
        datasets: [
          {
            label: 'Total Cost',
            data: costs,
            borderColor: 'rgb(75, 192, 192)',
            tension: 0.1,
            yAxisID: 'y',
            fill: false
          },
          {
            label: 'Number of Traces',
            data: traces,
            borderColor: 'rgb(255, 99, 132)',
            tension: 0.1,
            yAxisID: 'y1',
            fill: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        scales: {
          y: {
            type: 'linear',
            display: true,
            position: 'left',
            title: {
              display: true,
              text: 'Cost'
            }
          },
          y1: {
            type: 'linear',
            display: true,
            position: 'right',
            title: {
              display: true,
              text: 'Number of Traces'
            },
            grid: {
              drawOnChartArea: false
            }
          }
        }
      }
    });
  }

  onMount(() => {
    console.log('Component mounted, fetching statistics...');
    fetchStatistics();
  });

  onDestroy(() => {
    if (chartInstance) {
      chartInstance.destroy();
    }
  });
</script>

<div class="p-4">
  <h2 class="text-2xl font-bold mb-4">SwissAI Statistics</h2>
  
  {#if loading}
    <div class="flex justify-center items-center h-64">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-gray-900"></div>
    </div>
  {:else if error}
    <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
      {error}
    </div>
  {:else if statistics}
    <div class="bg-white rounded-lg shadow p-4" style="height: 400px;">
      <canvas id="statisticsChart"></canvas>
    </div>
    
    <div class="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {#each statistics.data as day}
        <div class="bg-white rounded-lg shadow p-4">
          <h3 class="font-semibold text-lg mb-2">{day.date}</h3>
          <div class="space-y-2">
            <p>Total Cost: ${day.totalCost.toFixed(4)}</p>
            <p>Traces: {day.countTraces}</p>
            <p>Observations: {day.countObservations}</p>
            {#each day.usage as model}
              <div class="mt-2 pt-2 border-t">
                <p class="font-medium">{model.model}</p>
                <p>Input: {model.inputUsage.toLocaleString()} tokens</p>
                <p>Output: {model.outputUsage.toLocaleString()} tokens</p>
                <p>Total: {model.totalUsage.toLocaleString()} tokens</p>
                <p>Cost: ${model.totalCost.toFixed(4)}</p>
              </div>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div> 