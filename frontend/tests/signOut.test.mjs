import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { after, afterEach, before, test } from 'node:test';
import { Auth, customFetch } from '@auth/core';
import { encode } from '@auth/core/jwt';
import { parseString, splitCookiesString } from 'set-cookie-parser';
import { createServer } from 'vite';

// Node 18 needs the Web Crypto global used by Auth.js; Node 22 provides it.
globalThis.crypto ??= webcrypto;
const origin = 'https://serving.example.test';
const sessionCookie = '__Secure-authjs.session-token';
const authConfig = {
  secret: 'test-secret-for-local-auth-regressions',
  trustHost: true,
  basePath: '/api/auth',
  providers: [],
  logger: { error() {} },
};
const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
let server;
let signOut;

before(async () => {
  server = await createServer({
    configFile: false,
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['auth-astro'] },
    plugins: [{
      name: 'test-auth-config',
      resolveId(id) { if (id === 'auth:config') return '\0test-auth-config'; },
      load(id) { if (id === '\0test-auth-config') return 'export default {}'; },
    }],
  });
  ({ signOut } = await server.ssrLoadModule('/src/lib/signOut.ts'));
});
after(async () => { await server?.close(); });
afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
});

function browser() {
  const cookies = new Map();
  const navigations = [];
  const requests = [];
  globalThis.window = { location: { origin, assign: url => navigations.push(url) } };
  const request = async (path, options = {}) => {
    requests.push(path);
    const headers = new Headers(options.headers);
    headers.set('Cookie', [...cookies].map(([key, value]) => `${key}=${value}`).join('; '));
    const response = await Auth(new Request(`${origin}${path}`, { ...options, headers }), authConfig);
    for (const header of splitCookiesString(response.headers.get('Set-Cookie') || '')) {
      const cookie = parseString(header);
      if (cookie.maxAge === 0 || cookie.expires?.getTime() <= Date.now()) cookies.delete(cookie.name);
      else cookies.set(cookie.name, cookie.value);
    }
    return response;
  };
  globalThis.fetch = request;
  return { cookies, navigations, requests, request };
}

for (const endpoint of [
  'https://auth.example.test/application/o/serving/end-session/',
  'https://tenant.example.test/oidc/logout',
]) {
  for (const chunked of [false, true]) {
    test(`clears ${chunked ? 'chunked' : 'single'} session before one navigation to ${endpoint}`, async () => {
      const client = browser();
      const token = await encode({
        secret: authConfig.secret,
        salt: sessionCookie,
        token: { name: 'Test User', email: 'user@example.test', padding: chunked ? 'x'.repeat(6000) : '' },
      });
      if (chunked) {
        for (let i = 0; i * 3800 < token.length; i++) {
          client.cookies.set(`${sessionCookie}.${i}`, token.slice(i * 3800, (i + 1) * 3800));
        }
      } else client.cookies.set(sessionCookie, token);

      // Normal visits preserve a valid session until explicit sign-out.
      const session = await (await client.request('/api/auth/session')).json();
      assert.equal(session.user.email, 'user@example.test');
      assert.ok([...client.cookies.keys()].some(key => key.startsWith(sessionCookie)));
      client.requests.length = 0;
      const request = globalThis.fetch;
      globalThis.fetch = async (...args) => {
        assert.deepEqual(client.navigations, [], 'must not navigate during local sign-out');
        const response = await request(...args);
        assert.deepEqual(client.navigations, [], 'must await the local response');
        return response;
      };

      await signOut({ endpoint, clientId: 'serving', idToken: 'test-id-token' });

      assert.deepEqual(client.requests, ['/api/auth/csrf', '/api/auth/signout']);
      assert.ok(![...client.cookies.keys()].some(key => key.startsWith(sessionCookie)));
      assert.equal(await (await client.request('/api/auth/session')).json(), null);
      assert.equal(client.navigations.length, 1);
      const logout = new URL(client.navigations[0]);
      assert.equal(`${logout.origin}${logout.pathname}`, endpoint);
      assert.equal(logout.searchParams.get('client_id'), 'serving');
      assert.equal(logout.searchParams.get('id_token_hint'), 'test-id-token');
      assert.equal(logout.searchParams.get('post_logout_redirect_uri'), origin);
    });
  }
}

test('does not navigate when Auth.js rejects the CSRF token with an HTTP 200 error redirect', async () => {
  const client = browser();
  const request = globalThis.fetch;
  globalThis.fetch = (path, options) => path.endsWith('/csrf')
    ? Promise.resolve(Response.json({ csrfToken: 'invalid-csrf-token' }))
    : request(path, options);

  await assert.rejects(signOut({ endpoint: 'https://auth.example.test/logout', clientId: '', idToken: '' }), /Could not sign out/);
  assert.deepEqual(client.navigations, []);
});

for (const [label, responses] of [
  ['failed CSRF request', [() => new Response(null, { status: 500 })]],
  ['missing CSRF token', [() => Response.json({})]],
  ['failed sign-out request', [() => Response.json({ csrfToken: 'token' }), () => new Response(null, { status: 500 })]],
  ['network failure', [() => { throw new Error('Network failure'); }]],
]) {
  test(`does not navigate after ${label}`, async () => {
    const client = browser();
    let calls = 0;
    globalThis.fetch = async () => responses[calls++]();
    await assert.rejects(signOut({ endpoint: 'https://auth.example.test/logout', clientId: '', idToken: '' }));
    assert.deepEqual(client.navigations, []);
    assert.equal(calls, responses.length);
  });
}

test('falls back to home after local sign-out if discovery supplied no end-session endpoint', async () => {
  const client = browser();
  await signOut({ endpoint: '', clientId: '', idToken: '' });
  assert.deepEqual(client.navigations, [origin]);
});

test('omits the post-logout redirect if no ID token is available', async () => {
  const client = browser();
  await signOut({ endpoint: 'https://auth.example.test/logout?existing=1', clientId: 'serving', idToken: '' });
  const logout = new URL(client.navigations[0]);
  assert.equal(logout.searchParams.get('existing'), '1');
  assert.equal(logout.searchParams.get('client_id'), 'serving');
  assert.equal(logout.searchParams.has('post_logout_redirect_uri'), false);
});

test('a new sign-in requests explicit confirmation even if the IdP still has an SSO session', async () => {
  const { default: configured } = await server.ssrLoadModule('/auth.config.ts');
  const provider = configured.providers[0];
  const issuer = 'https://auth.example.test';
  const config = {
    ...authConfig,
    session: configured.session,
    providers: [{
      ...provider,
      options: {
        ...provider.options,
        issuer,
        clientId: 'serving',
        clientSecret: 'test-client-secret',
        [customFetch]: async () => Response.json({
          issuer,
          authorization_endpoint: `${issuer}/authorize`,
          token_endpoint: `${issuer}/token`,
          jwks_uri: `${issuer}/jwks`,
        }),
      },
    }],
  };
  const client = browser();
  const csrf = await (await client.request('/api/auth/csrf')).json();
  const headers = new Headers({
    'Content-Type': 'application/x-www-form-urlencoded',
    'X-Auth-Return-Redirect': '1',
    Cookie: [...client.cookies].map(([key, value]) => `${key}=${value}`).join('; '),
  });
  const response = await Auth(new Request(`${origin}/api/auth/signin/auth0`, {
    method: 'POST',
    headers,
    body: new URLSearchParams({ csrfToken: csrf.csrfToken, callbackUrl: origin }),
  }), config);
  const authorization = new URL((await response.json()).url);
  assert.equal(authorization.origin, issuer);
  assert.equal(authorization.searchParams.get('prompt'), 'consent');
  assert.equal(authorization.searchParams.get('response_type'), 'code');
  assert.ok(authorization.searchParams.get('scope').includes('offline_access'));
  assert.equal(config.session.maxAge, 30 * 24 * 60 * 60);
});
