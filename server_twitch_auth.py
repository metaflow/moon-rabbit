from twitchAPI.twitch import Twitch
from twitchAPI.types import AuthScope
import os
import logging
import os
import time
import sys
import requests

from twitchAPI.twitch import Twitch
from twitchAPI.helper import build_url, build_scope, get_uuid, TWITCH_AUTH_BASE_URL, fields_to_enum
from twitchAPI.types import AuthScope, InvalidRefreshTokenException, UnauthorizedException, TwitchAPIException
from typing import List, Union
from aiohttp import web
import asyncio
from threading import Thread
from time import sleep
import requests
from concurrent.futures._base import CancelledError
from logging import getLogger, Logger
import storage


class UserAuthenticator:
    """Simple to use client for the Twitch User authentication flow.

        :param ~twitchAPI.twitch.Twitch twitch: A twitch instance
        :param list[~twitchAPI.types.AuthScope] scopes: List of the desired Auth scopes
        :param bool force_verify: If this is true, the user will always be prompted for authorization by twitch,
                    |default| :code:`False`
        :param str url: The reachable URL that will be opened in the browser.
                    |default| :code:`http://localhost:17563`

        :var int port: The port that will be used. |default| :code:`17653`
        :var str host: the host the webserver will bind to. |default| :code:`0.0.0.0`
       """

    __document: str = """<!DOCTYPE html>
 <html lang="en">
 <head>
     <meta charset="UTF-8">
     <title>pyTwitchAPI OAuth</title>
 </head>
 <body>
     <h1>Thanks for Authenticating with pyTwitchAPI!</h1>
 You may now close this page.
 </body>
 </html>"""

    __twitch: 'Twitch' = None
    port: int = 17563
    url: str = 'http://localhost:17563'
    host: str = '0.0.0.0'
    scopes: List[AuthScope] = []
    force_verify: bool = False
    __state: str = str(get_uuid())
    __logger: Logger = None
    __client_id: str = None
    __callback_func = None

    __server_running: bool = False
    __loop: Union['asyncio.AbstractEventLoop', None] = None
    __runner: Union['web.AppRunner', None] = None
    __thread: Union['threading.Thread', None] = None

    __user_token: Union[str, None] = None

    __can_close: bool = False

    def __init__(self,
                 twitch: 'Twitch',
                 scopes: List[AuthScope],
                 force_verify: bool = False,
                 url: str = 'http://localhost:17563'):
        self.__twitch = twitch
        self.__client_id = twitch.app_id
        self.scopes = scopes
        self.force_verify = force_verify
        self.__logger = getLogger('twitchAPI.oauth')
        self.url = url

    def __build_auth_url(self):
        params = {
            'client_id': self.__twitch.app_id,
            'redirect_uri': self.url,
            'response_type': 'code',
            'scope': build_scope(self.scopes),
            'force_verify': str(self.force_verify).lower(),
            'state': self.__state
        }
        return build_url(TWITCH_AUTH_BASE_URL + 'oauth2/authorize', params)

    def __build_runner(self):
        app = web.Application()
        app.add_routes([web.get('/', self.__handle_callback)])
        return web.AppRunner(app)

    async def __run_check(self):
        while not self.__can_close:
            try:
                await asyncio.sleep(1)
            except (CancelledError, asyncio.CancelledError):
                pass
        for task in asyncio.all_tasks(self.__loop):
            task.cancel()

    def __run(self, runner: 'web.AppRunner'):
        self.__runner = runner
        self.__loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.__loop)
        self.__loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, self.host, self.port)
        self.__loop.run_until_complete(site.start())
        self.__server_running = True
        self.__logger.info('running oauth Webserver')
        try:
            self.__loop.run_until_complete(self.__run_check())
        except (CancelledError, asyncio.CancelledError):
            pass

    def __start(self):
        self.__thread = Thread(
            target=self.__run, args=(self.__build_runner(),))
        self.__thread.start()

    def stop(self):
        """Manually stop the flow

        :rtype: None
        """
        self.__can_close = True

    async def __handle_callback(self, request: 'web.Request'):
        val = request.rel_url.query.get('state')
        self.__logger.debug(f'got callback with state {val}')
        # invalid state!
        if val != self.__state:
            return web.Response(status=401)
        self.__user_token = request.rel_url.query.get('code')
        if self.__user_token is None:
            # must provide code
            return web.Response(status=400)
        if self.__callback_func is not None:
            self.__callback_func(self.__user_token)
        return web.Response(text=self.__document, content_type='text/html')

    def return_auth_url(self):
        return self.__build_auth_url()

    def authenticate(self,
                     callback_func=None):
        """Start the user authentication flow\n
        If callback_func is not set, authenticate will wait till the authentication process finished and then return
        the access_token and the refresh_token
        If user_token is set, it will be used instead of launching the webserver and opening the browser

        :param callback_func: Function to call once the authentication finished.
        :param str user_token: Code obtained from twitch to request the access and refresh token.
        :return: None if callback_func is set, otherwise access_token and refresh_token
        :raises ~twitchAPI.types.TwitchAPIException: if authentication fails
        :rtype: None or (str, str)
        """
        self.__callback_func = callback_func
        logging.info('starting the server')
        self.__start()
        # wait for the server to start up
        while not self.__server_running:
            sleep(0.01)
        print("Open the link:")
        print(auth.return_auth_url())
        print()
        while True:
            logging.info('waiting for auth')
            while self.__user_token is None:
                sleep(0.01)
            # now we need to actually get the correct token
            param = {
                'client_id': self.__client_id,
                'client_secret': self.__twitch.app_secret,
                'code': self.__user_token,
                'grant_type': 'authorization_code',
                'redirect_uri': self.url
            }
            url = build_url(TWITCH_AUTH_BASE_URL + 'oauth2/token', param)
            response = requests.post(url)
            data: dict = response.json()
            logging.info(f'{data}')
            if callback_func:
                callback_func(data['access_token'], data['refresh_token'])


logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)s %(message)s',
                    level=logging.INFO)

# APP_ID = os.getenv('TWITCH_API_APP_ID')
# APP_SECRET = os.getenv('TWITCH_API_APP_SECRET')
AUTH_URL = os.getenv('TWITCH_API_AUTH_URL', 'http://localhost:17563')

def auth_callback(*args):
    logging.info(f'auth callback {args}')

if len(sys.argv) < 2:
    print('specify id from twitch_bots table')
bot_id = sys.argv[1]

storage.set_db(storage.DB(os.getenv('DB_CONNECTION')))
with storage.cursor() as cur:
    cur.execute("SELECT channel_name, api_app_id, api_app_secret, auth_token, api_url, api_port FROM twitch_bots WHERE id = %s", (bot_id,))
    channel_name, app_id, app_secret, auth_token, api_url, api_port = cur.fetchone()
    print(f'auth server for app_id {app_id} app_secret {app_secret} api_url {api_url} api_port {api_port}')
    twitch = Twitch(app_id, app_secret)
    target_scope = [AuthScope.CHANNEL_READ_REDEMPTIONS, AuthScope.BITS_READ, AuthScope.CHANNEL_READ_HYPE_TRAIN]
    auth = UserAuthenticator(twitch, target_scope, force_verify=False, url=AUTH_URL)
    auth.authenticate(callback_func=auth_callback)
