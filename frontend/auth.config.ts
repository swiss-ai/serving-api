import Auth0 from '@auth/core/providers/auth0';
import { defineConfig } from 'auth-astro';
import type { JWT } from '@auth/core/jwt';
import type { Session } from '@auth/core/types';

// Define custom types for our extended token and session
interface ExtendedJWT extends JWT {
  accessToken?: string;
  refreshToken?: string;
  accessTokenExpires?: number;
  user?: any;
  error?: string;
}

interface ExtendedSession extends Session {
  accessToken?: string;
  error?: string;
}

export default defineConfig({
  secret: import.meta.env.AUTH_SECRET || (typeof process !== 'undefined' ? process.env.AUTH_SECRET : undefined),

  // Session configuration
  session: {
    strategy: 'jwt',
    maxAge: 30 * 24 * 60 * 60, // 30 days
    updateAge: 24 * 60 * 60, // Update session every 24 hours to refresh the cookie
  },

  providers: [
    Auth0({
      clientId: import.meta.env.AUTH0_CLIENT_ID || (typeof process !== 'undefined' ? process.env.AUTH0_CLIENT_ID : undefined),
      clientSecret: import.meta.env.AUTH0_CLIENT_SECRET || (typeof process !== 'undefined' ? process.env.AUTH0_CLIENT_SECRET : undefined),
      issuer: import.meta.env.AUTH0_ISSUER || (typeof process !== 'undefined' ? process.env.AUTH0_ISSUER : undefined),
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
        const response = await fetch(`${import.meta.env.AUTH0_ISSUER}/oauth/token`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: new URLSearchParams({
            grant_type: 'refresh_token',
            client_id: import.meta.env.AUTH0_CLIENT_ID || process.env.AUTH0_CLIENT_ID || '',
            client_secret: import.meta.env.AUTH0_CLIENT_SECRET || process.env.AUTH0_CLIENT_SECRET || '',
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