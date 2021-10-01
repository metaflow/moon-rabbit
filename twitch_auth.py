from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.types import AuthScope
import os
import logging
import os
import sys

logging.basicConfig(stream = sys.stdout, 
                    format = '%(asctime)s %(levelname)s %(message)s', 
                    level = logging.INFO)

APP_ID = os.getenv('TWITCH_API_APP_ID')
APP_SECRET = os.getenv('TWITCH_API_APP_SECRET')
AUTH_URL = os.getenv('TWITCH_API_AUTH_URL')

def auth_callback(*args):
    logging.info(f'auth callback {args}')

twitch = Twitch(APP_ID, APP_SECRET)
target_scope = [AuthScope.CHANNEL_READ_REDEMPTIONS]
auth = UserAuthenticator(twitch, target_scope, force_verify=False, url=AUTH_URL)
print(auth.return_auth_url())
# auth.authenticate(auth_callback, user_token='')