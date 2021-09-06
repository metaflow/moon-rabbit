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

# TODO store variables
# TODO debug info: log for last X commands
# TODO context - commands only for discord / twitch
# TODO reactions in discord

"""Discord bot entry point."""

from cachetools import TTLCache
from jinja2.sandbox import SandboxedEnvironment
from twitchio.ext import commands
import argparse
import discord
import jinja2
import logging
import os
import sys
import random
import re
import ttldict2
import storage

logging.basicConfig(
    handlers=[logging.FileHandler('main.log', 'a', 'utf-8')],
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO)
stdoutHandler = logging.StreamHandler()
stdoutHandler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(stdoutHandler)

db = storage.DB(os.getenv('DB_CONNECTION'))

@jinja2.pass_context
def list(ctx, a):
    global templates
    channel_id = ctx.get('channel_id')
    ids = db.lists_ids(channel_id, a)
    id = random.choice(ids)
    vars = ctx.get_all()
    vars['_render_depth'] += 1
    if vars['_render_depth'] > 5:
        logging.error('rendering depth is > 5')
        return '?'
    t = templates.get_template(f'list:{channel_id}:{id}')
    return t.render(vars)


templates = SandboxedEnvironment(autoescape=jinja2.select_autoescape())
templates.loader = jinja2.FunctionLoader(lambda x : db.load_template(x))
templates.globals['list'] = list

async def fn_cmd_set(cur, log, channel_id, variables, txt):
    '''
    txt format '<name> [<value>]'
    missing value will drop the command
    '''
    parts = txt.split(' ', 1)
    name = parts[0]
    if len(parts) == 1:
        cur.execute(
            'DELETE FROM commands WHERE channel_id = %s AND name = %s', (channel_id, name))
        return f"Deleted command '{name}'"
    text = parts[1]
    cur.execute('''
    INSERT INTO commands (channel_id, author, name, text)
    VALUES (%(channel_id)s, %(author)s, %(name)s, %(text)s)
    ON CONFLICT ON CONSTRAINT uniq_name_in_channel DO
    UPDATE SET text = %(text)s RETURNING id;''',
                {'channel_id': channel_id,
                 'author': variables['author_name'],
                 'name': name,
                 'text': text,
                 })
    id = cur.fetchone()[0]
    log.info(
        f"channel={channel_id} author={variables['author_name']} added new template '{name}' '{text}' #{id}")
    templates.cache.clear()
    return f"Added new command '{name}' '{text}' #{id}"


async def fn_add_list(cur, log, channel_id, variables, txt):
    name, text = txt.split(' ', 1)
    cur.execute('INSERT INTO lists (channel_id, author, list_name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                (channel_id, variables['author_name'], name, text))
    id = cur.fetchone()[0]
    log.info(f"added new list item '{name}' '{text}' #{id}")
    # TODO cache clear
    return f"Added new list '{name}' item '{text}' #{id}"


async def fn_list_search(cur, log, channel_id, variables, txt):
    parts = txt.split(' ', 1)
    if len(parts) > 1:
        q = '%' + \
            parts[1].replace('=', '==').replace(
                '%', '=%').replace('_', '=_') + '%'
        cur.execute("select id, text from lists where (channel_id = %s) AND (list_name = %s) AND (text LIKE %s)",
                    (channel_id, parts[0], q))
    else:
        cur.execute("select id, text from lists where (channel_id = %s) AND (list_name = %s)",
                    (channel_id, parts[0]))
    rr = []
    for row in cur.fetchall():
        rr.append(f"#{row[0]}: {row[1]}")
    # TODO: clear cache
    if not rr:
        return "no results"
    else:
        return '\n'.join(rr)


async def fn_delete_list_item(cur, log, channel_id, variables, txt):
    if txt.isnumeric():
        cur.execute(
            'DELETE FROM lists WHERE channel_id = %s AND id = %s', (channel_id, txt))
        return f"Deleted list item #{id}"
    parts = txt.split(' ', 1)
    if len(parts) < 2 or parts[0] != 'all':
        return "command format is <number> or 'all <list name>'"
    cur.execute(
        'DELETE FROM lists WHERE channel_id = %s AND list_name = %s', (channel_id, parts[1]))
    return f"Deleted all items in list '{parts[1]}'"

class InvocationLog():
    def __init__(self, prefix):
        self.messages = []
        self.prefix = prefix + ' '

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

all_commands = {
    'set': fn_cmd_set,
    'list-add': fn_add_list,
    'list-rm': fn_delete_list_item,
    'list-search': fn_list_search,
}

async def process_message(il, channel_id, variables):
    txt = variables['text']
    prefix = variables['prefix']
    il.info(f"message text '{txt}'")
    if prefix not in txt:
        # No commands.
        return
    admin_command = False
    for c in all_commands:
        if txt.startswith(prefix + c):
            admin_command = True
            break
    if admin_command:
        if not variables['is_mod']:
            il.warning(f'non mod called an admin command')
            return f"you don't have permissions to do that", ""
        txt = txt[len(prefix):]
        results = []
        for p in txt.split('\n' + prefix):
            cmd, t = p.split(' ', 1)
            if cmd not in all_commands:
                il.info(f'unknown command {cmd}')
                continue
            il.info(f"running cmd {cmd} '{t}'")
            try:
                r = await all_commands[cmd](db.conn.cursor(), il, channel_id, variables, t)
                il.info(f"command result '{r}'")
                results.append(r)
                db.conn.commit()
            except Exception as e:
                db.conn.rollback()
                results.append('error occurred')
                il.error(f'failed to execute {cmd}: {e}')
        reply = '\n'.join(results)
        return reply if reply != '' else 'OK', ''
    commands = [x[1:] for x in txt.split(' ') if x.startswith(prefix)]
    template_names = db.get_templates(channel_id)
    new_messages = []
    for cmd in commands:
        variables['_render_depth'] = 0
        variables['channel_id'] = channel_id
        if cmd in template_names:
            try:
                t = templates.get_template(f'cmd:{channel_id}:{cmd}')
                if t:
                    new_messages.append(t.render(variables))
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
        logging.info(f'guild id {message.guild.id} {str(message.guild.id)}')
        try:
            channel_id, prefix = db.discord_channel_info(
                db.conn.cursor(), str(message.guild.id))
            db.conn.commit()
        except Exception as e:
            logging.error(f"'discord_channel_info': {e}")
            db.conn.rollback()
            return
        il = InvocationLog(
            f'guild={message.guild.id} channel={channel_id} author={message.author.id}')
        il.info(f'message {message.content}')
        permissions = message.author.guild_permissions
        variables = {
            'author': message.author.mention,
            'author_name': str(message.author.display_name),
            'mention': self.mentions(message),
            'media': 'discord',
            'text': message.content,
            'is_mod': permissions.ban_members or permissions.administrator,
            'prefix': prefix,
        }
        reply, new_messages = await process_message(il, channel_id, variables)
        db.add_log(channel_id, il)
        if reply != '':
            await message.reply(reply)
        for m in new_messages:
            await message.channel.send(m)

    def mentions(self, msg):
        if msg.mentions:
            return ' '.join([x.mention for x in msg.mentions])
        humans = [m for m in msg.channel.members if not m.bot and m.id != msg.author.id]
        online = [m for m in humans if m.status == discord.Status.online]
        if online:
            return random.choice(online).mention
        if humans:
            return random.choice(humans).mention
        return msg.author.mention


class TwitchBot(commands.Bot):
    def __init__(self, token):
        # Random prefix to not use default functionality.
        super().__init__(token=token, prefix='2f8648a8-8078-43b9-bbc6-0ccc2fd48f8d')
        self.channels = {}

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        logging.info(f'Logged in as | {self.nick}')
        with db.conn.cursor() as cur:
            cur.execute("SELECT channel_id, twitch_command_prefix, twitch_channel_name FROM channels")
            for row in cur.fetchall():
                id, prefix, name = row
                if not name:
                    continue
                self.channels[name] = {
                    'id': id,
                    'prefix': prefix,
                    'active_users': ttldict2.TTLDict(ttl_seconds=3600.0),
                }
        logging.info(f'joining channels: {self.channels.keys()}')
        await self.join_channels(self.channels.keys())

    async def event_message(self, message):
        # Ignore own messages.
        if message.echo:
            return
        info = self.channels[message.channel.name]
        if not info:
            logging.info(f'unknown channel {message.channel.name}')
            return
        il = InvocationLog(f"channel={message.channel.name} ({info['id']})")
        author = message.author.name
        info['active_users'][author] = 1
        il.info(f"active users {info ['active_users'].keys()}")
        il.info(f'{author} {message.content}')
        variables = {
            'author': str(author),
            'author_name': str(author),
            'mention': self.mentions(message.content, info, author),
            'media': 'twitch',
            'text': message.content,
            'is_mod': message.author.is_mod,
            'prefix': info['prefix'],
        }
        reply, new_messages = await process_message(il, info['id'], variables)
        db.add_log(il)
        ctx = await self.get_context(message)
        if reply != '':
            await ctx.send(reply)
        for m in new_messages:
            await ctx.send(m)
    
    def mentions(self, txt, info, author):
        result = re.findall(r'@\S+', txt)
        if result:
            return ' '.join(result)
        users = [x for x in info['active_users'].keys() if x != author]
        if users:
            return "@" + random.choice(users)
        return "@" + author


if __name__ == "__main__":
    print('starting')
    parser = argparse.ArgumentParser(description='moon rabbit')
    parser.add_argument('--twitch', action='store_true')
    parser.add_argument('--discord', action='store_true')
    parser.add_argument('--add_channel', action='store_true')
    parser.add_argument('--twitch_channel_name')
    parser.add_argument('--twitch_command_prefix', default='+')
    parser.add_argument('--channel_id')
    args = parser.parse_args()
    print(f'args {args}')
    if args.discord:
        logging.info('starting Discord Bot')
        client = DiscordClient(intents=discord.Intents.all())
        client.run(os.getenv('DISCORD_TOKEN'))
        sys.exit(0)
    if args.twitch:
        logging.info('starting Twitch Bot')
        bot = TwitchBot(token=os.getenv('TWITCH_ACCESS_TOKEN'))
        bot.run()
        sys.exit(0)
    if args.add_channel:
        if not args.twitch_channel_name:
            print('set --twitch_channel_name')
            sys.exit(1)
        id = args.channel_id
        if not id:
            id = db.new_channel_id()
        with db.conn.cursor() as cur:
            cur.execute('INSERT INTO channels (channel_id, twitch_channel_name, twitch_command_prefix) VALUES (%s, %s, %s)',
                        [id, args.twitch_channel_name, args.twitch_command_prefix])
            db.conn.commit()
        logging.info(f'added new channel #{id} {args.twitch_channel_name}')
        sys.exit(0)
    print('add --twitch or --discord argument to run bot')
