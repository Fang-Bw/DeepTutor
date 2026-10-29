from __future__ import annotations

import asyncio

import httpx
import pytest

from deeptutor.services.codex_auth import network
from deeptutor.services.config.network_proxy import normalize_codex_proxy
from deeptutor.services.config.runtime_settings import RuntimeSettingsService


@pytest.mark.parametrize("scheme", ["http", "https", "socks5"])
def test_proxy_settings_roundtrip(tmp_path, scheme):
    service = RuntimeSettingsService(tmp_path, process_env={})
    url = f"{scheme}://127.0.0.1:7890"
    service.save_system({"codex_proxy_url": f" {url}/ "})
    assert service.load_system()["codex_proxy_url"] == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:7890",
        "127.0.0.1:7890",
        "http://localhost",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:abc",
        "http://user:secret@localhost:7890",
        "http://localhost:7890/path",
        "http://localhost:7890?secret=value",
        "http://localhost:7890#fragment",
        "http://local host:7890",
        "http://localhost:7890\n/path",
    ],
)
def test_invalid_proxy_is_rejected_without_echoing_input(url):
    with pytest.raises(ValueError) as error:
        normalize_codex_proxy(url)
    assert url not in str(error.value)
    assert "secret" not in str(error.value)


def test_empty_proxy_preserves_environment_behavior(monkeypatch):
    monkeypatch.setattr(network, "load_system_settings", lambda: {"codex_proxy_url": ""})
    assert network.codex_proxy_options() == {}


@pytest.mark.asyncio
async def test_configured_proxy_receives_https_tunnel_even_with_no_proxy(monkeypatch):
    requests = []

    async def handle(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(
            network,
            "load_system_settings",
            lambda: {
                "codex_proxy_url": f"http://127.0.0.1:{port}",
            },
        )
        monkeypatch.setenv("NO_PROXY", "*")
        async with httpx.AsyncClient(timeout=2, **network.codex_proxy_options()) as client:
            with pytest.raises(httpx.ProxyError):
                await client.post(
                    "https://auth.openai.com/oauth/token", data={"code": "test-secret"}
                )

    assert len(requests) == 1
    assert requests[0].startswith(b"CONNECT auth.openai.com:443 HTTP/1.1")
    assert b"test-secret" not in requests[0]
