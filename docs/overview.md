# Moon-Rabbit Project Overview

A **multi-platform chatbot** that runs simultaneously on **Discord** and **Twitch**. It serves as an interactive, stateful bot where server/channel moderators define custom commands via chat. Commands use **Jinja2 templates** and can query a database of tagged text fragments, making the bot's responses dynamic and community-driven.

> [!NOTE]
> This document provides the top-level overview. See companion docs for details:
> - [Architecture & Data Flow](architecture.md) — component diagram, request lifecycle, database schema
> - [File Reference](file_reference.md) — per-file purpose, key classes/functions, and cross-references

---

## Key Concepts

| Concept | Description |
|---|---|
| **Channel** | A logical unit grouping a Discord guild or Twitch channel. Has its own command prefix, commands, texts, tags, and variables. A single DB `channel_id` may map to one Discord guild AND/OR one Twitch channel. |
| **Command (Persistent)** | A user-defined bot command stored in PostgreSQL as JSON. Has a regex `pattern`, a list of `Action`s (Jinja2 templates), and metadata (mod-only, hidden, discord/twitch toggle). Created/updated via the `+command` chat command. |
| **Command (Built-in)** | Hard-coded Python classes (e.g. `HelpCommand`, `Eval`, `TextSearch`). Always present for moderation and content management. |
| **Text** | A short text fragment stored in `texts` table, scoped to a channel. Texts are tagged and can be randomly selected via tag queries inside Jinja2 templates using `{{ txt('tag-query') }}`. |
| **Tag** | A label attached to a text. Tags are scoped per-channel. Text-tag associations can carry an optional `value` (used for morphological inflections). |
| **Tag Query** | A boolean expression over tags: `"adj and good"`, `"noun or (adj and not bad)"`. Parsed by a Lark grammar in `query.py`. |
| **Variable** | A short-lived key-value pair (per-channel, with optional category and TTL). Used within Jinja2 templates for stateful interactions (e.g. mini-games, counters). |
| **Jinja2 Templating** | All persistent command responses are Jinja2 templates rendered at invocation time. Custom globals like `txt()`, `get()`, `set()`, `randint()`, `dt()` are available. |
| **Russian Morphology** | The bot supports morphological inflection of Russian words via `pymorphy3`. Texts tagged with `morph` can be inflected to different grammatical cases (рд, дт, вн, тв, пр, etc.). |

---

## High-Level Architecture

```
┌──────────────┐     ┌──────────────┐
│   Discord    │     │    Twitch    │
│   (discord)  │     │  (twitchio)  │
└──────┬───────┘     └──────┬───────┘
       │                    │
       ▼                    ▼
  DiscordClient         Twitch3
  (discord_client.py)   (twitch_api.py)
       │                    │
       └────────┬───────────┘
                │
                ▼
        commands.process_message()   ← commands.py
                │
       ┌────────┼──────────┐
       │        │          │
       ▼        ▼          ▼
    Built-in  Persistent  Jinja2
    Commands  Commands    Rendering
                │          │
                └────┬─────┘
                     ▼
              storage.DB (storage.py)
                     │
                     ▼
              PostgreSQL Database
```

---

## Platforms & Event Types

| Platform | Client Class | Events Handled |
|---|---|---|
| **Discord** | `DiscordClient` in `discord_client.py` | Messages, cron (banner updates) |
| **Twitch** | `Twitch3` in `twitch_api.py` | Messages, channel point redemptions, hype train events, cron |

Both platforms share the same command processing pipeline (`commands.process_message()`), text database, and Jinja2 template engine. The `media` variable (`"discord"` or `"twitch"`) allows templates to branch per-platform via `{{ dt('discord text', 'twitch text') }}`.

---

## Entry Point & Startup

[main.py](file:///home/gem/src/moon-rabbit/main.py) is the entry point. The CLI accepts:

| Argument | Purpose |
|---|---|
| `--discord` | Start the Discord bot |
| `--twitch <bot_name>` | Start the Twitch bot (reads config from `twitch_bots` DB table) |
| `--cron_interval_s` | Interval for periodic cron tasks (default: 600s) |
| `--log` | Log file prefix (creates `.debug.log`, `.info.log`, `.errors.log`) |
| `--profile` | Benchmarking mode (loops message processing for 1s) |
| `--dev` | Dev mode: sends a smoke-test message to all channels on connect |

On startup:
1. Connects to PostgreSQL via `DB_CONNECTION` env var
2. Registers Jinja2 template globals (`txt`, `get`, `set`, `randint`, etc.)
3. Creates async event loop
4. Starts Discord and/or Twitch clients
5. Launches background tasks: `expireVariables()` (every 5min) and `cron()` (configurable interval)

---

## Deployment

- Runs on a DigitalOcean droplet at `/var/moon-rabbit`
- Two separate processes: one for Discord (`--discord`), one for Twitch (`--twitch moon_robot`)
- `restart.sh` kills and restarts both processes
- `pg_backup.sh` creates gzipped PostgreSQL dumps to `/mnt/backup`
- Both scripts scheduled via `crontab`
- Detailed setup instructions in [setup.md](file:///home/gem/src/moon-rabbit/setup.md)

---

## Dependencies

All managed via `requirements.txt` (uv). Key libraries:

| Library | Purpose |
|---|---|
| `discord` | Discord API client |
| `twitchio` | Twitch chat + EventSub (messages, channel point redemptions, hype trains) via WebSocket |
| `psycopg2` | PostgreSQL adapter |
| `jinja2` | Template rendering for command responses (sandboxed) |
| `lark` | PEG parser for tag query grammar |
| `pymorphy3` | Russian morphological analyzer |
| `llist` | Doubly-linked list for queue-based random text selection |
| `numpy` | RNG & Pareto distribution for "smart" random selection |
| `ttldict2` | TTL-expiring dictionaries for caching |
| `pillow` | Image manipulation (Discord banner generation) |
| `dacite` | Dataclass deserialization from dicts |
| `asyncio-throttle` | Rate-limiting Twitch message sending |
