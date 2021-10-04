
import asyncio
from io import StringIO
from data import *
from twitchio.ext import commands as twitchCommands  # type: ignore
import argparse
import discord  # type: ignore
import jinja2
import logging
import os
import sys
import random
import re
import ttldict2  # type: ignore
from storage import DB, db, set_db
from typing import Callable, List, Set, Union
import traceback
import commands
import time
import logging.handlers
import words

class TwitchCommands(twitchCommands.Bot):
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
        with db().conn.cursor() as cur:
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
        channel_id, prefix = db().twitch_channel_info(
            db().conn.cursor(), message.channel.name)
        info = self.channels[message.channel.name]
        if not info:
            logging.info(f'unknown channel {message.channel.name}')
            return
        log = InvocationLog(f"channel={message.channel.name} ({channel_id})")
        author = message.author.name
        info['active_users'][author] = 1
        log.info(f'{author} {message.content}')
        variables: Optional[Dict] = None
        is_mod = message.author.is_mod
        # postpone variable calculations as much as possible

        def get_vars():
            nonlocal variables
            if not variables:
                variables = {
                    'author': str(author),
                    'author_name': str(author),
                    'mention': Lazy(lambda: self.any_mention(message.content, info, author)),
                    'direct_mention': Lazy(lambda: self.mentions(message.content)),
                    'random_mention': Lazy(lambda: self.random_mention(info, author)),
                    'media': 'twitch',
                    'text': message.content,
                    'is_mod': is_mod,
                    'prefix': prefix,
                    'bot': self.nick,
                    'channel_id': channel_id,
                    '_log': log,
                    '_private': False,
                }
            return variables
        actions = await commands.process_message(log, channel_id, message.content, EventType.message, prefix, False, is_mod, False, get_vars)
        db().add_log(channel_id, log)
        ctx = await self.get_context(message)
        for a in actions:
            if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                if len(a.text) > 500:
                    a.text = a.text[:497] + "..."
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