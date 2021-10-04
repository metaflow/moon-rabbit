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

# TODO twitch - reaction to channel-points redeems
# TODO twitch error on too fast replies?
# TODO add "any" or "choose" that randomly picks from a literal list
# TODO updating text (incl txt-upload to accept ids)
# TODO data struct to pass to command execution
# TODO allow commands w/o prefix in private bot conversation
# TODO check sandbox settings
# TODO test perf of compiled template VS from_string
# TODO bingo or anagramms?
# TODO indexes
"""Bot entry point."""

import asyncio
from io import StringIO
from twitchAPI import twitch
from data import *
from twitchio.ext import commands as twitchCommands  # type: ignore
import argparse
import discord  # type: ignore
import jinja2
import logging
import os
import sys
import random
import ttldict2  # type: ignore
from storage import DB, db, set_db, cursor
from typing import Any, Callable, List, Set, Union
import traceback
import commands
import time
import logging.handlers
import words
import twitch_commands
import twitch_bot
import twitch_api

errHandler = logging.FileHandler('errors.log', encoding='utf-8')
errHandler.setLevel(logging.ERROR)

rotatingHandler = logging.handlers.TimedRotatingFileHandler(
    'bot.log', when='D', encoding='utf-8', backupCount=8)

logging.basicConfig(
    handlers=[rotatingHandler, errHandler],
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO)


@jinja2.pass_context
def render_list_item(ctx, list_name: str):
    v = ctx.get_all()
    v['_render_depth'] += 1
    if v['_render_depth'] > 5:
        v['_log'].error('rendering depth is > 5')
        return ''
    txt = db().get_random_list_item(v['channel_id'], list_name)
    v['_log'].info(f'rendering {txt}')
    return render(txt, v)


@jinja2.pass_context
def render_text_item(ctx, q: Union[str, int], inf: str = ''):
    v = ctx.get_all()
    v['_render_depth'] += 1
    if v['_render_depth'] > 50:
        v['_log'].error('rendering depth is > 50')
        return ''
    if isinstance(q, int):
        txt, tags = db().get_text(v['channel_id'], q)
    else:
        if inf:
            q = f'({q}) and morph'
        txt, tags = db().get_random_text(v['channel_id'], q)
    if not txt:
        v['_log'].info(f'no matchin text is found')
        return ''
    if inf:
        channel_id = v['channel_id']
        _, inv_tags = db().get_tags(channel_id)
        filter = []
        if tags:
            for tag_id in tags:
                name = inv_tags[tag_id]
                if name in words.morph_tags:
                    filter.append(words.morph_tags[name])
        return words.inflect_word(txt, inf, filter)
    v['_log'].info(f'rendering {txt}')
    return render(txt, v)


def randint(a=0, b=100):
    return random.randint(a, b)


def discord_literal(t):
    return t.replace('<@!', '<@')


@jinja2.pass_context
def get_variable(ctx, name: str, category: str = '', default_value: str = ''):
    channel_id = ctx.get('channel_id')
    return db().get_variable(channel_id, name, category, default_value)


@jinja2.pass_context
def set_variable(ctx, name: str, value: str = '', category: str = '', expires: int = 9 * 3600):
    channel_id = ctx.get('channel_id')
    db().set_variable(channel_id, name, value, category, expires + int(time.time()))
    return ''


@jinja2.pass_context
def get_variables_category_size(ctx, name: str) -> int:
    channel_id = ctx.get('channel_id')
    return db().count_variables_in_category(channel_id, name)


@jinja2.pass_context
def delete_category(ctx, name: str):
    channel_id = ctx.get('channel_id')
    db().delete_category(channel_id, name)
    return ''


@jinja2.pass_context
def discord_or_twitch(ctx, vd: str, vt: str):
    return vd if ctx.get('media') == 'discord' else vt


# templates.globals['list'] = render_list_item
templates.globals['txt'] = render_text_item
templates.globals['randint'] = randint
templates.globals['discord_literal'] = discord_literal
templates.globals['get'] = get_variable
templates.globals['set'] = set_variable
templates.globals['category_size'] = get_variables_category_size
templates.globals['delete_category'] = delete_category
templates.globals['timestamp'] = lambda: int(time.time())
templates.globals['dt'] = discord_or_twitch
# templates.globals['echo'] = lambda x: x
# templates.globals['log'] = lambda x: logging.info(x)

# https://discordpy.readthedocs.io/en/latest/api.html


class DiscordClient(discord.Client):
    def __init__(self, *args, **kwargs):
        self.channels: Dict[str, Any] = {}
        self.mods: Dict[str, str] = {}
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print('We have logged in as {0.user}'.format(self))

    async def on_message(self, message: discord.Message):
        # Don't react to own messages.
        if message.author == discordClient.user:
            return
        logging.info(f'channel {message.channel} {message.channel.type}')
        guild_id = ''
        is_mod = False
        private = False
        if message.channel.type == discord.ChannelType.private:
            g = self.mods.get(str(message.author.id))
            if not g:
                await message.channel.send('You are not a moderator. First send message in your discord and come back here.')
                return
            guild_id = g
            is_mod = True
            private = True
        else:
            permissions = message.author.guild_permissions
            guild_id = str(message.guild.id)
            is_mod = permissions.ban_members or permissions.administrator
            if is_mod:
                self.mods[str(message.author.id)] = guild_id
                logging.info(f'set {message.author.id} as mod for {guild_id}')
        try:
            channel_id, prefix = db().discord_channel_info(
                db().conn.cursor(), guild_id)
        except Exception as e:
            logging.error(
                f"'discord_channel_info': {e}\n{traceback.format_exc()}")
            return
        log = InvocationLog(
            f'guild={guild_id} channel={channel_id} author={message.author.id}')
        if channel_id not in self.channels:
            self.channels[channel_id] = {
                'active_users': ttldict2.TTLDict(ttl_seconds=3600.0 * 2)}
        self.channels[channel_id]['active_users'][discord_literal(
            message.author.mention)] = '+'
        self.channels[channel_id]['active_users'].drop_old_items()
        log.info(f'message "{message.content}"')
        variables: Optional[Dict] = None
        # postpone variable calculations as much as possible

        def get_vars():
            nonlocal variables
            if not variables:
                bot = discord_literal(self.user.mention)
                author = discord_literal(message.author.mention)
                exclude = [bot, author]
                variables = {
                    'author': author,
                    'author_name': discord_literal(str(message.author.display_name)),
                    'mention': Lazy(lambda: self.any_mention(message, self.channels[channel_id]['active_users'].keys(), exclude)),
                    'direct_mention': Lazy(lambda: self.mentions(message)),
                    'random_mention': Lazy(lambda: self.random_mention(message, self.channels[channel_id]['active_users'].keys(), exclude)),
                    'media': 'discord',
                    'text': message.content,
                    'is_mod': is_mod,
                    'prefix': prefix,
                    'bot': bot,
                    'channel_id': channel_id,
                    '_log': log,
                    '_discord_message': message,
                    '_private': private,
                }
            return variables
        actions = await commands.process_message(log, channel_id, message.content, EventType.message, prefix, True, is_mod, private, get_vars)
        db().add_log(channel_id, log)
        for a in actions:
            if len(a.text) > 2000:
                a.text = a.text[:1997] + "..."
            if a.kind == ActionKind.NEW_MESSAGE:
                await message.channel.send(a.text)
            if a.kind == ActionKind.REPLY:
                if a.attachment:
                    await message.reply(a.text, file=discord.File(StringIO(a.attachment), filename=a.attachment_name))
                else:
                    await message.reply(a.text)
            if a.kind == ActionKind.PRIVATE_MESSAGE:
                await message.author.send(a.text)
            if a.kind == ActionKind.REACT_EMOJI:
                await message.add_reaction(a.text)

    def random_mention(self, msg, users: List[str], exclude: List[str]):
        users = [x for x in users if x not in exclude]
        if users:
            return random.choice(users)
        return discord_literal(msg.author.mention)

    def mentions(self, msg):
        if msg.mentions:
            return ' '.join([discord_literal(x.mention) for x in msg.mentions])
        return ''

    def any_mention(self, msg, users: List[str], exclude: List[str]): 
        direct = self.mentions(msg)
        return direct if direct else self.random_mention(msg, users, exclude)


async def expireVariables():
    while True:
        db().expire_variables()
        await asyncio.sleep(120)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='moon rabbit')
    parser.add_argument('--twitch', action='store_true')
    parser.add_argument('--twitch2', action='store_true')
    parser.add_argument('--twitch3', action='store_true')
    parser.add_argument('--discord', action='store_true')
    parser.add_argument('--add_channel', action='store_true')
    parser.add_argument('--twitch_channel_name')
    parser.add_argument('--twitch_command_prefix', default='+')
    parser.add_argument('--channel_id')
    parser.add_argument('--drop_database', action='store_true')
    parser.add_argument('--alsologtostdout', action='store_true')
    args = parser.parse_args()
    print('connecting to', os.getenv('DB_CONNECTION'))
    set_db(DB(os.getenv('DB_CONNECTION')))

    print(f'args {args}')
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
        db().recreate_tables()
    loop = asyncio.get_event_loop()
    if args.discord:
        logging.info('starting Discord Bot')
        discordClient = DiscordClient(intents=discord.Intents.all(), loop=loop)
        loop.create_task(discordClient.start(os.getenv('DISCORD_TOKEN')))
    if args.twitch:
        logging.info('starting Twitch Commands')
        twitchClient = twitch_commands.TwitchCommands(token=os.getenv(
            'TWITCH_ACCESS_TOKEN'), loop=loop)
        loop.create_task(twitchClient.connect())
    if args.twitch2:
        logging.info('starting Twitch Bot')
        with db().conn.cursor() as cur:
            cur.execute(
                "SELECT channel_id, twitch_command_prefix, twitch_channel_name, twitch_auth_token, twitch_events FROM channels")
            for row in cur.fetchall():
                id, prefix, name, token, events, pubsub_token = row
                if not name or not token:
                    continue
                watch: List[EventType] = []
                if events:
                    for x in events.split(','):
                        watch.append(EventType[x.strip()])
                logging.info(
                    f'connecting to twitch {name} ({id}) prefix {prefix}, watch={watch} bot token="{token}" pubsub token="{pubsub_token}"')
                t = twitch_bot.Twitch(token=token, channel=name, internal_channel_id=id,
                                      prefix=prefix, watch=watch, pubsub_token=pubsub_token, loop=loop)
                loop.create_task(t.connect())
    if args.twitch3:
        with cursor() as cur:
            cur.execute("SELECT id FROM twitch_bots")
            for r in cur.fetchall():
                t = twitch_api.Twitch3(bot_id=r[0], loop=loop)
                loop.create_task(t.connect())
    if args.twitch or args.discord or args.twitch2 or args.twitch3:
        logging.info('running the async loop')
        loop.create_task(expireVariables())
        loop.run_forever()
        sys.exit(0)
    if args.add_channel:
        if not args.twitch_channel_name:
            print('set --twitch_channel_name')
            sys.exit(1)
        id = args.channel_id
        if not id:
            id = db().new_channel_id()
        with db().conn.cursor() as cur:
            cur.execute('UPDATE channels SET twitch_channel_name = %s, twitch_command_prefix = %s WHERE id = %s',
                        [args.twitch_channel_name, args.twitch_command_prefix, id])
            db().conn.commit()
        logging.info(f'updated channel #{id} {args.twitch_channel_name}')
        sys.exit(0)
    print('add --twitch or --discord argument to run bot')
