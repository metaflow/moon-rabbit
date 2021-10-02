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
from data import *
import logging
from typing import Any, Callable, Dict, Optional, Union
import twitchio  # type: ignore
from storage import db


class TwitchEvent(str, Enum):
    moderation_user_action = 'moderation_user_action'
    channel_points = 'channel_points'

class Twitch3(twitchio.Client):
    def __init__(self, app_id: str, app_secret: str, url: str, port:int, watch: Dict[str, List[TwitchEvent]]):
        twitchApi = Twitch(app_id, app_secret)
        twitchApi.authenticate_app([])
        hook = EventSub(url, app_id, port, twitchApi)
        hook.unsubscribe_all()
        hook.start()
        logging.info('subscribing to hooks:')
        for channel_name, events in watch.items():
            uid = twitchApi.get_users(logins=[channel_name])
            user_id = uid['data'][0]['id']
            logging.info(f'{channel_name} user id={user_id}')
            for e in events:
                if e == TwitchEvent.channel_points:
                    hook.listen_channel_points_custom_reward_redemption_add(user_id, self.on_redeption)

    async def on_redeption(self, *args):
        logging.info(f'on_redemption {args}')