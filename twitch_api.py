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
import traceback
from asyncio.locks import Event
from os import curdir
from twitchAPI.twitch import Twitch, AuthScope  # type: ignore
from twitchAPI import Twitch, EventSub  # type: ignore
import logging
from data import *
import logging
from typing import Dict, Optional
import twitchio  # type: ignore
from storage import cursor, db
import ttldict2  # type: ignore
import commands
import random
import re
import time
from asyncio_throttle import Throttler


@dataclasses.dataclass
class ChannelInfo:
    active_users: ttldict2.TTLDict
    throttled_users: ttldict2.TTLDict # user -> time
    prefix: str
    channel_id: int
    twitch_user_id: str
    events: List[EventType]
    twitch_channel: Optional[twitchio.Channel]
    last_activity: float


class Twitch3(twitchio.Client):
    def __init__(self, twitch_bot: str, loop: asyncio.AbstractEventLoop):
        logging.info(f'creating twitch bot {twitch_bot}')
        with cursor() as cur:
            cur.execute(
                "SELECT channel_name, api_app_id, api_app_secret, auth_token, api_url, api_port FROM twitch_bots WHERE channel_name = %s", (twitch_bot,))
            self.channel_name, self.app_id, self.app_secret, self.auth_token, self.api_url, self.api_port = cur.fetchone()
        self.channels: Dict[str, ChannelInfo] = {}
        self.throttler = Throttler(rate_limit=1, period=1)

        has_events = False
        with cursor() as cur:
            cur.execute(
                "SELECT channel_id, twitch_channel_name, twitch_command_prefix, twitch_events, twitch_throttle FROM channels WHERE twitch_bot = %s", (self.channel_name,))
            for row in cur.fetchall():
                channel_id, twitch_channel_name, twitch_command_prefix, twitch_events, twitch_throttle = row
                if not twitch_throttle:
                    twitch_throttle = 0.0
                events: List[EventType] = []
                if twitch_events:
                    for x in twitch_events.split(','):
                        events.append(EventType[x.strip()])
                        has_events = True
                self.channels[twitch_channel_name] = ChannelInfo(
                    active_users=ttldict2.TTLDict(ttl_seconds=3600.0),
                    prefix=twitch_command_prefix,
                    channel_id=channel_id,
                    twitch_user_id='',
                    events=events,
                    twitch_channel=None,
                    throttled_users=ttldict2.TTLDict(ttl_seconds=float(max(twitch_throttle, 1))),
                    last_activity=0.0)
        logging.info(f'channels {self.channels}')
        logging.info(f'joining channels {self.channels.keys()}')

        if self.app_id and self.app_secret and self.api_url and self.api_port and has_events:
            logging.info(f'starting EventSub for {self.channel_name} {self}')
            self.api = Twitch(app_id=self.app_id, app_secret=self.app_secret,
                              target_app_auth_scope=[AuthScope.CHANNEL_MODERATE])
            self.api.authenticate_app([AuthScope.CHANNEL_MODERATE])
            hook = EventSub(callback_url=self.api_url,
                            api_client_id=self.app_id, port=self.api_port, twitch=self.api)
            hook.unsubscribe_all()
            hook.start()
            for name, c in self.channels.items():
                uid = self.api.get_users(logins=[name])
                logging.info(f'uid {uid}')
                c.twitch_user_id = str(uid['data'][0]['id']) # TODO this might fail
                for e in c.events:
                    if e == EventType.twitch_reward_redemption:
                        logging.info(
                            f'subscribing {name} {c.twitch_user_id} to channel_points_custom_reward_redemption')
                        hook.listen_channel_points_custom_reward_redemption_add(
                            c.twitch_user_id, self.on_redemption)
                    if e == EventType.twitch_hype_train:
                        logging.info(
                            f'subscribing {name} {c.twitch_user_id} to hype train events')
                        hook.listen_hype_train_begin(
                            c.twitch_user_id, self.on_hype_train_begins)
                        hook.listen_hype_train_progress(
                            c.twitch_user_id, self.on_hype_train_progress)
                        hook.listen_hype_train_end(
                            c.twitch_user_id, self.on_hype_train_ends)
        super().__init__(self.auth_token, loop=loop,
                         initial_channels=list(self.channels.keys()))

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        logging.info(f'Logged in as {self.nick}')
        # await self.join_channels(self.channels.keys())

    async def event_join(self, channel: twitchio.Channel, user: twitchio.User):
        # Set channel just in case if reward redemption will happen before any message.
        info = self.channels.get(channel.name)
        # logging.info(f'join {channel.name} {user.name}')
        if info:
            info.twitch_channel = channel

    async def event_message(self, message):
        try:
            # Ignore own messages.
            if message.echo:
                return
            info: Optional[ChannelInfo] = self.channels.get(
                message.channel.name)
            if not info:
                logging.info(f'unknown channel {message.channel.name}')
                return
            info.twitch_channel = message.channel
            info.last_activity = time.time()
            channel_id = info.channel_id
            prefix = info.prefix
            log = InvocationLog(
                f"twitch channel {message.channel.name} ({channel_id})")
            author = message.author.name
            info.active_users[author] = 1
            info.throttled_users.drop_old_items()
            if author in info.throttled_users:
                return
            info.active_users.drop_old_items()
            log.info(f'{author} "{message.content}"')
            variables: Optional[Dict] = None
            is_mod = message.author.is_mod
            # postpone variable calculations as much as possible
            message_id = str(time.time_ns())
            def get_vars():
                nonlocal variables
                if not variables:
                    variables = {
                        'author': str(author),
                        'author_name': str(author),
                        'mention': Lazy(lambda: self.any_mention(message.content, info, author)),
                        'direct_mention': Lazy(lambda: self.mentions(message.content)),
                        'random_mention': Lazy(lambda: self.random_mention(info, author), stick=False),
                        'any_mention': Lazy(lambda: self.random_mention(info, ''), stick=False),
                        'media': 'twitch',
                        'text': message.content,
                        'is_mod': is_mod,
                        'prefix': prefix,
                        'bot': self.nick,
                        'channel_id': channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': message_id,
                    }
                return variables
            msg = Message(
                id = message_id,
                log = log,
                channel_id=channel_id,
                txt=message.content,
                event=EventType.message,
                prefix=info.prefix,
                is_discord=False,
                is_mod=is_mod,
                private=False,
                get_variables=get_vars)
            actions = await commands.process_message(msg)
            db().add_log(channel_id, log)
            for a in actions:
                if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                    await self.send_message(info, a.text)
            if actions and not is_mod:
                info.throttled_users[author] = '+'
        except Exception as e:
            log.error(f"event_message: {str(e)}")
            log.error(traceback.format_exc())

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

    async def on_hype_train_begins(self, *args):
        logging.info(f'on hype train beings {args}')

    async def on_hype_train_progress(self, *args):
        logging.info(f'on hype train progress {args}')

    async def on_hype_train_ends(self, data):
        try:
            logging.info(f'on_hype_train_ends {data}')
            event = data.get('event', {})
            logging.info(f'event {event}')
            channel_name = event.get('broadcaster_user_login')
            author = ''
            text = str(event.get('level'))
            contributors = ', '.join(['@' + c.get('user_name')
                                     for c in event.get('top_contributions')])
            logging.info(f'contributors {contributors}')
            logging.info(f'text "{text}"')
            info: Optional[ChannelInfo] = self.channels.get(channel_name)
            if not info:
                logging.info(f'unknown channel {channel_name}')
                return
            channel_id = info.channel_id
            log = InvocationLog(
                f"twitch channel {channel_name} ({info.channel_id})")
            variables: Optional[Dict] = None
            is_mod = False
            message_id = str(time.time_ns())
            def get_vars():
                nonlocal variables
                if not variables:
                    variables = {
                        'author': str(author),
                        'author_name': str(author),
                        'mention': Lazy(lambda: contributors),
                        'direct_mention': Lazy(lambda: contributors),
                        'random_mention': Lazy(lambda: contributors),
                        'any_mention':  Lazy(lambda: contributors),
                        'media': 'twitch',
                        'text': text,
                        'is_mod': is_mod,
                        'prefix': info.prefix,
                        'bot': self.nick,
                        'channel_id': info.channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': message_id,
                    }
                return variables
            msg = Message(
                id = message_id,
                log = log,
                channel_id=channel_id,
                txt=text,
                event=EventType.twitch_hype_train,
                prefix=info.prefix,
                is_discord=False,
                is_mod=is_mod,
                private=False,
                get_variables=get_vars)
            logging.info('discpathcing hype train event message')
            actions = await commands.process_message(msg)
            db().add_log(channel_id, log)
            for a in actions:
                if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                    await self.send_message(info, a.text)
        except Exception as e:
            log.error(f"on_hype_train_ends: {str(e)}")
            log.error(traceback.format_exc())

    async def send_message(self, info: ChannelInfo, txt: str):
        if not info.twitch_channel:
            return
        if len(txt) > 500:
            txt = txt[:497] + "..."
        async with self.throttler:
            logging.info(f'sending {txt}')
            await info.twitch_channel.send(txt)

    async def on_redemption(self, data):
        try:
            logging.info(f'on_redemption {data}')
            event = data.get('event', {})
            channel_name = event.get('broadcaster_user_login')
            author = event.get('user_name')
            text = event.get('user_input')
            reward_title = event.get('reward', {}).get('title')
            info: Optional[ChannelInfo] = self.channels.get(channel_name)
            if not info:
                logging.info(f'unknown channel {channel_name}')
                return
            channel_id = info.channel_id
            log = InvocationLog(
                f"twitch channel {channel_name} ({info.channel_id})")
            log.info(f'reward {reward_title} for user {author}')
            variables: Optional[Dict] = None
            is_mod = False
            message_id = str(time.time_ns())
            def get_vars():
                nonlocal variables
                if not variables:
                    variables = {
                        'author': str(author),
                        'author_name': str(author),
                        'mention': Lazy(lambda: self.any_mention(text, info, author)),
                        'direct_mention': Lazy(lambda: self.mentions(text)),
                        'random_mention': Lazy(lambda: self.random_mention(info, author), stick=False),
                        'any_mention': Lazy(lambda: self.random_mention(info, ''), stick=False),
                        'media': 'twitch',
                        'text': text,
                        'is_mod': is_mod,
                        'prefix': info.prefix,
                        'bot': self.nick,
                        'channel_id': info.channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': message_id,
                    }
                return variables
            msg = Message(
                id = message_id,
                log = log,
                channel_id=channel_id,
                txt=reward_title,
                event=EventType.twitch_reward_redemption,
                prefix=info.prefix,
                is_discord=False,
                is_mod=is_mod,
                private=False,
                get_variables=get_vars)
            actions = await commands.process_message(msg)
            db().add_log(channel_id, log)
            for a in actions:
                if a.kind == ActionKind.NEW_MESSAGE or a.kind == ActionKind.REPLY:
                    await self.send_message(info, a.text)
        except Exception as e:
            log.error(f"on_redemption: {str(e)}")
            log.error(traceback.format_exc())

    async def on_cron(self):
        info: ChannelInfo
        for channel_name, info in self.channels.items():
            if info.last_activity < time.time() - 1800.0:
                continue
            text = info.prefix + '_cron'
            log = InvocationLog(
                f"twitch channel {channel_name} ({info.channel_id})")
            variables: Optional[Dict] = None
            def get_vars():
                nonlocal variables
                if not variables:
                    variables = {
                        'mention': Lazy(lambda: self.random_mention(info, '')),
                        'direct_mention': '',
                        'random_mention': Lazy(lambda: self.random_mention(info, ''), stick=False),
                        'any_mention': Lazy(lambda: self.random_mention(info, ''), stick=False),
                        'media': 'twitch',
                        'text': text,
                        'is_mod': True,
                        'prefix': info.prefix,
                        'bot': self.nick,
                        'channel_id': info.channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': 'cron',
                    }
                return variables
            msg = Message(
                id = 'cron',
                log = log,
                channel_id=info.channel_id,
                txt=text,
                event=EventType.message,
                prefix=info.prefix,
                is_discord=False,
                is_mod=True,
                private=False,
                get_variables=get_vars)
            actions = await commands.process_message(msg)
            db().add_log(info.channel_id, log)
            for a in actions:
                if a.kind == ActionKind.NEW_MESSAGE:
                    await self.send_message(info, a.text)
