from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from deeptutor.services.codex_auth import http_pool
from deeptutor.services.codex_auth.http_pool import (
    CodexHTTPPool,
    codex_http_client,
    use_codex_http_pool,
)
from deeptutor.services.codex_auth.service import CodexOAuthService


@pytest.fixture
def clients(monkeypatch):
    created = []
    requests = []
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"set-cookie": "session=test-cookie; Path=/; Secure"},
        )

    def factory(**kwargs):
        options = dict(kwargs)
        kwargs.pop("proxy", None)
        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        created.append((client, options))
        return client

    monkeypatch.setattr(http_pool.httpx, "AsyncClient", factory)
    return SimpleNamespace(created=created, requests=requests)


@pytest.mark.asyncio
async def test_reuses_connections_until_owner_shutdown(clients):
    pool = CodexHTTPPool(options=lambda: {"proxy": "http://127.0.0.1:7890"})
    await pool.post("https://auth.openai.com/oauth/token", data={"code": "first"})
    await pool.get("https://chatgpt.com/backend-api/codex/models")
    assert len(clients.created) == 1
    client, config = clients.created[0]
    assert not client.is_closed
    assert config["proxy"] == "http://127.0.0.1:7890"
    assert config["verify"] is True
    await pool.aclose()
    assert client.is_closed
    await pool.aclose()


@pytest.mark.asyncio
async def test_proxy_rotation_waits_for_existing_stream_and_applies_to_next_request(clients):
    options = {"proxy": "http://127.0.0.1:7890"}
    pool = CodexHTTPPool(options=lambda: dict(options))
    async with pool.lease(timeout=60) as previous:
        options["proxy"] = "socks5://127.0.0.1:1080"
        async with pool.lease(timeout=60) as current:
            assert current is not previous
            assert not previous.is_closed
        assert not previous.is_closed
    assert previous.is_closed
    async with pool.lease(timeout=60) as reused:
        assert reused is current
    options.clear()
    async with pool.lease(timeout=60) as inherited:
        assert inherited is not current
        assert "proxy" not in clients.created[-1][1]
    assert current.is_closed
    await pool.aclose()


@pytest.mark.asyncio
async def test_owner_isolation_and_request_local_authentication(clients):
    alice = CodexHTTPPool(options=dict)
    bob = CodexHTTPPool(options=dict)
    await alice.get("https://chatgpt.com/test", headers={"Authorization": "Bearer alice"})
    await bob.get("https://chatgpt.com/test", headers={"Authorization": "Bearer bob"})
    await alice.get("https://chatgpt.com/test")
    assert len(clients.created) == 2
    assert [r.headers.get("authorization") for r in clients.requests] == [
        "Bearer alice",
        "Bearer bob",
        None,
    ]
    assert all("authorization" not in client.headers for client, _ in clients.created)
    assert all("cookie" not in request.headers for request in clients.requests)
    assert all(not list(client.cookies.jar) for client, _ in clients.created)
    await asyncio.gather(alice.aclose(), bob.aclose())


@pytest.mark.asyncio
async def test_inference_tls_option_does_not_weaken_oauth(clients):
    pool = CodexHTTPPool(options=dict)
    async with pool.lease(timeout=60, verify=False) as inference:
        await pool.post("https://auth.openai.com/oauth/token", data={})
        async with pool.lease(timeout=60, verify=True) as verified:
            assert verified is not inference
    assert [config["verify"] for _, config in clients.created] == [False, True, True]
    assert [config["timeout"] for _, config in clients.created] == [60, 30, 60]
    await pool.aclose()


@pytest.mark.asyncio
async def test_shutdown_defers_closing_leased_streams(clients):
    pool = CodexHTTPPool(options=dict)
    async with pool.lease() as client:
        await pool.aclose()
        assert not client.is_closed
        with pytest.raises(RuntimeError, match="closed"):
            async with pool.lease():
                pytest.fail("Closed pool must reject new requests")
    assert client.is_closed


@pytest.mark.asyncio
async def test_service_guard_scopes_pool_without_leaking_across_tasks(clients):
    first = CodexHTTPPool(options=dict)
    second = CodexHTTPPool(options=dict)
    service = CodexOAuthService(
        object(), object(), object(), oauth_client=object(), http_pool=first
    )
    async with service.inference_guard():
        async with codex_http_client(timeout=60, verify=True) as a:
            with use_codex_http_pool(second):
                async with codex_http_client(timeout=60, verify=True) as b:
                    assert b is not a
            async with codex_http_client(timeout=60, verify=True) as restored:
                assert restored is a
    assert http_pool._CURRENT_POOL.get() is None
    assert service._active_inferences == 0
    await service.aclose()
    await second.aclose()
    assert all(client.is_closed for client, _ in clients.created)


@pytest.mark.asyncio
async def test_rotation_closes_idle_client_even_if_new_client_creation_fails(clients, monkeypatch):
    options = {}
    pool = CodexHTTPPool(options=lambda: dict(options))
    async with pool.lease() as previous:
        pass

    def fail(**_kwargs):
        raise ValueError("client creation failed")

    options["proxy"] = "http://127.0.0.1:7890"
    monkeypatch.setattr(http_pool.httpx, "AsyncClient", fail)
    with pytest.raises(ValueError, match="client creation failed"):
        async with pool.lease():
            pytest.fail("Cannot lease a client that failed to initialize")
    await pool.aclose()
    assert previous.is_closed


@pytest.mark.asyncio
async def test_cancelling_rotation_keeps_cleanup_alive(clients, monkeypatch):
    options = {}
    pool = CodexHTTPPool(options=lambda: dict(options))
    async with pool.lease() as previous:
        pass
    async with pool.lease(timeout=60) as previous_inference:
        pass
    entered = asyncio.Event()
    release = asyncio.Event()
    original_close = previous.aclose

    async def slow_close():
        entered.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(previous, "aclose", slow_close)
    options["proxy"] = "http://127.0.0.1:7890"

    async def rotate():
        async with pool.lease():
            pass

    task = asyncio.create_task(rotate())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await pool.aclose()
    assert previous_inference.is_closed
    assert all(client.is_closed for client, _ in clients.created)
