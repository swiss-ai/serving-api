import Auth0 from '@auth/core/providers/auth0';
import { defineConfig } from 'auth-astro';
import type { JWT } from '@auth/core/jwt';
import type { Session } from '@auth/core/types';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadEnv } from 'vite';

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const env = loadEnv(
  process.env.MODE ?? process.env.NODE_ENV ?? 'development',
  rootDir,
  ['AUTH_', 'AUTH0_', 'VITE_'],
);

function authEnv(key: string): string | undefined {
  return env[key] || process.env[key];
}

// Define custom types for our extended token and session
interface ExtendedJWT extends JWT {
  accessToken?: string;
  refreshToken?: string;
  idToken?: string;
  accessTokenExpires?: number;
  user?: any;
  error?: string;
}

interface ExtendedSession extends Session {
  accessToken?: string;
  idToken?: string;
  error?: string;
}

export default defineConfig({
  secret: authEnv('AUTH_SECRET'),
  trustHost: authEnv('AUTH_TRUST_HOST') === 'true',

  // Session configuration
  session: {
    strategy: 'jwt',
    maxAge: 30 * 24 * 60 * 60, // 30 days
    updateAge: 24 * 60 * 60, // Update session every 24 hours to refresh the cookie
  },

  providers: [
    Auth0({
      clientId: authEnv('AUTH0_CLIENT_ID'),
      clientSecret: authEnv('AUTH0_CLIENT_SECRET'),
      issuer: authEnv('AUTH0_ISSUER'),
      client: {
        token_endpoint_auth_method: 'client_secret_post',
      },
      authorization: {
        params: {
          scope: 'openid profile email offline_access',
          // Force specific connection (e.g., Google Workspace)
          // connection: 'google-oauth2',

          // Or use organization for ETH/EPFL
          // organization: 'org_eth',

          // Prompt for login_hint to collect email first
          // prompt: 'login',
        },
      },
    }),
  ],

  callbacks: {
    async jwt({ token, user, account }) {
      const extToken = token as ExtendedJWT;

      // Initial sign in - store tokens and user info
      if (account && user) {
        console.log('Initial sign in - storing tokens');
        return {
          ...extToken,
          accessToken: account.access_token,
          refreshToken: account.refresh_token,
          idToken: account.id_token,
          accessTokenExpires: account.expires_at ? account.expires_at * 1000 : Date.now() + 24 * 60 * 60 * 1000,
          user,
        };
      }

      // Return previous token if the access token has not expired yet
      if (extToken.accessTokenExpires && Date.now() < extToken.accessTokenExpires) {
        console.log('Access token still valid');
        return extToken;
      }

      // Access token has expired, try to refresh it
      console.log('Access token expired, attempting refresh');

      if (!extToken.refreshToken) {
        console.error('No refresh token available');
        return {
          ...extToken,
          error: 'NoRefreshToken',
        };
      }

      try {
        // AUTH0_ISSUER is not VITE_-prefixed, so it is NOT baked into
        // import.meta.env in the built server bundle — at runtime it only
        // exists on process.env (from the deployment configmap). Without this
        // fallback the refresh URL becomes "undefined/..." and every token
        // refresh throws once the access token expires.
        const issuer = authEnv('AUTH0_ISSUER');
        const discoveryResponse = await fetch(
          `${issuer?.replace(/\/$/, '')}/.well-known/openid-configuration`,
        );
        if (!discoveryResponse.ok) {
          throw new Error(`Failed to fetch OIDC discovery: ${discoveryResponse.status}`);
        }
        const tokenEndpoint = (await discoveryResponse.json()).token_endpoint;
        const response = await fetch(tokenEndpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: new URLSearchParams({
            grant_type: 'refresh_token',
            client_id: authEnv('AUTH0_CLIENT_ID') || '',
            client_secret: authEnv('AUTH0_CLIENT_SECRET') || '',
            refresh_token: extToken.refreshToken,
          }),
        });

        const tokens = await response.json();

        if (!response.ok) {
          console.error('Failed to refresh token:', tokens);
          throw tokens;
        }

        console.log('Successfully refreshed access token');

        return {
          ...extToken,
          accessToken: tokens.access_token,
          accessTokenExpires: Date.now() + (tokens.expires_in || 86400) * 1000,
          refreshToken: tokens.refresh_token ?? extToken.refreshToken, // Use new refresh token if provided, otherwise keep old one
        };
      } catch (error) {
        console.error('Error refreshing access token:', error);
        return {
          ...extToken,
          error: 'RefreshAccessTokenError',
        };
      }
    },

    async session({ session, token }) {
      const extToken = token as ExtendedJWT;
      const extSession = session as ExtendedSession;

      // Add the access token and user to the session
      if (extToken) {
        extSession.accessToken = extToken.accessToken;
        extSession.idToken = extToken.idToken;
        extSession.error = extToken.error;

        if (extToken.user) {
          extSession.user = {
            ...extSession.user,
            ...extToken.user
          };
        }
      }

      return extSession;
    }
  }
});