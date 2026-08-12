export interface EndSessionConfig {
  endpoint: string;
  clientId: string;
  idToken: string;
}

/**
 * The IdP's RP-initiated logout URL to send the browser to after signing out
 * locally, or '' when the IdP cannot be reached (caller falls back to '/').
 *
 * `origin` is passed in rather than derived here because the caller is the
 * browser: it has to be the public origin the IdP has registered as a logout
 * redirect URI, which a server-side request URL behind the ingress is not.
 *
 * post_logout_redirect_uri only goes along with an id_token_hint. Authentik
 * rejects the request outright if it arrives without one, so a session with no
 * ID token gets the logout without the redirect back.
 */
export function endSessionUrl(config: EndSessionConfig, origin: string): string {
  if (!config.endpoint) return '';

  const params = new URLSearchParams();
  if (config.clientId) params.set('client_id', config.clientId);
  if (config.idToken) {
    params.set('id_token_hint', config.idToken);
    params.set('post_logout_redirect_uri', origin);
  }

  const separator = config.endpoint.includes('?') ? '&' : '?';
  return `${config.endpoint}${separator}${params.toString()}`;
}
