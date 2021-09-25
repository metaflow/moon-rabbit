"""
 Copyright 2021 Google LLC

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      https://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 """

import dataclasses
from typing import Dict, List, Optional, Tuple
from data import *
import psycopg2  # type: ignore
import psycopg2.extensions  # type: ignore
import psycopg2.extras  # type: ignore
import functools
import logging
import collections
import random
import time
from cachetools import TTLCache  # type: ignore
from query import tag_re

psycopg2.extensions.register_adapter(dict, psycopg2.extras.Json)


@dataclasses.dataclass
class ListInfo:
    items_ids: List[int]
    idx: int = 0


def escape_like(t):
    return t.replace('=', '==').replace('%', '=%').replace('_', '=_')


class DB:
    def __init__(self, connection):
        self.conn = psycopg2.connect(connection)
        self.conn.set_session(autocommit=True)
        self.cache = TTLCache(maxsize=100, ttl=600)
        self.lists: Dict[str, ListInfo] = {}
        self.tags: Dict[int, Dict[str, int]] = {}
        self.text_tags: Dict[int, Dict[int, List[int]]] = {}
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
                twitch_command_prefix varchar(10));
            CREATE TABLE IF NOT EXISTS variables
                (channel_id INT,
                name varchar(100),
                value TEXT,
                category varchar(100),
                expires INT,
                CONSTRAINT uniq_variable UNIQUE (channel_id, name, category)
            );
            CREATE TABLE IF NOT EXISTS texts (
                id SERIAL PRIMARY KEY,
                channel_id INT NOT NULL,
                value TEXT,
                CONSTRAINT uniq_text_value UNIQUE (channel_id, value)
            );
            CREATE TABLE IF NOT EXISTS tags (
                id SERIAL PRIMARY KEY,
                channel_id INT NOT NULL,
                value varchar(100),
                CONSTRAINT uniq_tag_value UNIQUE (channel_id, value)
            );
            CREATE TABLE IF NOT EXISTS text_tags (
                tag_id INT REFERENCES tags (id) ON DELETE CASCADE,
                text_id INT REFERENCES texts (id) ON DELETE CASCADE,
                CONSTRAINT uniq_text_tag UNIQUE (tag_id, text_id)
            );
            ''')
        self.conn.commit()

    @functools.lru_cache(maxsize=1000)
    def twitch_channel_info(self, cur: psycopg2.extensions.cursor, name) -> Tuple[int, str]:
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
    def discord_channel_info(self, cur: psycopg2.extensions.cursor, guild_id):
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

    def get_list_item(self, channel_id: int, id: int) -> Optional[Tuple[str, str]]:
        # TODO cache?
        with self.conn.cursor() as cur:
            cur.execute("SELECT text, list_name FROM lists WHERE channel_id = %s AND id = %s",
                        [channel_id, id])
            row = cur.fetchone()
            if not row:
                return None
            return row[0], row[1]

    def delete_list_item(self, channel_id: int, id: int) -> Optional[Tuple[str, str]]:
        t = self.get_list_item(channel_id, id)
        if not t:
            return None
        _, list_name = t
        self.conn.cursor().execute(
            'DELETE FROM lists WHERE channel_id = %s AND id = %s', (channel_id, id))
        self.lists.pop(f'{channel_id}_{list_name}', None)
        return t

    def delete_list(self, channel_id: int, list_name: str) -> int:
        self.lists.pop(f'{channel_id}_{list_name}', None)
        with self.conn.cursor() as cur:
            cur.execute('DELETE FROM lists WHERE channel_id = %s AND list_name = %s',
                        (channel_id, list_name))
            return cur.rowcount

    def _get_list(self, channel_id: int, name: str) -> ListInfo:
        key = f'{channel_id}_{name}'
        if key in self.lists:
            return self.lists[key]
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM lists WHERE channel_id = %s AND list_name = %s;",
                        [channel_id, name])
            self.lists[key] = ListInfo(items_ids=[x[0]
                                       for x in cur.fetchall()])
        return self.lists[key]

    def get_all_list_items(self, channel_id: int, name: str) -> List[Tuple[int, str]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, text FROM lists WHERE channel_id = %s AND list_name = %s;",
                        [channel_id, name])
            return [(x[0], x[1]) for x in cur.fetchall()]

    def get_list_names(self, channel_id: int) -> List[str]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT list_name FROM lists WHERE channel_id = %s", [channel_id])
            return [x[0] for x in cur.fetchall()]

    def get_tags(self, channel_id: int) -> Dict[str, int]:
        if channel_id not in self.tags:
            self.tags[channel_id] = {}
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT id, value FROM tags WHERE channel_id = %s", [channel_id])
                for row in cur.fetchall():
                    self.tags[channel_id][row[1]] = row[0]
        return self.tags[channel_id]

    def add_tag(self, channel_id: int, tag_name: str):
        if not tag_re.match(tag_name):
            raise Exception("tag name mismatch")
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO tags (channel_id, value) VALUES (%s, %s) ON CONFLICT DO NOTHING;',
                        (channel_id, tag_name))
            self.tags.pop(channel_id, None)
 
    def delete_tag(self, channel_id: int, tag_name: str):
        with self.conn.cursor() as cur:
            logging.info(f'delete tag "{tag_name}" from {channel_id}')
            cur.execute('DELETE FROM tags WHERE channel_id = %s AND value = %s',
                        (channel_id, tag_name))
            self.tags.pop(channel_id, None)
            self.text_tags.pop(channel_id, None)
            return cur.rowcount

    def get_text_tags(self, channel_id: int) -> Dict[int, List[int]]:
        if channel_id in self.text_tags:
            return self.text_tags[channel_id]
        z: Dict[int, List[int]] = {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT tt.text_id, tt.tag_id FROM text t JOIN text_tags tt ON tt.text_id = t.id WHERE t.channel_id = %s", [channel_id])
            for row in cur.fetchall():
                text, tag = row
                if text not in z:
                    z[text] = []
                z[text].append(tag)
        self.text_tags[channel_id] = z
        return z

    def add_text_tag(self, channel_id: int, text: int, tag: int):
        self.text_tags.pop(channel_id, None)
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO text_tags (text_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (text, tag))

    def delete_text_tags(self, channel_id: int, text_id: int) -> int:
        logging.info(f'delete all tags for text {text_id}')
        self.text_tags.pop(channel_id, None)
        with self.conn.cursor() as cur:
            cur.execute(
                'DELETE FROM text_tags WHERE text_id = %s', ( text_id, ))
            return cur.rowcount

    def delete_text(self, channel_id: int, text_id: int) -> int:
        self.text_tags.pop(channel_id, None)
        with self.conn.cursor() as cur:
            cur.execute(
                'DELETE FROM texts WHERE text_id = %s AND channel_id = %s', (text_id, channel_id))
            return cur.rowcount 

    def get_text(self, channel_id: int, id: int) -> Optional[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM texts WHERE channel_id = %s AND id = %s",
                        [channel_id, id])
            row = cur.fetchone()
            if not row:
                return None
            return row[0]

    def add_text(self, channel_id: int, value: str) -> Tuple[int, bool]:
        with self.conn.cursor() as cur:
            cur.execute('SELECT id FROM texts WHERE channel_id = %s and value = %s', (channel_id, value))
            row = cur.fetchone()
            if row:
                return row[0], False
            self.text_tags.pop(channel_id, None)
            cur.execute('INSERT INTO texts (channel_id, value) VALUES (%s, %s) ON CONFLICT ON CONSTRAINT uniq_text_value DO UPDATE SET value = %s RETURNING id;',
                        (channel_id, value, value))
            return cur.fetchone()[0], True

    def text_search(self, channel_id: int, txt: str) -> List[Tuple[int, str, List[str]]]:
        with self.conn.cursor() as cur:
            q = '%' + escape_like(txt) + '%'
            cur.execute('''select t.id, t.value, tags.value from texts t
            LEFT JOIN text_tags tt ON tt.text_id = t.id
            LEFT JOIN tags ON tags.id = tt.tag_id
            where (t.channel_id = %s) AND (t.value LIKE %s)''',
                        (channel_id, q))
            m: Dict[int, Tuple[str, List[str]]] = {}
            for row in cur.fetchall():
                id, text, tag = row[0], row[1], row[2]
                if id not in m:
                    m[id] = (text, [])
                if tag:
                    m[id][1].append(tag)
            return [(k, v[0], v[1]) for (k, v) in m.items()]

    def all_texts(self, channel_id: int) -> List[Tuple[int, str, List[str]]]:
        with self.conn.cursor() as cur:
            cur.execute('''select t.id, t.value, tags.value from texts t
            LEFT JOIN text_tags tt ON tt.text_id = t.id
            LEFT JOIN tags ON tags.id = tt.tag_id
            where (t.channel_id = %s)''', (channel_id,))
            m: Dict[int, Tuple[str, List[str]]] = {}
            for row in cur.fetchall():
                id, text, tag = row[0], row[1], row[2]
                if id not in m:
                    m[id] = (text, [])
                if tag:
                    m[id][1].append(tag)
            return [(k, v[0], v[1]) for (k, v) in m.items()]

    def get_random_list_item(self, channel_id: int, list_name: str) -> str:
        info = self._get_list(channel_id, list_name)
        n = len(info.items_ids)
        if n == 0:
            return ''
        if n == 1:
            item = self.get_list_item(channel_id, info.items_ids[0])
            if not item:
                return ''
            return item[0]
        info.idx = (info.idx + random.randint(1, n - 1)) % n
        item = self.get_list_item(channel_id, info.items_ids[info.idx])
        if not item:
            return ''
        return item[0]

#     SELECT t.id FROM texts t
# JOIN text_tags tt ON tt.text_id = t.id AND tt.tag_id IN (2, 3)
# GROUP BY t.id
# HAVING count(*) = 2

    def get_commands(self, channel_id, prefix) -> List[CommandData]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT data FROM commands WHERE channel_id = %s;", [channel_id])
            dicts = [x[0]for x in cur.fetchall()]
            return [dictToCommandData(x) for x in dicts]

    def set_command(self, cur: psycopg2.extensions.cursor, channel_id: int, author: str, cmd: CommandData) -> int:
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
        return cur.fetchone()[0]

    def set_variable(self, channel_id: int, name: str, value: str, category: str, expires: int):
        with self.conn.cursor() as cur:
            if value == '':
                cur.execute('DELETE FROM variables WHERE channel_id = %s AND name = %s AND category = %s',
                            (channel_id, name, category))
                return
            cur.execute('''
                INSERT INTO variables (channel_id, name, value, category, expires)
                VALUES (%(channel_id)s, %(name)s, %(value)s, %(category)s, %(expires)s)
                ON CONFLICT ON CONSTRAINT uniq_variable DO
                UPDATE SET value = %(value)s, expires = %(expires)s;''',
                        {'channel_id': channel_id,
                         'name': name,
                         'value': value,
                         'category': category,
                         'expires': expires,
                         })

    def get_variable(self, channel_id: int, name: str, category: str, default_value: str):
        with self.conn.cursor() as cur:
            cur.execute("SELECT value, expires FROM variables WHERE name = %s AND channel_id = %s AND category = %s",
                        [name, channel_id, category])
            row = cur.fetchone()
            if not row:
                return default_value
            value, expires = row
            if expires < time.time():
                return default_value
            return value

    def count_variables_in_category(self, channel_id: int, category: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM variables WHERE channel_id = %s AND category = %s",
                        [channel_id, category])
            return cur.fetchone()[0]

    def delete_category(self, channel_id: int, category: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM variables WHERE channel_id = %s AND category = %s",
                        [channel_id, category])
            return cur.rowcount

    def expire_variables(self):
        with self.conn.cursor() as cur:
            cur.execute('DELETE FROM variables WHERE expires < %s',
                        [int(time.time())])
            n = cur.rowcount
            if n:
                logging.info(f'deleted {n} expired variables')

    def add_list_item(self, channel_id: int, name: str, text: str) -> Tuple[int, bool]:
        if (not name) or (not text):
            return -1, False
        with db().conn.cursor() as cur:
            cur.execute('SELECT id FROM lists WHERE channel_id = %s AND list_name = %s AND text = %s',
                        [channel_id, name, text])
            row = cur.fetchone()
            if row:
                logging.info(f"list item '{name}' '{text}' already exists")
                return row[0], False
            self.lists.pop(f'{channel_id}_{name}', None)
            cur.execute('INSERT INTO lists (channel_id, list_name, text) VALUES (%s, %s, %s) RETURNING id;',
                        (channel_id, name, text))
            return cur.fetchone()[0], True

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
            self.twitch_channel_info.cache_clear()

    def set_discord_prefix(self, channel_id: int, prefix: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE channels SET discord_command_prefix = %s WHERE channel_id = %s",
                [prefix, channel_id])
            self.conn.commit()
            self.discord_channel_info.cache_clear()


_db: Optional[DB]


def set_db(d: DB):
    global _db
    _db = d


def db() -> DB:
    if not _db:
        raise Exception("database is not initialized")
    return _db


def cursor():
    return db().conn.cursor()
