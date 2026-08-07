import { getSession } from 'auth-astro/server';

/**
 * getSession, treating a wedged session as signed out.
 *
 * auth.config.ts stores `error` in the JWT when a token refresh fails
 * (e.g. two concurrent SSR requests raced to redeem the same rotated
 * refresh token). Such a session is a corpse: the cookie is alive but the
 * access token is dead and the refresh token consumed, so every API call
 * 401s while the UI still renders as signed in. Returning null here makes
 * pages degrade to the Sign in button; signing in replaces the cookie.
 */
export async function getActiveSession(request: Request): Promise<any> {
  const session: any = await getSession(request);
  if (!session || session.error) return null;
  return session;
}
