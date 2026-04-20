# Error Analysis & Fix Plan

Source: `/tmp/moon-rabbit/moon-rabbit/runtime/merged.errors.log` (2026-03-15 to 2026-04-15, 2321 total ERROR entries)

---

## Error Categories

### A. DB Connection Not Resilient — FIXED (2026-04-20)

**Symptoms**
- `psycopg2.InterfaceError: connection already closed` in `discord_channel_info`
- `psycopg2.OperationalError: SSL connection has been closed unexpectedly` in `expireVariables`

**Root causes & fixes applied**

1. `discord_client.py:153,328` — was calling `db().conn.cursor()` directly, bypassing the reconnect logic. Changed to `db().cursor()` at both call sites.

2. `storage.py:DB.cursor()` — only checked `conn.closed`, which isn't set for silently dropped SSL connections. Added a `SELECT 1` liveness probe and `OperationalError` catch that triggers `_reconnect()` before returning a fresh cursor.

3. `main.py:expireVariables` — DB calls were blocking the event loop (no `asyncio.to_thread`) and an unhandled exception killed the loop permanently. Wrapped body in `try/except Exception` with `logging.exception`, and moved both DB calls to `asyncio.to_thread`.

Tests added in `tests/test_db_resilience.py` covering all three scenarios.

---

### B. PIL Corrupt Image Cache — FIXED (~Mar 23)

**Symptom**: `PIL.UnidentifiedImageError: cannot identify image file b8e090b8257a24c681d570c7d43b22dd1e03187f.png` — every 10 minutes from 2026-03-15 to 2026-03-23 (~1824 occurrences). Same hash = same URL always returning corrupt/non-image content.

**Root cause (historical)**: `discord_client.py:create_banner_image` called `Image.open()` without a try/except. The `download_file` function wrote valid bytes to disk (HTTP 200 but non-image body), so the "ERROR:" prefix guard was never triggered.

**Current code**: already wraps `Image.open` in try/except and deletes the bad file on failure (commit `fc9b0fe`). No further action needed unless the same URL still serves bad content — in that case, confirm `runtime/img/b8e090b8257a24c681d570c7d43b22dd1e03187f.png` has been removed from the server.

---

### C. Twitch Message Delivery Failures — MEDIUM (773 occurrences, Mar 24)

**Symptoms**
- `user_timed_out` — bot attempts to send messages after being timed out in a channel
- `msg_duplicate` — Twitch rejects a message identical to the previous one sent within 30s
- `Too Many Requests (429)` — bot is rate-limited mid-burst

**Context**: These all occurred on 2026-03-24 in channel `jl_in_july` during a burst of connection-closed errors (category A). The reconnect loop triggered repeated retries of the same message.

**Fix plan**

1. **Deduplication**: track the last sent message per channel with a 30s TTL; skip sending if identical.

2. **Timeout awareness**: on `user_timed_out` response, back off for the timeout duration (parse it from the NOTICE if available) rather than retrying.

3. **Rate-limit backoff**: on 429, wait and retry once with exponential backoff instead of dropping.

4. **Underlying cause**: fixing category A (DB reconnects → no more connection-lost retry storms) will eliminate most of these.

---

### D. WebSocket Lifecycle Leaks — LOW (transient / informational)

#### D1. Unclosed Twitch EventSub connection (11 occurrences)

**Symptom**: `ERROR Unclosed connection … eventsub.wss.twitch.tv`

aiohttp warns that a WebSocket session was garbage-collected without being explicitly closed. Happens when twitchio reconnects the eventsub conduit and the old session object is not closed.

**Fix**: in twitchio upgrade path or custom teardown, ensure `session.close()` is awaited before replacing the session. Consider upgrading twitchio if a newer version fixes this.

#### D2. Discord reconnect failures (6 occurrences)

**Symptom**: `aiohttp.WSServerHandshakeError: 520` / `ClientConnectionResetError: Cannot write to closing transport`

HTTP 520 ("Unknown Error") from the Discord gateway — transient Cloudflare/Discord-side issue. The `discord.py` library already handles this by retrying with backoff. No code change needed.

#### D3. Twitch conduit welcome-message timeout (3 occurrences)

**Symptom**: `Task exception was never retrieved … WebsocketConnectionException: did not receive a welcome message from Twitch within the allowed timeframe`

Happens when twitchio's conduit shard connects but Twitch doesn't send the welcome in time. The task silently dies. twitchio should retry automatically; if not, add an exception handler in the conduit setup to reschedule a reconnect.

#### D4. EventSub resubscription 400 (1 occurrence)

**Symptom**: `Unable to resubscribe … websocket transport session does not exist or has already disconnected`

Single occurrence on 2026-03-16. Stale session ID used during resubscription. Either upgrade twitchio or add a guard to drop the subscription and re-create it when this error is received.

---

## Priority Summary

| # | Issue | Occurrences | Status | Effort |
|---|-------|-------------|--------|--------|
| A | DB connection not resilient | ~1836 | Active | Small |
| C | Twitch message retry storm | 773 | Active (triggered by A) | Small |
| B | PIL corrupt image | ~1824 | Fixed Mar 23 | Done |
| D1 | Unclosed eventsub session | 11 | Periodic | Medium |
| D3 | Conduit welcome timeout | 3 | Periodic | Medium |
| D2 | Discord 520 reconnect | 6 | Transient | None |
| D4 | EventSub resubscription 400 | 1 | Transient | None |

Fixing **A** first is the highest leverage: it stops the cascade that causes **C** and it's a one-line fix for the most frequent active error.
