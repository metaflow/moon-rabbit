"""Periodic resource heartbeat, so slow leaks (memory, sockets, tasks) are visible in the log.

Every `interval_s` one INFO line is written, e.g.
    health rss=148MB sockets=17 fds=41 tasks=52
`sockets` is the number of open socket file descriptors of this process. A twitchio websocket leak (see
docs/log.md, 2026-11-07) showed up as ever-growing `sockets` and `rss`; a healthy bot stays flat. A
WARNING is logged when sockets/rss/tasks exceed the thresholds below. Linux only (reads /proc); on other
platforms the loop logs nothing.
"""

import asyncio
import logging
import os

SOCKET_WARN = 100  # healthy is roughly 15-30 (discord gateway, twitch conduit shards, db, http)
RSS_WARN_MB = 300  # healthy is ~150 MB; PM2 restarts at 400 MB
TASKS_WARN = 500


def read_rss_mb() -> int | None:
    try:
        with open("/proc/self/statm") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None


def count_fds() -> tuple[int, int] | None:
    """Returns (open sockets, all open file descriptors)."""
    try:
        names = os.listdir("/proc/self/fd")
    except OSError:
        return None
    sockets = 0
    for name in names:
        try:
            if os.readlink(f"/proc/self/fd/{name}").startswith("socket:"):
                sockets += 1
        except OSError:
            pass  # the fd was closed since listdir (including listdir's own fd)
    return sockets, len(names)


def log_health() -> None:
    rss = read_rss_mb()
    fds = count_fds()
    if rss is None or fds is None:
        return
    sockets, total_fds = fds
    tasks = len(asyncio.all_tasks())
    logging.info(f"health rss={rss}MB sockets={sockets} fds={total_fds} tasks={tasks}")
    if sockets > SOCKET_WARN or rss > RSS_WARN_MB or tasks > TASKS_WARN:
        logging.warning(
            f"health: possible leak (rss={rss}MB sockets={sockets} tasks={tasks}; "
            f"thresholds {RSS_WARN_MB}MB/{SOCKET_WARN}/{TASKS_WARN})"
        )


async def run(interval_s: int = 600) -> None:
    while True:
        try:
            log_health()
        except Exception:
            logging.exception("health monitor failed")
        await asyncio.sleep(interval_s)
