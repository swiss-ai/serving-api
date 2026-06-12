from fastapi import HTTPException, Request


async def json_body(request: Request) -> dict:
    """Parse the request body as a JSON object, returning a clean 400 on
    bad input.

    The proxy routers read the body manually via ``await request.json()``.
    Done bare, a missing/empty/malformed body raises ``json.JSONDecodeError``
    deep in Starlette and — because the body isn't bound to a Pydantic
    param — there is no validation layer to turn it into a 422, so it
    surfaces as an opaque 500. A body that *is* valid JSON but isn't an
    object (e.g. ``[]`` or ``5``) is just as bad: the very next
    ``data.get("model")`` blows up with the same 500.

    Depending on this collapses both cases into a 400 so a caller that
    sends the wrong shape gets a 4xx, not an Internal Server Error.
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Request body must be valid JSON.",
        )
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object.",
        )
    return data
