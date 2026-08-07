<script>
  import { onMount } from 'svelte';
  import { getApiUrl } from '../lib/config';
  import { getAccessToken } from '../lib/auth';

  let days = 30;
  let models = [];
  let totals = null;
  let loading = true;
  let error = null;
  let signedOut = false;

  function formatTokens(num) {
    if (num >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M';
    if (num >= 1_000) return (num / 1_000).toFixed(1) + 'K';
    return (num ?? 0).toString();
  }

  async function fetchData(selectedDays) {
    days = selectedDays;
    loading = true;
    error = null;
    signedOut = false;
    try {
      const token = await getAccessToken();
      if (!token) {
        signedOut = true;
        return;
      }
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), 20000);
      let res;
      try {
        res = await fetch(`${getApiUrl()}/v1/profile/usage?days=${selectedDays}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: abort.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (res.status === 401 || res.status === 403) {
        signedOut = true;
        return;
      }
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();
      models = data.models || [];
      totals = data.totals || null;
    } catch (e) {
      error =
        e.name === 'AbortError' ? 'Timed out after 20s.' : e.message;
    } finally {
      loading = false;
    }
  }

  onMount(() => fetchData(days));
</script>

<div class="space-y-6">
  <div class="flex justify-center gap-2">
    {#each [1, 7, 30, 90] as d}
      <button
        class="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors
          {days === d
          ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
          : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'}"
        on:click={() => fetchData(d)}
      >
        {d === 1 ? 'Today' : `${d} days`}
      </button>
    {/each}
  </div>

  {#if loading}
    <p class="text-center text-slate-500 dark:text-slate-400 py-10">Loading…</p>
  {:else if signedOut}
    <p class="text-center text-slate-500 dark:text-slate-400 py-10">
      Sign in to see your usage.
    </p>
  {:else if error}
    <p class="text-center text-red-600 dark:text-red-400 py-10">{error}</p>
  {:else if !models.length}
    <p class="text-center text-slate-500 dark:text-slate-400 py-10">
      No requests recorded in this window.
    </p>
  {:else}
    {#if totals}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        {#each [['Requests', totals.requests], ['Input tokens', totals.prompt_tokens], ['Output tokens', totals.completion_tokens], ['Total tokens', totals.total_tokens]] as [label, value]}
          <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-4 text-center">
            <div class="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {label}
            </div>
            <div class="mt-1 text-2xl font-semibold tabular-nums text-slate-900 dark:text-white">
              {label === 'Requests' ? value.toLocaleString() : formatTokens(value)}
            </div>
          </div>
        {/each}
      </div>
    {/if}

    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-200 dark:border-slate-700 text-left text-slate-500 dark:text-slate-400">
            <th class="py-2 pr-4">Model</th>
            <th class="py-2 pr-4 text-right">Requests</th>
            <th class="py-2 pr-4 text-right">Input</th>
            <th class="py-2 pr-4 text-right">Output</th>
            <th class="py-2 text-right">Total</th>
          </tr>
        </thead>
        <tbody>
          {#each models as m}
            <tr class="border-b border-slate-100 dark:border-slate-800">
              <td class="py-2 pr-4 font-medium text-slate-900 dark:text-white">{m.model}</td>
              <td class="py-2 pr-4 text-right tabular-nums">{m.requests.toLocaleString()}</td>
              <td class="py-2 pr-4 text-right tabular-nums">{formatTokens(m.prompt_tokens)}</td>
              <td class="py-2 pr-4 text-right tabular-nums">{formatTokens(m.completion_tokens)}</td>
              <td class="py-2 text-right tabular-nums">{formatTokens(m.total_tokens)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
