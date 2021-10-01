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

async def on_redeption(*args):
    logging.info(f'on_redemption {args}')

APP_ID = os.getenv('TWITCH_API_APP_ID')
APP_SECRET = os.getenv('TWITCH_API_APP_SECRET')
# create instance of twitch API and create app authentication
twitch = Twitch(APP_ID, '4abnt6fc2kjts5pgxl9u3yhl5eyac7')
twitch.authenticate_app([])
TARGET_USERNAME = 'go_olga'
WEBHOOK_URL = 'https://twitch.apexlegendsrecoils.online'
# get ID of user
uid = twitch.get_users(logins=[TARGET_USERNAME])
user_id = uid['data'][0]['id']
logging.info(f'user id {user_id}')
hook = EventSub(WEBHOOK_URL, APP_ID, 8080, twitch)
# unsubscribe from all to get a clean slate
hook.unsubscribe_all()
# start client
hook.start()
print('subscribing to hooks:')
# hook.listen_channel_points_custom_reward_redemption_add(user_id, on_redeption)
# hook.listen_channel_follow(user_id, on_follow)
try:
    input('press Enter to shut down...')
finally:
    hook.stop()
print('done')