"""Regression tests for twitchio_patches (websocket close-leak workaround).

Uses a local fake EventSub server: it sends `session_welcome` and then stays silent, and we
trigger the stale-socket path (`_create_connection_task`) from inside a task registered as
`_keep_alive_task` - exactly what twitchio's keepalive watchdog does.
"""

import asyncio
import contextlib
import json
from typing import Any, cast

import pytest
from aiohttp import web
from twitchio.eventsub import websockets as ws_mod

import twitchio_patches

_WELCOME = json.dumps(
    {
        "metadata": {"message_type": "session_welcome"},
        "payload": {
            "session": {
                "id": "SESSION1",
                "keepalive_timeout_seconds": 10,
                "connected_at": "2026-01-01T00:00:00.000000000Z",
            }
        },
    }
)


class _StubClient:
    def dispatch(self, *args, **kwargs) -> None:
        pass


@pytest.fixture
def restore_websocket_class():
    original = ws_mod.Websocket._create_connection_task
    yield
    ws_mod.Websocket._create_connection_task = original


async def _stale_socket_scenario() -> tuple[bool, bool, bool]:
    """Returns (close_completed, socket_released, server_saw_close)."""
    server_saw_close = asyncio.Event()

    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(_WELCOME)
        async for _ in ws:
            pass
        server_saw_close.set()
        return ws

    app = web.Application()
    app.add_routes([web.get("/ws", handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = cast("Any", site)._server.sockets[0].getsockname()[1]
    original_wss = ws_mod.WSS
    ws_mod.WSS = f"ws://127.0.0.1:{port}/ws"
    ws = ws_mod.Websocket(client=cast("Any", _StubClient()), http=cast("Any", None), shard_id="0")
    try:
        await ws.connect(fail_once=True)
        # Replace the real watchdog task by one running the stale-socket path, like the watchdog.
        assert ws._keep_alive_task is not None
        ws._keep_alive_task.cancel()
        watchdog = asyncio.create_task(ws._create_connection_task())
        ws._keep_alive_task = watchdog
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server_saw_close.wait(), 2)
        # close() finishes asynchronously after the server sees the close frame; give it a moment.
        for _ in range(40):
            if ws._socket is None and not ws._closing:
                break
            await asyncio.sleep(0.05)
        return (not ws._closing, ws._socket is None, server_saw_close.is_set())
    finally:
        ws_mod.WSS = original_wss
        if ws._socket is not None:  # leaked by the upstream bug: close it so cleanup is quick
            await ws._socket.close()
        await runner.cleanup()


def test_patch_makes_stale_socket_close_complete(restore_websocket_class):
    assert twitchio_patches.patch_websocket_close_leak() is True
    assert asyncio.run(_stale_socket_scenario()) == (True, True, True)


def test_patch_is_idempotent(restore_websocket_class):
    assert twitchio_patches.patch_websocket_close_leak() is True
    assert twitchio_patches.patch_websocket_close_leak() is False


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Canary: upstream twitchio still leaks the socket when close() runs inside the keepalive "
        "task. If this XPASSes, twitchio fixed it - drop twitchio_patches.py and this test."
    ),
)
def test_unpatched_twitchio_still_leaks():
    assert asyncio.run(_stale_socket_scenario()) == (True, True, True)
