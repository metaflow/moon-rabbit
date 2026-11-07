# Project Log

Dated records of significant changes, migrations, and bug-fix campaigns. Newest first.

---

## 2026-11-07 — Memory investigation: twitchio websocket leak (~1 GB RSS), dependency upgrade

Trigger: the bot used ~1 GB on the a-cmd server (process up 14 days, 996 MB RSS + 522 MB swapped, peak 1.35 GB). A fresh process with all imports and pymorphy loaded is ~88 MB; DB data is tiny (1,853 texts), so this was not a cache problem.

### Root cause — twitchio `Websocket.close()` cancels its own task (upstream bug)

- `Websocket._process_keepalive` (the `_keep_alive_task`) calls `_create_connection_task()` → `close()`, and `close()` calls `self._keep_alive_task.cancel()` — cancelling the very task that is running it. The cancel fires at `await self._socket.close()` as a `CancelledError` (not caught by `except Exception`), so `close()` aborts half-way: socket stays open, `_closing` stays `True` forever, no "Successfully closed" log.
- Every stale-socket trip leaves one extra live Conduit connection while `Client` opens a replacement shard. Evidence in the production logs: 1,000 distinct EventSub session ids alive at once (~380k `session_keepalive` DEBUG lines/hour), Twitch answering new connections with HTTP 429, "needs to close unexpectedly" per day growing from ~15 (Oct 27) to ~3,500 (Nov 6), chat events no longer arriving after the last successful shard association.
- Reproduced locally with a fake EventSub server (`docs/twitchio_websocket_leak_report.md`, MRE). Present in twitchio 3.2.2, 3.3.2 (latest) and upstream `main`; no matching upstream issue found.
- Caveat: the MRE proves the half-aborted `close()`. In production the leaked sockets were additionally still being read, so another leak route may exist; the metrics after deploy (RSS, count of live sessions) will tell.

### Changes

- `twitchio_patches.py` (+ `tests/test_twitchio_patches.py`): shielded-task workaround, applied in `main()`.
- `ecosystem.config.cjs`: launch `.venv/bin/python3` directly (PM2 now sees the real process) and `max_memory_restart: '400M'`. Deploys must now run `uv sync --no-dev --frozen`.
- `main.py`: `twitchio`, `discord.gateway`, `discord.http` loggers capped at INFO (debug log was rotating a 50 MB file hourly, mostly keepalives).
- Dependencies upgraded (`uv lock --upgrade`): twitchio 3.2.2→3.3.2, aiohttp 3.13.5→3.14.5, pillow 12.2→13.0, psycopg2-binary 2.9.11→2.9.13, cachetools, requests, urllib3, etc.; dev: ruff 0.15→0.17, ty 0.0.31→0.0.99, pytest 9.2. discord.py unchanged (2.7.1 current). ruff 0.17 widened its default rule set (~150 new findings), so `pyproject.toml` now pins the previous baseline (`select = ["E4","E7","E9","F"]` + existing `extend-select`).

- `deploy.sh` rewritten for a-cmd: the server dir is not a git checkout (copied with a tar pipe during the migration, no GitHub key on the server), so it now rsyncs the committed files (`--checksum`, `--delete`, protecting `.env`/`.git`/`runtime`/`.venv`), runs `uv sync --no-dev --locked`, smoke-tests imports, re-registers the PM2 app (`delete` + `start` + `save`, because `pm2 restart` keeps the old script/args and the PM2 dump still held the `uv run` command) and verifies the process. Added `--dry-run`. `ecosystem.config.cjs` also got `kill_timeout: 15000`. The first deploy will also delete two stale hash-named PNG files in the server root (legacy image cache; current code uses `runtime/img/`).

### Not done / follow-ups

- Minor, not memory-critical: `commands.messages` TTLDict is only pruned when a template calls `message()`; `lru_cache` on `DB.discord_channel_info`/`twitch_channel_info` includes a fresh cursor in the key, so it never hits and pins up to 1,000 cursors.
- Upstream report is a draft, not filed.

---

## 2026-05-01 — Error suppression: shutdown task noise + Discord reconnect storm

Source: `/var/moon-rabbit/runtime/merged.errors.log` (2026-04-20 to 2026-05-01, ~36 post-cutoff ERROR entries)

### E1. "Task was destroyed but it is pending!" at shutdown — SUPPRESSED

**Symptoms**
- 14 ERROR entries at `2026-04-20 14:36:58` during app restart
- Twitchio internal websocket tasks (`Websocket._process_keepalive`, `Websocket._listen`) and our own background tasks (`cron()`, `expireVariables()`, `Client.start()`) all logged at ERROR

**Root cause**: asyncio emits this via the exception handler when a Task object is garbage-collected while still in `PENDING` state. Occurs because `loop.close()` is called before cancellation of all tasks fully propagates. Always harmless at shutdown — all tasks have already been asked to cancel by `shutdown()`.

**Fix**: Extended `_twitchio_exception_handler` in `main.py` to intercept `"Task was destroyed but it is pending!"` messages and log them at WARNING instead, with the task repr for diagnostics.

---

### E2. Discord reconnect storm — SUPPRESSED

**Symptoms**
- 3 occurrences on 2026-04-28: `WSServerHandshakeError: 520` (Cloudflare/Discord-side)
- ~15 occurrences on 2026-05-01 00:44–05:29: `ClientConnectionResetError: Cannot write to closing transport` and `TimeoutError` (wrapping `CancelledError`) during reconnect attempts

**Root cause**: Transient Discord gateway outage. discord.py's internal `connect()` loop catches all these exceptions, logs them at ERROR (from inside the library), and retries with exponential backoff. The bot reconnected successfully after ~90 minutes; no data loss or stuck state. Same category as D2 from the 2026-04-20 log.

**Fix**: Added `_DiscordReconnectFilter` (a `logging.Filter` subclass) in `main.py`. Applied to the `discord.client` logger in `setup_logging()`. Downgrades any record with level ERROR whose message starts with `"Attempting a reconnect"` to WARNING before it reaches any handler. The reconnect loop itself and discord.py behaviour are unchanged.

---

### Occurrence Summary

| # | Issue | Occurrences | Status |
|---|-------|-------------|--------|
| E1 | Task destroyed at shutdown | 14 | Suppressed to WARNING |
| E2 | Discord reconnect storm (Apr 28) | 3 | Suppressed to WARNING |
| E2 | Discord reconnect storm (May 1) | ~15 | Suppressed to WARNING |

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
