"""
 Copyright 2021  Google LLC

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

import asyncio
from os import curdir
from twitchAPI.twitch import Twitch
from twitchAPI.types import AuthScope
from twitchAPI import Twitch, EventSub
import logging
from data import *
import logging
from typing import Any, Callable, Dict, Optional, Union
import twitchio  # type: ignore
from storage import cursor, db
import ttldict2
import commands
import random
import re


class TwitchEvent(str, Enum):
    moderation_user_action = 'moderation_user_action'
    channel_points = 'channel_points'


@dataclasses.dataclass
class ChannelInfo:
    active_users: ttldict2.TTLDict
    prefix: str
    channel_id: int
    twitch_user_id: int
    events: List[TwitchEvent]


class Twitch3(twitchio.Client):
    def __init__(self, bot_id: int, loop: asyncio.AbstractEventLoop):
        logging.info(f'creating twitch bot {bot_id}')
        with cursor() as cur:
            cur.execute(
                "SELECT channel_name, api_app_id, api_app_secret, auth_token, api_url, api_port FROM twitch_bots WHERE id = %s", (bot_id,))
            self.channel_name, self.app_id, self.app_secret, self.auth_token, self.api_url, self.api_port = cur.fetchone()
        self.channels: Dict[str, ChannelInfo] = {}
        super().__init__(self.auth_token, loop=loop)

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        logging.info(f'Logged in as {self.nick}')
        has_events = False
        with cursor() as cur:
            cur.execute(
                "SELECT channel_id, twitch_channel_name, twitch_command_prefix, twitch_events FROM channels WHERE twitch_bot = %s", (self.channel_name,))
            for row in cur.fetchall():
                channel_id, twitch_channel_name, twitch_command_prefix, twitch_events = row
                user = (await self.fetch_users([twitch_channel_name]))[0]
                user_id = user.id
                events: List[TwitchEvent] = []
                if twitch_events:
                    for x in twitch_events.split(','):
                        events.append(TwitchEvent[x.strip()])
                        has_events = True
                self.channels[twitch_channel_name] = ChannelInfo(
                    active_users=ttldict2.TTLDict(ttl_seconds=3600.0),
                    prefix=twitch_command_prefix,
                    channel_id=channel_id,
                    twitch_user_id=user_id,
                    events=events)
        logging.info(f'channels {self.channels}')
        logging.info(f'joining channels {self.channels.keys()}')
        await self.join_channels(self.channels.keys())
        if self.app_id and self.app_secret and self.api_url and self.api_port and has_events:
            logging.info(f'starting EventSub for {self.channel_name}')
            self.api = Twitch(self.app_id, self.app_secret)
            self.api.authenticate_app([])
            hook = EventSub(self.api_url, self.app_id, self.api_port, self.api)
            hook.unsubscribe_all()
            hook.start()
            for name, c in self.channels.items():
                for e in c.events:
                    if e == TwitchEvent.channel_points:
                        logging.info(
                            f'subscribing {name} {c.twitch_user_id} to channel_points_custom_reward_redemption')
                        hook.listen_channel_points_custom_reward_redemption_add(
                            c.twitch_user_id, self.on_redeption)

    async def event_message(self, message):
        # Ignore own messages.
        if message.echo:
            return
        info: Optional[ChannelInfo] = self.channels.get(message.channel.name)
        if not info:
            logging.info(f'unknown channel {message.channel.name}')
            return
        channel_id = info.channel_id
        prefix = info.prefix
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
        actions = await commands.process_message(log, channel_id, message.content, prefix, False, is_mod, False, get_vars)
        db().add_log(channel_id, log)
        ctx = await self.get_context(message)
        for a in actions:
            if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                if len(a.text) > 500:
                    a.text = a.text[:497] + "..."
                await ctx.send(a.text)

    def any_mention(self, txt: str, info: ChannelInfo, author):
        direct = self.mentions(txt)
        return direct if direct else self.random_mention(info, author)

    def mentions(self, txt):
        result = re.findall(r'@\S+', txt)
        if result:
            return ' '.join(result)
        return ''

    def random_mention(self, info: ChannelInfo, author):
        users = [x for x in info.active_users.keys() if x != author]
        if users:
            return "@" + random.choice(users)
        return "@" + author

    async def on_redeption(self, *args):
        logging.info(f'on_redemption {args}')
