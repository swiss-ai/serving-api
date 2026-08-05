<script>
  import { onMount } from 'svelte';
  import { getApiUrl } from '../lib/config';
  import { getAccessToken, getApiKey } from '../lib/auth';

  let days = 30;
  let users = [];
  let truncated = false;
  let loading = true;
  let error = null;
  let forbidden = false;

  function formatTokens(num) {
    if (num >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M';
    if (num >= 1_000) return (num / 1_000).toFixed(1) + 'K';
    return num.toString();
  }

  function formatWhen(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString();
  }

  async function fetchData(selectedDays) {
    days = selectedDays;
    loading = true;
    error = null;
    forbidden = false;
    try {
      const token = (await getAccessToken()) || getApiKey();
      if (!token) {
        forbidden = true;
        return;
      }
      const response = await fetch(
        `${getApiUrl()}/v1/admin/metrics/users?days=${selectedDays}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (response.status === 401 || response.status === 403) {
        forbidden = true;
        return;
      }
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const result = await response.json();
      users = result.users || [];
      truncated = !!result.truncated;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  onMount(() => fetchData(days));
</script>

<div class="space-y-4">
  <div class="flex justify-center gap-2">
    {#each [7, 30, 90] as d}
      <button
        class="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors
          {days === d
          ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
          : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300'}"
        on:click={() => fetchData(d)}
      >
        {d} days
      </button>
    {/each}
  </div>

  {#if loading}
    <p class="text-center text-slate-500 dark:text-slate-400 py-10">Loading…</p>
  {:else if forbidden}
    <p class="text-center text-slate-500 dark:text-slate-400 py-10">
      Admin access required. Sign in with an admin account (or store an admin
      API key) to view user activity.
    </p>
  {:else if error}
    <p class="text-center text-red-600 dark:text-red-400 py-10">{error}</p>
  {:else if users.length === 0}
    <p class="text-center text-slate-500 dark:text-slate-400 py-10">
      No activity recorded in this window.
    </p>
  {:else}
    {#if truncated}
      <p class="text-center text-sm text-amber-600 dark:text-amber-400">
        Showing a truncated sample of the window's traffic.
      </p>
    {/if}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-200 dark:border-slate-700 text-left text-slate-500 dark:text-slate-400">
            <th class="py-2 pr-4">#</th>
            <th class="py-2 pr-4">User</th>
            <th class="py-2 pr-4 text-right">Requests</th>
            <th class="py-2 pr-4 text-right">Total tokens</th>
            <th class="py-2 text-right">Last active</th>
          </tr>
        </thead>
        <tbody>
          {#each users as u, i}
            <tr class="border-b border-slate-100 dark:border-slate-800">
              <td class="py-2 pr-4 text-slate-400">{i + 1}</td>
              <td class="py-2 pr-4 font-medium text-slate-900 dark:text-white">{u.user}</td>
              <td class="py-2 pr-4 text-right tabular-nums">{u.requests.toLocaleString()}</td>
              <td class="py-2 pr-4 text-right tabular-nums">{formatTokens(u.total_tokens)}</td>
              <td class="py-2 text-right text-slate-500 dark:text-slate-400">{formatWhen(u.last_active)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
