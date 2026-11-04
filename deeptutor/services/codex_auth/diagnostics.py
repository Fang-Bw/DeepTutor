"""Public OAuth diagnostics built from a small allow-list, never upstream text."""

from __future__ import annotations

import ssl

import httpx

from .contracts import CodexAuthError


def transport_error(error: httpx.HTTPError) -> CodexAuthError:
    if isinstance(error, httpx.TimeoutException):
        return CodexAuthError(
            "network_timeout",
            "The Codex connection timed out. Check the server network and proxy.",
            504,
        )
    if isinstance(error, httpx.ProxyError):
        return CodexAuthError(
            "proxy_connection_failed",
            "The proxy connection failed. Check that the proxy is running and its address is correct.",
            502,
        )
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(cause):
            return CodexAuthError(
                "tls_verification_failed",
                "The server could not verify the Codex connection certificate. Check the system clock and certificate configuration.",
                502,
            )
        cause = cause.__cause__ or cause.__context__
    return CodexAuthError(
        "network_connection_failed",
        "The server could not connect to Codex. Check the server network and proxy.",
        502,
    )


def response_error(response: httpx.Response, *, refreshing: bool = False) -> CodexAuthError | None:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    value = payload.get("error") if isinstance(payload, dict) else None
    code = value.get("code") if isinstance(value, dict) else value
    if code == "unsupported_country_region_territory":
        return CodexAuthError(
            "unsupported_region",
            "OpenAI does not support the server's network region. Use a supported deployment region or another provider.",
            403,
        )
    if code in ("invalid_grant", "invalid_token") and response.status_code in (400, 401):
        return CodexAuthError(
            "token_refresh_rejected" if refreshing else "authorization_code_rejected",
            "Codex sign-in expired or was rejected. Start a new sign-in attempt.",
            401,
        )
    if response.status_code == 401:
        return CodexAuthError(
            "authentication_required", "Codex authentication was rejected. Sign in again.", 401
        )
    if response.status_code == 403:
        return CodexAuthError(
            "authorization_forbidden",
            "Codex access was denied. Check account access and the server network.",
            403,
        )
    if response.status_code == 429:
        return CodexAuthError(
            "authorization_rate_limited",
            "Codex authentication is rate limited. Wait before trying again.",
            429,
        )
    if response.status_code >= 500:
        return CodexAuthError(
            "authorization_service_unavailable",
            "The Codex authentication service is temporarily unavailable. Try again later.",
            502,
        )
    return None
