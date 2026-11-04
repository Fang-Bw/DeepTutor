"""Per-owner Codex connections, rotated without interrupting leased requests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
import logging
import threading
from typing import Any

import httpx

from .network import codex_proxy_options

logger = logging.getLogger(__name__)


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Any, request: Any) -> bool:
        return False

    def return_ok(self, cookie: Any, request: Any) -> bool:
        return False


@dataclass(eq=False)
class _Entry:
    client: httpx.AsyncClient
    loop: asyncio.AbstractEventLoop
    users: int = 0
    retired: bool = False
    close_task: asyncio.Task[None] | None = None


class CodexHTTPPool:
    """Owned by one OAuth service; credentials remain request-local.

    Clients are never shared across owners or event loops. Saving a different
    proxy retires old connections on the next request. Leased streams finish
    before their old connection closes. TLS policies and timeouts have separate
    client keys, so inference's opt-in TLS setting cannot affect OAuth.
    """

    def __init__(self, *, options: Callable[[], dict[str, str]] = codex_proxy_options) -> None:
        self._options = options
        self._entries: dict[tuple[asyncio.AbstractEventLoop, str, float, bool], _Entry] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    async def _close(entry: _Entry) -> None:
        try:
            if entry.loop is asyncio.get_running_loop() or not entry.loop.is_running():
                await entry.client.aclose()
            else:
                await asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(entry.client.aclose(), entry.loop)
                )
        except Exception as exc:
            # Never log proxy URLs, cookies or request headers.
            logger.warning("Could not close a Codex connection: %s", type(exc).__name__)

    def _start_close(self, entry: _Entry) -> asyncio.Task[None]:
        with self._lock:
            if entry.close_task is None:
                entry.close_task = asyncio.create_task(self._close(entry))
                self._cleanup_tasks.add(entry.close_task)
                entry.close_task.add_done_callback(self._forget_cleanup)
            return entry.close_task

    def _forget_cleanup(self, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._cleanup_tasks.discard(task)

    @asynccontextmanager
    async def lease(
        self, *, timeout: float = 30.0, verify: bool = True
    ) -> AsyncIterator[httpx.AsyncClient]:
        options = self._options()
        proxy = options.get("proxy", "")
        loop = asyncio.get_running_loop()
        key = (loop, proxy, timeout, verify)
        closing: list[_Entry] = []
        with self._lock:
            if self._closed:
                raise RuntimeError("Codex network service is closed.")
            for old_key, old in list(self._entries.items()):
                if old_key[0] is loop and old_key[1] != proxy:
                    old.retired = True
                    del self._entries[old_key]
                    if old.users == 0:
                        closing.append(old)
            entry = self._entries.get(key)
            if entry is None:
                # No account credentials, default authorization headers or
                # global proxy mutation belong in a connection pool.
                try:
                    entry = _Entry(
                        httpx.AsyncClient(
                            timeout=timeout,
                            verify=verify,
                            cookies=CookieJar(policy=_NoCookies()),
                            **options,
                        ),
                        loop,
                    )
                except Exception:
                    for old in closing:
                        self._start_close(old)
                    raise
                self._entries[key] = entry
            entry.users += 1
        try:
            # Schedule every retired idle client before the first await. A
            # cancellation while closing one must not orphan the rest after
            # they have already been removed from the reusable registry.
            close_tasks = [self._start_close(old) for old in closing]
            if close_tasks:
                await asyncio.gather(*(asyncio.shield(task) for task in close_tasks))
            yield entry.client
        finally:
            with self._lock:
                entry.users -= 1
                close = entry.retired and entry.users == 0
            if close:
                await asyncio.shield(self._start_close(entry))

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        async with self.lease() as client:
            return await client.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        async with self.lease() as client:
            return await client.post(url, **kwargs)

    async def aclose(self) -> None:
        with self._lock:
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
            for entry in entries:
                entry.retired = True
        for entry in entries:
            if entry.users == 0:
                self._start_close(entry)
        loop = asyncio.get_running_loop()
        with self._lock:
            tasks = [task for task in self._cleanup_tasks if task.get_loop() is loop]
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))


_CURRENT_POOL: ContextVar[CodexHTTPPool | None] = ContextVar("codex_http_pool", default=None)


@contextmanager
def use_codex_http_pool(pool: CodexHTTPPool | None):
    token = _CURRENT_POOL.set(pool)
    try:
        yield
    finally:
        _CURRENT_POOL.reset(token)


@asynccontextmanager
async def codex_http_client(*, timeout: float, verify: bool) -> AsyncIterator[httpx.AsyncClient]:
    pool = _CURRENT_POOL.get()
    if pool is not None:
        async with pool.lease(timeout=timeout, verify=verify) as client:
            yield client
    else:
        # Direct callers and injected test services retain scoped ownership.
        async with httpx.AsyncClient(
            timeout=timeout, verify=verify, **codex_proxy_options()
        ) as client:
            yield client
