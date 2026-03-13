<script>
  import { onMount } from 'svelte';
  import Chart from 'chart.js/auto';
  import { getApiUrl } from '../lib/config';

  let chart;
  let loading = true;
  let error = null;
  let debugInfo = '';

  const fetchStatistics = async () => {
    try {
      const response = await fetch(`${getApiUrl()}/v1/statistics`);
      if (!response.ok) throw new Error('Failed to fetch statistics');
      const data = await response.json();
      debugInfo += `Data received: ${JSON.stringify(data)}\n`;
      return data;
    } catch (err) {
      error = err.message;
      debugInfo += `Error: ${err.message}\n`;
      return null;
    }
  };

  const createChart = (data) => {
    const canvas = document.getElementById('tokenUsageChart');
    if (!canvas) {
      debugInfo += 'Canvas element not found\n';
      return;
    }

    try {
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        debugInfo += 'Failed to get canvas context\n';
        return;
      }
      
      // Destroy existing chart if it exists
      if (chart) {
        chart.destroy();
      }

      // Process data for the chart
      // Sort data by date in ascending order
      const sortedData = [...data.data].sort((a, b) => new Date(a.date) - new Date(b.date));
      const dates = sortedData.map(item => item.date);
      const modelData = {};

      // Collect all unique models and their token usage per date
      sortedData.forEach(item => {
        item.usage.forEach(usage => {
          if (!modelData[usage.model]) {
            modelData[usage.model] = new Array(dates.length).fill(0);
          }
          const dateIndex = dates.indexOf(item.date);
          // Convert to millions of tokens
          modelData[usage.model][dateIndex] = usage.totalUsage / 1000000;
        });
      });

      // Create datasets for each model
      const datasets = Object.entries(modelData).map(([model, values], index) => ({
        label: model,
        data: values,
        backgroundColor: `hsl(${index * 137.5 % 360}, 70%, 60%)`,
        borderColor: `hsl(${index * 137.5 % 360}, 70%, 50%)`,
        borderWidth: 1
      }));

      debugInfo += `Creating chart with ${datasets.length} datasets\n`;

      // Get the current theme color
      const isDarkMode = document.documentElement.classList.contains('dark');
      const backgroundColor = isDarkMode ? '#1f2937' : '#ffffff'; // gray-800 in dark mode, white in light mode
      const textColor = isDarkMode ? '#e5e7eb' : '#1f2937'; // gray-200 in dark mode, gray-800 in light mode

      chart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: dates,
          datasets: datasets
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              stacked: true,
              title: {
                display: true,
                text: 'Date',
                color: textColor
              },
              ticks: {
                color: textColor
              },
              grid: {
                color: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'
              }
            },
            y: {
              stacked: true,
              beginAtZero: true,
              title: {
                display: true,
                text: 'Token Usage (Millions)',
                color: textColor
              },
              ticks: {
                color: textColor,
                callback: function(value) {
                  return value.toFixed(1);
                }
              },
              grid: {
                color: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'
              }
            }
          },
          plugins: {
            title: {
              display: true,
              text: 'Token Usage by Model Over Time',
              color: textColor
            },
            legend: {
              position: 'bottom',
              labels: {
                boxWidth: 12,
                color: textColor
              }
            },
            tooltip: {
              callbacks: {
                label: function(context) {
                  const value = context.raw;
                  return `${context.dataset.label}: ${value.toFixed(2)}M tokens`;
                }
              }
            }
          }
        }
      });
    } catch (err) {
      debugInfo += `Chart creation error: ${err.message}\n`;
      error = `Failed to create chart: ${err.message}`;
    }
  };

  onMount(async () => {
    debugInfo += 'Component mounted\n';
    const data = await fetchStatistics();
    if (data) {
      // Add a small delay to ensure the canvas is in the DOM
      setTimeout(() => {
        createChart(data);
      }, 100);
    }
    loading = false;
  });
</script>

<div class="w-full p-4 dark:bg-gray-800 rounded-lg">
  {#if loading}
    <div class="flex justify-center items-center h-64">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
    </div>
  {:else if error}
    <div class="text-red-500 text-center p-4">
      Error loading statistics: {error}
    </div>
  {:else}
    <div class="h-[600px]">
      <canvas id="tokenUsageChart"></canvas>
    </div>
  {/if}
  
  <!-- Debug information -->
  <!--
  <div class="mt-4 p-2 bg-gray-100 dark:bg-gray-700 rounded text-xs font-mono whitespace-pre-wrap">
    {debugInfo}
  </div>
  -->
</div> 