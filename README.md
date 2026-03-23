# moon-rabbit

A multi-platform chatbot running simultaneously on **Discord** and **Twitch**. Moderators define custom commands via chat using Jinja2 templates that can query a database of tagged text fragments, making responses dynamic and community-driven.

[Invite moon-rabbit to Discord](https://discord.com/api/oauth2/authorize?client_id=884131362251079730&permissions=515396455488&scope=bot)

## Documentation

- [Overview](docs/overview.md) — project goals, key concepts, architecture
- [Architecture & Data Flow](docs/architecture.md) — component diagram, request lifecycle, DB schema
- [File Reference](docs/file_reference.md) — per-file purpose, key classes and functions

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for details.

## License

Apache 2.0; see [`LICENSE`](LICENSE) for details.

## Disclaimer

This project is not an official Google project. It is not supported by Google and Google specifically disclaims all warranties as to its quality, merchantability, or fitness for a particular purpose.

---

# Setup Guide

## 1. Setting Up a New Instance (Dev Machine, Fresh Database)

Use this when running the bot locally from scratch with no existing data.

### Install system dependencies

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib libpq-dev python3-dev npm

# macOS (Homebrew)
brew install postgresql@16
brew services start postgresql@16
```

Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Check that `uv` is on your PATH (e.g. add to `.bashrc`/`.zshrc`).

### Clone the repo

```bash
git clone https://github.com/metaflow/moon-rabbit.git
cd moon-rabbit
```

### Create the database

```bash
sudo -u postgres psql
```

```sql
CREATE USER bot WITH PASSWORD 'bot';
CREATE DATABASE chatbot OWNER bot;
GRANT ALL PRIVILEGES ON DATABASE chatbot TO bot;
\q
```

### Set up `.env`

Create `.env` in the project root:

```bash
DB_CONNECTION="dbname=chatbot user=bot password=bot host=localhost"
DISCORD_TOKEN=<your_discord_bot_token>
TWITCH_OAUTH_DOMAIN="http://localhost:4343"
```

### Install dependencies

```bash
uv sync        # installs project + dev dependencies
```

For production (no dev tools):

```bash
uv sync --no-dev
```

### Run in dev mode

```bash
uv run python3 main.py --dev --discord --log dev --also_log_to_stdout
uv run python3 main.py --dev --twitch <bot name> --log dev --also_log_to_stdout
```

The `--dev` flag sends a smoke-test message to all connected channels on startup.

To run code quality checks:

```bash
./check.sh
```

---

## 2. Setting Up a New Production Instance (Restoring Database from Backup)

Use this when deploying to a new server (DigitalOcean droplet or similar) and restoring data from an existing backup file.

### Connect and update the server

```bash
ssh -i <key> root@<ip>
apt update && apt upgrade
```

### Install system dependencies

```bash
apt install postgresql postgresql-contrib libpq-dev python3-dev npm
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Clone the repo

```bash
cd /var
git clone https://github.com/metaflow/moon-rabbit.git
cd moon-rabbit
```

### Set up the database

```bash
sudo -u postgres psql
```

```sql
CREATE USER bot WITH PASSWORD '<password>';
CREATE DATABASE chatbot OWNER bot;
GRANT ALL PRIVILEGES ON DATABASE chatbot TO bot;
\q
```

### Create a backup from the source server

```bash
# On the source server:
sudo -u postgres pg_dump rabbit --no-owner --no-privilege --no-acl --column-inserts | gzip > backup.sql.gz
```

### Restore the backup on the new server

```bash
gunzip backup.sql.gz
sudo -u postgres psql chatbot < backup.sql
```

Reassign ownership to the `bot` user:

```bash
sudo -u postgres psql chatbot -t -A -c "
  SELECT format('ALTER TABLE %I OWNER TO bot;', tablename) FROM pg_tables WHERE schemaname='public'
  UNION ALL
  SELECT format('ALTER SEQUENCE %I OWNER TO bot;', sequence_name) FROM information_schema.sequences WHERE sequence_schema='public'
" | sudo -u postgres psql chatbot
```

### Set up `.env`

```bash
DB_CONNECTION="dbname=chatbot user=bot password=<password> host=localhost"
DISCORD_TOKEN=<your_discord_bot_token>
TWITCH_OAUTH_DOMAIN="https://<your-domain>"
```


You will also need an nginx entry to proxy `<your-domain>` to twitchio's built-in OAuth server (port 4343):

```nginx
server {
    listen 80;
    server_name <your-domain>;

    location /oauth {
        proxy_pass http://127.0.0.1:4343;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Enable the config and reload nginx:

```bash
ln -s /etc/nginx/sites-available/<your-domain> /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

For HTTPS (recommended), use [Certbot](https://certbot.eff.org/) to obtain a certificate — it will update the nginx config automatically.

### Install PM2 and start the bot

```bash
npm install -g pm2
uv sync --no-dev
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup
```

Check status:

```bash
pm2 list
pm2 logs
```

To override PM2 config for this instance:

```bash
cp ecosystem.config.cjs runtime/ecosystem.config.cjs
# Edit runtime/ecosystem.config.cjs as needed
pm2 start runtime/ecosystem.config.cjs
# Apply config changes later:
pm2 restart runtime/ecosystem.config.cjs --update-env
```

### Set up database backups

```bash
mkdir -p /mnt/backup
cp pg_backup.sh ./runtime/
# Edit runtime/pg_backup.sh with correct credentials
./runtime/pg_backup.sh   # verify it works
```

Backups are managed by PM2 (see `ecosystem.config.cjs`). Inspect the database directly:

```bash
psql postgres://bot:<password>@localhost/chatbot
```

---

## 3. Registering a New Bot and Connecting It to the Database

### Create a Discord bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → give it a name
3. Go to **Bot** → **Reset Token** → copy the token (`DISCORD_TOKEN`)
4. Under **Privileged Gateway Intents**, enable all three (Presence, Server Members, Message Content)
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Permissions: `Send Messages`, `Read Message History`, `Add Reactions`, `Manage Guild`, `View Channels`
6. Open the generated URL to invite the bot to your server

### Create a Twitch bot application

1. Go to [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)
2. Click **Register Your Application**:
   - OAuth Redirect URL: `http://<TWITCH_OAUTH_DOMAIN>/oauth/callback`
   - Category: Chat Bot
3. Copy the **Client ID** and generate a **Client Secret**

### Get the bot's numeric Twitch user ID

```bash
# Step 1: get an app access token
curl -X POST "https://id.twitch.tv/oauth2/token" \
  -d "client_id=<client_id>&client_secret=<client_secret>&grant_type=client_credentials" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"

# Step 2: look up the bot account's numeric ID by login name
curl -H "Authorization: Bearer <access_token>" \
     -H "Client-Id: <client_id>" \
     "https://api.twitch.tv/helix/users?login=<bot_username>" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])"
```

### Insert bot credentials into the database

```sql
INSERT INTO twitch_bots (channel_name, api_app_id, api_app_secret, bot_user_id)
VALUES ('<bot_username>', '<client_id>', '<client_secret>', '<numeric_bot_user_id>');
```

### Authorize the bot (one-time OAuth flow)

Start the bot. It will print two URLs:

1. **Bot Account Authorization URL** — open in a browser **while logged in as the bot Twitch account**:
   ```
   http://<TWITCH_OAUTH_DOMAIN>/oauth?scopes=user:read:chat+user:write:chat+user:bot&force_verify=true
   ```

2. **Channel Owner Authorization URL** — open in a browser **while logged in as the channel owner**:
   ```
   http://<TWITCH_OAUTH_DOMAIN>/oauth?scopes=channel:bot+channel:read:redemptions+channel:read:hype_train&force_verify=true
   ```

Tokens are saved to the `twitch_tokens` DB table and reused on subsequent restarts.

---

## 4. Adding a New Discord Server

Enable **Developer Mode** in Discord: **Settings → Advanced → Developer Mode**.

Get IDs by right-clicking:
- Server name → **Copy Server ID** (guild ID)
- Channel → **Copy Channel ID**
- User → **Copy User ID**

The bot auto-creates a `channels` entry on the first message it receives in a new guild — no manual DB insert needed.

> `discord_allowed_channels` controls which Discord channels the bot listens in. If empty (the default), the bot responds in all channels. Restrict later with the `allow_here` / `disallow_here` commands.

If you imported production data and need to point the bot at a different guild:

```sql
UPDATE channels SET discord_guild_id = '<new_guild_id>' WHERE discord_guild_id = '<old_guild_id>';
```

---

## 5. Adding a New Twitch Channel

Your Twitch channel name is your Twitch username (lowercase), visible in the URL: `twitch.tv/<channel_name>`.

Grant the bot moderator rights in your channel:

```
/mod <bot_username>
```

Then open the **Channel Owner Authorization URL** printed by the bot on startup (see section 3) **while logged in as the channel owner**.

If you imported production data and need to update the channel name:

```sql
UPDATE channels SET twitch_channel_name = '<new_channel>' WHERE twitch_channel_name = '<old_channel>';
```

---

## 6. Dev: Patching Imported Production Data for Local Use

If you imported a production backup and want to run it locally against your own dev accounts:

```sql
-- Point Discord bot at your test server
UPDATE channels SET discord_guild_id = '<your_guild_id>' WHERE discord_guild_id = '<prod_guild_id>';

-- Point Twitch bot at your dev channel
UPDATE channels SET twitch_channel_name = '<your_twitch_channel>' WHERE twitch_channel_name = '<prod_channel>';

-- Update Twitch bot credentials
UPDATE twitch_bots SET
  api_app_id = '<your_client_id>',
  api_app_secret = '<your_client_secret>',
  bot_user_id = '<your_numeric_bot_user_id>'
WHERE channel_name = 'moon_robot';
```

> If starting with a fresh database (no import), skip this — the bot auto-creates channel entries on the first message.
