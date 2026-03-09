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

### Known Issues / Questions

- [ ] What is the specific auth error? (need to check logs on current droplet)
- [ ] Are the tokens expired and not refreshing correctly?
- [ ] Is the `twitchAPI` / `twitchio` library version outdated relative to Twitch API changes?
- [ ] Has Twitch deprecated or changed any OAuth endpoints or scopes?
- [ ] The EventSub setup uses the old `EventSub` class — Twitch may have migrated to EventSub v2 (WebSocket-based instead of webhook).

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

*(To be filled in after debug hooks confirm the failure mode)*

### Next Steps (library upgrades)

- [x] Setup running instance on a developer machine.
- [ ] Upgrade `twitchio` from `2.6.0` → latest stable (3.x) — better token management, auto-reconnect, EventSub-based chat
- [ ] Upgrade `twitchAPI` from `3.10.0` → `4.x` — EventSub WebSocket transport (no public callback URL needed)
- [ ] Implement proper `event_token_expired` with refresh logic once failure mode is confirmed
- [ ] Consider using PM2 (already available) with `--watch` or `--restart-delay` instead of `restart.sh` crontab for automatic restarts
- [ ] add context7 as mcp

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

- [ ] Install dependencies (Python, uv, PostgreSQL, etc.)
- [ ] Transfer PostgreSQL data (dump from old → restore on new)
- [ ] Clone repo to `/var/moon-rabbit`
- [ ] Configure environment (`DB_CONNECTION`, etc.)
- [ ] Set up crontab (restart.sh, pg_backup.sh)
- [ ] Update DNS / IP references if any
- [ ] Verify bot connects on both platforms
- [ ] Decommission old droplet

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
