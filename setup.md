# installing discord and twitch bots on a new droplet

connecting

ssh -i <key> root@<ip>

apt update
apt upgrade

# clone repo
cd /var
git clone https://github.com/metaflow/moon-rabbit.git

# install postgres and uv
apt update
apt install postgresql postgresql-contrib
apt install libpq-dev python3-dev # for psycopg2 python package
curl -LsSf https://astral.sh/uv/install.sh | sh

check that uv is available e.g. add to .bashrc

# create a backup volume

mkdir -p /mnt/backup
# ... instructions from digital ocean to mount a volume to /mnt/backup

# restoring backup

mkdir -p /mnt/backup
sudo -u postgres pg_dump rabbit --schema-only --no-owner --no-privilege -F p > scheme.sql
sudo -u postgres pg_dump rabbit --data-only --no-owner --no-privilege -F c > backup.dump

# setup database

sudo -u postgres dropdb --if-exists chatbot
sudo -u postgres psql
CREATE USER bot WITH PASSWORD '*****';
CREATE DATABASE chatbot OWNER bot;
GRANT ALL PRIVILEGES ON DATABASE chatbot TO bot;
\q

## from a full backup

create a full backup

sudo -u postgres pg_dump rabbit --no-owner --no-privilege --no-acl --column-inserts | gzip > backup.sql.gz

restore frome full backup on destination server

gunzip backup.sql.gz
sudo -u postgres psql chatbot < backup.sql

# Reassign ownership of all user-created tables and sequences to bot:
sudo -u postgres psql chatbot -t -A -c "
  SELECT format('ALTER TABLE %I OWNER TO bot;', tablename) FROM pg_tables WHERE schemaname='public'
  UNION ALL
  SELECT format('ALTER SEQUENCE %I OWNER TO bot;', sequence_name) FROM information_schema.sequences WHERE sequence_schema='public'
" | sudo -u postgres psql chatbot

# test that bot can connect to db

create file in /var/moon-rabbit/.env

DB_CONNECTION="dbname=chatbot user=bot password=***** host=localhost"
DISCORD_TOKEN=*****

Use a token to dev discord first and only run for discord.

> uv run python3 main.py --discord --log_level INFO --log discord --also_log_to_stdout
 uv run python3 main.py --twitch moon_robot --log_level INFO --log moon_robot --also_log_to_stdout

now change discord to normal token.
make runtime directory and copy scripts there

> cd /var/moon-rabbit
> mkdir -p runtime
> cp restart.sh ./runtime
> cp pg_backup.sh ./runtime

update pg_backup.sh with correct credentials
run ./pg_backup.sh and check if database backup looks OK

update restart.sh if needed
run restart.sh and check output of ./runtime/*_stdout files

update crontab with new entries:

> crontab -e

*/5 * * * * /var/moon-rabbit/runtime/pg_backup.sh
*/5 * * * * /var/moon-rabbit/runtime/restart.sh

that wil restart and create backup every 5 minutes - to check if it really works.
Wait for 10 minutes and then check
> cat /var/moon-rabbit/runtime/restart_date.txt
> ls -al /mnt/backup/

then update crontab to make it more rate

2 5 * * * /var/moon-rabbit/runtime/pg_backup.sh
4 */3 * * * /var/moon-rabbit/runtime/restart.sh

# how to work with DB

sudo -u postgres psql
/c chatbot
/dt

---

# Local Development Setup (PRELIMINARY)

> **Not yet tested.** These steps are a best-effort guide for setting up a local dev environment. Update this section once verified.

## 1. Install PostgreSQL

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib libpq-dev python3-dev

# macOS (Homebrew)
brew install postgresql@16
brew services start postgresql@16
```

## 2. Create local database

```bash
sudo -u postgres psql
```

```sql
CREATE USER bot WITH PASSWORD 'bot';
CREATE DATABASE chatbot OWNER bot;
GRANT ALL PRIVILEGES ON DATABASE chatbot TO bot;
\q
```

## 3. Import production data

Get a full backup from the production server:

```bash
# On production server:
sudo -u postgres pg_dump rabbit --no-owner --no-privilege --no-acl --column-inserts | gzip > backup.sql.gz

# Copy to dev machine, then:
gunzip backup.sql.gz
sudo -u postgres psql chatbot < backup.sql
```

# Reassign ownership of all user-created tables and sequences to bot:
sudo -u postgres psql chatbot -t -A -c "
  SELECT format('ALTER TABLE %I OWNER TO bot;', tablename) FROM pg_tables WHERE schemaname='public'
  UNION ALL
  SELECT format('ALTER SEQUENCE %I OWNER TO bot;', sequence_name) FROM information_schema.sequences WHERE sequence_schema='public'
" | sudo -u postgres psql chatbot

## 4. Register a Discord bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**, give it a name
3. Go to **Bot** → click **Reset Token** → copy the token (this is your `DISCORD_TOKEN`)
4. Under **Privileged Gateway Intents**, enable **all three** (Presence, Server Members, Message Content)
5. Go to **OAuth2** → **URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Read Message History`, `Add Reactions`, `Manage Guild` (for banner), `View Channels`
6. Open the generated URL to invite the bot to your test server

## 5. Add a Discord channel

Enable **Developer Mode** in Discord: **Settings → Advanced → Developer Mode**. Then:
- **Guild (server) ID**: Right-click the server name → **Copy Server ID**
- **Channel ID**: Right-click a channel → **Copy Channel ID**
- **User ID**: Right-click a user → **Copy User ID**

The bot uses `discord_guild_id` in the `channels` table to identify which server it's serving. On first message in a new guild, it auto-creates a channel entry — no manual DB insert needed.

> `discord_allowed_channels` controls which Discord channels the bot listens in. If empty (the default), the bot responds in **all** channels. You can restrict it later with the `allow_here` / `disallow_here` commands.

## 6. Register a Twitch bot

1. Go to [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)
2. Click **Register Your Application**:
   - Name: anything unique
   - OAuth Redirect URL: `http://localhost:4343` ← must be exactly this (twitchio's built-in OAuth server)
   - Category: Chat Bot
3. Copy the **Client ID** (`api_app_id`) and generate a **Client Secret** (`api_app_secret`)
4. **Important**: The bot account must be modded in your dev channel: `/mod <bot_username>`

### Get the bot's numeric user ID (`bot_user_id`)

You need the numeric Twitch user ID of the bot account — not its username. Get an app access token first:

```bash
curl -X POST "https://id.twitch.tv/oauth2/token" \
  -d "client_id=<api_app_id>&client_secret=<api_app_secret>&grant_type=client_credentials" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"
```

Then look up the bot account by its Twitch login name:

```bash
curl -H "Authorization: Bearer <access_token_from_above>" \
     -H "Client-Id: <api_app_id>" \
     "https://api.twitch.tv/helix/users?login=<bot_username>" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])"
```

This prints the numeric ID (e.g. `123456789`) — that's `bot_user_id`.

### Insert bot credentials into DB

```sql
sudo -u postgres psql chatbot

INSERT INTO twitch_bots (channel_name, api_app_id, api_app_secret, bot_user_id)
VALUES ('<bot_username>', '<client_id>', '<client_secret>', '<numeric_bot_user_id>');
```

### First-time OAuth (one-time, after bot starts)

twitchio 3.x manages tokens itself via a built-in OAuth server on port 4343. After starting the bot:

1. **Bot account** — open this URL in a browser **while logged in as the bot Twitch account**:
   ```
   http://localhost:4343/oauth?scopes=user:read:chat+user:write:chat+user:bot&force_verify=true
   ```
2. **Channel owner** — open this URL **while logged in as the channel owner**:
   ```
   http://localhost:4343/oauth?scopes=channel:bot+channel:read:redemptions+channel:read:hype_train&force_verify=true
   ```

Tokens are saved automatically to `.tio.tokens.json` in the project root. Subsequent restarts reuse them — no repeat auth needed unless tokens are deleted.

## 7. Add a Twitch channel

Your Twitch channel name is your Twitch username (lowercase), visible in the URL: `twitch.tv/<channel_name>`.

The bot looks up channels by the `twitch_bot` column in the `channels` table. On first message, it auto-creates a channel entry — no manual DB insert needed.

## 8. Patch channel data for dev

If you imported production data, update the channel and bot entries to point at your dev accounts:

```sql
sudo -u postgres psql chatbot

-- Update Discord guild ID to your test server
UPDATE channels SET discord_guild_id = '<your_guild_id>' WHERE discord_guild_id = '<prod_guild_id>';

-- Update Twitch channel name to your dev channel
UPDATE channels SET twitch_channel_name = '<your_twitch_channel>' WHERE twitch_channel_name = '<prod_channel>';

-- Update Twitch bot credentials (get bot_user_id via the curl commands in section 6)
UPDATE twitch_bots SET
  api_app_id = '<your_client_id>',
  api_app_secret = '<your_client_secret>',
  bot_user_id = '<numeric_bot_user_id>'
WHERE channel_name = 'moon_robot';
```

> If you're starting with a fresh database (no import), skip this step — the bot auto-creates channel entries on the first message it receives.

## 9. Set up `.env`

Create (or update) `.env` in the project root:

```bash
DB_CONNECTION="dbname=chatbot user=bot password=bot host=localhost"
DISCORD_TOKEN=<your_discord_bot_token>
```

Source it before running:

```bash
source .env
```

## 10. Install dependencies

```bash
uv venv
uv pip install -r requirements.txt
```

For development (type checking with mypy, formatting with black):

```bash
uv pip install -r requirements-dev.txt
```

## 11. Run in dev mode

```bash
uv run python3 main.py --dev --discord --log dev --also_log_to_stdout
uv run python3 main.py --dev --twitch moon_robot --log dev --also_log_to_stdout
```

The `--dev` flag sends a smoke-test message to all connected channels on startup, confirming the bot is alive and can reach the chat APIs.