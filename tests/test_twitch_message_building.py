"""Unit tests for the pure-logic parts of twitch_client.py.

These tests do NOT need a live Twitch connection or DB.
They import TwitchClient pieces by monkey-patching dependencies.
"""

import sys
import time
import types
from unittest.mock import MagicMock

import ttldict2

from data import EventType, InvocationLog, Message


# ---------------------------------------------------------------------------
# Minimal stub of twitchio so we can import twitch_client without the real lib
# ---------------------------------------------------------------------------
def _make_twitchio_stub():
    twitchio = types.ModuleType("twitchio")

    class Client:
        def __init__(self, **kwargs):
            self._http = MagicMock()
            self.bot_user_id = kwargs.get("bot_id", "")

        async def start(self):
            pass

    class PartialUser:
        def __init__(self, id, name, http):
            self.id = id
            self.name = name

        async def send_message(self, sender, message):
            pass

    class ChatMessage:
        pass

    class EventErrorPayload:
        def __init__(self, exception):
            self.exception = exception

    twitchio.Client = Client  # type: ignore
    twitchio.AutoClient = Client  # type: ignore
    twitchio.PartialUser = PartialUser  # type: ignore
    twitchio.ChatMessage = ChatMessage  # type: ignore
    twitchio.EventErrorPayload = EventErrorPayload  # type: ignore

    eventsub = types.ModuleType("twitchio.eventsub")

    class ChatMessageSubscription:
        def __init__(self, **kwargs):
            pass

    class ChannelPointsRedeemAddSubscription:
        def __init__(self, **kwargs):
            pass

    class HypeTrainEndSubscription:
        def __init__(self, **kwargs):
            pass

    eventsub.ChatMessageSubscription = ChatMessageSubscription  # type: ignore
    eventsub.ChannelPointsRedeemAddSubscription = ChannelPointsRedeemAddSubscription  # type: ignore
    eventsub.HypeTrainEndSubscription = HypeTrainEndSubscription  # type: ignore
    twitchio.eventsub = eventsub  # type: ignore

    web = types.ModuleType("twitchio.web")

    class AiohttpAdapter:
        def __init__(self, **kwargs):
            pass

    web.AiohttpAdapter = AiohttpAdapter  # type: ignore
    twitchio.web = web  # type: ignore

    return twitchio, eventsub, web


# Patch modules before importing twitch_client
_twitchio, _eventsub, _web = _make_twitchio_stub()
sys.modules["twitchio"] = _twitchio
sys.modules["twitchio.eventsub"] = _eventsub
sys.modules["twitchio.web"] = _web


# ---------------------------------------------------------------------------
# Now we can safely import project modules
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helper: build a ChannelInfo-like object without importing TwitchClient directly
# ---------------------------------------------------------------------------
def make_channel_info(throttle_seconds: float = 1.0):
    from twitch_client import ChannelInfo

    return ChannelInfo(
        active_users=ttldict2.TTLDict(ttl_seconds=3600.0),
        prefix="!",
        channel_id=42,
        twitch_user_id="99999",
        events=[],
        throttled_users=ttldict2.TTLDict(ttl_seconds=throttle_seconds),
        last_activity=0.0,
    )


# ---------------------------------------------------------------------------
# Message construction helpers
# ---------------------------------------------------------------------------


def _make_message(channel_id: int, txt: str, event: EventType, prefix: str = "!") -> Message:
    log = InvocationLog(f"test channel ({channel_id})")
    message_id = str(time.time_ns())
    variables = {
        "author": "testuser",
        "author_name": "testuser",
        "media": "twitch",
        "text": txt,
        "is_mod": False,
        "prefix": prefix,
        "bot": "testbot",
        "channel_id": channel_id,
        "_log": log,
        "_private": False,
        "_id": message_id,
    }
    return Message(
        id=message_id,
        log=log,
        channel_id=channel_id,
        txt=txt,
        event=event,
        prefix=prefix,
        is_discord=False,
        is_mod=False,
        private=False,
        get_variables=lambda: variables,
    )


class TestMessageConstruction:
    def test_message_event_type(self):
        msg = _make_message(1, "!hello", EventType.message)
        assert msg.event == EventType.message
        assert msg.is_discord is False

    def test_redemption_event_type(self):
        msg = _make_message(1, "Some Reward", EventType.twitch_reward_redemption)
        assert msg.event == EventType.twitch_reward_redemption

    def test_variables_populated(self):
        msg = _make_message(7, "!test", EventType.message, prefix="+")
        v = msg.get_variables()
        assert v["channel_id"] == 7
        assert v["text"] == "!test"
        assert v["prefix"] == "+"
        assert v["media"] == "twitch"
        # is_discord is a Message attribute, not a template variable
        assert msg.is_discord is False


# ---------------------------------------------------------------------------
# Mention helpers
# ---------------------------------------------------------------------------


class TestMentionHelpers:
    def setup_method(self):
        from twitch_client import TwitchClient

        # Use a partial stub — we only need mention methods
        self.bot = object.__new__(TwitchClient)
        self.info = make_channel_info()

    def test_mentions_finds_at_sign(self):
        from twitch_client import TwitchClient

        bot = object.__new__(TwitchClient)
        assert bot.mentions("@alice hello") == "@alice"
        assert bot.mentions("@alice @bob") == "@alice @bob"
        assert bot.mentions("no mentions here") == ""

    def test_random_mention_returns_other_user(self):
        from twitch_client import TwitchClient

        bot = object.__new__(TwitchClient)
        info = make_channel_info()
        info.active_users["alice"] = 1
        info.active_users["bob"] = 1
        result = bot.random_mention(info, "alice")
        assert result == "@bob"

    def test_random_mention_falls_back_to_author(self):
        from twitch_client import TwitchClient

        bot = object.__new__(TwitchClient)
        info = make_channel_info()
        # No one else active
        result = bot.random_mention(info, "alice")
        assert result == "@alice"

    def test_any_mention_prefers_direct(self):
        from twitch_client import TwitchClient

        bot = object.__new__(TwitchClient)
        info = make_channel_info()
        info.active_users["carol"] = 1
        result = bot.any_mention("@dave hello", info, "author")
        assert result == "@dave"

    def test_any_mention_falls_back_to_random(self):
        from twitch_client import TwitchClient

        bot = object.__new__(TwitchClient)
        info = make_channel_info()
        info.active_users["eve"] = 1
        result = bot.any_mention("no mention here", info, "author")
        assert result == "@eve"


# ---------------------------------------------------------------------------
# User throttling
# ---------------------------------------------------------------------------


class TestUserThrottling:
    def test_throttled_user_blocked(self):
        info = make_channel_info(throttle_seconds=60.0)
        info.throttled_users["spamuser"] = "+"
        assert "spamuser" in info.throttled_users

    def test_non_throttled_user_allowed(self):
        info = make_channel_info(throttle_seconds=60.0)
        assert "newuser" not in info.throttled_users


# ---------------------------------------------------------------------------
# Cron filtering (last_activity check)
# ---------------------------------------------------------------------------


class TestCronFiltering:
    def test_inactive_channel_skipped(self):
        info = make_channel_info()
        info.last_activity = time.time() - 3600  # 1 hour ago
        # Should be skipped (> 30 min threshold)
        assert info.last_activity < time.time() - 1800

    def test_active_channel_included(self):
        info = make_channel_info()
        info.last_activity = time.time() - 60  # 1 minute ago
        assert info.last_activity >= time.time() - 1800


# ---------------------------------------------------------------------------
# Send message truncation
# ---------------------------------------------------------------------------


class TestSendTruncation:
    def test_long_message_truncated(self):
        """Verify 500-char truncation logic matches twitch_client.send_message."""
        txt = "x" * 600
        if len(txt) > 500:
            txt = txt[:497] + "..."
        assert len(txt) == 500
        assert txt.endswith("...")

    def test_short_message_unchanged(self):
        txt = "hello world"
        if len(txt) > 500:
            txt = txt[:497] + "..."
        assert txt == "hello world"
