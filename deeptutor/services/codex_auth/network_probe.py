"""Credential-free probes of fixed Codex endpoints using a draft proxy."""

from __future__ import annotations

import asyncio
import json

import httpx

from deeptutor.services.config.network_proxy import normalize_codex_proxy

from .constants import CODEX_MODELS_URL, CODEX_OAUTH_ISSUER
from .diagnostics import response_error, transport_error

_ENDPOINTS = (
    ("authorization", f"{CODEX_OAUTH_ISSUER}/.well-known/openid-configuration"),
    ("models", CODEX_MODELS_URL),
)


async def _probe(client: httpx.AsyncClient, target: str, url: str) -> dict[str, object]:
    try:
        async with asyncio.timeout(12):
            async with client.stream("GET", url) as response:
                code = response.status_code
                # A model endpoint may require authentication. This probe
                # intentionally sends no credentials and only tests reachability.
                if 200 <= code < 300 or (target == "models" and code == 401):
                    return {"target": target, "status": "reachable", "http_status": code}
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk[: max(0, 4096 - len(body))])
                    if len(body) >= 4096:
                        break
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    payload = {}
                diagnostic = response_error(httpx.Response(code, json=payload))
                return {
                    "target": target,
                    "status": "blocked" if code in (401, 403, 429) else "error",
                    "http_status": code,
                    "error_code": diagnostic.code if diagnostic else "network_probe_failed",
                }
    except httpx.HTTPError as exc:
        return {"target": target, "status": "error", "error_code": transport_error(exc).code}
    except TimeoutError:
        return {"target": target, "status": "error", "error_code": "network_timeout"}


async def probe_codex_network(proxy_url: str) -> dict[str, object]:
    proxy_url = normalize_codex_proxy(proxy_url)
    options = {"proxy": proxy_url} if proxy_url else {}
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, **options) as client:
        checks = await asyncio.gather(*(_probe(client, target, url) for target, url in _ENDPOINTS))
    return {"ok": all(check["status"] == "reachable" for check in checks), "checks": checks}
