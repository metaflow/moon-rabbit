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

*(To be filled in as investigation progresses)*

### Resolution

*(To be filled in)*

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

- [ ] Provision new droplet
- [ ] Install dependencies (Python, pipenv, PostgreSQL, etc.)
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

## Timeline

| Date | What |
|---|---|
| 2026-03-08 | Created this log. Starting Twitch auth investigation. |
