<script>
  import { onMount } from 'svelte';
  import { getApiUrl } from '../lib/config';

  // Provided by the page after its server-side session check — sessions are
  // auth-astro cookies, so there is no token in localStorage to read here.
  export let accessToken = '';

  let rows = [];
  let loading = true;
  let error = null;
  let denied = false;

  onMount(async () => {
    try {
      if (!accessToken) {
        denied = true;
        return;
      }
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), 20000);
      let res;
      try {
        res = await fetch(`${getApiUrl()}/v1/admin/models`, {
          headers: { Authorization: `Bearer ${accessToken}` },
          signal: abort.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (res.status === 401 || res.status === 403) {
        denied = true;
        return;
      }
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();
      rows = data.models || [];
    } catch (e) {
      error = e.name === 'AbortError' ? 'Timed out after 20s.' : e.message;
    } finally {
      loading = false;
    }
  });
</script>

{#if loading}
  <p class="text-center text-slate-500 dark:text-slate-400 py-10">Loading…</p>
{:else if denied}
  <p class="text-center text-slate-500 dark:text-slate-400 py-10">
    Admin access required.
  </p>
{:else if error}
  <p class="text-center text-red-600 dark:text-red-400 py-10">{error}</p>
{:else if rows.length === 0}
  <p class="text-center text-slate-500 dark:text-slate-400 py-10">
    No models known to any source.
  </p>
{:else}
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="text-left text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
          <th class="py-2 pr-4 font-medium">Model ID</th>
          <th class="py-2 pr-4 font-medium">Source</th>
          <th class="py-2 pr-4 font-medium">Launched by</th>
          <th class="py-2 pr-4 font-medium">OpenTela</th>
          <th class="py-2 pr-4 font-medium">SML</th>
          <th class="py-2 pr-4 font-medium">Peers</th>
          <th class="py-2 font-medium">Publicly listed</th>
        </tr>
      </thead>
      <tbody>
        {#each rows as m (m.source + '/' + m.id)}
          <tr class="border-b border-slate-100 dark:border-slate-800">
            <td class="py-2 pr-4 font-mono text-xs text-slate-900 dark:text-white">{m.id}</td>
            <td class="py-2 pr-4 text-slate-600 dark:text-slate-300">{m.source}</td>
            <td class="py-2 pr-4 text-slate-600 dark:text-slate-300">{m.launched_by}</td>
            <td class="py-2 pr-4 text-slate-600 dark:text-slate-300">{m.otela_version || '—'}</td>
            <td class="py-2 pr-4 text-slate-600 dark:text-slate-300">{m.sml_version || '—'}</td>
            <td class="py-2 pr-4 text-slate-600 dark:text-slate-300">{m.peers || '—'}</td>
            <td class="py-2">
              {#if m.hidden_reason}
                <span class="text-amber-600 dark:text-amber-400">✗ {m.hidden_reason}</span>
              {:else}
                <span class="text-emerald-600 dark:text-emerald-400">✓</span>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
