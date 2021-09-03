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

# TODO random mention
# TODO store variables
# TODO delete commands / list items
# TODO debug info: log for last X commands
# TODO search lists

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


@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))


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


async def set_template(message):
    txt = message.content
    guild_id = message.guild.id
    permissions = message.author.guild_permissions
    editor = permissions.ban_members or permissions.administrator
    if not editor:
        logging.warn(
            f'guild={guild_id} author={message.author.id} not editor called +set')
        await message.channel.send('you have to be a moderator or administrator to use "+add-list"')
        return
    m = re.match(r'^\+set +(\S+) +(\S.*)$', txt)
    if not m:
        await message.channel.send("Bad message format. Use '+set <name> <message>'")
        return
    with conn.cursor() as c:
        name = m.group(1)
        text = m.group(2)
        # TODO: insert or update existing.
        c.execute(
            'DELETE FROM commands WHERE guild_id = %s AND name = %s', (guild_id, name))
        c.execute('INSERT INTO commands (guild_id, author_id, name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                  (guild_id, message.author.id, name, text))
        id = c.fetchone()[0]
        conn.commit()
        logging.info(
            f"guild={guild_id} author={message.author.id} added new template '{name}' '{text}' #{id}")
        templates.cache.clear()
        await message.channel.send(f"Added new command '{name}' '{text}' #{id}")


async def add_list(message):
    txt = message.content
    guild_id = message.guild.id
    permissions = message.author.guild_permissions
    editor = permissions.ban_members or permissions.administrator
    if not editor:
        logging.warn(
            f'guild={guild_id} author={message.author.id} not editor called add_list')
        await message.channel.send('you have to be a moderator or administrator to use this command')
        return
    m = re.match(r'^\+add +(\S+) +(\S.*)$', txt)
    if not m:
        await message.channel.send("Bad message format. Use '+add <name> <message>'")
        return
    with conn.cursor() as c:
        name = m.group(1)
        text = m.group(2)
        c.execute('INSERT INTO lists (guild_id, author_id, list_name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                  (guild_id, message.author.id, name, text))
        id = c.fetchone()[0]
        conn.commit()
        logging.info(
            f"guild={guild_id} author={message.author.id} added new list item '{name}' '{text}' #{id}")
        templates.cache.clear()
        await message.channel.send(f"Added new list '{name}' item '{text}' #{id}")

@client.event
async def on_message(message):
    # Don't react to own messages.
    if message.author == client.user:
        return
    txt = message.content
    if '+' not in txt:
        # No commands.
        return
    if txt.startswith('+set '):
        await set_template(message)
        return
    if txt.startswith('+add '):
        await add_list(message)
        return
    
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
            try:
                t = templates.get_template(f'cmd:{message.guild.id}:{cmd}')
                if t:
                    await message.channel.send(t.render(vars))
                else:
                    logging.error(f'guild={message.guild.id} author={message.author.id} no template {cmd}')
            except Exception as e:
                logging.error(f'guild={message.guild.id} author={message.author.id} rendering issue {e}')

if __name__ == "__main__":
    logging.info('started')
    client.run(os.getenv('TOKEN'))
