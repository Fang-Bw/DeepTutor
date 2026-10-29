"""Explicit proxy settings for Codex outbound requests, never loopback callbacks."""

from deeptutor.services.config.runtime_settings import load_system_settings


def codex_proxy_options() -> dict[str, str]:
    proxy = load_system_settings().get("codex_proxy_url", "")
    # Omitting the argument preserves HTTPX's existing environment proxy support.
    return {"proxy": proxy} if proxy else {}
