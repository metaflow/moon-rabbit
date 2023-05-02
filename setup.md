# installing discord and twitch bots on a new droplet

connecting

ssh -i <key> root@<ip>

apt update
apt upgrade

# install postgres
apt install postgresql postgresql-contrib

# backup on another instance

sudo -u postgres pg_dump rabbit --schema-only --no-owner  --no-privilege -F p > scheme.sql
sudo -u postgres pg_dump rabbit --data-only --no-owner --no-privilege -F c > data.dump
copy to the new location

# setup database

sudo -u postgres dropdb --if-exists chatbo
sudo -u postgres psql
CREATE USER bot WITH PASSWORD '*****';
CREATE DATABASE chatbot OWNER bot;
GRANT ALL PRIVILEGES ON DATABASE chatbot TO bot;
\q

sudo -u postgres psql -d chatbot -f /mnt/backup/schema.sq
sudo -u postgres pg_restore -d chatbot -no-owner --role=bot /mnt/backup/data.dump

## setup schema
cd /var
git clone https://github.com/metaflow/moon-rabbit.git
sudo -u postgres psql -d chatbot -f scheme.sql

copy backup to the machine - I have used winSCP

cd /mnt/backup
/mnt/backup# ls
20230428090822_pg_backup.sql.gz

gzip -dk 20230428090822_pg_backup.sql.gz

> restore schema

sudo -u postgres psql -d chatbot -f 20230428095641_pg_backup.sql

pg_restore --no-owner --role=bot -d chatbot 20230428090822_pg_backup.sql
restore from backup / create an empty schema

# install git

# add domain for auth

# periodic restart

# migration

- check discord on personal server first
- check twitch on personal server first
- try on jl
- move database again
- turn down old instance
- setup access