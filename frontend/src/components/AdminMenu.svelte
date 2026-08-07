<script>
  import { onMount, onDestroy } from 'svelte';
  import { getApiUrl } from '../lib/config';
  import { getAccessToken } from '../lib/auth';

  // Renders nothing at all unless the signed-in user is an admin. This is
  // presentation only — every admin endpoint checks apikey.is_admin for
  // itself, so hiding the menu is convenience, not a security boundary.
  export let mobile = false;

  const LINKS = [{ href: '/users', label: 'User Activity' }];

  let isAdmin = false;
  let open = false;
  let root;

  async function check() {
    try {
      const token = await getAccessToken();
      if (!token) return;
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), 10000);
      let res;
      try {
        res = await fetch(`${getApiUrl()}/v1/profile`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: abort.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (!res.ok) return;
      isAdmin = !!(await res.json()).is_admin;
    } catch {
      // Signed out, offline, or a slow API: just don't offer the menu.
      isAdmin = false;
    }
  }

  function onDocumentClick(event) {
    if (open && root && !root.contains(event.target)) open = false;
  }

  function onKey(event) {
    if (event.key === 'Escape') open = false;
  }

  onMount(() => {
    check();
    document.addEventListener('click', onDocumentClick);
    document.addEventListener('keydown', onKey);
  });

  onDestroy(() => {
    if (typeof document === 'undefined') return;
    document.removeEventListener('click', onDocumentClick);
    document.removeEventListener('keydown', onKey);
  });
</script>

{#if isAdmin}
  {#if mobile}
    <div class="py-2">
      <p class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500 mb-1">
        Admin
      </p>
      {#each LINKS as link}
        <a
          href={link.href}
          class="block text-sm font-medium text-slate-600 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 transition-colors py-1"
        >
          {link.label}
        </a>
      {/each}
    </div>
  {:else}
    <div class="relative" bind:this={root}>
      <button
        type="button"
        class="flex items-center gap-1 text-sm font-medium text-slate-600 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 transition-colors"
        aria-haspopup="true"
        aria-expanded={open}
        on:click|stopPropagation={() => (open = !open)}
      >
        Admin
        <svg
          class="w-3.5 h-3.5 transition-transform {open ? 'rotate-180' : ''}"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          stroke-width="2"
          aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {#if open}
        <div
          class="absolute right-0 mt-2 w-48 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg py-1 z-50"
        >
          {#each LINKS as link}
            <a
              href={link.href}
              class="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800 transition-colors"
            >
              {link.label}
            </a>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
{/if}
