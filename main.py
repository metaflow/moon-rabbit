#!/usr/bin/python
#
# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Permissions integer: 240518548544
# https://discord.com/api/oauth2/authorize?client_id=880861994788470785&permissions=240518548544&scope=bot

# TODO store variables
# TODO debug info: log for last X commands

"""Discord bot entry point."""

import discord
import os
import random
import time
import psycopg2
import functools
import re
from cachetools import TTLCache
import logging
import jinja2
import argparse
from jinja2.sandbox import SandboxedEnvironment
from twitchio.ext import commands

logging.basicConfig(
    handlers=[logging.FileHandler('main.log', 'a', 'utf-8')],
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO)
stdoutHandler = logging.StreamHandler()
stdoutHandler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(stdoutHandler)

conn = psycopg2.connect(os.getenv('DB_CONNECTION'))
cache = TTLCache(maxsize=100, ttl=1)  # TODO: configure.

def new_channel_id():
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(id) FROM channels")
        row = cur.fetchone()
        if row:
            return row[0] + 1
        return 0

@functools.lru_cache(maxsize=1000)
def twitch_channel_info(name):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, twitch_command_prefix FROM channels WHERE twitch_channel_name = %s", [name])
        row = cur.fetchone()
        if row:
            id = row[0]
            prefix = row[1]
            logging.info(f"got Twitch channel ID '{name}' #{id} '{prefix}'")
            return id, prefix
        id = new_channel_id()
        prefix = '+'
        cur.execute('INSERT INTO channels (twitch_channel_name, twitch_command_prefix) VALUES (%s, %s, %s)', [name, prefix])
        logging.info(f"added Twitch channel ID '{name}' #{id} '{prefix}'")
        conn.commit()
        return id, prefix


@functools.lru_cache(maxsize=1000)
def discord_channel_info(guild_id):
    with conn.cursor() as cur:
        cur.execute("SELECT id, discord_command_prefix FROM channels WHERE discord_guild_id = %s", [guild_id])
        row = cur.fetchone()
        if row:
            id = row[0]
            prefix = row[1]
            logging.info(f"got Discord channel ID '{cur}' '{prefix}' #{id}")
            return id
        id = new_channel_id()
        prefix = '+'
        cur.execute('INSERT INTO channels (id, discord_guild_id, discord_command_prefix) VALUES (%s, %s, %s)', [id, guild_id, prefix])
        conn.commit()
        logging.info(f"added Discord channel ID '{cur}' #{id}")
        return id, prefix


def load_template(name):
    logging.info(f'loading template {name}')
    if ':' not in name:
        logging.error(f"bad template name '{name}'")
        return None
    type, channel_id, id = name.split(':', 2)
    if type == 'cmd':
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM commands WHERE channel_id = %s AND name = %s;",
                        [int(channel_id), id])
            z = cur.fetchone()[0]
            logging.info(f'template {name} = {z}')
            return z
    if type == 'list':
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM lists WHERE channel_id = %s AND id = %s;",
                        [int(channel_id), id])
            z = cur.fetchone()[0]
            logging.info(f'template {name} = {z}')
            return z
    raise f'unknown template type {type} for name `{name}`'


def lists_ids(channel_id, list):
    key = f'get_lists_{channel_id}_{list}'
    if not key in cache:
        print('loading', key)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM lists WHERE channel_id = %s AND list_name = %s;",
                        [channel_id, list])
            cache[key] = [x[0] for x in cur.fetchall()]
    return cache[key]


@jinja2.pass_context
def list(ctx, a):
    global templates
    channel_id = ctx.get('channel_id')
    ids = lists_ids(channel_id, a)
    id = random.choice(ids)
    vars = ctx.get_all()
    vars['_render_depth'] += 1
    if vars['_render_depth'] > 5:
        logging.error('rendering depth is > 5')
        return '?'
    t = templates.get_template(f'list:{channel_id}:{id}')
    return t.render(vars)


def mention(msg):
    if msg.mentions:
        return ' '.join([x.mention for x in msg.mentions])
    humans = [m for m in msg.channel.members if not m.bot and m.id != msg.author.id]
    online = [m for m in humans if m.status == discord.Status.online]
    if online:
        return random.choice(online).mention
    if humans:
        return random.choice(humans).mention
    return msg.author.mention


templates = SandboxedEnvironment(
    # loader=jinja2.FunctionLoader(load_template),
    autoescape=jinja2.select_autoescape(),
)

templates.loader = jinja2.FunctionLoader(load_template)
templates.globals['list'] = list


def get_templates(channel_id):
    key = f'get_templates_{channel_id}'
    if not key in cache:
        print('loading', key)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT name FROM commands WHERE channel_id = %s;", [channel_id])
            cache[key] = [x[0] for x in cur.fetchall()]
    return cache[key]


def get_message(id):
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM lists WHERE id = %s", [id])
        return cur.fetchone()[0]


async def fn_cmd_set(log, channel_id, author, txt):
    '''
    txt format '<name> [<value>]'
    missing value will drop the command
    '''
    parts = txt.split(' ', 1)
    name = parts[0]
    with conn.cursor() as c:
        c.execute(
            'DELETE FROM commands WHERE channel_id = %s AND name = %s', (channel_id, name))
        if len(parts) == 1:
            return f"Deleted command '{name}'"
        text = parts[1]
        c.execute('INSERT INTO commands (channel_id, author_id, name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                  (channel_id, author, name, text))
        id = c.fetchone()[0]
        conn.commit()
        logging.info(
            f"channel={channel_id} author={author} added new template '{name}' '{text}' #{id}")
        templates.cache.clear()
        return f"Added new command '{name}' '{text}' #{id}"


async def fn_add_list(log, channel_id, author, txt):
    name, text = txt.split(' ', 1)
    with conn.cursor() as c:
        c.execute('INSERT INTO lists (channel_id, author_id, list_name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                  (channel_id, author, name, text))
        id = c.fetchone()[0]
        conn.commit()
        log.info(f"added new list item '{name}' '{text}' #{id}")
        # TODO cache clear
        return f"Added new list '{name}' item '{text}' #{id}"


async def fn_list_search(log, channel_id, _, txt):
    parts = txt.split(' ', 1)
    with conn.cursor() as c:
        if len(parts) > 1:
            q = '%' + \
                parts[1].replace('=', '==').replace(
                    '%', '=%').replace('_', '=_') + '%'
            c.execute("select id, text from lists where (channel_id = %s) AND (list_name = %s) AND (text LIKE %s)",
                      (channel_id, parts[0], q))
        else:
            c.execute("select id, text from lists where (channel_id = %s) AND (list_name = %s)",
                      (channel_id, parts[0]))
        rr = []
        for row in c.fetchall():
            rr.append(f"#{row[0]} '{row[1]}'")
        # TODO: clear cache
        if not rr:
            return "no results"
        else:
            return '\n'.join(rr)


async def fn_delete_list_item(log, channel_id, author, id):
    result = ''
    with conn.cursor() as c:
        if id.isnumeric():
            c.execute(
                'DELETE FROM lists WHERE channel_id = %s AND id = %s', (channel_id, id))
            result = f"Deleted list item #{id}"
        else:
            parts = id.split(' ', 1)
            if len(parts) < 2 or parts[0] != 'all':
                return "command format is <number> or 'all <list name>'"
            c.execute(
                'DELETE FROM lists WHERE channel_id = %s AND list_name = %s', (channel_id, parts[1]))
            log.info(f"Deleted all items in list '{parts[1]}'")
            result = f"Deleted all items in list '{parts[1]}'"
        conn.commit()
    return result

cmd_set = 'set'
cmd_list_add = 'list-add'
cmd_list_rm = 'list-rm'
cmd_list_search = 'list-search'
all_commands = {
    cmd_set: fn_cmd_set,
    cmd_list_add: fn_add_list,
    cmd_list_rm: fn_delete_list_item,
    cmd_list_search: fn_list_search
}

class InvocationLog():

    def __init__(self, prefix):
        self.messages = []
        self.prefix = prefix
    
    def info(self, s):
        logging.info(self.prefix + s)
        self.messages.append((logging.INFO, s))

    def warning(self, s):
        logging.warning(self.prefix + s)
        self.messages.append((logging.WARNING, s))
    
    def debug(self, s):
        logging.debug(self.prefix + s)
        self.messages.append((logging.DEBUG, s))

    def error(self, s):
        logging.error(self.prefix + s)
        self.messages.append((logging.ERROR, s))

async def process_message(il, channel_id, editor, prefix, vars, txt):
    il.info(f"message text '{txt}'")
    if prefix not in txt:
        # No commands.
        return
    admin_command = False
    for c in all_commands:
        if txt.startswith(prefix + c):
            admin_command = True
            break
    author = vars['author']
    if admin_command:
        if not editor:
            il.warning(f'not editor called an admin command')
            return f"you don't have permissions to do that", ""
        txt = txt[len(prefix):]
        results = []
        for p in txt.split('\n' + prefix):
            cmd, t = p.split(' ', 1)
            if cmd not in all_commands:
                il.info(f'unknown command {cmd}')
                continue
            il.info(f"running cmd {cmd} '{t}'")
            results.append(await all_commands[cmd](il, channel_id, author, t))
        reply = '\n'.join(results)
        return reply if reply != '' else 'OK', ''
    commands = [x[1:] for x in txt.split(' ') if x.startswith(prefix)]
    template_names = get_templates(channel_id)
    new_messages = []
    for cmd in commands:
        vars['_render_depth'] = 0
        vars['channel_id'] = channel_id
        if cmd in template_names:
            try:
                t = templates.get_template(f'cmd:{channel_id}:{cmd}')
                if t:
                    new_messages.append(t.render(vars))
                else:
                    il.warning(f"'{cmd}' is not defined")
            except Exception as e:
                logging.error(f"failed to render '{cmd}': {e}")
    return '', new_messages

class DiscordClient(discord.Client):
    async def on_ready(self):
        print('We have logged in as {0.user}'.format(self))

    async def on_message(self, message):
        # Don't react to own messages.
        if message.author == client.user:
            return
        channel_id, prefix = discord_channel_info(message.guild.id)
        il = InvocationLog(f'guild={message.guild.id} channel={channel_id} author={message.author.id}')
        permissions = message.author.guild_permissions
        editor = permissions.ban_members or permissions.administrator
        vars = {
            'author': message.author.mention,
            'mention': mention(message),
        }
        reply, new_messages = await process_message(il, channel_id, editor, prefix, vars, message.content)
        if reply != '':
            await message.reply(reply)
        for m in new_messages:
            await message.channel.send(m)


class TwitchBot(commands.Bot):
    def __init__(self, token):
        super().__init__(token=token)

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        print(f'Logged in as | {self.nick}')
        # TODO: join channels defined in table
        await self.join_channels(['equanimity_rulez'])

    async def event_message(self, message):
        # Messages with echo set to True are messages sent by the bot...
        # For now we just want to ignore them...
        if message.echo:
            return

        # Print the contents of our message to console...
        logging.info(message.content)
        

        # Since we have commands and are overriding the default `event_message`
        # We must let the bot know we want to handle and invoke our commands...
        # await self.handle_commands(message)


def init_db():
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS commands
            (id SERIAL,
            channel_id INT,
            author_id NUMERIC,
            name VARCHAR(50),
            text TEXT);''')
        cur.execute('''CREATE TABLE IF NOT EXISTS lists
            (id SERIAL,
            channel_id INT,
            author_id NUMERIC,
            list_name varchar(50),
            text TEXT);''')
        cur.execute('''CREATE TABLE IF NOT EXISTS channels
            (id INT,
            discord_guild_id NUMERIC,
            discord_command_prefix varchar(10),
            twitch_channel_name TEXT,
            twitch_command_prefix varchar(10));''')
    conn.commit()


if __name__ == "__main__":
    print('starting')
    parser = argparse.ArgumentParser(description='moon rabbit')
    parser.add_argument('--twitch', action='store_true')
    parser.add_argument('--discord', action='store_true')
    parser.add_argument('--add-channel', action='store_true')
    parser.add_argument('--discord_guild_id')
    parser.add_argument('--discord_command_prefix', default='+')
    parser.add_argument('--twitch_channel_name')
    parser.add_argument('--twitch_command_prefix', default='+')
    parser.add_argument('--channel_id')
    args = parser.parse_args()
    print(f'args {args}')
    init_db()
    if args.discord:
        logging.info('starting Discord Bot')
        client = DiscordClient(intents=discord.Intents.all())
        client.run(os.getenv('DISCORD_TOKEN'))
    if args.twitch:
        logging.info('starting Twitch Bot')
        bot = TwitchBot(token=os.getenv('TWITCH_ACCESS_TOKEN'), initial_channels=[])
        bot.run()
    print('add --twitch or --discord argument to run bot')
