"""Tests for NtfyHandler — push notification logging handler."""

import logging
import time
import urllib.error
from unittest.mock import MagicMock, patch

from notifier import NtfyHandler


def make_handler(**kwargs) -> NtfyHandler:
    return NtfyHandler(topic="test-topic", server="https://ntfy.sh", **kwargs)


def make_record(
    msg: str = "something broke",
    level: int = logging.ERROR,
    pathname: str = "/app/foo.py",
    lineno: int = 42,
    exc_info=None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=pathname,
        lineno=lineno,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    return record


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_emit_posts_to_correct_url():
    handler = make_handler()
    record = make_record()
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(record)
    assert mock_open.called
    req = mock_open.call_args[0][0]
    assert req.full_url == "https://ntfy.sh/test-topic"
    assert req.method == "POST"


def test_emit_sets_title_header():
    handler = make_handler()
    record = make_record()
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(record)
    req = mock_open.call_args[0][0]
    assert req.get_header("Title") == "[ERROR] test.logger"


def test_emit_sets_rotating_light_tag_for_error():
    handler = make_handler()
    record = make_record()
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(record)
    req = mock_open.call_args[0][0]
    assert req.get_header("Tags") == "rotating_light"


def test_emit_sets_high_priority_for_critical():
    handler = make_handler()
    record = make_record(level=logging.CRITICAL)
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(record)
    req = mock_open.call_args[0][0]
    assert req.get_header("Priority") == "high"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_second_identical_emit_is_suppressed():
    handler = make_handler(dedup_window_s=60)
    record = make_record()
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(record)
        handler.emit(record)
    assert mock_open.call_count == 1


def test_emit_after_window_expiry_is_sent():
    handler = make_handler(dedup_window_s=1)
    record = make_record()
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(record)
        # backdate the seen timestamp to simulate window expiry
        fp = NtfyHandler._fingerprint(record)
        handler._seen[fp] = time.monotonic() - 2
        handler.emit(record)
    assert mock_open.call_count == 2


def test_different_call_sites_not_deduplicated():
    handler = make_handler(dedup_window_s=3600)
    r1 = make_record(pathname="/app/foo.py", lineno=10)
    r2 = make_record(pathname="/app/foo.py", lineno=99)
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(r1)
        handler.emit(r2)
    assert mock_open.call_count == 2


# ---------------------------------------------------------------------------
# Exception fingerprinting
# ---------------------------------------------------------------------------


def test_same_exception_type_and_message_deduplicates():
    handler = make_handler(dedup_window_s=3600)

    def _exc_info():
        try:
            raise ValueError("db connection refused")
        except ValueError:
            import sys

            return sys.exc_info()

    r1 = make_record(exc_info=_exc_info())
    r2 = make_record(exc_info=_exc_info())
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(r1)
        handler.emit(r2)
    assert mock_open.call_count == 1


def test_different_exception_types_not_deduplicated():
    handler = make_handler(dedup_window_s=3600)

    def _val_exc():
        try:
            raise ValueError("boom")
        except Exception:
            import sys

            return sys.exc_info()

    def _key_exc():
        try:
            raise KeyError("boom")
        except Exception:
            import sys

            return sys.exc_info()

    r1 = make_record(exc_info=_val_exc())
    r2 = make_record(exc_info=_key_exc())
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(r1)
        handler.emit(r2)
    assert mock_open.call_count == 2


# ---------------------------------------------------------------------------
# Network failure
# ---------------------------------------------------------------------------


def test_network_failure_does_not_propagate():
    handler = make_handler()
    record = make_record()
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")),
        patch.object(handler, "handleError") as mock_handle,
    ):
        handler.emit(record)
    mock_handle.assert_called_once_with(record)


def test_seen_not_updated_on_network_failure():
    handler = make_handler(dedup_window_s=3600)
    record = make_record()
    fp = NtfyHandler._fingerprint(record)
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")),
        patch.object(handler, "handleError"),
    ):
        handler.emit(record)
    assert fp not in handler._seen


def test_retry_allowed_after_failed_send():
    handler = make_handler(dedup_window_s=3600)
    record = make_record()
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")),
        patch.object(handler, "handleError"),
    ):
        handler.emit(record)
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        handler.emit(record)
    assert mock_open.call_count == 1


# ---------------------------------------------------------------------------
# from_env()
# ---------------------------------------------------------------------------


def test_from_env_returns_none_when_topic_unset(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert NtfyHandler.from_env() is None


def test_from_env_returns_handler_when_topic_set(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "my-topic")
    monkeypatch.delenv("NTFY_SERVER", raising=False)
    h = NtfyHandler.from_env()
    assert isinstance(h, NtfyHandler)
    assert h._url == "https://ntfy.sh/my-topic"


def test_from_env_uses_custom_server(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "my-topic")
    monkeypatch.setenv("NTFY_SERVER", "https://custom.ntfy.example.com")
    h = NtfyHandler.from_env()
    assert h is not None
    assert h._url == "https://custom.ntfy.example.com/my-topic"


def test_from_env_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "my-topic")
    monkeypatch.setenv("NTFY_SERVER", "https://custom.ntfy.example.com/")
    h = NtfyHandler.from_env()
    assert h is not None
    assert h._url == "https://custom.ntfy.example.com/my-topic"


# ---------------------------------------------------------------------------
# Body truncation
# ---------------------------------------------------------------------------


def test_body_truncated_to_4096_bytes():
    handler = make_handler()
    record = make_record(msg="x" * 10000)
    captured = {}
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        def capture(req, timeout):
            captured["data"] = req.data
            return mock_open.return_value

        mock_open.side_effect = capture
        handler.emit(record)
    assert len(captured["data"]) <= 4096
