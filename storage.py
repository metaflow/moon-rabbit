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
from typing import Any, Dict, List, Optional, Set, Tuple, Type
from ttldict2.impl import TTLDict
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
import query
import lark
import ttldict2  # type: ignore
from llist import dllist  # type: ignore
import numpy as np

psycopg2.extensions.register_adapter(dict, psycopg2.extras.Json)


@dataclasses.dataclass
class ListInfo:
    items_ids: List[int]
    idx: int = 0


def escape_like(t):
    return t.replace('=', '==').replace('%', '=%').replace('_', '=_')


@dataclasses.dataclass
class ChannelCache:
    tags: Optional[Tuple[Dict[str, int], Dict[int, str]]]
    text_tags: Optional[Dict[int, Set[int]]]
    # ttldict query -> dllist[int]
    text_queries: Any
    # plan:
    # make a dict queries (str -> [id(int), size, parsed query])
    # TTL dict of active queries 
    # and a dict of text_id -> [set(id of matched queries), pointer to list element]
    # for every text store all queries it matches
    #   - new query added - add new id, evaluate all texts for it, store size of match
    #   - query expired - remove it from every text. Cleanup every minute with variables.
    # tags:
    #   - added tag - noop
    #   - removed tag - noop (there should be no active queries with this flag)
    # text - tag update:
    #   - evaluate every query on text and update text dict and counter on queries
    # text:
    #   - add - add it to the front of the queue and dict
    #   - remove - drop
    # select random for query:
    # pick a random number N within the size of the query and iterate over the list until we find N-th text that matches this query
    # move the element to the end of the queue


class DB:
    def __init__(self, connection):
        self.conn = psycopg2.connect(connection)
        self.conn.set_session(autocommit=True)
        self.channels: Dict[int, Type[ChannelCache]] = {}
        self.logs = {}
        self.rng = np.random.default_rng()
        self.init_db()

    def channel(self, channel_id: int) -> ChannelCache:
        if channel_id not in self.channels:
            self.channels[channel_id] = ChannelCache(
                tags=None,
                text_tags=None,
                text_queries=ttldict2.TTLDict(
                    ttl_seconds=3600.0 * 24 * 14))
        return self.channels.get(channel_id)

    def recreate_tables(self):
        logging.warning('dropping and creating tables anew')
        with self.conn.cursor() as c:
            c.execute('''
DROP TABLE commands;
DROP TABLE channels;
DROP TABLE variables;
DROP TABLE texts;
DROP TABLE tags;
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
            CREATE TABLE IF NOT EXISTS channels
                (id SERIAL,
                channel_id INT,
                discord_guild_id varchar(50),
                discord_command_prefix varchar(10),
                twitch_channel_name varchar(50),
                twitch_command_prefix varchar(10),
                twitch_events TEXT,
                twitch_bot TEXT);
            CREATE TABLE IF NOT EXISTS twitch_bots
                (id SERIAL,
                channel_name TEXT,
                api_app_id TEXT,
                api_app_secret TEXT,
                api_url TEXT,
                api_port INT,
                auth_token TEXT);
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

    def get_tags(self, channel_id: int) -> Tuple[Dict[str, int], Dict[int, str]]:
        ch = self.channel(channel_id)
        if not ch.tags:
            ch.tags = ({}, {})
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT id, value FROM tags WHERE channel_id = %s", [channel_id])
                for row in cur.fetchall():
                    ch.tags[0][row[1]] = row[0]
                    ch.tags[1][row[0]] = row[1]
        return ch.tags

    def add_tag(self, channel_id: int, tag_name: str):
        if not tag_re.match(tag_name):
            raise Exception("tag name mismatch")
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO tags (channel_id, value) VALUES (%s, %s) ON CONFLICT DO NOTHING;',
                        (channel_id, tag_name))
            self.channel(channel_id).tags = None

    def purge_text_to_tag_cache(self, channel_id: int):
        ch = self.channel(channel_id)
        ch.text_queries.clear()
        ch.text_tags = None

    def delete_tag(self, channel_id: int, tag_id: int):
        with self.conn.cursor() as cur:
            logging.info(f'delete tag "{tag_id}" from {channel_id}')
            cur.execute('DELETE FROM tags WHERE channel_id = %s AND id = %s',
                        (channel_id, tag_id))
            self.channel(channel_id).tags = None
            self.purge_text_to_tag_cache(channel_id)
            return cur.rowcount

    def get_text_tags(self, channel_id: int) -> Dict[int, Set[int]]:
        ch = self.channel(channel_id)
        if ch.text_tags:
            return ch.text_tags
        z: Dict[int, Set[int]] = {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT tt.text_id, tt.tag_id FROM texts t JOIN text_tags tt ON tt.text_id = t.id WHERE t.channel_id = %s", [channel_id])
            for row in cur.fetchall():
                text, tag = row
                if text not in z:
                    z[text] = set()
                z[text].add(tag)
        ch.text_tags = z
        return z

    def add_text_tag(self, channel_id: int, text: int, tag: int):
        self.purge_text_to_tag_cache(channel_id)
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO text_tags (text_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (text, tag))

    def set_text_tags(self, channel_id: int, text_id: int, new_tags: Set[int]) -> Tuple[Optional[Set[int]], bool]:
        """returns previous and new tags if text exists"""
        txt_value, current_tags = db().get_text(channel_id, text_id)
        if not txt_value:
            return (None, False)
        self.delete_text_tags(channel_id, text_id)
        for x in new_tags:
            self.add_text_tag(channel_id, text_id, x)
        return (current_tags, True)

    def delete_text_tags(self, channel_id: int, text_id: int) -> int:
        self.purge_text_to_tag_cache(channel_id)
        with self.conn.cursor() as cur:
            cur.execute(
                'DELETE FROM text_tags WHERE text_id = %s', (text_id, ))
            return cur.rowcount

    def delete_text(self, channel_id: int, text_id: int) -> int:
        self.purge_text_to_tag_cache(channel_id)
        with self.conn.cursor() as cur:
            cur.execute(
                'DELETE FROM texts WHERE id = %s AND channel_id = %s', (text_id, channel_id))
            return cur.rowcount

    def get_text(self, channel_id: int, id: int) -> Tuple[Optional[str], Optional[Set[int]]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM texts WHERE channel_id = %s AND id = %s",
                        [channel_id, id])
            row = cur.fetchone()
            if not row:
                return None, None
            # TODO: really need to return tags?
            return row[0], self.get_text_tags(channel_id).get(id)

    def add_text(self, channel_id: int, value: str) -> Tuple[int, bool]:
        with self.conn.cursor() as cur:
            cur.execute(
                'SELECT id FROM texts WHERE channel_id = %s and value = %s', (channel_id, value))
            row = cur.fetchone()
            if row:
                return row[0], False
            self.purge_text_to_tag_cache(channel_id)
            cur.execute('INSERT INTO texts (channel_id, value) VALUES (%s, %s) ON CONFLICT ON CONSTRAINT uniq_text_value DO UPDATE SET value = %s RETURNING id;',
                        (channel_id, value, value))
            return cur.fetchone()[0], True

    def set_text(self, channel_id: int, value: str, id: int) -> Optional[str]:
        txt, _ = self.get_text(channel_id, id)
        if not txt:
            return None
        with self.conn.cursor() as cur:
            cur.execute(
                'UPDATE texts SET value = %s WHERE channel_id = %s and id = %s', (value, channel_id, id))
            return txt

    def text_search(self, channel_id: int, txt: str, q: str = '') -> List[Tuple[int, str, Set[int]]]:
        logging.info(f'text search "{txt}" "{q}"')
        with self.conn.cursor() as cur:
            cur.execute('select id, value from texts WHERE (channel_id = %s) AND (value LIKE %s)',
                        (channel_id, '%' + escape_like(txt.strip()) + '%'))
            qt: Optional[lark.Tree] = None
            texts: Optional[Dict[int, Set[int]]]
            texts = self.get_text_tags(channel_id)
            if q:
                qt = query.parse_query(self.get_tags(channel_id)[0], q)
            z: List[Tuple[int, str, Set[int]]] = []
            for row in cur.fetchall():
                text_id, text = row[0], row[1]
                tags = texts.get(text_id, set())
                if not qt or (tags and query.match_tags(qt, tags)):
                    z.append((text_id, text, tags))
            return z

    def all_texts(self, channel_id: int) -> List[Tuple[int, str, Set[int]]]:
        with self.conn.cursor() as cur:
            cur.execute(
                'SELECT id, value from texts t WHERE (channel_id = %s)', (channel_id,))
            tt = self.get_text_tags(channel_id)
            return [(row[0], row[1], tt.get(row[0], set())) for row in cur.fetchall()]

    def get_texts_matching_tags(self, channel_id: int, q: str) -> Type[dllist]:
        q = q.strip()
        ch = self.channel(channel_id)
        z = ch.text_queries.get(q, None, True)
        if z:
            return z
        texts = self.get_text_tags(channel_id)
        match: List[int] = []
        qt: Optional[lark.Tree] = None
        if q:
            qt = query.parse_query(self.get_tags(channel_id)[0], q)
        for text_id, tag_ids in texts.items():
            if not qt or query.match_tags(qt, tag_ids):
                match.append(text_id)
        self.rng.shuffle(match)
        ch.text_queries[q] = dllist(match)
        return ch.text_queries[q]

    def get_random_text(self, channel_id: int, q: str) -> Tuple[Optional[str], Optional[Set[int]]]:
        tt = self.get_texts_matching_tags(channel_id, q)
        if tt.size == 0:
            return None, None
        j = int(self.rng.pareto(4) * tt.size) % tt.size
        node = tt.nodeat(j)
        tt.remove(node)
        tt.append(node)
        return self.get_text(channel_id, node.value)

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


def cursor() -> psycopg2.extensions.cursor:
    return db().conn.cursor()
