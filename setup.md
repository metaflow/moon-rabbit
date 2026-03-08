# installing discord and twitch bots on a new droplet

connecting

ssh -i <key> root@<ip>

apt update
apt upgrade

# clone repo
cd /var
git clone https://github.com/metaflow/moon-rabbit.git

# install postgres and pipenv
apt update
apt install postgresql postgresql-contrib
apt install libpq-dev python3-dev # for psycopg2 python package
pip install --user pipenv

check that pipenv is available e.g. add to .bashrc

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

sudo -u postgres psql
ALTER DATABASE chatbot OWNER TO bot;
select 'ALTER TABLE ' || table_name || ' OWNER TO bot;' from information_schema.tables where table_schema = 'public';
\q

# test that bot can connect to db

create file in /var/moon-rabbit/.env

DB_CONNECTION="dbname=chatbot user=bot password=***** host=localhost"
DISCORD_TOKEN=*****

Use a token to dev discord first and only run for discord.

> pipenv run python3 main.py --discord --log_level INFO --log discord --also_log_to_stdout
 pipenv run python3 main.py --twitch moon_robot --log_level INFO --log moon_robot --also_log_to_stdout

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
CREATE USER bot WITH PASSWORD 'localdev';
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

Fix ownership:

```bash
sudo -u postgres psql
```

```sql
ALTER DATABASE chatbot OWNER TO bot;
-- Run the generated ALTER TABLE statements:
SELECT 'ALTER TABLE ' || table_name || ' OWNER TO bot;' FROM information_schema.tables WHERE table_schema = 'public';
\q
```

## 4. Patch channel data for dev

Update `channels` and `twitch_bots` to point at your dev Discord server and Twitch channel:

```bash
sudo -u postgres psql chatbot
```

```sql
-- Update Discord guild ID to your test server
UPDATE channels SET discord_guild_id = '<your_test_server_id>' WHERE discord_guild_id = '<prod_guild_id>';

-- Update Twitch channel name to your dev channel
UPDATE channels SET twitch_channel_name = '<your_twitch_channel>' WHERE twitch_channel_name = '<prod_channel>';

-- Update Twitch bot credentials (see API keys section below)
UPDATE twitch_bots SET
  api_app_id = '<your_client_id>',
  api_app_secret = '<your_client_secret>',
  auth_token = '<your_oauth_token>',
  refresh_token = '<your_refresh_token>'
WHERE channel_name = 'moon_robot';
```

## 5. Obtain API keys

### Discord

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**, give it a name
3. Go to **Bot** → click **Reset Token** → copy the token (this is your `DISCORD_TOKEN`)
4. Under **Privileged Gateway Intents**, enable **all three** (Presence, Server Members, Message Content)
5. Go to **OAuth2** → **URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Read Message History`, `Add Reactions`, `Manage Guild` (for banner), `View Channels`
6. Open the generated URL to invite the bot to your test server

### Twitch

1. Go to [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)
2. Click **Register Your Application**:
   - Name: anything unique
   - OAuth Redirect URL: `http://localhost:3000` (for token generation)
   - Category: Chat Bot
3. Copy the **Client ID** (`api_app_id`) and generate a **Client Secret** (`api_app_secret`)
4. Generate a user OAuth token for the bot account. Use the Twitch CLI or the implicit grant flow:

   ```
   # Using Twitch CLI:
   twitch token -u -s "chat:read chat:edit channel:read:redemptions"

   # Or open in browser (implicit grant):
   https://id.twitch.tv/oauth2/authorize?client_id=<CLIENT_ID>&redirect_uri=http://localhost:3000&response_type=token&scope=chat:read+chat:edit+channel:read:redemptions
   ```

5. The resulting `access_token` goes into `twitch_bots.auth_token`
6. For refresh tokens, use the authorization code flow instead of implicit grant
7. **Important**: The bot account must have permission to chat in your dev channel. If you own the channel, mod the bot account: `/mod <bot_username>`

## 6. Set up `.env`

Create (or update) `.env` in the project root:

```bash
DB_CONNECTION="dbname=chatbot user=bot password=localdev host=localhost"
DISCORD_TOKEN=<your_discord_bot_token>
```

Source it before running:

```bash
source .env
```

## 7. Install dependencies

```bash
pipenv install
```

## 8. Run in dev mode

```bash
pipenv run python3 main.py --dev --discord --log dev --also_log_to_stdout
pipenv run python3 main.py --dev --twitch moon_robot --log dev --also_log_to_stdout
```

The `--dev` flag sends a smoke-test message to all connected channels on startup, confirming the bot is alive and can reach the chat APIs.