"""Tests for TwitchClient.send_message: dedup, timeout awareness, rate-limit backoff."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from twitch_client import _MSG_DEDUP_SECS, ChannelInfo, TwitchClient


def _make_info(**kwargs) -> ChannelInfo:
    defaults: dict = {
        "active_users": MagicMock(),
        "throttled_users": MagicMock(),
        "prefix": "+",
        "channel_id": 1,
        "twitch_user_id": "123",
        "events": [],
        "last_activity": time.time(),
    }
    defaults.update(kwargs)
    return ChannelInfo(**defaults)  # type: ignore[arg-type]


def _make_client() -> TwitchClient:
    client = MagicMock(spec=TwitchClient)
    client.throttler = MagicMock()
    client.throttler.__aenter__ = AsyncMock(return_value=None)
    client.throttler.__aexit__ = AsyncMock(return_value=False)
    client.user = MagicMock()
    client.send_message = TwitchClient.send_message.__get__(client, TwitchClient)
    return client


def _make_broadcaster(side_effect=None) -> MagicMock:
    broadcaster = MagicMock()
    if side_effect:
        broadcaster.send_message = AsyncMock(side_effect=side_effect)
    else:
        broadcaster.send_message = AsyncMock()
    return broadcaster


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_send_message_success_updates_last_sent():
    client = _make_client()
    info = _make_info()
    broadcaster = _make_broadcaster()
    with patch.object(client, "create_partialuser", return_value=broadcaster):
        asyncio.run(client.send_message(info, "hello"))

    broadcaster.send_message.assert_awaited_once()
    assert info.last_sent_text == "hello"
    assert info.last_sent_at > 0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_send_message_skips_duplicate_within_window():
    client = _make_client()
    info = _make_info(last_sent_text="hello", last_sent_at=time.time())
    broadcaster = _make_broadcaster()
    with patch.object(client, "create_partialuser", return_value=broadcaster):
        asyncio.run(client.send_message(info, "hello"))

    broadcaster.send_message.assert_not_awaited()


def test_send_message_sends_duplicate_after_window():
    client = _make_client()
    info = _make_info(last_sent_text="hello", last_sent_at=time.time() - _MSG_DEDUP_SECS - 1)
    broadcaster = _make_broadcaster()
    with patch.object(client, "create_partialuser", return_value=broadcaster):
        asyncio.run(client.send_message(info, "hello"))

    broadcaster.send_message.assert_awaited_once()


def test_send_message_sends_different_text_immediately():
    client = _make_client()
    info = _make_info(last_sent_text="hello", last_sent_at=time.time())
    broadcaster = _make_broadcaster()
    with patch.object(client, "create_partialuser", return_value=broadcaster):
        asyncio.run(client.send_message(info, "world"))

    broadcaster.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# user_timed_out — drop without retry
# ---------------------------------------------------------------------------


def test_send_message_drops_on_user_timed_out():
    client = _make_client()
    info = _make_info()
    broadcaster = _make_broadcaster(side_effect=Exception("user_timed_out"))
    with patch.object(client, "create_partialuser", return_value=broadcaster):
        asyncio.run(client.send_message(info, "hello"))

    broadcaster.send_message.assert_awaited_once()
    assert info.last_sent_text == ""  # not updated on failure


# ---------------------------------------------------------------------------
# msg_duplicate — drop without retry
# ---------------------------------------------------------------------------


def test_send_message_drops_on_msg_duplicate():
    client = _make_client()
    info = _make_info()
    broadcaster = _make_broadcaster(side_effect=Exception("msg_duplicate"))
    with patch.object(client, "create_partialuser", return_value=broadcaster):
        asyncio.run(client.send_message(info, "hello"))

    broadcaster.send_message.assert_awaited_once()
    assert info.last_sent_text == ""


# ---------------------------------------------------------------------------
# 429 rate-limit — sleep and retry
# ---------------------------------------------------------------------------


def test_send_message_retries_on_429():
    client = _make_client()
    info = _make_info()
    broadcaster = MagicMock()
    broadcaster.send_message = AsyncMock(side_effect=[Exception("Too Many Requests (429)"), None])
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with (
        patch.object(client, "create_partialuser", return_value=broadcaster),
        patch("twitch_client.asyncio.sleep", side_effect=fake_sleep),
    ):
        asyncio.run(client.send_message(info, "hello"))

    assert broadcaster.send_message.await_count == 2
    assert len(sleep_calls) == 1
    assert info.last_sent_text == "hello"


def test_send_message_logs_error_if_retry_also_fails():
    client = _make_client()
    info = _make_info()
    broadcaster = _make_broadcaster(side_effect=Exception("Too Many Requests (429)"))

    async def fake_sleep(_: float) -> None:
        pass

    with (
        patch.object(client, "create_partialuser", return_value=broadcaster),
        patch("twitch_client.asyncio.sleep", side_effect=fake_sleep),
    ):
        asyncio.run(client.send_message(info, "hello"))

    assert broadcaster.send_message.await_count == 2
    assert info.last_sent_text == ""
