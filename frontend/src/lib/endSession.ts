import type { EndSessionConfig } from '@lib/endSessionUrl';

const runtimeEnv = (key: string): string =>
  process.env[key] || (import.meta.env as Record<string, string>)[key] || '';

// Mirrors the resolution in auth.config.ts: AUTH_PROVIDER picks the active IdP
// and the AUTHENTIK_* values fall back to the legacy AUTH0_* names (issue #58).
function oidcIssuer(): string {
  const provider = (runtimeEnv('AUTH_PROVIDER') || 'auth0').toLowerCase();
  return provider === 'authentik'
    ? runtimeEnv('AUTHENTIK_ISSUER') || runtimeEnv('AUTH0_ISSUER')
    : runtimeEnv('AUTH0_ISSUER');
}

function oidcClientId(): string {
  const provider = (runtimeEnv('AUTH_PROVIDER') || 'auth0').toLowerCase();
  return (
    (provider === 'authentik'
      ? runtimeEnv('AUTHENTIK_CLIENT_ID') || runtimeEnv('AUTH0_CLIENT_ID')
      : runtimeEnv('AUTH0_CLIENT_ID')) ||
    (import.meta.env as Record<string, string>).VITE_AUTH0_CLIENT_ID ||
    ''
  );
}

// Read from discovery rather than built by hand, so this works for whichever
// IdP is live: Authentik's /end-session/, Auth0's /oidc/logout. Memoised per
// issuer across SSR requests — the header resolves this on every render.
async function endSessionEndpoint(issuer: string): Promise<string> {
  if (!issuer) return '';
  const cache = globalThis as unknown as { __endSessionCache?: Record<string, string> };
  cache.__endSessionCache ??= {};
  if (cache.__endSessionCache[issuer] !== undefined) return cache.__endSessionCache[issuer];
  try {
    const res = await fetch(`${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`);
    if (res.ok) {
      const endpoint = (await res.json()).end_session_endpoint || '';
      cache.__endSessionCache[issuer] = endpoint;
      return endpoint;
    }
  } catch (e) {
    console.error('Failed to resolve end_session_endpoint:', e);
  }
  return '';
}

/**
 * What the browser needs to end the IdP session, resolved during SSR.
 *
 * The ID token is only readable server-side from the auth-astro cookie, and the
 * endpoint needs the issuer, which is not exposed to the client bundle.
 */
export async function endSessionConfig(session: any): Promise<EndSessionConfig> {
  return {
    endpoint: await endSessionEndpoint(oidcIssuer()),
    clientId: oidcClientId(),
    idToken: session?.idToken ?? '',
  };
}
