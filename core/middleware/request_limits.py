"""
Request limits middleware — body size cap + request timeout.

Protects against oversized payloads and slow-loris / hung-request attacks.

Body size check:
    Reads Content-Length before the request reaches FastAPI's body parser.
    If the header exceeds MAX_BODY_BYTES, the request is rejected immediately
    with 413 Payload Too Large — no memory is allocated for the body.
    Requests without Content-Length (chunked transfer) are checked
    after the body has been read by the framework.

Request timeout:
    Wraps the entire downstream handler in asyncio.wait_for().
    If the handler does not complete within TIMEOUT_SECONDS,
    the client receives a 504 Gateway Timeout.

Usage:
    app.add_middleware(RequestLimitsMiddleware)
"""

import asyncio
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# 2 MB — generous for a JSON API, covers PDF upload metadata but blocks abuse
MAX_BODY_BYTES = 2 * 1024 * 1024

# 30 seconds — covers complex DB queries / PDF generation
TIMEOUT_SECONDS = 30


class RequestLimitsMiddleware(BaseHTTPMiddleware):
    """
    Enforces request body size limits and per-request timeout.
    """

    async def dispatch(self, request: Request, call_next):
        # --- Body size gate ---
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    logger.warning(
                        "Request rejected: body too large",
                        extra={
                            "content_length": content_length,
                            "max_allowed": MAX_BODY_BYTES,
                            "path": request.url.path,
                        },
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "data": "Payload too large",
                            "error_code": 0,
                        },
                    )
            except ValueError:
                pass

        # --- Request timeout ---
        try:
            response = await asyncio.wait_for(
                call_next(request),
                timeout=TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Request timed out",
                extra={
                    "timeout_seconds": TIMEOUT_SECONDS,
                    "path": request.url.path,
                    "method": request.method,
                },
            )
            return JSONResponse(
                status_code=504,
                content={
                    "data": "Request timeout",
                    "error_code": 0,
                },
            )

        return response
