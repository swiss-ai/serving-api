from cachetools import cached, TTLCache
from auth0.authentication import GetToken


@cached(cache=TTLCache(maxsize=1024, ttl=24 * 60 * 60))
def get_auth0_token(domain, client_id, client_secret, audience):
    get_token = GetToken(domain, client_id, client_secret)
    management_token = get_token.client_credentials(
        audience=audience,
    )
    return management_token
