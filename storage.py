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

from typing import Any, Dict, List, Optional, Set, Tuple, Type
from data import *
import psycopg2  # type: ignore
import psycopg2.extensions  # type: ignore
import psycopg2.extras  # type: ignore
import functools
import logging
import collections
import time
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
class TextEntry:
    id: int
    queue_nodes: Dict[int, Any]
    tags: Set[int]
    in_all: Optional[Any]


@dataclasses.dataclass
class QueryQueue:
    id: int
    queue: Type[dllist]
    parsed: lark.Tree


@dataclasses.dataclass
class ChannelCache:
    channel_id: int
    active_queries: Any  # str -> str
    query_to_id: Dict[str, int]
    queries: Dict[int, QueryQueue]
    all_text_by_id: Dict[int, TextEntry]
    all_texts_list: Type[dllist]
    # tags: Tuple[Dict[str, int], Dict[int, str]]
    tag_by_id: Dict[int, str]
    tag_by_value: Dict[str, int]
    query_counter = 0


class DB:
    def __init__(self, connection):
        self.conn = psycopg2.connect(connection)
        self.conn.set_session(autocommit=True)
        self.channels: Dict[int, ChannelCache] = {}
        self.logs = {}
        self.rng = np.random.default_rng()
        self.init_db()

    def channel(self, channel_id: int) -> ChannelCache:
        if channel_id in self.channels:
            return self.channels[channel_id]
        ch = ChannelCache(
            channel_id=channel_id,
            active_queries=ttldict2.TTLDict(
                ttl_seconds=float(10.0 * 3600 * 24)),
            queries={},
            all_text_by_id={},
            all_texts_list=dllist(),
            tag_by_id={},
            tag_by_value={},
            query_to_id={})
        self.reload_texts(ch)
        self.reload_tags(ch)
        self.channels[channel_id] = ch
        return ch

    def recreate_tables(self):
        logging.warning('dropping and creating tables anew')
        with self.conn.cursor() as c:
            c.execute('''
DROP TABLE commands CASCADE;
DROP TABLE channels CASCADE;
DROP TABLE variables CASCADE;
DROP TABLE texts CASCADE;
DROP TABLE tags CASCADE;
DROP TABLE text_tags CASCADE;
DROP TABLE twitch_bots CASCADE;
            ''')
        self.conn.commit()
        self.init_db()

    def init_db(self):
        with self.conn.cursor() as cur:
            with open('scheme.sql', 'r') as f:
                cur.execute(f.read())
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
    def discord_channel_info(self, cur: psycopg2.extensions.cursor, guild_id: str):
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

    def reload_tags(self, ch: ChannelCache):
        with self.conn.cursor() as cur:
            ch.tag_by_id.clear()
            ch.tag_by_value.clear()
            cur.execute(
                "SELECT id, value FROM tags WHERE channel_id = %s", [ch.channel_id])
            for row in cur.fetchall():
                ch.tag_by_id[row[0]] = row[1]
                ch.tag_by_value[row[1]] = row[0]

    def reload_texts(self, ch: ChannelCache):
        ch.all_texts_list.clear()
        ch.queries.clear()
        ch.query_to_id.clear()
        ch.active_queries.clear()
        ch.all_text_by_id.clear()
        z: Dict[int, Set[int]] = {}
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM texts t WHERE t.channel_id = %s", [
                        ch.channel_id])
            for row in cur.fetchall():
                z[row[0]] = set()
            cur.execute(
                "SELECT tt.text_id, tt.tag_id FROM texts t JOIN text_tags tt ON tt.text_id = t.id WHERE t.channel_id = %s", [ch.channel_id])
            for row in cur.fetchall():
                text, tag = row
                z[text].add(tag)
        lst: List[TextEntry] = []
        for text_id, tags in z.items():
            te = TextEntry(id=text_id, queue_nodes={}, tags=tags, in_all=None)
            lst.append(te)
            ch.all_text_by_id[text_id] = te
        self.rng.shuffle(lst)
        for te in lst:
            te.in_all = ch.all_texts_list.append(te)

    def new_channel_id(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT MAX(channel_id) FROM channels")
            row = cur.fetchone()
            if row and (row[0] is not None):
                return int(row[0]) + 1
            return 0

    def tag_by_id(self, channel_id: int) -> Dict[int, str]:
        return self.channel(channel_id).tag_by_id

    def tag_by_value(self, channel_id: int) -> Dict[str, int]:
        return self.channel(channel_id).tag_by_value

    def add_tag(self, channel_id: int, tag_name: str):
        if not query.good_tag_name(tag_name):
            raise Exception("bad tag name")
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO tags (channel_id, value) VALUES (%s, %s) ON CONFLICT DO NOTHING;',
                        (channel_id, tag_name))
            self.reload_tags(self.channel(channel_id))

    def delete_tag(self, channel_id: int, tag_id: int):
        with self.conn.cursor() as cur:
            cur.execute('DELETE FROM tags WHERE channel_id = %s AND id = %s',
                        (channel_id, tag_id))
            ch = self.channel(channel_id)
            self.reload_tags(ch)
            self.reload_texts(ch)
            return cur.rowcount

    def get_text_tags(self, channel_id: int, text_id: int) -> Optional[Set[int]]:
        te = self.channel(channel_id).all_text_by_id.get(text_id)
        if not te:
            return None
        return te.tags

    def get_text_tag_values(self, channel_id: int, text_id: int) -> Dict[int, Optional[str]]:
        z = {}
        with self.conn.cursor() as cur:
            cur.execute("SELECT tt.tag_id, tt.value FROM text_tags tt JOIN texts t ON t.channel_id = %s AND t.id = tt.text_id WHERE tt.text_id = %s",
                        [channel_id, text_id])
            for row in cur.fetchall():
                z[row[0]] = row[1]
            return z

    def get_text_tag_value(self, channel_id: int, text_id: int, tag_id: int) -> Optional[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT tt.value FROM text_tags tt JOIN texts t ON t.channel_id = %s AND t.id = tt.text_id WHERE tt.text_id = %s and tt.tag_id = %s",
                        [channel_id, text_id, tag_id])
            row = cur.fetchone()
            if not row:
                return None
            return row[0]

    def set_text_tags(self, channel_id: int, text_id: int, new_tags: Dict[int, Optional[str]]) -> Tuple[Optional[Dict[int, Optional[str]]], bool]:
        """returns previous and new tags if text exists"""
        ch = self.channel(channel_id)
        te: Optional[TextEntry] = ch.all_text_by_id.get(text_id)
        if not te:
            logging.info(f'text {text_id} is not found')
            return (None, False)
        previous_tags = self.get_text_tag_values(channel_id, text_id)
        with self.conn.cursor() as cur:
            cur.execute(
                'DELETE FROM text_tags WHERE text_id = %s', (text_id, ))
            for name, value in new_tags.items():
                cur.execute(
                    'INSERT INTO text_tags (text_id, tag_id, value) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING', (text_id, name, value))
        te.tags = set(new_tags.keys())
        prev: Set[int] = set(te.queue_nodes.keys())
        current: Set[int] = set()
        for qq in ch.queries.values():
            if query.match_tags(qq.parsed, te.tags):
                current.add(qq.id)
        # Remove from the queries we don't match anymore.
        for qid in (prev - current):
            node = te.queue_nodes[qid]
            node.owner().remove(node)
            te.queue_nodes.pop(qid, None)
        # Add to queries we now match.
        # Technically this is not correct and we should insert according to the global order.
        # But it's quite tricky and doesn't seems worth it for this corner case.
        for qid in (current - prev):
            qq = ch.queries[qid]
            te.queue_nodes[qid] = qq.queue.appendleft(te)
        return (previous_tags, True)

    def delete_text(self, channel_id: int, text_id: int) -> int:
        ch = self.channel(channel_id)
        te = ch.all_text_by_id.get(text_id)
        if te:
            for node in te.queue_nodes.values():
                node.owner().remove(node)
            if te.in_all:
                te.in_all.owner().remove(te.in_all)
        with self.conn.cursor() as cur:
            cur.execute(
                'DELETE FROM texts WHERE id = %s AND channel_id = %s', (text_id, channel_id))
            return cur.rowcount

    def get_text(self, channel_id: int, id: int) -> Optional[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM texts WHERE channel_id = %s AND id = %s",
                        [channel_id, id])
            row = cur.fetchone()
            if not row:
                return None
            return row[0]

    def find_text(self, channel_id: int, value: str) -> Optional[int]:
        with self.conn.cursor() as cur:
            cur.execute(
                'SELECT id FROM texts WHERE channel_id = %s and value = %s', (channel_id, value))
            row = cur.fetchone()
            if row:
                return row[0]
            return None

    def add_text(self, channel_id: int, value: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO texts (channel_id, value) VALUES (%s, %s) ON CONFLICT ON CONSTRAINT uniq_text_value DO UPDATE SET value = %s RETURNING id;',
                        (channel_id, value, value))
            text_id = cur.fetchone()[0]
            ch = self.channel(channel_id)
            te = TextEntry(id=text_id, queue_nodes={}, tags=set(), in_all=None)
            ch.all_text_by_id[text_id] = te
            te.in_all = ch.all_texts_list.append(te)
            # No need to check against queries as we don't expect any query to match a text w/o any tags.
            return text_id

    def set_text(self, channel_id: int, value: str, id: int) -> Optional[str]:
        txt = self.get_text(channel_id, id)
        if not txt:
            return None
        with self.conn.cursor() as cur:
            cur.execute(
                'UPDATE texts SET value = %s WHERE channel_id = %s and id = %s', (value, channel_id, id))
            return txt

    def text_search(self, channel_id: int, txt: str, q: str = '') -> List[Tuple[int, str, Set[int]]]:
        with self.conn.cursor() as cur:
            cur.execute('select id, value from texts WHERE (channel_id = %s) AND (value LIKE %s)',
                        (channel_id, '%' + escape_like(txt.strip()) + '%'))
            ch = self.channel(channel_id)
            qt: Optional[lark.Tree] = None
            if q:
                qt = query.parse_query(ch.tag_by_value, q)
            z: List[Tuple[int, str, Set[int]]] = []
            for row in cur.fetchall():
                text_id, text = row[0], row[1]
                tags = self.get_text_tags(channel_id, text_id)
                if not tags:
                    tags = set()
                if not qt or query.match_tags(qt, tags):
                    z.append((text_id, text, tags))
            return z

    def all_texts(self, channel_id: int) -> List[Tuple[int, str, Set[int]]]:
        with self.conn.cursor() as cur:
            cur.execute(
                'SELECT id, value from texts t WHERE (channel_id = %s)', (channel_id,))
            # tt = self.get_text_tags(channel_id)
            # self.get_tags(channel_id)
            z = []
            for row in cur.fetchall():
                text_id = int(row[0])
                value = str(row[1])
                tags = self.get_text_tags(channel_id, text_id)
                if not tags:
                    tags = set()
                z.append((text_id, value, tags))
            return z

    def get_random_text_id(self, channel_id: int, q: str) -> Optional[int]:
        ch = self.channel(channel_id)
        qq: Optional[QueryQueue] = None
        qid: Optional[int] = ch.query_to_id.get(q)
        if qid is None:
            # Add a new query an match every text against it.
            ch.query_counter += 1
            qid = ch.query_counter
            qq = QueryQueue(id=qid, queue=dllist(),
                            parsed=query.parse_query(ch.tag_by_value, q))
            t: TextEntry
            for t in ch.all_texts_list:
                if query.match_tags(qq.parsed, t.tags):
                    t.queue_nodes[qid] = qq.queue.append(t)
        else:
            qq = ch.queries.get(qid)
            if not qq:
                raise Exception(
                    f'query with id {qid} not found in query_to_id')
        if qq.queue.size == 0:
            return None
        j = int(self.rng.pareto(4) * qq.queue.size) % qq.queue.size
        # Move picked text to the end of all queues.
        node = qq.queue.nodeat(j)
        t = node.value
        if t.in_all:
            ll = t.in_all.owner()
            ll.remove(t.in_all)
            ll.appendnode(t.in_all)
        for qn in t.queue_nodes.values():
            ll = qn.owner()
            ll.remove(qn)
            ll.appendnode(qn)
        # Cache query at the very end to avoid caching invalid queries.
        ch.query_to_id[q] = qid
        ch.queries[qid] = qq
        ch.active_queries[q] = '+'
        return t.id

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

    def list_variables(self, channel_id: int, category: str) -> List[Tuple[str, str]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT name, value FROM variables WHERE channel_id = %s AND category = %s",
                        [channel_id, category])
            z = []
            for row in cur.fetchall():
                z.append((row[0], row[1]))
            return z

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

    def get_discord_allowed_channels(self, channel_id: int) -> Set[str]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT discord_allowed_channels FROM channels WHERE channel_id = %s",
                [channel_id])
            row = cur.fetchone()
            if not row or not row[0]:
                return set()
            return set(row[0].split(','))
    
    def set_discord_allowed_channels(self, channel_id: int, allowed: Set[str]):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE channels SET discord_allowed_channels = %s WHERE channel_id = %s",
                [','.join(allowed), channel_id])
            self.conn.commit()

    def expire_old_queries(self):
        for ch in self.channels.values():
            prev = set(ch.active_queries.keys())
            ch.active_queries.drop_old_items()
            active = set(ch.active_queries.keys())
            for query_text in (prev - active):
                qid = ch.query_to_id[query_text]
                qq = ch.queries[qid]
                logging.info(f'query {query_text} {qid} has expired')
                t: TextEntry
                for t in qq.queue:
                    t.queue_nodes.pop(qid, None)
                qq.queue.clear()
                ch.queries.pop(qid, None)
                ch.query_to_id.pop(query_text, None)

    def check_database(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, discord_guild_id, twitch_channel_name FROM channels")            
            for row in cur.fetchall():
                logging.info(row)

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
