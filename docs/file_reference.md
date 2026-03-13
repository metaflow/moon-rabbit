# File Reference

> Cross-reference: [Project Overview](overview.md) · [Architecture & Data Flow](architecture.md)

Every file in the repository, grouped by role. Each entry describes purpose, key exports, and cross-references.

---

## Core Application

### [main.py](file:///home/gem/src/moon-rabbit/main.py) — Entry Point
**Role:** Bootstrap, CLI parsing, Jinja2 setup

- Parses CLI arguments (`--discord`, `--twitch`, `--log`, `--profile`, etc.)
- Initializes `DB` (PostgreSQL connection via `DB_CONNECTION` env var)
- Registers all Jinja2 template globals (`txt`, `get`, `set`, `randint`, `dt`, `timestamp`, `message`, `category_size`, `list_category`, `delete_category`)
- Creates the async event loop and starts platform clients
- Launches background tasks: `expireVariables()` (5-min cycle) and `cron()` (configurable)

**Key functions:**
| Function | Purpose |
|---|---|
| `render_text_item()` | Jinja2 global `txt()` — resolves tag queries, picks random text, optionally inflects |
| `get_variable()` | Jinja2 global `get()` |
| `set_variable()` | Jinja2 global `set()` |
| `get_variables_category_size()` | Jinja2 global `category_size()` |
| `delete_category()` | Jinja2 global `delete_category()` |
| `list_category()` | Jinja2 global `list_category()` |
| `discord_or_twitch()` | Jinja2 global `dt()` |
| `new_message()` | Jinja2 global `message()` — queues additional actions |
| `expireVariables()` | Background: expire variables + stale queries every 5 min |
| `cron()` | Background: calls `client.on_cron()` periodically |
| `main()` | CLI entry point |

**Depends on:** `data`, `storage`, `commands`, `discord_client`, `twitch_api`

---

### [data.py](file:///home/gem/src/moon-rabbit/data.py) — Shared Data Types
**Role:** Core data structures, enums, and the Jinja2 environment

- Defines `ActionKind` enum: `NOOP`, `REPLY`, `NEW_MESSAGE`, `PRIVATE_MESSAGE`, `REACT_EMOJI`
- Defines `Action` dataclass (kind + text + optional attachment)
- Defines `EventType` enum: `message`, `twitch_reward_redemption`, `twitch_hype_train`
- Defines `CommandData` dataclass (pattern, event_type, actions, mod flag, hidden flag, help text)
- Defines `Message` dataclass — the unified message object passed through the pipeline
- Defines `InvocationLog` — per-request log collector with prefix
- Provides `Lazy` class — a lazily-evaluated string that supports "sticky" (compute once) or "non-sticky" (recompute each access) modes
- Hosts the shared `SandboxedEnvironment` (`templates`) and `render()` function
- `dictToCommandData()` — deserializes JSON dicts to `CommandData` via `dacite`

**Imported by:** every other module via `from data import *`

---

### [commands.py](file:///home/gem/src/moon-rabbit/commands.py) — Command Registry & Processing
**Role:** All command logic, message processing pipeline (largest file in the project)

**Central function:** `process_message(msg: Message) → List[Action]`
- Iterates through all commands (built-in + persistent)
- Checks permissions (mod, platform, event type)
- Runs each command; a command returns `(actions, continue_flag)`
- Appends any `additionalActions` from template-side `message()` calls

**Built-in Command Classes:**

| Class | Chat Trigger | Purpose |
|---|---|---|
| `HelpCommand` | `+help` / `+commands` | Lists available commands; shows detailed help for specific command |
| `Eval` | `+eval` | Evaluate a Jinja2 expression and return the result |
| `SetCommand` | `+command` | Create/update/delete a persistent command (JSON or plain text) |
| `SetPrefix` | `+prefix-set` | Change the command prefix for current platform |
| `TextSet` | `+add` | Add or update a text entry (CSV-like syntax: `text;id;tags`) |
| `TextNew` | `+new` | Auto-analyze text morphology and print the `+add` command |
| `TextSetNew` | `+setnew` | Like `+new` but immediately inserts the text |
| `TextDescribe` | `+describe` | Show full info about a text by ID |
| `TextSearch` | `+search` | Search texts by substring and optional tag query |
| `TextRemove` | `+rm` | Delete a text by ID or unique substring match |
| `TextUpload` | `+upload` | Bulk import texts from an attached CSV file (Discord only) |
| `TextDownload` | `+download` | Export texts to CSV file (Discord only) |
| `TagList` | `+tags` | List all tags with their IDs |
| `TagDelete` | `+tag-rm` | Delete a tag by ID or name |
| `Multiline` | `+multiline` | Execute multiple commands from one message (newline-separated) |
| `Debug` | `+debug` | View recent logs or get JSON of a command (private message only) |
| `InvalidateCache` | `+invalidate_cache` | Clear the commands cache |

**`PersistentCommand`**: Wraps a `CommandData` from DB. Compiles regex pattern, renders action templates via Jinja2 on match.

**Key helper functions:**
- `command_prefix()` — checks if message starts with prefix+keyword, returns remainder
- `get_commands()` — builds and caches the command list for a channel
- `import_text_row()` — imports a single text row with tags (used by `TextSet` and `TextUpload`)
- `str_to_tags()` / `tag_values_to_str()` — serialize/deserialize tag dicts
- `text_to_row()` — format a text entry as CSV row
- `morph_text()` — auto-generate morphological inflections for a text

**Depends on:** `data`, `storage`, `query`, `words`

---

## Platform Clients

### [discord_client.py](file:///home/gem/src/moon-rabbit/discord_client.py) — Discord Integration
**Role:** Discord event handling, banner generation

**`DiscordClient(discord.Client)`:**
- `on_message()` — Main message handler. Resolves guild → channel_id, checks permissions, builds lazy variables dict, calls `commands.process_message()`, dispatches actions (reply, new message, private message, emoji reaction)
- `on_cron()` — Banner update. For guilds with `BANNER` feature, renders banner template, downloads base image, overlays text with Pillow, uploads as guild banner
- Tracks `active_users` per channel via TTLDict (2h TTL) for `random_mention`
- Manages `allowed_channels` per channel — bot only responds in explicitly allowed Discord channels (or all if none set)
- Supports `+allow_here` / `+disallow_here` commands (handled directly, not via command pipeline)
- Moderators who message in a guild can later DM the bot for private mod commands

**Helper functions:**
- `discord_literal()` — normalizes `<@!id>` to `<@id>`
- `download_file()` — downloads URL to local file (SHA1-hashed filename), with caching

**Depends on:** `data`, `storage`, `commands`, `Pillow`, `ttldict2`

---

### [twitch_api.py](file:///home/gem/src/moon-rabbit/twitch_api.py) — Twitch Integration
**Role:** Twitch chat + EventSub (redemptions, hype trains) via twitchio 3.x

**`Twitch3(twitchio.Client)`:**
- Constructor reads `api_app_id`, `api_app_secret`, `bot_user_id` from `twitch_bots` table; loads per-channel config from `channels` table
- `setup_hook()` — called by twitchio after login. Resolves broadcaster user IDs via `fetch_users()`, then calls `multi_subscribe()` to create EventSub WebSocket subscriptions:
  - `ChatMessageSubscription` — for all channels (chat messages)
  - `ChannelPointsCustomRewardRedemptionAddSubscription` — if `twitch_reward_redemption` in `twitch_events`
  - `HypeTrainEndSubscription` — if `twitch_hype_train` in `twitch_events`
- `event_ready()` — logs login; on `--dev`, sends smoke-test message to all channels
- `event_message(payload: ChatMessage)` — main message handler. Resolves channel via `payload.broadcaster.name`, skips bot's own messages, applies per-user throttle, builds lazy variables, calls `commands.process_message()`
- `event_channel_points_redemption_add(payload)` — handles channel point redemptions; builds Message with `event=twitch_reward_redemption`
- `event_channel_hype_train_end(payload)` — handles hype train end; builds Message with `event=twitch_hype_train`
- `event_token_refreshed` / `event_oauth_authorized` — diagnostic logging for auth lifecycle
- `on_cron()` — sends synthetic `<prefix>_cron` to active channels (within 30 min)
- `send_message()` — sends via `PartialUser.send_message(sender=bot_user_id, message=text)`, rate-limited (1 msg/sec), truncates to 500 chars

**Auth:** twitchio 3.x runs a built-in OAuth server on port 4343 (no public URL needed). On first run, the bot account and each channel owner visit OAuth URLs. Tokens auto-refresh and persist to the PostgreSQL `twitch_tokens` table via overrides in `Twitch3`. See `setup.md` for details.

**Per-channel state (`ChannelInfo`):**
- `active_users` — TTLDict (1h TTL) of recent chatters
- `throttled_users` — TTLDict to rate-limit non-mod users
- `last_activity` — timestamp of last message (used by cron)
- `twitch_user_id` — resolved at `setup_hook()` time

**Depends on:** `data`, `storage`, `commands`, `twitchio 3.x`, `ttldict2`, `asyncio-throttle`

---

## Data & Query Layer

### [storage.py](file:///home/gem/src/moon-rabbit/storage.py) — Database Abstraction
**Role:** All PostgreSQL operations, in-memory caching

**`DB` class** — Singleton-ish (set via `set_db()`, accessed via `db()`):

| Method Group | Methods | Purpose |
|---|---|---|
| **Channel mgmt** | `discord_channel_info()`, `twitch_channel_info()`, `new_channel_id()` | Resolve platform IDs to internal `channel_id`; auto-create new channels |
| **Tags** | `add_tag()`, `delete_tag()`, `tag_by_id()`, `tag_by_value()`, `reload_tags()` | CRUD for tags, bidirectional lookup |
| **Texts** | `add_text()`, `set_text()`, `get_text()`, `find_text()`, `delete_text()`, `all_texts()`, `text_search()` | CRUD for text fragments |
| **Text-Tag links** | `get_text_tags()`, `get_text_tag_values()`, `get_text_tag_value()`, `set_text_tags()` | Manage tag associations on texts |
| **Random selection** | `get_random_text_id()` | Core algorithm: Pareto-biased pick from per-query queues |
| **Commands** | `get_commands()`, `set_command()` | Load/save persistent commands |
| **Variables** | `get_variable()`, `set_variable()`, `count_variables_in_category()`, `list_variables()`, `delete_category()`, `expire_variables()` | TTL key-value store |
| **Logs** | `add_log()`, `get_logs()` | In-memory log ring buffer (10 entries per channel) |
| **Prefix** | `set_twitch_prefix()`, `set_discord_prefix()` | Update command prefixes |
| **Allowed channels** | `get_discord_allowed_channels()`, `set_discord_allowed_channels()` | Channel allowlisting |
| **Cache expiry** | `expire_old_queries()` | Remove stale query queues |
| **Twitch Tokens** | `add_token()`, `load_twitch_tokens()` | Persist and recover TwitchIO OAuth credentials |
| **Health check** | `check_database()` | Log all channels on startup |

**Module-level helpers:** `set_db()`, `db()`, `cursor()`

**Depends on:** `data`, `query`, `psycopg2`, `llist`, `numpy`, `ttldict2`, `lark`

---

### [query.py](file:///home/gem/src/moon-rabbit/query.py) — Tag Query Parser
**Role:** Parse and evaluate boolean tag queries

- Defines Lark grammar for tag queries (`and`, `or`, `not`, parentheses)
- `parse_query()` — parse query string, normalize tag names to IDs
- `match_tags()` — evaluate parsed tree against a set of tag IDs → bool
- `good_tag_name()` — validates tag names (rejects reserved words and invalid chars)

**Depends on:** `lark`

---

## Russian Morphology

### [words.py](file:///home/gem/src/moon-rabbit/words.py) — Morphological Analysis
**Role:** Russian word inflection, morph tag definitions

- Creates `pymorphy3.MorphAnalyzer(lang='ru')` as the shared `morph` instance
- `morph_tags` dict — maps internal tag names (e.g. `_NOUN`, `_masc`) to pymorphy3 grammemes
- `case_tags` list — Russian case abbreviations used for inflection
- `inflect_word()` — inflects a word to a target case, with optional tag filtering for disambiguation

**Depends on:** `pymorphy3`

---

### [word_processing.py](file:///home/gem/src/moon-rabbit/word_processing.py) — Batch Word Analysis Tool
**Role:** Standalone CLI tool for analyzing words from a TSV file

- Reads words from a file, runs morphological analysis, generates inflection tables
- Outputs to a TSV file with suggested tags and inflected forms
- **Not part of the bot runtime** — a development/data-preparation utility

**Usage:** `python word_processing.py input.tsv output.tsv`

**Depends on:** `data`, `storage`, `words`, `query`, `pymorphy3`

---

---

### [restart.sh](file:///home/gem/src/moon-rabbit/restart.sh) — Process Manager

- Kills all existing bot processes (by virtualenv path pattern)
- Starts Discord and Twitch bots as background processes
- Records restart timestamp

---

### [pg_backup.sh](file:///home/gem/src/moon-rabbit/pg_backup.sh) — Database Backup

- Creates gzipped PostgreSQL dump to `/mnt/backup/`
- Prunes backups older than 14 days

---

## Configuration & Schema

### [.env](file:///home/gem/src/moon-rabbit/.env) — Environment Variables
Contains `DB_CONNECTION`, `DISCORD_TOKEN`, `TWITCH_ACCESS_TOKEN`, `TWITCH_API_APP_ID`, `TWITCH_API_APP_SECRET`. Both dev and prod values (prod commented out).

### [schema_backup.sql](file:///home/gem/src/moon-rabbit/schema_backup.sql) — Database Schema
Full PostgreSQL schema dump. See [architecture.md#database-schema](architecture.md#database-schema) for diagram.

### [Pipfile](file:///home/gem/src/moon-rabbit/Pipfile) — Python Dependencies
Exact version pins for all dependencies. See [overview.md#dependencies](overview.md#dependencies) for table.

### [setup.md](file:///home/gem/src/moon-rabbit/setup.md) — Server Setup Guide
Step-by-step instructions for deploying to a new DigitalOcean droplet.

### [playbooks.md](file:///home/gem/src/moon-rabbit/playbooks.md) — Operational Runbook
Currently just one entry: how to restart the bot remotely via SSH.

---

## Static Assets

| File | Purpose |
|---|---|
| `arial.ttf` | Font used by Pillow for Discord banner text overlays |
| `permissions.png` | Screenshot of Discord permission settings (documentation) |

---

## Cross-Reference: Import Graph

```
main.py
├── data (*)
├── storage (DB, db, set_db, cursor)
├── commands
├── discord_client (DiscordClient, discord_literal)
├── twitch_api
└── words (implicitly through txt() → storage → query)

commands.py
├── data (*)
├── storage (cursor, db)
├── query
└── words

discord_client.py
├── data (*)
├── storage (db)
├── commands
└── Pillow

twitch_api.py
├── data (*)
├── storage (cursor, db)
├── commands
└── twitchio (3.x — chat + EventSub)

storage.py
├── data (*)
├── query
├── psycopg2
├── llist
├── numpy
└── ttldict2

query.py
└── lark

words.py
└── pymorphy3

word_processing.py (standalone)
├── data, storage, words, query, pymorphy3

```
