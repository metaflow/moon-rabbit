import asyncio
import logging
import socket

import health_monitor


def test_read_rss_is_plausible():
    rss = health_monitor.read_rss_mb()
    assert rss is not None and 5 < rss < 10_000


def test_count_fds_sees_new_sockets():
    before = health_monitor.count_fds()
    assert before is not None
    a, b = socket.socketpair()
    try:
        after = health_monitor.count_fds()
        assert after is not None
        assert after[0] == before[0] + 2
        assert after[1] >= before[1] + 2
    finally:
        a.close()
        b.close()


def test_log_health_line_and_leak_warning(caplog, monkeypatch):
    async def run() -> None:
        health_monitor.log_health()
        monkeypatch.setattr(health_monitor, "SOCKET_WARN", 0)
        a, b = socket.socketpair()
        try:
            health_monitor.log_health()
        finally:
            a.close()
            b.close()

    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    infos = [r for r in caplog.records if r.getMessage().startswith("health rss=")]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(infos) == 2
    assert len(warnings) == 1 and "possible leak" in warnings[0].getMessage()
