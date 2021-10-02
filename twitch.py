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

from twitchAPI.twitch import Twitch
from twitchAPI.types import AuthScope
from twitchAPI import Twitch, EventSub
import logging
import os
import sys

logging.basicConfig(stream = sys.stdout, 
                    format = '%(asctime)s %(levelname)s %(message)s', 
                    level = logging.INFO)



import asyncio
from data import *
import logging
from typing import Any, Callable, Dict, Optional, Union
import twitchio  # type: ignore
import ttldict2  # type: ignore
import commands
from storage import db
import re
import random
from twitchio.ext import pubsub


class TwitchEvent(str, Enum):
    moderation_user_action = 'moderation_user_action'
    channel_points = 'channel_points'

class Twitch3(twitchio.Client):
    def __init__(self, token: str, channel: str, internal_channel_id: int, prefix: str, watch: List[TwitchEvent] = None, client_secret: str = None, loop: asyncio.AbstractEventLoop = None, heartbeat: Optional[float] = 30):
        self.token = token
        self.channel_name = channel
        self.channel_id = internal_channel_id
        self.prefix = prefix
        self.active_users = ttldict2.TTLDict(ttl_seconds=3600.0)
        self.watch = watch
        super().__init__(token, client_secret=client_secret,
                         initial_channels=[channel], loop=loop, heartbeat=heartbeat)

    async def event_ready(self):
        logging.info(f'Logged in as "{self.nick}"')
        channel_user = (await self.fetch_users([self.channel_name]))[0]
        logging.info(f'fetched channel user {channel_user}')
        APP_ID = os.getenv('TWITCH_API_APP_ID')
        APP_SECRET = os.getenv('TWITCH_API_APP_SECRET')
        # create instance of twitch API and create app authentication
        twitchApi = Twitch(APP_ID, APP_SECRET)
        twitchApi.authenticate_app([])
        # twitch.auto_refresh_auth = False
        # twitch.set_user_authentication(token=os.getenv('TWITCH_API_USER_TOKEN'), scope=[AuthScope.CHAT_READ, AuthScope.CHANNEL_READ_REDEMPTIONS])
        WEBHOOK_URL = 'https://twitch.apexlegendsrecoils.online'
        # get ID of user
        uid = twitchApi.get_users(logins=[self.channel_name])
        user_id = uid['data'][0]['id']
        logging.info(f'user id {user_id}')
        hook = EventSub(WEBHOOK_URL, APP_ID, 8080, twitchApi)
        # unsubscribe from all to get a clean slate
        hook.unsubscribe_all()
        # start client
        hook.start()
        logging.info('subscribing to hooks:')
        for w in self.watch:
            if w == TwitchEvent.channel_points:
                hook.listen_channel_points_custom_reward_redemption_add(user_id, on_redeption)
    

    async def event_pubsub_moderation(self, event):
        logging.info(f'event_pubsub_moderation {event}')
    
    async def event_channel_points(self, event: pubsub.PubSubChannelPointsMessage):
        logging.info(f'event_channel_points {event.reward} {event.user}')

    async def on_redeption(*args):
        logging.info(f'on_redemption {args}')

    async def event_message(self, message):
        # Ignore own messages.
        if message.echo:
            return
        if message.channel.name != self.channel_name:
            return
        # TODO: simplify
        channel_id = self.channel_id
        prefix = self.prefix
        log = InvocationLog(f"channel={message.channel.name} ({channel_id})")
        author_name = message.author.name
        self.active_users[author_name] = 1
        log.info(f'{author_name}: {message.content}')
        variables: Optional[Dict] = None
        is_mod = message.author.is_mod

        # postpone variable calculations as much as possible
        def get_vars():
            nonlocal variables
            if not variables:
                variables = {
                    'author': str(author_name),
                    'author_name': str(author_name),
                    'mention': Lazy(lambda: self.any_mention(message.content, author_name)),
                    'direct_mention': Lazy(lambda: self.mentions(message.content)),
                    'random_mention': Lazy(lambda: self.random_mention(author_name)),
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
        # ctx = await self.get_context(message)

        for a in actions:
            if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                if len(a.text) > 500:
                    a.text = a.text[:497] + "..."
                await message.channel.send(a.text)

    def any_mention(self, txt: str, author: str):
        direct = self.mentions(txt)
        return direct if direct else self.random_mention(author)

    def mentions(self, txt: str):
        result = re.findall(r'@\S+', txt)
        if result:
            return ' '.join(result)
        return ''

    def random_mention(self, author: str):
        users = [x for x in self.active_users.keys() if x != author]
        if users:
            return "@" + random.choice(users)
        return "@" + author
