from __future__ import annotations

import asyncio
import ssl
from unittest.mock import AsyncMock

import httpx
import pytest

from deeptutor.services.codex_auth import network_probe
from deeptutor.services.codex_auth import service as service_module
from deeptutor.services.codex_auth.contracts import CodexAuthError
from deeptutor.services.codex_auth.diagnostics import transport_error
from deeptutor.services.codex_auth.network_probe import _probe, probe_codex_network
from deeptutor.services.codex_auth.oauth import CodexOAuthClient
from deeptutor.services.codex_auth.service import CodexOAuthService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "code"),
    [
        (
            403,
            {"error": {"code": "unsupported_country_region_territory", "message": "secret"}},
            "unsupported_region",
        ),
        (403, {"error": "private details"}, "authorization_forbidden"),
        (400, {"error": "invalid_grant"}, "authorization_code_rejected"),
        (401, {}, "authentication_required"),
        (429, {}, "authorization_rate_limited"),
        (503, {}, "authorization_service_unavailable"),
    ],
)
async def test_exchange_errors_are_actionable_without_exposing_upstream(status, payload, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json=payload),
        )
    ) as client:
        with pytest.raises(CodexAuthError) as error:
            await CodexOAuthClient(client).exchange_code(
                "secret", "http://localhost/callback", "verifier"
            )
    assert error.value.code == code
    assert "secret" not in str(error.value)
    assert "private details" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (httpx.ConnectTimeout, "network_timeout"),
        (httpx.ProxyError, "proxy_connection_failed"),
        (httpx.ConnectError, "network_connection_failed"),
    ],
)
async def test_exchange_transport_errors_do_not_echo_proxy_credentials(exception, code):
    def handler(request):
        raise exception("http://user:secret@proxy.test:1234", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CodexAuthError) as error:
            await CodexOAuthClient(client).exchange_code(
                "private-code", "http://localhost/callback", "v"
            )
    assert error.value.code == code
    assert "secret" not in str(error.value)
    assert "proxy.test" not in str(error.value)


def test_certificate_failure_keeps_verification_enabled():
    error = httpx.ConnectError("private-host")
    error.__cause__ = ssl.SSLCertVerificationError("certificate error")
    assert transport_error(error).code == "tls_verification_failed"


@pytest.mark.asyncio
async def test_network_probe_is_credential_free_and_recognizes_protected_endpoint(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "auth.openai.com":
            return httpx.Response(200, json={"issuer": "https://auth.openai.com"})
        return httpx.Response(401, json={"error": "unauthorized"})

    real_client = httpx.AsyncClient
    options = []

    def client_factory(**kwargs):
        options.append(dict(kwargs))
        kwargs.pop("proxy", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(network_probe.httpx, "AsyncClient", client_factory)
    result = await probe_codex_network("http://127.0.0.1:7890")
    assert result["ok"] is True
    assert options[0]["proxy"] == "http://127.0.0.1:7890"
    assert len(requests) == 2
    for request in requests:
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert not request.content


@pytest.mark.asyncio
async def test_probe_does_not_follow_redirects_or_treat_blocked_regions_as_reachable():
    def handler(request):
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "unsupported_country_region_territory",
                    "message": "private-message",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _probe(client, "authorization", "https://auth.openai.com/test")
    assert result == {
        "target": "authorization",
        "status": "blocked",
        "http_status": 403,
        "error_code": "unsupported_region",
    }
    assert "private-message" not in str(result)


@pytest.mark.asyncio
async def test_service_shutdown_cancels_login_and_closes_owned_http_only(monkeypatch):
    from types import SimpleNamespace

    client = AsyncMock()
    callback = SimpleNamespace(cancel=AsyncMock())
    task = asyncio.create_task(asyncio.sleep(3600))
    service = CodexOAuthService(
        object(), object(), object(), oauth_client=object(), owned_http=client
    )
    service._operation = SimpleNamespace(task=task, callback=callback)
    await service.aclose()
    assert task.cancelled()
    callback.cancel.assert_awaited_once()
    client.aclose.assert_awaited_once()
    await service.aclose()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_drains_all_owner_clients_even_if_one_close_fails(monkeypatch):
    from types import SimpleNamespace

    first = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("secret")))
    second = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(service_module, "_SERVICE_INSTANCES", {"first": first, "second": second})
    await service_module.close_codex_oauth_services()
    assert service_module._SERVICE_INSTANCES == {}
    first.aclose.assert_awaited_once()
    second.aclose.assert_awaited_once()
