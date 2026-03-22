"""Security middleware for the LEVIATHAN FastAPI server.

Provides:
- IPWhitelistMiddleware: blocks /api/v1/* requests from non-whitelisted IPs (403)
- RateLimitMiddleware: limits /api/v1/* to 100 req/min per IP (429)
"""
from __future__ import annotations

import ipaddress
import logging
import os
import time
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_API_PREFIX = "/api/v1/"

TRUSTED_PROXIES = set(
    os.environ.get("TRUSTED_PROXIES", "127.0.0.1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16").split(",")
)


def _is_trusted_proxy(ip: str) -> bool:
    """Check if IP is in trusted proxy list (supports CIDR notation)."""
    try:
        client = ipaddress.ip_address(ip)
        for proxy in TRUSTED_PROXIES:
            proxy = proxy.strip()
            try:
                if '/' in proxy:
                    if client in ipaddress.ip_network(proxy, strict=False):
                        return True
                else:
                    if client == ipaddress.ip_address(proxy):
                        return True
            except ValueError:
                continue
    except ValueError:
        return False
    return False


def _get_client_ip(request: Request) -> str:
    """Return the real client IP, honoring X-Forwarded-For only from trusted proxies.

    When client is None (embedded/test), X-Forwarded-For is used as fallback.
    When client is present, X-Forwarded-For is only trusted if that IP is a trusted proxy.
    """
    direct_ip = request.client.host if request.client else None
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for and (direct_ip is None or _is_trusted_proxy(direct_ip)):
        return forwarded_for.split(",")[0].strip()
    return direct_ip or "unknown"


def _parse_allowed_ips(raw: str) -> frozenset[str]:
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


# ---------------------------------------------------------------------------
# IP Whitelist Middleware
# ---------------------------------------------------------------------------

class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """Allow only whitelisted IPs to reach /api/v1/* routes.

    Env var ``ALLOWED_IPS`` (comma-separated).  Defaults to loopback only.
    Requests to other paths (health, metrics, auth, WS) are passed through.
    """

    def __init__(self, app: ASGIApp, allowed_ips: frozenset[str] | None = None) -> None:
        super().__init__(app)
        if allowed_ips is not None:
            self._allowed = allowed_ips
        else:
            raw = os.environ.get("ALLOWED_IPS", "127.0.0.1,::1,testclient")
            self._allowed = _parse_allowed_ips(raw)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith(_API_PREFIX):
            return await call_next(request)

        client_ip = _get_client_ip(request)
        if client_ip not in self._allowed:
            logger.warning(
                "IP whitelist blocked %s %s from %s",
                request.method,
                request.url.path,
                client_ip,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": f"Forbidden: IP {client_ip!r} not whitelisted"},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Rate Limit Middleware
# ---------------------------------------------------------------------------

_RATE_LIMIT_REQUESTS = 100
_RATE_LIMIT_WINDOW = 60  # seconds


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding-window rate limiter for /api/v1/* routes.

    Allows up to 100 requests per 60-second window per IP.
    Returns 429 when exceeded.
    """

    def __init__(self, app: ASGIApp, max_requests: int = _RATE_LIMIT_REQUESTS,
                 window_seconds: int = _RATE_LIMIT_WINDOW) -> None:
        super().__init__(app)
        self._max = max_requests
        self._window = window_seconds
        # {ip: [timestamp, ...]}
        self._counts: dict[str, list[float]] = defaultdict(list)

    def _is_allowed(self, ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        timestamps = self._counts[ip]
        # Evict expired entries
        self._counts[ip] = [t for t in timestamps if t > cutoff]
        if len(self._counts[ip]) >= self._max:
            return False
        self._counts[ip].append(now)
        return True

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith(_API_PREFIX):
            return await call_next(request)

        client_ip = _get_client_ip(request)
        if not self._is_allowed(client_ip):
            logger.warning(
                "Rate limit exceeded for %s on %s %s",
                client_ip,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests — rate limit exceeded"},
                headers={"Retry-After": str(self._window)},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Login Rate Limit Middleware (US-319)
# ---------------------------------------------------------------------------

_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW = 60  # seconds
_LOGIN_PREFIX = "/api/auth/"


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    """Dedicated rate limiter for /api/auth/* endpoints (brute-force protection).

    Allows up to 5 requests per 60-second window per IP.
    Stricter than the general API rate limiter to guard credential endpoints.
    """

    def __init__(self, app: ASGIApp, max_requests: int = _LOGIN_RATE_LIMIT,
                 window_seconds: int = _LOGIN_RATE_WINDOW) -> None:
        super().__init__(app)
        self._max = max_requests
        self._window = window_seconds
        self._counts: dict[str, list[float]] = defaultdict(list)

    def _is_allowed(self, ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        timestamps = self._counts[ip]
        self._counts[ip] = [t for t in timestamps if t > cutoff]
        if len(self._counts[ip]) >= self._max:
            return False
        self._counts[ip].append(now)
        return True

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith(_LOGIN_PREFIX):
            return await call_next(request)

        client_ip = _get_client_ip(request)
        if not self._is_allowed(client_ip):
            logger.warning(
                "Login rate limit exceeded for %s on %s %s",
                client_ip,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests — login rate limit exceeded"},
                headers={"Retry-After": str(self._window)},
            )
        return await call_next(request)
