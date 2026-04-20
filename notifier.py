"""Self-contained ntfy push notification handler for Python's stdlib logging.

Reusable in any project — no third-party dependencies required.

Usage:
    from notifier import NtfyHandler
    handler = NtfyHandler.from_env()   # reads NTFY_TOPIC / NTFY_SERVER from env
    if handler:
        logging.getLogger().addHandler(handler)

Required env vars:
    NTFY_TOPIC   — ntfy topic name (e.g. "8i0i3kWl6LRR")
    NTFY_SERVER  — optional, defaults to "https://ntfy.sh"
"""

import hashlib
import logging
import os
import time
import traceback
import urllib.error
import urllib.request
from typing import Optional


class NtfyHandler(logging.Handler):
    """Logging handler that sends ERROR+ records to an ntfy topic.

    Deduplicates by fingerprinting the error site: same exception type+message,
    or same call-site+message-prefix, within dedup_window_s are suppressed.
    Failed HTTP calls do not advance the dedup clock, so the next occurrence retries.
    """

    DEFAULT_SERVER = "https://ntfy.sh"
    DEFAULT_DEDUP_WINDOW_S = 3600

    def __init__(
        self,
        topic: str,
        server: str = DEFAULT_SERVER,
        dedup_window_s: int = DEFAULT_DEDUP_WINDOW_S,
        timeout_s: int = 5,
    ):
        super().__init__(level=logging.ERROR)
        self._url = f"{server.rstrip('/')}/{topic}"
        self._dedup_window_s = dedup_window_s
        self._timeout_s = timeout_s
        self._seen: dict[str, float] = {}
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s")
        )

    @classmethod
    def from_env(cls, **kwargs) -> Optional["NtfyHandler"]:
        """Construct from NTFY_TOPIC / NTFY_SERVER env vars; returns None if unset."""
        topic = os.getenv("NTFY_TOPIC")
        if not topic:
            return None
        server = os.getenv("NTFY_SERVER", cls.DEFAULT_SERVER)
        return cls(topic=topic, server=server, **kwargs)

    @staticmethod
    def _fingerprint(record: logging.LogRecord) -> str:
        exc_info = record.exc_info
        if exc_info and exc_info[1] is not None:
            raw = f"{type(exc_info[1]).__name__}:{str(exc_info[1])[:80]}"
        else:
            try:
                msg = record.getMessage()[:80]
            except Exception:
                msg = str(record.msg)[:80]
            raw = f"{record.pathname}:{record.lineno}:{msg}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def send(self, title: str, body: str) -> None:
        """Send a one-off notification, bypassing deduplication."""
        try:
            req = urllib.request.Request(
                self._url,
                data=body[:4096].encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": "default",
                    "Tags": "white_check_mark",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout_s):
                pass
        except Exception:
            logging.getLogger(__name__).warning("ntfy send failed", exc_info=True)

    def emit(self, record: logging.LogRecord) -> None:
        fp = self._fingerprint(record)
        now = time.monotonic()
        if now - self._seen.get(fp, 0.0) < self._dedup_window_s:
            return
        try:
            body = self.format(record)
            if record.exc_info and record.exc_info[1] is not None:
                tb_lines = traceback.format_exception(*record.exc_info)
                body += "\n" + "".join(tb_lines[-6:])
            body = body[:4096]
            req = urllib.request.Request(
                self._url,
                data=body.encode("utf-8"),
                headers={
                    "Title": f"[{record.levelname}] {record.name}",
                    "Priority": "high" if record.levelno >= logging.CRITICAL else "default",
                    "Tags": "rotating_light" if record.levelno >= logging.ERROR else "warning",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout_s):
                pass
            self._seen[fp] = now  # only mark sent after successful POST
        except Exception:
            self.handleError(record)  # writes to stderr, never raises
