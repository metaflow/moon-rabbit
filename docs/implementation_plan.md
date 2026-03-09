# TwitchIO 2.6.0 → 3.x Migration

Migrate from two libraries (`twitchio 2.6.0` for IRC chat + `twitchAPI 3.10.0` for EventSub webhooks) to a single `twitchio 3.x` that handles both chat and events via EventSub. This is a **complete rewrite** of [twitch_api.py](file:///home/gem/src/moon-rabbit/twitch_api.py).

## User Review Required

> [!IMPORTANT]
> **New auth flow requires one-time manual setup.** TwitchIO 3.x runs a built-in OAuth web server (port 4343). After the first run, both the **bot account** and each **channel owner** must visit OAuth URLs in a browser. Tokens are then managed automatically (auto-refresh, persisted to `.tio.tokens.json`). Detailed instructions will be added to [setup.md](file:///home/gem/src/moon-rabbit/setup.md).

> [!WARNING]
> **`twitchAPI` library will be fully removed.** All EventSub functionality (channel point redemptions, hype trains) moves to twitchio 3.x. [server_twitch_auth.py](file:///home/gem/src/moon-rabbit/server_twitch_auth.py) will be **deleted** — twitchio's built-in OAuth server replaces it entirely.

> [!IMPORTANT]
> **DB schema changes.** The `twitch_bots` table needs a new `bot_user_id TEXT` column (required by twitchio 3.x). Columns `api_url`, `api_port`, `auth_token` become obsolete (kept, not dropped).

---

## Key Architecture Changes

| Aspect | Current (v2) | New (v3) |
|---|---|---|
| **Chat** | IRC via `twitchio.Client` | EventSub via `twitchio.Client` |
| **Events** | `twitchAPI.EventSub` webhooks (needs public URL) | twitchio built-in EventSub WebSocket (no public URL) |
| **Libraries** | `twitchio 2.6.0` + `twitchAPI 3.10.0` | `twitchio 3.x` only |
| **Auth** | Manual refresh at startup, tokens in DB | twitchio auto-refresh; tokens in `.tio.tokens.json` |
| **Startup** | `Client(token, loop=loop, initial_channels=[...])` | `Client(client_id, client_secret, bot_id=...)` |
| **Chat join** | `initial_channels` constructor param | `ChatMessageSubscription(broadcaster_user_id=..., user_id=bot_id)` |
| **Sending** | `channel.send(text)` on cached channel | `broadcaster.send_message(sender=bot_user, message=text)` via `PartialUser` |
| **Events** | [event_message(message)](file:///home/gem/src/moon-rabbit/twitch_api.py#181-250), [on_redemption(data: dict)](file:///home/gem/src/moon-rabbit/twitch_api.py#346-405) | [event_message(payload: ChatMessage)](file:///home/gem/src/moon-rabbit/twitch_api.py#181-250), `event_channel_points_redemption_add(payload)` |
| **Auth tool** | Custom [server_twitch_auth.py](file:///home/gem/src/moon-rabbit/server_twitch_auth.py) using twitchAPI | Deleted — twitchio's built-in OAuth server on port 4343 |
| **Python** | Any | ≥ 3.11 (we have 3.12.3 ✓) |

---

## Proposed Changes

### Dependencies

#### [MODIFY] [requirements.txt](file:///home/gem/src/moon-rabbit/requirements.txt)

- Upgrade `twitchio==2.6.0` → `twitchio==3.2.1`
- Remove `twitchapi==3.10.0`
- Remove `requests==2.30.0` (no longer needed for manual token refresh)
- Keep `asyncio-throttle==1.0.2` — only used for send rate-limiting (`Throttler` in [send_message()](file:///home/gem/src/moon-rabbit/twitch_api.py#337-345)); the per-user command spam throttling is independent (`ttldict2.TTLDict` in `throttled_users`)

---

### Database Schema

#### [MODIFY] [schema_backup.sql](file:///home/gem/src/moon-rabbit/schema_backup.sql)

Add `bot_user_id TEXT` to `twitch_bots`. Obsolete columns kept but ignored.

```sql
ALTER TABLE twitch_bots ADD COLUMN bot_user_id TEXT;
-- Populate manually after looking up the bot's Twitch user ID (see setup.md)
```

---

### Twitch Client (complete rewrite)

#### [MODIFY] [twitch_api.py](file:///home/gem/src/moon-rabbit/twitch_api.py)

Complete rewrite. New [Twitch3(twitchio.Client)](file:///home/gem/src/moon-rabbit/twitch_api.py#52-450):

**Constructor:**
- Takes `client_id`, `client_secret`, `bot_id` (from `twitch_bots` table)
- No `loop` param (twitchio 3.x manages its own loop)
- No `initial_channels` — chat subs created via EventSub

**`setup_hook()` (new):** Create EventSub subscriptions per channel:
```python
eventsub.ChatMessageSubscription(broadcaster_user_id=uid, user_id=self.bot_id)
eventsub.ChannelPointsCustomRewardRedemptionAddSubscription(broadcaster_user_id=uid)
eventsub.HypeTrainEndSubscription(broadcaster_user_id=uid)
```

**Event handlers (changed signatures):**
- [event_ready()](file:///home/gem/src/moon-rabbit/twitch_api.py#137-152) — no args
- [event_message(payload: twitchio.ChatMessage)](file:///home/gem/src/moon-rabbit/twitch_api.py#181-250) — uses `payload.broadcaster`, `payload.chatter`, `payload.text`
- `event_channel_points_redemption_add(payload)` — replaces [on_redemption(data: dict)](file:///home/gem/src/moon-rabbit/twitch_api.py#346-405)
- `event_channel_hype_train_end(payload)` — replaces [on_hype_train_ends(data: dict)](file:///home/gem/src/moon-rabbit/twitch_api.py#273-336)
- `event_token_refreshed(payload)` / `event_oauth_authorized(payload)` — diagnostic logging

**Sending messages:**
- `broadcaster.send_message(sender=bot_user, message=text)` via `PartialUser`
- Keep `asyncio-throttle` `Throttler` for 1 msg/sec rate limiting

**Unchanged:**
- [ChannelInfo](file:///home/gem/src/moon-rabbit/twitch_api.py#38-48) dataclass, [Message](file:///home/gem/src/moon-rabbit/data.py#132-145) building, `commands.process_message()` call
- [on_cron()](file:///home/gem/src/moon-rabbit/twitch_api.py#406-450), mention helpers, user throttling via `ttldict2`

---

### Entry Point

#### [MODIFY] [main.py](file:///home/gem/src/moon-rabbit/main.py)

- Remove unused `from twitchio.ext import commands as twitchCommands`
- Adjust Twitch startup: no `loop` param, use twitchio's [start()](file:///home/gem/src/moon-rabbit/server_twitch_auth.py#121-125) method
- Handle running Discord + Twitch concurrently in single `asyncio.run()`

---

### Auth Tool

#### [DELETE] [server_twitch_auth.py](file:///home/gem/src/moon-rabbit/server_twitch_auth.py)

TwitchIO 3.x has a built-in OAuth web server that runs alongside the bot on port 4343. The custom [UserAuthenticator](file:///home/gem/src/moon-rabbit/server_twitch_auth.py#24-191) class and all `twitchAPI` auth helpers are no longer needed.

The one-time auth flow becomes:
1. Start the bot (twitchio's web adapter starts automatically)
2. Bot account visits `http://localhost:4343/oauth?scopes=user:read:chat user:write:chat user:bot&force_verify=true`
3. Channel owner visits `http://localhost:4343/oauth?scopes=channel:bot channel:read:redemptions channel:read:hype_train&force_verify=true`

Will be documented in [setup.md](file:///home/gem/src/moon-rabbit/setup.md).

---

### Documentation Updates

#### [MODIFY] [setup.md](file:///home/gem/src/moon-rabbit/setup.md)

**Section 6 (Register Twitch bot):**
- Replace Twitch CLI token generation with twitchio's built-in OAuth flow
- Add instructions for getting `bot_user_id`:
  - Option A: Use twitchio's `fetch_users()` helper script (from FAQ)
  - Option B: `twitch api get users -q login=<bot_username>` via Twitch CLI
  - Option C: Visit `https://api.twitch.tv/helix/users?login=<bot_username>` with app token
- Add `http://localhost:4343/oauth/callback` as redirect URL in Twitch dev console
- Update `twitch_bots` INSERT to include `bot_user_id`
- Remove manual `auth_token`/`refresh_token` from INSERT (managed by twitchio)

**Section 7 (Add Twitch channel):**
- Add instructions for channel owner OAuth authorization (the port 4343 URL)
- Explain scopes needed per feature (chat, redemptions, hype trains)

#### [MODIFY] [docs/architecture.md](file:///home/gem/src/moon-rabbit/docs/architecture.md)
- Update component diagram: remove `twitchAPI`
- Update DB schema: add `bot_user_id` to `twitch_bots`

#### [MODIFY] [docs/file_reference.md](file:///home/gem/src/moon-rabbit/docs/file_reference.md)
- Update [twitch_api.py](file:///home/gem/src/moon-rabbit/twitch_api.py) section
- Remove [server_twitch_auth.py](file:///home/gem/src/moon-rabbit/server_twitch_auth.py)
- Update dependencies list

#### [MODIFY] [docs/overview.md](file:///home/gem/src/moon-rabbit/docs/overview.md)
- Update dependencies table: remove `twitchapi`

#### [MODIFY] [docs/migration_log.md](file:///home/gem/src/moon-rabbit/docs/migration_log.md)
- Add section "4. TwitchIO 3.x Migration" with findings and checklist

---

### Tests

#### [NEW] [tests/test_twitch_message_building.py](file:///home/gem/src/moon-rabbit/tests/test_twitch_message_building.py)

Unit tests for the non-network parts of [twitch_api.py](file:///home/gem/src/moon-rabbit/twitch_api.py):

- **Message construction**: Verify [Message](file:///home/gem/src/moon-rabbit/data.py#132-145) dataclass is built correctly from EventSub payloads (channel_id, prefix, event type, variables dict)
- **Mention helpers**: Test [mentions()](file:///home/gem/src/moon-rabbit/twitch_api.py#255-260), [random_mention()](file:///home/gem/src/moon-rabbit/twitch_api.py#261-266), [any_mention()](file:///home/gem/src/moon-rabbit/twitch_api.py#251-254) with various inputs
- **User throttling**: Test that `throttled_users` TTLDict correctly blocks rapid messages from same user
- **Cron filtering**: Test that [on_cron()](file:///home/gem/src/moon-rabbit/twitch_api.py#406-450) only processes channels with recent activity (within 30 min)
- **Send message truncation**: Test 500-char truncation logic

These test the pure logic without needing a live Twitch connection or mocking the EventSub layer.

#### [NEW] [tests/test_data.py](file:///home/gem/src/moon-rabbit/tests/test_data.py)

Unit tests for [data.py](file:///home/gem/src/moon-rabbit/data.py):
- [fold_actions()](file:///home/gem/src/moon-rabbit/data.py#98-113) merging logic
- [Lazy](file:///home/gem/src/moon-rabbit/data.py#115-125) evaluation (sticky vs non-sticky)
- [dictToCommandData()](file:///home/gem/src/moon-rabbit/data.py#72-74) deserialization

---

## Order of Operations

1. Schema change — `ALTER TABLE twitch_bots ADD COLUMN bot_user_id TEXT`
2. Dependencies — Update [requirements.txt](file:///home/gem/src/moon-rabbit/requirements.txt), install twitchio 3.x
3. Add tests for [data.py](file:///home/gem/src/moon-rabbit/data.py) (pre-migration — validates existing logic)
4. Rewrite [twitch_api.py](file:///home/gem/src/moon-rabbit/twitch_api.py)
5. Update [main.py](file:///home/gem/src/moon-rabbit/main.py) startup
6. Delete [server_twitch_auth.py](file:///home/gem/src/moon-rabbit/server_twitch_auth.py)
7. Add tests for twitch message building
8. Update [setup.md](file:///home/gem/src/moon-rabbit/setup.md) (auth flow, bot_user_id instructions)
9. Update other docs
10. Manual testing with `--twitch --dev`

---

## Verification Plan

### Automated Tests

```bash
python -m pytest tests/ -v
```

### Manual Verification (user-assisted)

1. `python -c "import twitch_api"` — no import errors
2. `python main.py --twitch moon_robot --dev --also_log_to_stdout` — bot starts, OAuth server on port 4343
3. First-time auth: visit OAuth URLs for bot account and channel owner
4. Chat: send message in dev channel → bot receives and responds
5. Channel point redemption (if available): trigger → bot receives event
6. Cron: wait for interval → [_cron](file:///home/gem/src/moon-rabbit/twitch_api.py#406-450) messages fire
7. Restart bot → verify it reconnects without re-auth (tokens persisted)
