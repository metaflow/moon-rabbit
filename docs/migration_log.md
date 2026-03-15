# Project Log: Twitch Auth Fix & DigitalOcean Migration

> Running documentation for the current work-in-progress.
> Started: 2026-03-08

---

## Goals

1. **Fix Twitch authentication issues** — the bot's Twitch integration has auth problems that need diagnosis and resolution.
2. **Migrate the running instance** to a new DigitalOcean droplet.

---

## 1. Twitch Auth Investigation

### Current Auth Flow (as-is)

The Twitch bot (`Twitch3` in [twitch_api.py](file:///home/gem/src/moon-rabbit/twitch_api.py)) uses two separate libraries and auth paths:

| Library | Purpose | Auth Method |
|---|---|---|
| `twitchio` | IRC chat (messages, joins) | OAuth token passed to `twitchio.Client.__init__` |
| `twitchAPI` (`Twitch` + `EventSub`) | EventSub (redemptions, hype trains) | App ID + App Secret → `authenticate_app()` |

On startup (`__init__`):
1. Reads `auth_token`, `refresh_token`, `api_app_id`, `api_app_secret` from `twitch_bots` DB table.
2. If `refresh_token` exists, calls `POST https://id.twitch.tv/oauth2/token` with `grant_type=refresh_token`.
3. On success, updates `auth_token` and `refresh_token` in DB.
4. Passes the (refreshed) `auth_token` to `twitchio.Client`.
5. Separately calls `Twitch(app_id, app_secret)` + `authenticate_app()` for EventSub.

### Findings

#### Error log analysis (2023-05-08 to 2026-03-08)

From `moon_robot.errors.log`:
- **115 `Websocket connection was closed: None`** events — twitchio's IRC WebSocket drops
- **10,589 `channel was unable to be joined`** errors — `gg_em` permanently broken since 2023-05-08, failing every 3h on cron
- **`jl_in_july`** triggers `KeyError` storms in twitchio's `_join_future_handle()` — a known twitchio 2.x bug where rapid retry join attempts race and double-pop from `_join_pending`
- Disconnect intervals are irregular (hours to months), ruling out token expiry as the sole cause

#### Root causes identified

1. **No lifecycle hooks** — `event_token_expired`, `event_error`, `event_reconnect` were not implemented. Token expiry killed the connection silently.
2. **Token only refreshed at startup** — Twitch OAuth tokens expire ~4h. After that, any reconnect attempt uses a stale token.
3. **Broken channel re-joins after reconnect** — twitchio auto-reconnects but fails to re-join channels (especially `gg_em`, which appears to be a decommissioned/renamed channel).
4. **twitchAPI EventSub uses deprecated webhook transport** — `twitchAPI 3.x` EventSub webhook class is deprecated; v4 uses WebSocket transport.
5. **`twitchio 2.6.0` is EOL** — v3 has improved reconnect, token management, and moves to EventSub for chat (away from IRC).

#### Debug hooks added (2026-03-08)

Added `[lifecycle]`-prefixed logging hooks to `twitch_api.py`:
- `event_error`, `event_reconnect`, `event_token_expired`, `event_channel_join_failure`, `event_part`
- These log only; they do not fix anything. Purpose is to confirm which failure mode triggers on the next disconnect.

### Resolution

error trace captured by server

2026-03-09 18:14:52,052 WARNING [lifecycle] received RECONNECT from Twitch, reconnecting...
2026-03-09 18:15:03,962 ERROR [lifecycle] failed to join channel: jl_in_july
2026-03-09 18:15:14,867 ERROR [lifecycle] failed to join channel: jl_in_july
2026-03-09 18:15:33,071 ERROR Task exception was never retrieved
future: <Task finished name='Task-6741' coro=<WSConnection._join_future_handle() done, defined at /r
Traceback (most recent call last):
  File "/usr/lib/python3.10/asyncio/tasks.py", line 456, in wait_for
    return fut.result()
asyncio.exceptions.CancelledError

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/root/.local/share/virtualenvs/moon-rabbit-T0OZLLxu/lib/python3.10/site-packages/twitchio/we
    await asyncio.wait_for(fut, timeout=timeout)
  File "/usr/lib/python3.10/asyncio/tasks.py", line 458, in wait_for
    raise exceptions.TimeoutError() from exc
asyncio.exceptions.TimeoutError

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/root/.local/share/virtualenvs/moon-rabbit-T0OZLLxu/lib/python3.10/site-packages/twitchio/we
    self._join_pending.pop(channel)
│KeyError: 'jl_in_july'

Viewed migration_log.md:1-253

```ai(gemini)
The exception trace is a symptom of a well-known race condition bug in `twitchio` 2.x's IRC websocket client, specifically inside `WSConnection._join_future_handle()`.

**What is happening:**
1. Twitch sends a standard `RECONNECT` command, forcing the bot's IRC websocket to disconnect.
2. `twitchio` attempts to auto-reconnect and simultaneously re-join the active channels.
3. The join attempt for the channel `jl_in_july` times out (as seen by the `asyncio.exceptions.TimeoutError`).
4. The exception handler catches the timeout and attempts to clean up the pending join by running `self._join_pending.pop(channel)`. However, multiple retry tasks are racing, and the key has already been popped. This triggers the unhandled `KeyError: 'jl_in_july'` and permanently breaks the bot's connection state.

**Has migrating to TwitchIO 3 addressed this?**
Yes, migrating to version 3 definitively fixes this issue for several reasons:

1. **Abandoning IRC for Chat:** TwitchIO 3.x completely drops the old IRC chat protocol in favor of Twitch's new EventSub WebSockets. The bot now subscribes to chat messages via `ChatMessageSubscription` rather than explicitly "joining" IRC channels. The entire `_join_pending` queue and the code that caused the race condition no longer exist in the stack you are using.
2. **Proper Token Management:** In your previous setup, tokens were only refreshed at startup. When the 4-hour Twitch OAuth token expired, Twitch would forcibly disconnect you, which heavily contributed to the `RECONNECT` loops that triggered the crashing bug. TwitchIO 3 now handles token refreshing automatically in the background, minimizing required reconnects.
3. **Resilient Reconnects:** EventSub WebSocket reconnects are robustly handled by the new library in the event of dropped connections without fatal unhandled exceptions.
```

### Next Steps (library upgrades)

- [x] Setup running instance on a developer machine.
- [x] add context7 as mcp
- [x] review claude changes
- [x] Upgrade `twitchio` from `2.6.0` → latest stable (3.x) — better token management, auto-reconnect, EventSub-based chat
- [x] delete twitchapi
- [x] how to turn the bot "off" properly?
- [x] reexport schema_backup
- [x] setup linter checks
- [ ] check if any other lib updates are necessary
- [ ] setup review prompt
- [ ] overall code review and health
- [ ] any burning todo items?

---

## 2. DigitalOcean Migration

### Current Setup

- Running on a DigitalOcean droplet at `/var/moon-rabbit`
- Two processes: Discord (`--discord`) and Twitch (`--twitch moon_robot`)
- `restart.sh` manages process lifecycle
- `pg_backup.sh` backs up PostgreSQL to `/mnt/backup`
- Both scripts run via crontab
- Detailed setup: [setup.md](file:///home/gem/src/moon-rabbit/setup.md)

### Migration Plan

- [x] Install remote editor
- [x] Install dependencies (Python, uv, PostgreSQL, etc.)
- [x] do I need a separate mount disk for backups - no it's better to backup / snaphot the whole thing
- [x] drop old rabbit databas for clarity and create a new one
- [x] Transfer PostgreSQL data from dev machine
- [ ] setup auth nginx redirect
    set up https://moon-robot.tative.net/oauth/callback
- [x] confirm that no changes from old db are missing
- [x] Clone repo to `/var/moon-rabbit`
- [x] Configure environment (`DB_CONNECTION`, etc.)
- [x] Update DNS / IP references if any
- [ ] move from using twitch settings from the database - just load them from the .env
- [ ] test oauth
- [ ] drop auth entities
- [ ] check that bot cannot connect to my twitch, authenticate and check that now it does
- [ ] update twitch channel to july_in_july
- [ ] shut down old bot
- [ ] start a new one and ask to authenticate
- [ ] dump old database and compare with the new - are there any mismatches?
- [ ] review documentation, most of setup.md should go to readme
- [ ] Set up pm2 for backup and bot itself
- [ ] after a week - drop old droplet


### Notes

*(To be filled in as migration progresses)*

---

## 3. Local Dev Environment

> **PRELIMINARY** — steps documented but not yet tested end-to-end.

Setting up a local dev environment to do all migrations and new functionality locally before deploying.

### Checklist

- [ ] Install PostgreSQL locally
- [ ] Create local `chatbot` database and `bot` user
- [ ] Import production DB dump
- [ ] Patch `channels` table with dev Discord guild ID and Twitch channel name
- [ ] Patch `twitch_bots` table with dev API keys and OAuth tokens
- [ ] Obtain Discord API key (dev application + bot token + invite to test server)
- [ ] Obtain Twitch API keys (client ID/secret + user OAuth token + mod bot in dev channel)
- [ ] Set up `.env` with `DB_CONNECTION` and `DISCORD_TOKEN`
- [ ] `uv pip install -r requirements.txt` — verify all deps install cleanly
- [ ] Implement `--dev` flag in `main.py` (smoke-test message on connect)
- [ ] Test Discord bot connects and sends smoke-test message
- [ ] Test Twitch bot connects and sends smoke-test message

### Dev setup docs

Detailed steps in [setup.md](file:///home/gem/src/moon-rabbit/setup.md) under "Local Development Setup (PRELIMINARY)".

---

## Timeline

| Date | What |
|---|---|
| 2026-03-08 | Created this log. Starting Twitch auth investigation. |
| 2026-03-08 | Analyzed error logs (2023–2026). Identified 5 root causes. Added `[lifecycle]` debug hooks to `twitch_api.py`. |
| 2026-03-08 | Added local dev environment setup documentation to `setup.md` and `migration_log.md`. |
| 2026-03-08 | Disabled crontab `restart.sh` (every 3h) on production to reveal standing connection issues instead of masking them with periodic restarts. |
| 2026-03-08 | Added `python-dotenv` to dependencies and `load_dotenv()` to `main.py` and `server_twitch_auth.py` for reading `.env`. |
| 2026-03-10 | Completed TwitchIO 3.x migration (see section 4 below). |
| 2026-03-12 | Implemented `twitch_tokens` database storage for TwitchIO tokens to replace local `.tio.tokens.json`. |
| 2026-03-14 | Fixed "Unclosed client session" errors by implementing graceful shutdown in `run_loop()` with a 10s timeout. |
| 2026-03-15 | Fixed OAuth `redirect_mismatch` by allowing custom `TWITCH_REDIRECT_URI` in `.env`. |

---

## 4. TwitchIO 3.x Migration

**Goal:** Replace `twitchio 2.6.0` + `twitchAPI 3.10.0` with `twitchio 3.2.1` only. Eliminates the need for a public webhook URL; EventSub runs over WebSocket.

### Changes Made

| File | Change |
|---|---|
| `requirements.txt` | `twitchio==3.2.1`, removed `twitchapi`, removed `requests` |
| `schema_backup.sql` | Added `bot_user_id TEXT` to `twitch_bots`; old columns kept, marked obsolete |
| `twitch_api.py` | **Complete rewrite** — see below |
| `main.py` | Removed unused `twitchCommands` import; `Twitch3` no longer takes `loop` param; use `t.start()` |
| `server_twitch_auth.py` | **Deleted** — replaced by twitchio's built-in OAuth server (port 4343) |
| `tests/test_data.py` | **New** — unit tests for `data.py` pure logic |
| `tests/test_twitch_message_building.py` | **New** — unit tests for `twitch_api.py` logic (no live connection) |
| `tests/conftest.py` | **New** — adds project root to `sys.path` for test imports |
| `docs/overview.md` | Updated dependencies table |
| `docs/architecture.md` | Updated component diagram + DB schema |
| `docs/file_reference.md` | Updated `twitch_api.py` section, removed `server_twitch_auth.py` |

### twitch_api.py Rewrite Summary

- `Twitch3.__init__` now takes `client_id`, `client_secret`, `bot_id` (from `bot_user_id` column); no `loop` param
- `setup_hook()` resolves broadcaster IDs via `fetch_users()` and calls `multi_subscribe()` for:
  - `ChatMessageSubscription` (all channels)
  - `ChannelPointsCustomRewardRedemptionAddSubscription` (if `twitch_reward_redemption` in events)
  - `HypeTrainEndSubscription` (if `twitch_hype_train` in events)
- Event handlers renamed/reshaped: `event_message(payload: ChatMessage)`, `event_channel_points_redemption_add(payload)`, `event_channel_hype_train_end(payload)`
- Sending: `PartialUser.send_message(sender=bot_user_id, message=text)` instead of `channel.send()`
- Auth: twitchio-managed; tokens persisted to `.tio.tokens.json`; no manual refresh

### New Auth Flow (one-time setup)

1. Populate `twitch_bots.bot_user_id` with the numeric Twitch user ID of the bot account
2. Start the bot — twitchio's OAuth server starts on `http://localhost:4343`
3. **Bot account** visits: `http://localhost:4343/oauth?scopes=user:read:chat+user:write:chat+user:bot&force_verify=true`
4. **Channel owner** visits: `http://localhost:4343/oauth?scopes=channel:bot+channel:read:redemptions+channel:read:hype_train&force_verify=true`
5. Tokens saved to `.tio.tokens.json` — subsequent restarts reuse them automatically

### DB Migration

Run on live DB before deploying:
```sql
ALTER TABLE twitch_bots ADD COLUMN IF NOT EXISTS bot_user_id TEXT;
-- Then populate:
UPDATE twitch_bots SET bot_user_id = '<numeric_id>' WHERE channel_name = 'moon_robot';

-- Create table for storing TwitchIO OAuth tokens per user
CREATE TABLE IF NOT EXISTS public.twitch_tokens (
    user_id text PRIMARY KEY,
    token text NOT NULL,
    refresh text NOT NULL
);

-- Drop obsolete columns
ALTER TABLE twitch_bots
    DROP COLUMN auth_token,
    DROP COLUMN api_url,
    DROP COLUMN api_port,
    DROP COLUMN refresh_token;
```