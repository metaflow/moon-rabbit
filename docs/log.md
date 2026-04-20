# Project Log

Dated records of significant changes, migrations, and bug-fix campaigns. Newest first.

---

## 2026-04-20 — ntfy push notification handler

Added `notifier.py`: a self-contained `NtfyHandler(logging.Handler)` that sends ERROR+ log records to an ntfy topic. Integrated into `main.py:setup_logging()` — enabled automatically when `NTFY_TOPIC` env var is set. Deduplicates by error fingerprint (exception type+message or call-site+message prefix) within a configurable window (default 1 hour). No new pip dependencies. Tests in `tests/test_notifier.py`.

Also moved `load_dotenv()` before `setup_logging()` in `main()` so env vars from `.env` are available at handler construction time.

---

## 2026-04-20 — Error fixes from recent logs (A/B/C/D)

Source: `/tmp/moon-rabbit/moon-rabbit/runtime/merged.errors.log` (2026-03-15 to 2026-04-15, 2321 total ERROR entries)

### A. DB Connection Not Resilient — FIXED

**Symptoms**
- `psycopg2.InterfaceError: connection already closed` in `discord_channel_info`
- `psycopg2.OperationalError: SSL connection has been closed unexpectedly` in `expireVariables`

**Root causes & fixes applied**

1. `discord_client.py:153,328` — was calling `db().conn.cursor()` directly, bypassing the reconnect logic. Changed to `db().cursor()` at both call sites.

2. `storage.py:DB.cursor()` — only checked `conn.closed`, which isn't set for silently dropped SSL connections. Added a `SELECT 1` liveness probe and `OperationalError` catch that triggers `_reconnect()` before returning a fresh cursor.

3. `main.py:expireVariables` — DB calls were blocking the event loop (no `asyncio.to_thread`) and an unhandled exception killed the loop permanently. Wrapped body in `try/except Exception` with `logging.exception`, and moved both DB calls to `asyncio.to_thread`.

Tests: `tests/test_db_resilience.py`

---

### B. PIL Corrupt Image Cache — FIXED (~Mar 23)

**Symptom**: `PIL.UnidentifiedImageError: cannot identify image file b8e090b8257a24c681d570c7d43b22dd1e03187f.png` — every 10 minutes from 2026-03-15 to 2026-03-23 (~1824 occurrences). Same hash = same URL always returning corrupt/non-image content.

**Root cause (historical)**: `discord_client.py:create_banner_image` called `Image.open()` without a try/except. The `download_file` function wrote valid bytes to disk (HTTP 200 but non-image body), so the "ERROR:" prefix guard was never triggered.

**Fix**: Code already wraps `Image.open` in try/except and deletes the bad file on failure (commit `fc9b0fe`). No further action needed unless the same URL still serves bad content.

---

### C. Twitch Message Delivery Failures — FIXED

**Symptoms**
- `user_timed_out` — bot attempts to send messages after being timed out in a channel
- `msg_duplicate` — Twitch rejects a message identical to the previous one sent within 30s
- `Too Many Requests (429)` — bot is rate-limited mid-burst

**Context**: All occurred on 2026-03-24 in channel `jl_in_july` during a burst of connection-closed errors (category A). The reconnect loop triggered repeated retries of the same message.

**Root causes & fixes applied**

1. **Deduplication** (`twitch_client.py:ChannelInfo`, `send_message`): Added `last_sent_text` / `last_sent_at` fields to `ChannelInfo`. `send_message` now skips sending if the text is identical to the last sent message within `_MSG_DEDUP_SECS` (30 s).

2. **Timeout awareness** (`twitch_client.py:send_message`): On `user_timed_out` error, logs a warning and drops the message without retrying.

3. **Rate-limit backoff** (`twitch_client.py:send_message`): On `429` / `Too Many Requests`, sleeps 2 s and retries once. On second failure, logs the error and drops.

4. **Underlying cause**: Category A fix eliminated the retry storm that triggered most occurrences.

Tests: `tests/test_twitch_send.py`

---

### D. WebSocket Lifecycle Leaks — FIXED

#### D1. Unclosed Twitch EventSub connection (11 occurrences)

**Symptom**: `ERROR Unclosed connection … eventsub.wss.twitch.tv`

aiohttp warns that a WebSocket session was garbage-collected without being explicitly closed. Internal twitchio bug — `session.detach()` called without closing the old `ClientSession` during eventsub reconnects.

**Fix**: Custom asyncio exception handler `_twitchio_exception_handler` in `main.py:run_loop` demotes this from ERROR to WARNING, since it is unactionable from application code. Also upgraded twitchio 3.2.1 → 3.2.2.

#### D2. Discord reconnect failures (6 occurrences) — no action needed

**Symptom**: `aiohttp.WSServerHandshakeError: 520` / `ClientConnectionResetError: Cannot write to closing transport`

HTTP 520 from Discord gateway — transient Cloudflare/Discord-side issue. `discord.py` already retries with backoff.

#### D3. Twitch conduit welcome-message timeout (3 occurrences)

**Symptom**: `Task exception was never retrieved … WebsocketConnectionException: did not receive a welcome message from Twitch within the allowed timeframe`

Twitchio conduit shard dies when Twitch doesn't send a welcome in time; asyncio logs it as an unretrieved task exception at ERROR level. Transient — twitchio reconnects on next activity.

**Fix**: Same `_twitchio_exception_handler` catches this and logs at WARNING.

#### D4. EventSub resubscription 400 (1 occurrence) — no action needed

**Symptom**: `Unable to resubscribe … websocket transport session does not exist or has already disconnected`

Single occurrence (2026-03-16). Stale session ID; addressed by twitchio upgrade.

---

### Occurrence Summary

| # | Issue | Occurrences | Status |
|---|-------|-------------|--------|
| A | DB connection not resilient | ~1836 | Fixed 2026-04-20 |
| B | PIL corrupt image | ~1824 | Fixed ~2026-03-23 |
| C | Twitch message retry storm | 773 | Fixed 2026-04-20 |
| D1 | Unclosed eventsub session | 11 | Fixed 2026-04-20 |
| D3 | Conduit welcome timeout | 3 | Fixed 2026-04-20 |
| D2 | Discord 520 reconnect | 6 | Transient — no action |
| D4 | EventSub resubscription 400 | 1 | Transient — no action |

---

## 2026-03 — TwitchIO 2.6 → 3.x Migration + DigitalOcean Move

### What changed

Migrated from two libraries (`twitchio 2.6.0` for IRC chat + `twitchAPI 3.10.0` for EventSub webhooks) to a single `twitchio 3.x` that handles both chat and events via EventSub WebSocket, and moved the hosting environment to DigitalOcean.

| Aspect | Before | After |
|---|---|---|
| Chat | IRC via `twitchio.Client` | EventSub via `twitchio.AutoClient` |
| Events | `twitchAPI.EventSub` webhooks (needs public URL) | twitchio built-in EventSub WebSocket (no public URL) |
| Libraries | `twitchio 2.6.0` + `twitchAPI 3.10.0` | `twitchio 3.x` only |
| Auth | Manual token refresh at startup | twitchio auto-refresh; tokens stored in `twitch_tokens` DB table |
| Sending | `channel.send(text)` on cached channel object | `broadcaster.send_message(sender=bot_user, message=text)` via `PartialUser` |
| Auth tool | `server_twitch_auth.py` (custom OAuth server) | Deleted — replaced by twitchio's built-in OAuth server on port 4343 |

### Key files changed

- `twitch_api.py` → renamed `twitch_client.py`, complete rewrite as `TwitchClient(twitchio.AutoClient)`
- `server_twitch_auth.py` — deleted
- `main.py` — updated startup: no `loop` param, runs Discord + Twitch concurrently via `asyncio`
- `pyproject.toml` — replaced `requirements.txt`; removed `twitchapi`, upgraded `twitchio`

### DB schema change

```sql
ALTER TABLE twitch_bots ADD COLUMN bot_user_id TEXT;
```

`bot_user_id` is the numeric Twitch user ID of the bot account, required by twitchio 3.x for EventSub chat subscriptions. Columns `api_url`, `api_port`, `auth_token`, `refresh_token` are now obsolete (kept, not dropped).

### Auth flow (one-time setup per environment)

1. Start bot — twitchio's web adapter starts on port 4343.
2. Bot account authorizes via its OAuth URL (logged at startup with required scopes).
3. Each channel owner authorizes via their OAuth URL.
4. Tokens are persisted to the `twitch_tokens` DB table and auto-refreshed.

See `setup.md` for the full setup sequence.
