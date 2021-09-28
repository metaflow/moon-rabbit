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
from data import *
import logging
from typing import Any, Callable, Dict, Optional, Union
import twitchio  # type: ignore
import ttldict2
from storage import db


class Twitch(twitchio.Client):
    def __init__(self, token: str, client_secret: str = None, initial_channels: Union[list, tuple, Callable] = None, loop: asyncio.AbstractEventLoop = None, heartbeat: Optional[float] = 30):
        super().__init__(token, client_secret=client_secret,
                         initial_channels=initial_channels, loop=loop, heartbeat=heartbeat)
        self.channels: Dict[str, Any] = {}

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
        log.info(message.content)
