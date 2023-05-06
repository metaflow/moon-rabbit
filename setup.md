# installing discord and twitch bots on a new droplet

connecting

ssh -i <key> root@<ip>

apt update
apt upgrade

# clone repo
cd /var
git clone https://github.com/metaflow/moon-rabbit.git

# install postgres
apt install postgresql postgresql-contrib

# restoring backup

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

>

# otherwise setup schema

sudo -u postgres psql -d chatbot -f schema_backup.sql

# test that bot can connect to db

create file in /var/moon-rabbit/.env

DB_CONNECTION="dbname=chatbot user=bot password=***** host=localhost"
DISCORD_TOKEN=*****

# add domain for auth

# periodic restart

# migration

- check discord on personal server first
- check twitch on personal server first
- try on jl
- move database again
- turn down old instance
- setup access

# how to work with DB

sudo -u postgres psql

/c chatbot
/dt