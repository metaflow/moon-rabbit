"""Runtime workarounds for upstream twitchio bugs.

Remove a patch once the fixed twitchio release is the minimum supported version.
"""

import asyncio
import functools
import logging

from twitchio.eventsub import websockets as _ws


def _log_task_failure(task: asyncio.Future) -> None:
    """Retrieve the result so a failure is logged instead of 'exception was never retrieved'."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logging.error("twitchio websocket close/reconnect task failed", exc_info=exc)


def patch_websocket_close_leak() -> bool:
    """Stop twitchio's keepalive watchdog from leaking a websocket on every stale-socket trip.

    Bug (twitchio 3.2.2 .. 3.3.2 and main): ``Websocket._process_keepalive`` (running as
    ``_keep_alive_task``) calls ``_create_connection_task()`` -> ``close()``, and ``close()``
    cancels ``_keep_alive_task`` - i.e. the task that is executing it. The pending cancellation
    fires at ``await self._socket.close()`` as a ``CancelledError`` (a BaseException, so the
    ``except Exception`` there does not catch it) and ``close()`` aborts half-way: the connection
    stays open and ``_closing`` stays True forever. After enough trips Twitch answers 429 and the
    process holds ~1 GB of leaked sockets. Details: docs/twitchio_websocket_leak_report.md.

    Fix: run the original ``_create_connection_task`` in its own task and ``shield`` it, so the
    cancel of the calling task no longer reaches the code doing the closing.

    Returns True if the patch was applied, False if it was already applied or twitchio changed.
    """
    cls = getattr(_ws, "Websocket", None)
    original = getattr(cls, "_create_connection_task", None)
    if cls is None or original is None:
        logging.warning("twitchio_patches: Websocket._create_connection_task not found; skipped")
        return False
    if getattr(original, "_moon_rabbit_patched", False):
        return False

    @functools.wraps(original)
    async def _create_connection_task(self) -> None:
        inner = asyncio.ensure_future(original(self))
        inner.add_done_callback(_log_task_failure)
        await asyncio.shield(inner)

    setattr(_create_connection_task, "_moon_rabbit_patched", True)
    cls._create_connection_task = _create_connection_task
    logging.info("twitchio_patches: applied websocket close-leak workaround")
    return True


def apply_all() -> None:
    patch_websocket_close_leak()
