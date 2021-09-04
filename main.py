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

# TODO delete commands / list items
# TODO store variables
# TODO debug info: log for last X commands
# TODO search lists
# TODO multiline commands

"""Discord bot entry point."""

import discord
import os
import random
import time
import psycopg2
import re
from cachetools import TTLCache
import logging
import jinja2
from jinja2.sandbox import SandboxedEnvironment


logging.basicConfig(
    handlers=[logging.FileHandler('main.log', 'a', 'utf-8')],
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO)
stdoutHandler = logging.StreamHandler()
stdoutHandler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(stdoutHandler)

client = discord.Client(intents=discord.Intents.all())
conn = psycopg2.connect(os.getenv('DB_CONNECTION'))
cache = TTLCache(maxsize=100, ttl=1)  # TODO: configure.


def load_template(name):
    logging.info(f'loading template {name}')
    if ':' not in name:
        logging.error(f"bad template name '{name}'")
        return None
    type, guild_id, id = name.split(':', 2)
    if type == 'cmd':
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM commands WHERE guild_id = %s AND name = %s;",
                        [int(guild_id), id])
            z = cur.fetchone()[0]
            logging.info(f'template {name} = {z}')
            return z
    if type == 'list':
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM lists WHERE guild_id = %s AND id = %s;",
                        [int(guild_id), id])
            z = cur.fetchone()[0]
            logging.info(f'template {name} = {z}')
            return z
    raise f'unknown template type {type} for name `{name}`'


def lists_ids(guild_id, list):
    key = f'get_lists_{guild_id}_{list}'
    if not key in cache:
        print('loading', key)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM lists WHERE guild_id = %s AND list_name = %s;", [
                        guild_id, list])
            cache[key] = [x[0] for x in cur.fetchall()]
    return cache[key]


@jinja2.pass_context
def list(ctx, a):
    global templates
    msg = ctx.get('_msg')
    guild_id = msg.guild.id
    ids = lists_ids(guild_id, a)
    id = random.choice(ids)
    vars = ctx.get_all()
    vars['_render_depth'] += 1
    if vars['_render_depth'] > 5:
        logging.error('rendering depth is > 5')
        return '?'
    t = templates.get_template(f'list:{guild_id}:{id}')
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
    loader=jinja2.FunctionLoader(load_template),
    autoescape=jinja2.select_autoescape(),
)

templates.globals['list'] = list

with conn.cursor() as cur:
    cur.execute('''CREATE TABLE IF NOT EXISTS test
          (id SERIAL,
          value TEXT);''')
    cur.execute('''CREATE TABLE IF NOT EXISTS commands
          (id SERIAL,
          guild_id NUMERIC,
          author_id NUMERIC,
          name VARCHAR(50),
          text TEXT);''')
    cur.execute('''CREATE TABLE IF NOT EXISTS lists
          (id SERIAL,
          guild_id NUMERIC,
          author_id NUMERIC,
          list_name varchar(50),
          text TEXT);''')
    conn.commit()


def get_templates(guild_id):
    key = f'get_templates_{guild_id}'
    if not key in cache:
        print('loading', key)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT name FROM commands WHERE guild_id = %s;", [guild_id])
            cache[key] = [x[0] for x in cur.fetchall()]
    return cache[key]


def get_message(id):
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM lists WHERE id = %s", [id])
        return cur.fetchone()[0]


async def fn_cmd_set(message, txt):
    guild_id = message.guild.id
    parts = txt.split(' ', 1)
    name = parts[0]
    with conn.cursor() as c:
        c.execute(
            'DELETE FROM commands WHERE guild_id = %s AND name = %s', (guild_id, name))
        if len(parts) == 1:
            await message.reply(f"Deleted command '{name}'")
            return
        text = parts[1]
        c.execute('INSERT INTO commands (guild_id, author_id, name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                  (guild_id, message.author.id, name, text))
        id = c.fetchone()[0]
        conn.commit()
        logging.info(
            f"guild={guild_id} author={message.author.id} added new template '{name}' '{text}' #{id}")
        templates.cache.clear()
        await message.reply(f"Added new command '{name}' '{text}' #{id}")


async def add_list(message, txt):
    guild_id = message.guild.id
    name, text = txt.split(' ', 1)
    with conn.cursor() as c:
        c.execute('INSERT INTO lists (guild_id, author_id, list_name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                  (guild_id, message.author.id, name, text))
        id = c.fetchone()[0]
        conn.commit()
        logging.info(
            f"guild={guild_id} author={message.author.id} added new list item '{name}' '{text}' #{id}")
        # TODO cache clear
        await message.channel.send(f"Added new list '{name}' item '{text}' #{id}")


async def fn_list_search(message, txt):
    guild_id = int(message.guild.id)
    parts = txt.split(' ', 1)
    with conn.cursor() as c:
        if len(parts) > 1:
            q = '%' + parts[1].replace('=', '==').replace('%', '=%').replace('_', '=_') + '%'
            c.execute("select id, text from lists where (guild_id = %s) AND (list_name = %s) AND (text LIKE %s)",
                    (guild_id, parts[0], q))
        else:
            c.execute("select id, text from lists where (guild_id = %s) AND (list_name = %s)",
                    (guild_id, parts[0]))
        rr = []
        for row in c.fetchall():
            rr.append(f"#{row[0]} '{row[1]}'")
        # TODO: clear cache
        if not rr:
            await message.reply("no results")
            return
        await message.reply('\n'.join(rr))


async def fn_delete_list_item(message, id):
    guild_id = int(message.guild.id)
    with conn.cursor() as c:
        if id.isnumeric():
            c.execute(
                'DELETE FROM lists WHERE guild_id = %s AND id = %s', (guild_id, id))
            logging.info(
                f"guild={guild_id} author={message.author.id} Deleted all items in list '{id}'")
            await message.channel.send(f"Deleted list item #{id}")
        else:
            c.execute(
                'DELETE FROM lists WHERE guild_id = %s AND list_name = %s', (guild_id, id))
            await message.channel.send(f"Deleted all items in list '{id}'")
        conn.commit()
        templates.cache.clear()

cmd_set = 'set'
cmd_list_add = 'list-add'
cmd_list_rm = 'list-rm'
cmd_list_search = 'list-search'
all_commands = {
    cmd_set: fn_cmd_set,
    cmd_list_add: add_list,
    cmd_list_rm: fn_delete_list_item,
    cmd_list_search: fn_list_search
}


@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))

# TODO multiline commands?


@client.event
async def on_message(message):
    # Don't react to own messages.
    if message.author == client.user:
        return
    txt = message.content
    logging.info(f"message text '{txt}'")
    if ('+' not in txt) and (not txt.startswith('>>')):
        # No commands.
        return
    admin_command = False
    for c in all_commands:
        if txt.startswith('>>' + c):
            admin_command = True
            break
    if admin_command:
        permissions = message.author.guild_permissions
        editor = permissions.ban_members or permissions.administrator
        if not editor:
            logging.warn(
                f'guild={guild_id} author={message.author.id} not editor called > command')
            await message.reply(f"you don't have permissions to do that")
            return
        txt = txt[2:]
        for p in txt.split('\n>>'):
            logging.info(f'raw p {p}')
            cmd, t = p.split(' ', 1)
            logging.info(f'split {cmd} {t}')
            if cmd not in all_commands:
                logging.info(f'unknown command {cmd}')
                continue
            logging.info(f"running cmd {cmd} '{t}'")
            await all_commands[cmd](message, t)
    commands = [x[1:] for x in txt.split(' ') if x.startswith('+')]
    template_names = get_templates(message.guild.id)
    for cmd in commands:
        vars = {
            '_render_depth': 0,
            '_msg': message,
            'author': message.author.mention,
            'mention': mention(message),
        }
        if cmd in template_names:
            try:  # TODO do we need that?
                t = templates.get_template(f'cmd:{message.guild.id}:{cmd}')
                if t:
                    await message.channel.send(t.render(vars))
                else:
                    logging.error(
                        f'guild={message.guild.id} author={message.author.id} no template {cmd}')
            except Exception as e:
                logging.error(
                    f'guild={message.guild.id} author={message.author.id} rendering issue {e}')

if __name__ == "__main__":
    logging.info('started')
    client.run(os.getenv('TOKEN'))
