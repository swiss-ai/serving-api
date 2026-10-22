import { endSessionUrl, type EndSessionConfig } from './endSessionUrl';

/** Clear the app session before navigating once to the IdP's logout endpoint. */
export async function signOut(config: EndSessionConfig): Promise<void> {
  const callbackUrl = window.location.origin;
  const target = endSessionUrl(config, callbackUrl) || callbackUrl;

  // auth-astro 4.2's signOut always navigates, even with redirect: false.
  // Use Auth.js's CSRF-protected API directly so it cannot race IdP logout.
  const csrfResponse = await fetch('/api/auth/csrf');
  if (!csrfResponse.ok) throw new Error('Could not start sign-out. Please try again.');
  const { csrfToken } = await csrfResponse.json();
  if (!csrfToken) throw new Error('Could not start sign-out. Please try again.');

  const response = await fetch('/api/auth/signout', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Auth-Return-Redirect': '1',
    },
    body: new URLSearchParams({ csrfToken, callbackUrl }),
  });
  if (!response.ok) throw new Error('Could not sign out. Please try again.');

  // Auth.js can report a CSRF/configuration error as HTTP 200 with an error
  // redirect. Only the requested callback confirms the local sign-out worked.
  const { url } = await response.json();
  if (!url || new URL(url, callbackUrl).href !== new URL(callbackUrl).href) {
    throw new Error('Could not sign out. Please try again.');
  }

  window.location.assign(target);
}
