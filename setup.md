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