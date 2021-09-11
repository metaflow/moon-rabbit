import dataclasses
from typing import List
from data import Command
import psycopg2
import psycopg2.extensions
import psycopg2.extras
import functools
import logging
import collections
from cachetools import TTLCache
import dacite
import re

psycopg2.extensions.register_adapter(dict, psycopg2.extras.Json)


class DB:
    def __init__(self, connection):
        self.conn = psycopg2.connect(connection)
        self.cache = TTLCache(maxsize=100, ttl=1)  # TODO: configure > 1.
        self.logs = {}
        self.init_db()

    def recreate_tables(self):
        logging.warning('dropping and creating tables anew')
        with self.conn.cursor() as c:
            c.execute('''
DROP TABLE commands;
DROP TABLE lists;
DROP TABLE channels;
            ''')
        self.conn.commit()
        self.init_db()

    def init_db(self):
        with self.conn.cursor() as cur:
            cur.execute('''
            CREATE TABLE IF NOT EXISTS commands
                (id SERIAL,
                channel_id INT,
                name VARCHAR(50),
                data JSONB,
                author TEXT,
                text TEXT,
                discord BOOLEAN,
                twitch BOOLEAN,
                CONSTRAINT uniq_name_in_channel UNIQUE (channel_id, name));
            CREATE TABLE IF NOT EXISTS lists
                (id SERIAL,
                channel_id INT,
                author TEXT,
                list_name varchar(50),
                discord BOOLEAN,
                twitch BOOLEAN,
                text TEXT); 
            CREATE TABLE IF NOT EXISTS channels
                (id SERIAL,
                channel_id INT,
                discord_guild_id varchar(50),
                discord_command_prefix varchar(10),
                twitch_channel_name varchar(50),
                twitch_command_prefix varchar(10));''')
        self.conn.commit()

    @functools.lru_cache(maxsize=1000)
    def twitch_channel_info(self, cur, name):
        cur.execute(
            "SELECT channel_id, twitch_command_prefix FROM channels WHERE twitch_channel_name = %s", [name])
        row = cur.fetchone()
        if row:
            id = row[0]
            prefix = row[1]
            logging.info(f"got Twitch channel ID '{name}' #{id} '{prefix}'")
            return id, prefix
        id = self.new_channel_id()
        prefix = '+'
        cur.execute('INSERT INTO channels (channel_id, twitch_channel_name, twitch_command_prefix) VALUES (%s, %s, %s)', [
                    id, name, prefix])
        logging.info(f"added Twitch channel ID '{name}' #{id} '{prefix}'")
        return id, prefix

    @functools.lru_cache(maxsize=1000)
    def discord_channel_info(self, cur, guild_id):
        cur.execute(
            "SELECT channel_id, discord_command_prefix FROM channels WHERE discord_guild_id = %s", [guild_id])
        row = cur.fetchone()
        if row:
            id = row[0]
            prefix = row[1]
            logging.info(
                f"got Discord channel ID '{guild_id}' '{prefix}' #{id}")
            return id, prefix
        id = self.new_channel_id()
        prefix = '+'
        cur.execute('INSERT INTO channels (channel_id, discord_guild_id, discord_command_prefix) VALUES (%s, %s, %s)', [
                    id, guild_id, prefix])
        logging.info(f"added Discord channel ID '{guild_id}' #{id}")
        return id, prefix

    def new_channel_id(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT MAX(channel_id) FROM channels")
            row = cur.fetchone()
            if row and (row[0] is not None):
                return int(row[0]) + 1
            return 0

    # def load_template(self, name):
    #     logging.info(f'loading template {name}')
    #     if ':' not in name:
    #         logging.error(f"bad template name '{name}'")
    #         return None
    #     type, channel_id, id = name.split(':', 2)
    #     if type == 'cmd':
    #         with self.conn.cursor() as cur:
    #             cur.execute("SELECT text FROM commands WHERE channel_id = %s AND name = %s;",
    #                         [int(channel_id), id])
    #             z = cur.fetchone()[0]
    #             logging.info(f'template {name} = {z}')
    #             return z
    #     if type == 'list':
    #         with self.conn.cursor() as cur:
    #             cur.execute("SELECT text FROM lists WHERE channel_id = %s AND id = %s;",
    #                         [int(channel_id), id])
    #             z = cur.fetchone()[0]
    #             logging.info(f'template {name} = {z}')
    #             return z
    #     raise f'unknown template type {type} for name `{name}`'

    def get_list(self, channel_id: int, id: int):
        with self.conn.cursor() as cur:
            cur.execute("SELECT text FROM lists WHERE channel_id = %s AND id = %s;",
                        [int(channel_id), id])
            z = cur.fetchone()[0]
            logging.info(f'template {name} = {z}')
            return z

    def lists_ids(self, channel_id, list):
        key = f'get_lists_{channel_id}_{list}'
        if not key in self.cache:
            print('loading', key)
            with self.conn.cursor() as cur:
                cur.execute("SELECT id FROM lists WHERE channel_id = %s AND list_name = %s;",
                            [channel_id, list])
                self.cache[key] = [x[0] for x in cur.fetchall()]
        return self.cache[key]

    def get_commands(self, channel_id, prefix) -> List[Command]:
        key = f'get_commands_{channel_id}_{prefix}'
        if not key in self.cache:
            print('loading', key)
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT data FROM commands WHERE channel_id = %s;", [channel_id])
                z: List[Command] = [dacite.from_dict(Command, x[0]) for x in cur.fetchall()]
                for c in z:
                    c.pattern = c.pattern.replace('!prefix', prefix)
                    c.regex = re.compile(c.pattern, re.IGNORECASE)
                self.cache[key] = z
        return self.cache[key]

    def set_command(self, cur, channel_id: int, author: str, cmd: Command) -> int:
        cmd.regex = None
        cur.execute('''
            INSERT INTO commands (channel_id, author, name, data)
            VALUES (%(channel_id)s, %(author)s, %(name)s, %(data)s)
            ON CONFLICT ON CONSTRAINT uniq_name_in_channel DO
            UPDATE SET data = %(data)s RETURNING id;''',
                    {'channel_id': channel_id,
                     'author': author,
                     'name': cmd.name,
                     'data': dataclasses.asdict(cmd),
                     })
        self.cache.clear()
        return cur.fetchone()[0]

    def get_message(self, id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT text FROM lists WHERE id = %s", [id])
            return cur.fetchone()[0]

    def add_log(self, channel_id, entry):
        if channel_id not in self.logs:
            self.logs[channel_id] = collections.deque(maxlen=10)
        self.logs[channel_id].append(entry)

    def get_logs(self, channel_id):
        if channel_id not in self.logs:
            return []
        return list(self.logs[channel_id])

    def set_twitch_prefix(self, channel_id: int, prefix: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE channels SET twitch_command_prefix = %s WHERE channel_id = %s",
                [prefix, channel_id])
            self.conn.commit()
            self.cache.clear()
            self.twitch_channel_info.cache_clear()

    def set_discord_prefix(self, channel_id: int, prefix: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE channels SET discord_command_prefix = %s WHERE channel_id = %s",
                [prefix, channel_id])
            self.conn.commit()
            self.cache.clear()
            self.discord_channel_info.cache_clear()
