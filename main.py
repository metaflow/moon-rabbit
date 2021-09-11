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

# TODO non prefix (regex) commands
# TODO: check sandbox settings
# TODO: commands metadata
# TODO python typings
# TODO DB backups
# TODO store variables
# TODO context - commands only for discord / twitch
# TODO reactions in discord
# TODO help command

# command syntax is [condition] + [action]
# simplest condition is just a string "word" -> [regex: '\b<perefix>word\b']
# general format is JSON e.g. +set {...}

"""Bot entry point."""

from data import *
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
from typing import List
import traceback
import control_commands


logging.basicConfig(
    handlers=[logging.FileHandler('main.log', 'a', 'utf-8')],
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO)
stdoutHandler = logging.StreamHandler()
stdoutHandler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(stdoutHandler)

db = storage.DB(os.getenv('DB_CONNECTION'))


def render(text, vars):
    global next_template
    next_template = text
    templates.cache.clear()
    return templates.get_template('x').render(vars)


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
    return render(db.get_list(channel_id, id), vars)


templates = SandboxedEnvironment(autoescape=jinja2.select_autoescape())
next_template = ''
templates.loader = jinja2.FunctionLoader(lambda _: next_template)
templates.globals['list'] = list


async def process_message(log: InvocationLog, channel_id, variables) -> List[Action]:
    txt = variables['text']
    prefix = variables['prefix']
    log.info(f"message text '{txt}'")
    actions: List[Action] = []
    # TODO: do that in control commands
    admin_command = False
    for c in control_commands.all_commands:
        if txt.startswith(prefix + c + ' ') or txt == prefix + c:
            admin_command = True
            break
    if admin_command:
        log.info('running control commands')
        if not variables['is_mod']:
            log.warning(f'non mod called an admin command')
            return f"you don't have permissions to do that", ""
        txt = txt[len(prefix):]
        # results = []
        for p in txt.split('\n' + prefix):
            parts = p.split(' ', 1)
            cmd = parts[0]
            t = ''
            if len(parts) > 1:
                t = parts[1]
            if cmd not in control_commands.all_commands:
                log.info(f'unknown command {cmd}')
                continue
            log.info(f"running cmd {cmd} '{t}'")
            try:
                r = await control_commands.all_commands[cmd](db, db.conn.cursor(), log, channel_id, variables, t)
                log.info(f"command result '{r}'")
                actions.extend(r)
                db.conn.commit()
            except Exception as e:
                db.conn.rollback()
                actions.append(
                    Action(kind=ActionKind.REPLY, text='error ocurred'))
                log.error(f'failed to execute {cmd}: {str(e)}')
                logging.error(traceback.format_exc())
        return actions
    commands = db.get_commands(channel_id, prefix)
    logging.info(f'loaded commands {commands}')
    for cmd in commands:
        if not re.search(cmd.regex, txt):
            continue
        try:
            for e in cmd.effects:
                variables['_render_depth'] = 0
                variables['channel_id'] = channel_id
                actions.append(Action(
                    kind=e.kind,
                    text=render(e.text, variables)))
        except Exception as e:
            log.error(f"failed to render '{cmd}': {str(e)}")
            logging.error(traceback.format_exc())
    return actions


class DiscordClient(discord.Client):
    async def on_ready(self):
        print('We have logged in as {0.user}'.format(self))

    async def on_message(self, message):
        # Don't react to own messages.
        if message.author == discordClient.user:
            return
        logging.info(f'guild id {message.guild.id} {str(message.guild.id)}')
        try:
            channel_id, prefix = db.discord_channel_info(
                db.conn.cursor(), str(message.guild.id))
            db.conn.commit()
        except Exception as e:
            logging.error(
                f"'discord_channel_info': {e}\n{traceback.format_exc()}")
            db.conn.rollback()
            return
        log = InvocationLog(
            f'guild={message.guild.id} channel={channel_id} author={message.author.id}')
        log.info(f'message {message.content}')
        permissions = message.author.guild_permissions
        direct_mention = self.mentions(message)
        random_mention = self.random_mention(message)
        mention = direct_mention if direct_mention else random_mention
        variables = {
            'author': message.author.mention,
            'author_name': str(message.author.display_name),
            'mention': mention,
            'direct_mention': direct_mention,
            'random_mention': random_mention,
            'media': 'discord',
            'text': message.content,
            'is_mod': permissions.ban_members or permissions.administrator,
            'prefix': prefix,
            'bot': discordClient.user.mention,
        }
        log.info(f'variables {variables}')
        actions = await process_message(log, channel_id, variables)
        db.add_log(channel_id, log)
        for a in actions:
            if a.kind == ActionKind.NEW_MESSAGE:
                await message.channel.send(a.text)
            if a.kind == ActionKind.REPLY:
                await message.reply(a.text)
            if a.kind == ActionKind.PRIVATE_MESSAGE:
                await message.author.send(a.text)

    def random_mention(self, msg):
        humans = [
            m for m in msg.channel.members if not m.bot and m.id != msg.author.id]
        online = [m for m in humans if m.status == discord.Status.online]
        if online:
            return random.choice(online).mention
        if humans:
            return random.choice(humans).mention
        return msg.author.mention

    def mentions(self, msg):
        if msg.mentions:
            return ' '.join([x.mention for x in msg.mentions])
        return ''


class TwitchBot(commands.Bot):
    def __init__(self, token):
        # Random prefix to not use default functionality.
        super().__init__(token=token, prefix='2f8648a8-8078-43b9-bbc6-0ccc2fd48f8d')
        self.channels = {}

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        logging.info(f'Logged in as | {self.nick}')
        with db.conn.cursor() as cur:
            cur.execute(
                "SELECT channel_id, twitch_command_prefix, twitch_channel_name FROM channels")
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
        direct_mention = self.mentions(message.content)
        random_mention = self.random_mention(info, author)
        mention = direct_mention if direct_mention else random_mention
        variables = {
            'author': str(author),
            'author_name': str(author),
            'mention': mention,
            'direct_mention': direct_mention,
            'random_mention': random_mention,
            'media': 'twitch',
            'text': message.content,
            'is_mod': message.author.is_mod,
            'prefix': info['prefix'],
            'bot': self.nick,
        }
        il.info(f'variables {variables}')
        actions = await process_message(il, info['id'], variables)
        db.add_log(info['id'], il)
        ctx = await self.get_context(message)
        for a in actions:
            if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                await ctx.send(a.text)

    def mentions(self, txt):
        result = re.findall(r'@\S+', txt)
        if result:
            return ' '.join(result)
        return ''

    def random_mention(self, info, author):
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
    parser.add_argument('--drop_database', action='store_true')
    args = parser.parse_args()
    print(f'args {args}')
    if args.drop_database:
        confirm = input('type "yes" to drop database and continue: ')
        if confirm != 'yes':
            print(f'you typed "{confirm}", want "yes"')
            sys.exit(1)
        db.recreate_tables()
    if args.discord:
        logging.info('starting Discord Bot')
        discordClient = DiscordClient(intents=discord.Intents.all())
        discordClient.run(os.getenv('DISCORD_TOKEN'))
        sys.exit(0)
    if args.twitch:
        logging.info('starting Twitch Bot')
        twitchClient = TwitchBot(token=os.getenv('TWITCH_ACCESS_TOKEN'))
        twitchClient.run()
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
