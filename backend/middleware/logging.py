import logging
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

access_logger = logging.getLogger("access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        token = request.headers.get("authorization", "")
        if token.startswith("Bearer "):
            token = f"...{token[-8:]}"
        else:
            token = "-"
        access_logger.info(
            "%s %s %s %d %.0fms",
            token,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
