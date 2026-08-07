<script>
  // The collapsed nav: an About dropdown (Docs / Research / FAQ) and,
  // depending on session state, either a profile menu (My Usage, API Key,
  // Users for admins, Sign out) or a Sign in button. Session and admin
  // status are resolved server-side in Header.astro and passed as props —
  // sessions are auth-astro cookies, invisible to client code.
  //
  // Hiding the admin entry is presentation only: every admin endpoint
  // checks apikey.is_admin for itself.
  import { onMount, onDestroy } from 'svelte';
  import { signIn, signOut } from 'auth-astro/client';

  export let signedIn = false;
  export let isAdmin = false;
  export let email = '';
  export let mobile = false;

  const ABOUT = [
    { href: '/guides', label: 'Docs' },
    { href: '/articles', label: 'Research' },
    { href: '/faq', label: 'FAQ' },
  ];

  $: profileLinks = [
    { href: '/usage', label: 'My Usage' },
    { href: '/api_key', label: 'API Key' },
    ...(isAdmin ? [{ href: '/users', label: 'Users' }] : []),
  ];

  let openMenu = null; // 'about' | 'profile' | null
  let root;

  const initial = () => (email || '?').charAt(0).toUpperCase();

  function toggle(menu) {
    openMenu = openMenu === menu ? null : menu;
  }

  function onDocumentClick(event) {
    if (openMenu && root && !root.contains(event.target)) openMenu = null;
  }

  function onKey(event) {
    if (event.key === 'Escape') openMenu = null;
  }

  async function doSignOut() {
    await signOut({ redirect: false });
    window.location.href = '/';
  }

  onMount(() => {
    document.addEventListener('click', onDocumentClick);
    document.addEventListener('keydown', onKey);
  });

  onDestroy(() => {
    if (typeof document === 'undefined') return;
    document.removeEventListener('click', onDocumentClick);
    document.removeEventListener('keydown', onKey);
  });

  const itemClass =
    'block px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800 transition-colors';
  const triggerClass =
    'flex items-center gap-1 text-sm font-medium text-slate-600 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 transition-colors';
</script>

{#if mobile}
  <!-- Mobile: flat labelled sections instead of dropdowns -->
  <div class="py-2">
    <p class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500 mb-1">
      About
    </p>
    {#each ABOUT as link}
      <a href={link.href} class="block text-sm font-medium text-slate-600 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 transition-colors py-1">
        {link.label}
      </a>
    {/each}
  </div>
  {#if signedIn}
    <div class="py-2">
      <p class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500 mb-1">
        {email || 'Account'}
      </p>
      {#each profileLinks as link}
        <a href={link.href} class="block text-sm font-medium text-slate-600 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 transition-colors py-1">
          {link.label}
        </a>
      {/each}
      <button
        type="button"
        class="block text-sm font-medium text-slate-600 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 transition-colors py-1"
        on:click={doSignOut}
      >
        Sign out
      </button>
    </div>
  {:else}
    <button
      type="button"
      class="block text-sm font-medium text-indigo-600 dark:text-indigo-400 py-2"
      on:click={() => signIn('auth0')}
    >
      Sign in
    </button>
  {/if}
{:else}
  <div class="flex items-center gap-6" bind:this={root}>
    <!-- About ▾ -->
    <div class="relative">
      <button
        type="button"
        class={triggerClass}
        aria-haspopup="true"
        aria-expanded={openMenu === 'about'}
        on:click|stopPropagation={() => toggle('about')}
      >
        About
        <svg class="w-3.5 h-3.5 transition-transform {openMenu === 'about' ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {#if openMenu === 'about'}
        <div class="absolute right-0 mt-2 w-44 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg py-1 z-50">
          {#each ABOUT as link}
            <a href={link.href} class={itemClass}>{link.label}</a>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Profile ◉ ▾ / Sign in -->
    {#if signedIn}
      <div class="relative">
        <button
          type="button"
          class="flex items-center gap-1"
          aria-haspopup="true"
          aria-expanded={openMenu === 'profile'}
          aria-label="Account menu"
          title={email}
          on:click|stopPropagation={() => toggle('profile')}
        >
          <span class="w-8 h-8 rounded-full bg-indigo-600 text-white text-sm font-semibold flex items-center justify-center select-none">
            {initial()}
          </span>
          <svg class="w-3.5 h-3.5 text-slate-500 transition-transform {openMenu === 'profile' ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        {#if openMenu === 'profile'}
          <div class="absolute right-0 mt-2 w-52 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg py-1 z-50">
            <p class="px-4 py-2 text-xs text-slate-400 dark:text-slate-500 truncate border-b border-slate-100 dark:border-slate-800">
              {email}
            </p>
            {#each profileLinks as link}
              <a href={link.href} class={itemClass}>{link.label}</a>
            {/each}
            <button type="button" class="w-full text-left {itemClass}" on:click={doSignOut}>
              Sign out
            </button>
          </div>
        {/if}
      </div>
    {:else}
      <button
        type="button"
        class="text-sm font-medium px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
        on:click={() => signIn('auth0')}
      >
        Sign in
      </button>
    {/if}
  </div>
{/if}
