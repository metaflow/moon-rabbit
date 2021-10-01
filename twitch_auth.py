from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.types import AuthScope
import os
import logging
import os
import time
import sys
import requests

logging.basicConfig(stream = sys.stdout, 
                    format = '%(asctime)s %(levelname)s %(message)s', 
                    level = logging.INFO)

APP_ID = os.getenv('TWITCH_API_APP_ID')
APP_SECRET = os.getenv('TWITCH_API_APP_SECRET')
AUTH_URL = os.getenv('TWITCH_API_AUTH_URL')

def auth_callback(*args):
    logging.info(f'auth callback {args}')

TWITCH_AUTH_BASE_URL = "https://id.twitch.tv/"
twitch = Twitch(APP_ID, APP_SECRET)
target_scope = [AuthScope.CHANNEL_READ_REDEMPTIONS]
auth = UserAuthenticator(twitch, target_scope, force_verify=False, url=AUTH_URL)
print(auth.return_auth_url())
auth.__start()
logging.info('starting the server')
while not auth.__server_running:
    time.sleep(0.01)
while True:
  logging.info('waiting for auth request')
  while auth.__user_token is None:
      time.sleep(0.01)
  logging.info(f'got user token {auth.__user_token}')
  param = {
      'client_id': auth.__client_id,
      'client_secret': auth.__twitch.app_secret,
      'code': auth.__user_token,
      'grant_type': 'authorization_code',
      'redirect_uri': auth.url
  }
  url = auth.build_url(TWITCH_AUTH_BASE_URL + 'oauth2/token', param)
  response = requests.post(url)
  data: dict = response.json()
  logging.info(f'got tokens! {data["access_token"]} {data["refresh_token"]}')