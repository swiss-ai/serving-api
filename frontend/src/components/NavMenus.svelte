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
  import { endSessionUrl } from '@lib/endSessionUrl';

  export let signedIn = false;
  export let isAdmin = false;
  export let email = '';
  export let mobile = false;
  // Resolved in Header.astro; the ID token and issuer are server-side only.
  export let endSession = { endpoint: '', clientId: '', idToken: '' };
  // Which part to render: 'about', 'profile', or 'both' (mobile). Lets the
  // header put Get Help between the two on desktop while the profile menu
  // keeps the rightmost slot.
  export let section = 'both';
  // Current URL path, so the About pill lights up on Docs / Research / FAQ.
  export let currentPath = '';

  const ABOUT = [
    { href: '/guides', label: 'Docs' },
    { href: '/articles', label: 'Research' },
    { href: '/faq', label: 'FAQ' },
  ];

  $: profileLinks = [
    { href: '/usage', label: 'My Usage' },
    { href: '/api_key', label: 'API Key' },
    ...(isAdmin
      ? [
          { href: '/users', label: 'Users' },
          { href: '/all_models', label: 'All Models' },
        ]
      : []),
  ];

  let openMenu = null; // 'about' | 'profile' | null
  let root;

  const initial = () => (email || '?').charAt(0).toUpperCase();
  const isCurrent = (href) => currentPath === href || currentPath.startsWith(`${href}/`);
  $: aboutActive = ABOUT.some((link) => isCurrent(link.href));

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
    const target = endSessionUrl(endSession, window.location.origin);
    await signOut({ redirect: false });
    // Clearing our own cookie leaves the IdP session alive, so the next sign in
    // completes with no prompt and "signed out" is not signed out.
    window.location.href = target || '/';
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

  const mobileLinkClass =
    'block py-1.5 text-sm font-medium text-ink hover:text-primary-ink aria-[current=page]:text-primary-ink';
</script>

{#if mobile}
  <!-- Mobile: flat labelled sections instead of dropdowns -->
  <div class="py-2">
    <p class="eyebrow mb-1">About</p>
    {#each ABOUT as link}
      <a href={link.href} class={mobileLinkClass} aria-current={isCurrent(link.href) ? 'page' : undefined}>
        {link.label}
      </a>
    {/each}
  </div>
  {#if signedIn}
    <div class="py-2">
      <p class="eyebrow mb-1 truncate normal-case tracking-normal">{email || 'Account'}</p>
      {#each profileLinks as link}
        <a href={link.href} class={mobileLinkClass} aria-current={isCurrent(link.href) ? 'page' : undefined}>
          {link.label}
        </a>
      {/each}
      <button type="button" class={mobileLinkClass} on:click={doSignOut}>Sign out</button>
    </div>
  {:else}
    <button type="button" class="btn btn-primary mt-2 w-fit" on:click={() => signIn('auth0')}>
      Sign in
    </button>
  {/if}
{:else}
  <div class="contents" bind:this={root}>
    <!-- About ▾ -->
    {#if section !== 'profile'}
    <div class="relative">
      <button
        type="button"
        class="pill"
        class:active={aboutActive}
        aria-haspopup="true"
        aria-expanded={openMenu === 'about'}
        on:click|stopPropagation={() => toggle('about')}
      >
        About
        <svg class="size-3 transition-transform {openMenu === 'about' ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {#if openMenu === 'about'}
        <div class="menu absolute right-0 z-50 mt-2 w-44">
          {#each ABOUT as link}
            <a href={link.href} class="menu-item" aria-current={isCurrent(link.href) ? 'page' : undefined}>{link.label}</a>
          {/each}
        </div>
      {/if}
    </div>
    {/if}

    <!-- Profile ◉ ▾ / Sign in -->
    {#if section !== 'about'}
    {#if signedIn}
      <div class="relative">
        <button
          type="button"
          class="flex items-center gap-1 text-muted hover:text-ink"
          aria-haspopup="true"
          aria-expanded={openMenu === 'profile'}
          aria-label="Account menu"
          title={email}
          on:click|stopPropagation={() => toggle('profile')}
        >
          <span class="avatar">{initial()}</span>
          <svg class="size-3 transition-transform {openMenu === 'profile' ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        {#if openMenu === 'profile'}
          <div class="menu absolute right-0 z-50 mt-2 w-60">
            <p class="truncate px-4 py-2 text-xs text-muted">{email}</p>
            <div class="menu-sep"></div>
            {#each profileLinks as link}
              <a href={link.href} class="menu-item" aria-current={isCurrent(link.href) ? 'page' : undefined}>{link.label}</a>
            {/each}
            <div class="menu-sep"></div>
            <button type="button" class="menu-item" on:click={doSignOut}>Sign out</button>
          </div>
        {/if}
      </div>
    {:else}
      <button type="button" class="btn btn-primary" on:click={() => signIn('auth0')}>
        Sign in
      </button>
    {/if}
    {/if}
  </div>
{/if}
