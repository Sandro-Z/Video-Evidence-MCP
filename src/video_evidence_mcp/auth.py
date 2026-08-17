from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, cast

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from video_evidence_mcp.config import Settings


class PublicSecurityMiddleware:
    """OIDC bearer validation, bounded concurrency, and per-client rate limiting."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.semaphore = asyncio.Semaphore(settings.max_http_concurrency)
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self._jwks: dict[str, Any] | None = None
        self._jwks_at = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if path in {"/healthz", "/readyz"} or path.startswith("/.well-known/"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_key = str(client[0]) if client else "unknown"
        if not self._within_rate(client_key):
            await JSONResponse(
                {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
            )(scope, receive, send)
            return
        if self.settings.auth_mode == "oidc":
            headers = Headers(scope=scope)
            authorization = headers.get("authorization", "")
            if not authorization.startswith("Bearer ") or not await self._valid_token(
                authorization.removeprefix("Bearer ").strip()
            ):
                resource = (
                    f"{self.settings.public_base_url}/.well-known/oauth-protected-resource/mcp"
                )
                await JSONResponse(
                    {"error": "invalid_token"},
                    status_code=401,
                    headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource}"'},
                )(scope, receive, send)
                return
        async with self.semaphore:
            await self.app(scope, receive, send)

    def _within_rate(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self.hits[key]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= self.settings.rate_limit_per_minute:
            return False
        bucket.append(now)
        return True

    async def _load_jwks(self) -> dict[str, Any]:
        if self._jwks is not None and time.monotonic() - self._jwks_at < 300:
            return self._jwks
        url = self.settings.oidc_jwks_url
        if not url:
            assert self.settings.oidc_issuer is not None
            async with httpx.AsyncClient(timeout=10) as client:
                discovery = await client.get(
                    f"{self.settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
                )
                discovery.raise_for_status()
                url = str(discovery.json()["jwks_uri"])
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            self._jwks = response.json()
        self._jwks_at = time.monotonic()
        return self._jwks

    async def _valid_token(self, token: str) -> bool:
        try:
            issuer = self.settings.oidc_issuer
            audience = self.settings.oidc_audience
            if issuer is None or audience is None:
                return False
            decoded = jwt.decode(
                token,
                KeySet.import_key_set(cast(Any, await self._load_jwks())),
                algorithms=["RS256", "ES256", "PS256"],
            )
            claims = decoded.claims
            JWTClaimsRegistry(
                iss={"essential": True, "value": issuer},
                aud={"essential": True, "value": audience},
                exp={"essential": True},
            ).validate(claims)
            token_scopes = set(str(claims.get("scope", "")).split())
            required = set(self.settings.oidc_required_scopes.split())
            return required <= token_scopes
        except Exception:
            return False
