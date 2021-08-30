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

"""Discord bot entry point."""

import discord
import os
import random
import psycopg2
import re
from cachetools import TTLCache
import logging
import jinja2
from jinja2.sandbox import SandboxedEnvironment

logging.basicConfig(
    filename='main.log',
    filemode='a',
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO)
stdoutHandler = logging.StreamHandler()
stdoutHandler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(stdoutHandler)

client = discord.Client()
conn = psycopg2.connect("dbname=rabbit user=rabbit password=rabbit")
cache = TTLCache(maxsize=100, ttl=1)  # TODO: configure.


def load_template(name):
    logging.info(f'loading template {name}')
    if ':' not in name:
        logging.error(f"bad template name '{name}'")
        return None
    guild_id, template_name = name.split(':', 1)
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM templates WHERE guild_id = %s AND name = %s;",
                    [int(guild_id), template_name])
        z = cur.fetchone()[0]
        logging.info(f'template {name} = {z}')
        return z


templates = SandboxedEnvironment(
    loader=jinja2.FunctionLoader(load_template),
    autoescape=jinja2.select_autoescape(),
)

with conn.cursor() as cur:
    cur.execute('''CREATE TABLE IF NOT EXISTS test
          (id SERIAL,
          value TEXT);''')
    cur.execute('''CREATE TABLE IF NOT EXISTS templates
          (id SERIAL,
          guild_id NUMERIC,
          author_id NUMERIC,
          name VARCHAR(50),
          text TEXT);''')
    cur.execute('''CREATE TABLE IF NOT EXISTS lists
          (id SERIAL,
          guild_id NUMERIC,
          author_id NUMERIC,
          list varchar(50),
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
                "SELECT DISTINCT name FROM templates WHERE guild_id = %s;", [guild_id])
            cache[key] = [x[0] for x in cur.fetchall()]
    return cache[key]


def get_messages_lists(guild_id, list):
    key = f'get_lists_{guild_id}_{list}'
    if not key in cache:
        print('loading', key)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM lists WHERE guild_id = %s AND list = %s;", [
                        guild_id, list])
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
            f'guild={guild_id} author={message.author.id} not editor called +set-template')
        await message.channel.send('you have to be a moderator or administrator to use "+add-list"')
        return
    m = re.match(r'^\+set-template +(\S+) +(\S.*)$', txt)
    if not m:
        await message.channel.send("Bad message format. Use '+set-template <name> <message>'")
        return
    with conn.cursor() as c:
        name = m.group(1)
        text = m.group(2)
        c.execute(
            'DELETE FROM templates WHERE guild_id = %s AND name = %s', (guild_id, name))
        c.execute('INSERT INTO templates (guild_id, author_id, name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                  (guild_id, message.author.id, name, text))
        id = c.fetchone()[0]
        conn.commit()
        logging.info(
            f"guild={guild_id} author={message.author.id} added new template '{name}' '{text}' #{id}")
        templates.cache.clear()
        await message.channel.send(f"Successfully added new template '{name}' '{text}' #{id}")


@client.event
async def on_message(message):
    # Don't react to own messages.
    if message.author == client.user:
        return
    txt = message.content
    # No commands.
    if '+' not in txt:
        return
    if txt.startswith('+set-template'):
        await set_template(message)
        return
    commands = [x[1:] for x in txt.split(' ') if x.startswith('+')]
    logging.info(f'commands {commands}')
    template_names = get_templates(message.guild.id)
    logging.info(f'template names {template_names}')
    for cmd in commands:
        if cmd in template_names:
            # ids = get_messages_lists(message.guild.id, cmd)
            # print(ids)
            # if not ids:
            #     continue
            # id = random.choice(ids)
            # print(id)
            # t = get_message(id)
            try:
                t = templates.get_template(f'{message.guild.id}:{cmd}')
                if t:
                    await message.channel.send(t.render())
                else:
                    logging.error(f'guild={message.guild.id} author={message.author.id} no template {cmd}')
            except Exception as e:
                logging.error(f'guild={message.guild.id} author={message.author.id} rendering issue {e}')

if __name__ == "__main__":
    # with conn.cursor() as cur:
    #     cur.execute(f"INSERT INTO test (value) VALUES ('{random.randrange(1, 10000)}');")
    #     conn.commit()
    # with conn.cursor() as cur:
    #     cur.execute("SELECT id, value FROM test")
    #     for r in  cur.fetchall():
    #         print(r[0], r[1])
    logging.info('started')
    client.run(os.getenv('TOKEN'))
