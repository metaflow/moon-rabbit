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
 
# TODO !multiline command
# TODO bingo
# TODO check sandbox settings
# TODO DB backups
# TODO help command

"""Bot entry point."""

import asyncio
from data import *
from jinja2.sandbox import SandboxedEnvironment
from twitchio.ext import commands
import argparse
import discord
import jinja2
import logging
import os
import sys
import json
import random
import re
import ttldict2
from storage import db
from typing import Callable, List
import traceback
import control_commands
import time
import logging.handlers

logging.basicConfig(
    handlers=[],
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO)


def render(text, vars):
    return templates.from_string(text).render(vars).strip()


templates = SandboxedEnvironment()
next_template = ''
templates.loader = jinja2.FunctionLoader(lambda _: next_template)


@jinja2.pass_context
def render_list_item(ctx, list_name: str):
    channel_id = ctx.get('channel_id')
    vars = ctx.get_all()
    vars['_render_depth'] += 1
    if vars['_render_depth'] > 5:
        logging.error('rendering depth is > 5')
        return ''
    txt = db.get_random_list_item(channel_id, list_name)
    logging.info(f'rendering {txt}')
    return render(txt, vars)


def randint(a=0, b=100):
    return random.randint(a, b)


def discord_literal(t):
    return t.replace('<@!', '<@')


@jinja2.pass_context
def get_variable(ctx, name: str, value: str = ''):
    channel_id = ctx.get('channel_id')
    return db.get_variable(db.conn.cursor(), channel_id, name, value)


@jinja2.pass_context
def set_variable(ctx, name: str, value: str):
    channel_id = ctx.get('channel_id')
    db.set_variable(db.conn.cursor(), channel_id, name, value)
    return ''


templates.globals['list'] = render_list_item
templates.globals['randint'] = randint
templates.globals['discord_literal'] = discord_literal
templates.globals['echo'] = lambda x: x
templates.globals['log'] = lambda x: logging.info(x)
templates.globals['get'] = get_variable
templates.globals['set'] = set_variable
templates.globals['timestamp'] = lambda: int(time.time())


async def process_message(log: InvocationLog, channel_id: int, txt: str, prefix: str, twitch: bool, get_variables: Callable[[], Dict]) -> List[Action]:
    actions: List[Action] = []
    try:
        controls = await control_commands.process_control_message(log, channel_id, txt, prefix, get_variables)
        if controls:
            return [x for x in controls if x.text]
        commands = db.get_commands(channel_id, prefix)
        variables = {}
        for cmd in commands:
            if (not cmd.data.discord) and (not twitch):
                continue
            if (not cmd.data.twitch) and twitch:
                continue
            if not re.search(cmd.regex, txt):
                continue
            if not variables:
                variables = get_variables()
            log.info(
                f'matched command {json.dumps(dataclasses.asdict(cmd.data), ensure_ascii=False)}')
            try:
                for e in cmd.data.actions:
                    variables['_render_depth'] = 0
                    variables['channel_id'] = channel_id
                    a = Action(
                        kind=e.kind,
                        text=render(e.text, variables))
                    if a.text:
                        actions.append(a)
            except Exception as e:
                log.error(f"failed to render '{cmd.data.name}': {str(e)}")
                log.error(traceback.format_exc())
        log.info(f'actions {actions}')
    except Exception as e:
        db.conn.rollback()
        actions.append(
            Action(kind=ActionKind.REPLY, text='error ocurred'))
        log.error(f'{e}\n{traceback.format_exc()}')
    finally:
        db.conn.commit()
    return actions


class DiscordClient(discord.Client):
    async def on_ready(self):
        print('We have logged in as {0.user}'.format(self))

    async def on_message(self, message: discord.Message):
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
        log.info(f'message "{message.content}"')
        permissions = message.author.guild_permissions

        def get_vars(): return {
            'author': message.author.mention,
            'author_name': str(message.author.display_name),
            'mention': Lazy(lambda: self.any_mention(message)),
            'direct_mention': Lazy(lambda: self.mentions(message)),
            'random_mention': Lazy(lambda: self.random_mention(message)),
            'media': 'discord',
            'text': message.content,
            'is_mod': permissions.ban_members or permissions.administrator,
            'prefix': prefix,
            'bot': discordClient.user.mention,
        }
        # log.info(f'variables {variables}')
        actions = await process_message(log, channel_id, message.content, prefix, False, get_vars)
        db.add_log(channel_id, log)
        for a in actions:
            if a.kind == ActionKind.NEW_MESSAGE:
                await message.channel.send(a.text)
            if a.kind == ActionKind.REPLY:
                await message.reply(a.text)
            if a.kind == ActionKind.PRIVATE_MESSAGE:
                await message.author.send(a.text)
            if a.kind == ActionKind.REACT_EMOJI:
                await message.add_reaction(a.text)

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

    def any_mention(self, msg):
        direct = self.mentions(msg)
        return direct if direct else self.random_mention(msg)


class Lazy():
    def __init__(self, f):
        self.func = f

    def __repr__(self):
        return self.func()


class TwitchBot(commands.Bot):
    def __init__(self, token, loop):
        # Random prefix to not use default functionality.
        super().__init__(token=token, prefix='2f8648a8-8078-43b9-bbc6-0ccc2fd48f8d', loop=loop)
        self.channels = {}

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        logging.info(f'Logged in as | {self.nick}')
        await self.reload_channels()

    async def reload_channels(self):
        logging.info('reloading twitch channels')
        with db.conn.cursor() as cur:
            cur.execute(
                "SELECT channel_id, twitch_command_prefix, twitch_channel_name FROM channels")
            for row in cur.fetchall():
                id, prefix, name = row
                if not name:
                    continue
                self.channels[name] = {
                    'active_users': ttldict2.TTLDict(ttl_seconds=3600.0),
                }
        logging.info(f'joining channels: {self.channels.keys()}')
        await self.join_channels(self.channels.keys())

    async def event_message(self, message):
        # Ignore own messages.
        if message.echo:
            return
        channel_id, prefix = db.twitch_channel_info(
            db.conn.cursor(), message.channel.name)
        info = self.channels[message.channel.name]
        if not info:
            logging.info(f'unknown channel {message.channel.name}')
            return
        il = InvocationLog(f"channel={message.channel.name} ({channel_id})")
        author = message.author.name
        info['active_users'][author] = 1
        il.info(f'{author} {message.content}')

        def get_vars(): return {
            'author': str(author),
            'author_name': str(author),
            'mention': Lazy(lambda: self.any_mention(message.content)),
            'direct_mention': Lazy(lambda: self.mentions(message.content)),
            'random_mention': Lazy(lambda: self.random_mention(info, author)),
            'media': 'twitch',
            'text': message.content,
            'is_mod': message.author.is_mod,
            'prefix': prefix,
            'bot': self.nick,
        }
        actions = await process_message(il, channel_id, message.content, prefix, True, get_vars)
        db.add_log(channel_id, il)
        ctx = await self.get_context(message)
        for a in actions:
            if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                await ctx.send(a.text)

    def any_mention(self, txt: str, info, author):
        direct = self.mentions(txt)
        return direct if direct else self.random_mention(info, author)

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
    parser = argparse.ArgumentParser(description='moon rabbit')
    parser.add_argument('--twitch', action='store_true')
    parser.add_argument('--discord', action='store_true')
    parser.add_argument('--add_channel', action='store_true')
    parser.add_argument('--twitch_channel_name')
    parser.add_argument('--twitch_command_prefix', default='+')
    parser.add_argument('--channel_id')
    parser.add_argument('--drop_database', action='store_true')
    parser.add_argument('--alsologtostdout', action='store_true')
    args = parser.parse_args()
    print(f'args {args}')
    log_file = 'main.log'
    if args.discord:
        log_file = 'discord.log'
    elif args.twitch:
        log_file = 'twitch.log'
    logging.getLogger().addHandler(
        logging.handlers.TimedRotatingFileHandler(
            log_file, when='h', encoding='utf-8', backupCount=8))
    if args.alsologtostdout:
        stdoutHandler = logging.StreamHandler()
        stdoutHandler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(message)s'))
        logging.getLogger().addHandler(stdoutHandler)
    if args.drop_database:
        confirm = input('type "yes" to drop database and continue: ')
        if confirm != 'yes':
            print(f'you typed "{confirm}", want "yes"')
            sys.exit(1)
        db.recreate_tables()
    loop = asyncio.get_event_loop()
    if args.discord:
        logging.info('starting Discord Bot')
        discordClient = DiscordClient(intents=discord.Intents.all(), loop=loop)
        loop.create_task(discordClient.start(os.getenv('DISCORD_TOKEN')))
    if args.twitch:
        logging.info('starting Twitch Bot')
        twitchClient = TwitchBot(token=os.getenv('TWITCH_ACCESS_TOKEN'), loop=loop)
        loop.create_task(twitchClient.connect())
    if args.twitch or args.discord:
        loop.run_forever()
        sys.exit(0)
    if args.add_channel:
        if not args.twitch_channel_name:
            print('set --twitch_channel_name')
            sys.exit(1)
        id = args.channel_id
        if not id:
            id = db.new_channel_id()
        with db.conn.cursor() as cur:
            cur.execute('UPDATE channels SET twitch_channel_name = %s, twitch_command_prefix = %s WHERE id = %s',
                        [args.twitch_channel_name, args.twitch_command_prefix, id])
            db.conn.commit()
        logging.info(f'updated channel #{id} {args.twitch_channel_name}')
        sys.exit(0)
    print('add --twitch or --discord argument to run bot')
