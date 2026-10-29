"""Validation shared by stored settings and the network settings API."""

from urllib.parse import urlsplit


def normalize_codex_proxy(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    message = "Use an HTTP, HTTPS or SOCKS5 proxy URL with a host and port, without credentials."
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https", "socks5"}
            or not parsed.hostname
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise ValueError(message)
    except ValueError:
        raise ValueError(message) from None
    return value.rstrip("/")
