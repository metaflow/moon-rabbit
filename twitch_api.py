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
import logging
from typing import Dict, List, Optional
import twitchio
from twitchio import eventsub
import dataclasses
from data import ActionKind, EventType, Message, InvocationLog, Lazy
from storage import cursor, db
import ttldict2
import commands
import random
import re
import time
from asyncio_throttle import Throttler


@dataclasses.dataclass
class ChannelInfo:
    active_users: ttldict2.TTLDict
    throttled_users: ttldict2.TTLDict  # user -> time
    prefix: str
    channel_id: int
    twitch_user_id: str
    events: List[EventType]
    last_activity: float


def is_moderator(payload: twitchio.ChatMessage) -> bool:
    """Return True if the chatter is a moderator or broadcaster."""
    try:
        badges = payload.badges or []
        badge_ids = [b.id if hasattr(b, 'id') else str(b) for b in badges]
        return 'moderator' in badge_ids or 'broadcaster' in badge_ids
    except Exception:
        return False


class Twitch3(twitchio.Client):
    """Twitch bot client based on twitchio.

    Stores auth tokens in database and executes custom commands defined in the database.
    """

    def __init__(self, twitch_bot: str, dev_message: Optional[str] = None):
        self.dev_message = dev_message
        logging.info(f'creating twitch bot {twitch_bot}')

        with cursor() as cur:
            cur.execute(
                "SELECT channel_name, api_app_id, api_app_secret, bot_user_id "
                "FROM twitch_bots WHERE channel_name = %s",
                (twitch_bot,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f'No twitch_bots row found for channel_name={twitch_bot!r}')
            self.channel_name, self.app_id, self.app_secret, self.bot_user_id = row

        if not self.bot_user_id:
            raise ValueError(
                f'bot_user_id is NULL for twitch_bot={twitch_bot!r}. '
                'Populate twitch_bots.bot_user_id with the numeric Twitch user ID of the bot account. '
                'See setup.md for instructions.')

        # Map Twitch channel login name -> ChannelInfo
        self.channels: Dict[str, ChannelInfo] = {}
        with cursor() as cur:
            cur.execute(
                "SELECT channel_id, twitch_channel_name, twitch_command_prefix, "
                "twitch_events, twitch_throttle "
                "FROM channels WHERE twitch_bot = %s",
                (self.channel_name,))
            for row in cur.fetchall():
                channel_id, twitch_channel_name, twitch_command_prefix, twitch_events, twitch_throttle = row
                if not twitch_throttle:
                    twitch_throttle = 0.0
                events: List[EventType] = []
                if twitch_events:
                    for x in twitch_events.split(','):
                        events.append(EventType[x.strip()])
                self.channels[twitch_channel_name] = ChannelInfo(
                    active_users=ttldict2.TTLDict(ttl_seconds=3600.0),
                    prefix=twitch_command_prefix,
                    channel_id=channel_id,
                    twitch_user_id='',
                    events=events,
                    throttled_users=ttldict2.TTLDict(ttl_seconds=float(max(twitch_throttle, 1))),
                    last_activity=0.0)

        logging.info(f'channels: {list(self.channels.keys())}')
        self.throttler = Throttler(rate_limit=1, period=1)

        # twitchio 3.x: Client(client_id, client_secret, bot_id=...)
        super().__init__(
            client_id=self.app_id,
            client_secret=self.app_secret,
            bot_id=self.bot_user_id,
        )

    # ------------------------------------------------------------------
    # Token Management
    # ------------------------------------------------------------------

    async def add_token(self, token: str, refresh: str):
        resp = await super().add_token(token, refresh)
        if resp.user_id:
            db().save_twitch_token(resp.user_id, token, refresh)
            logging.info(f'[auth] Added token to the database for user: {resp.user_id}')
        else:
            logging.warning('no user_id in response')
        return resp

    async def load_tokens(self, path: Optional[str] = None) -> None:
        tokens = db().load_twitch_tokens()
        logging.info(f'loaded {len(tokens)} auth tokens')
        for token, refresh in tokens:
            try:
                await super().add_token(token, refresh)
            except Exception as e:
                logging.warning(f'[auth] Failed to load token: {e}')

    async def save_tokens(self, path: Optional[str] = None) -> None:
        # We save tokens dynamically in add_token, so we do nothing here
        # to prevent creating the default .tio.tokens.json file.
        pass

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        """Called after login() but before the client is ready.

        Resolves broadcaster user IDs and creates EventSub subscriptions.
        Requires that broadcaster accounts have already authorized via the
        built-in OAuth server (port 4343) so their tokens exist.
        """
        logging.info('[setup_hook] resolving broadcaster IDs and subscribing to EventSub')

        broadcaster_logins = list(self.channels.keys())
        if not broadcaster_logins:
            logging.warning('[setup_hook] no channels configured, nothing to subscribe to')
            return

        # Fetch broadcaster user IDs in one batch call
        try:
            users = await self.fetch_users(logins=broadcaster_logins)
        except Exception as e:
            logging.error(f'[setup_hook] fetch_users failed: {e}\n{traceback.format_exc()}')
            return

        user_map: Dict[str, str] = {
            u.name.lower(): str(u.id) for u in users if u.name
        }
        # Create EventSub subscriptions. subscribe_websocket() is per-payload on plain Client.
        # - Chat message: as_bot=True uses the bot's user token (requires user:read:chat + user:bot)
        # - Redemptions/hype train: token_for=uid uses the channel owner's token
        for channel_name, info in self.channels.items():
            uid = user_map.get(channel_name.lower())
            if not uid:
                logging.warning(f'[setup_hook] could not resolve user ID for channel {channel_name!r}')
                continue
            info.twitch_user_id = uid

            # Chat messages — always subscribed for every managed channel
            try:
                await self.subscribe_websocket(
                    eventsub.ChatMessageSubscription(
                        broadcaster_user_id=uid,
                        user_id=self.bot_user_id,
                    ),
                    as_bot=True,
                )
                logging.info(f'[setup_hook] subscribed to chat in #{channel_name}')
            except Exception as e:
                logging.warning(f'[setup_hook] chat sub failed for #{channel_name}: {e}')

            for event_type in info.events:
                if event_type == EventType.twitch_reward_redemption:
                    try:
                        await self.subscribe_websocket(
                            eventsub.ChannelPointsCustomRewardRedemptionAddSubscription(
                                broadcaster_user_id=uid,
                            ),
                            token_for=uid,
                        )
                        logging.info(f'[setup_hook] subscribed to redemptions in #{channel_name}')
                    except Exception as e:
                        logging.warning(f'[setup_hook] redemption sub failed for #{channel_name}: {e}')

                elif event_type == EventType.twitch_hype_train:
                    try:
                        await self.subscribe_websocket(
                            eventsub.HypeTrainEndSubscription(
                                broadcaster_user_id=uid,
                            ),
                            token_for=uid,
                        )
                        logging.info(f'[setup_hook] subscribed to hype train in #{channel_name}')
                    except Exception as e:
                        logging.warning(f'[setup_hook] hype train sub failed for #{channel_name}: {e}')

    async def event_ready(self) -> None:
        logging.info(f'event_ready {self.user}')
        if self.dev_message:
            await asyncio.sleep(3) # wait for cannel join
            for channel_name in self.channels:
                try:
                    broadcaster = self.create_partialuser(
                        user_id=self.channels[channel_name].twitch_user_id,
                        user_login=channel_name,
                    )
                    if self.user:
                        await broadcaster.send_message(
                            sender=self.user,
                            message=self.dev_message,
                        )
                    logging.info(f'[dev] sent smoke-test to #{channel_name}')
                except Exception as e:
                    logging.warning(f'[dev] failed to send to #{channel_name}: {e}')

    async def event_error(self, error: Exception, *args, **kwargs) -> None:
        logging.error(f'event_error: {error}')
        logging.error(traceback.format_exc())

    async def event_token_refreshed(self, payload) -> None:
        logging.debug(f'event_token_refreshed: user_id={getattr(payload, "user_id", "?")}')

    async def event_oauth_authorized(self, payload) -> None:
        logging.info(f'event_oauth_authorized: access_token={payload.access_token}, refresh_token={payload.refresh_token}')
        await self.add_token(payload.access_token, payload.refresh_token)

    # ------------------------------------------------------------------
    # Chat messages
    # ------------------------------------------------------------------

    async def event_message(self, payload: twitchio.ChatMessage) -> None:
        try:
            # Ignore the bot's own messages
            chatter_id = str(payload.chatter.id)
            if chatter_id == str(self.bot_user_id):
                return

            channel_name: str = payload.broadcaster.name.lower()
            info: Optional[ChannelInfo] = self.channels.get(channel_name)
            if not info:
                logging.debug(f'[event_message] unknown channel {channel_name!r}')
                return

            info.last_activity = time.time()
            channel_id = info.channel_id
            prefix = info.prefix
            log = InvocationLog(f"twitch channel {channel_name} ({channel_id})")

            author_raw = payload.chatter.name
            if not author_raw:
                return
            author: str = author_raw
            text: str = payload.text

            info.active_users[author] = 1
            info.throttled_users.drop_old_items()
            if author in info.throttled_users:
                return
            info.active_users.drop_old_items()

            log.debug(f'{author} "{text}"')

            is_mod: bool = is_moderator(payload)
            message_id: str = str(time.time_ns())
            variables: Optional[Dict] = None

            def get_vars():
                nonlocal variables
                if not variables:
                    variables = {
                        'author': author,
                        'author_name': author,
                        'mention': Lazy(lambda: self.any_mention(text, info, author)),
                        'direct_mention': Lazy(lambda: self.mentions(text)),
                        'random_mention': Lazy(lambda: self.random_mention(info, author), stick=False),
                        'any_mention': Lazy(lambda: self.random_mention(info, ''), stick=False),
                        'media': 'twitch',
                        'text': text,
                        'is_mod': is_mod,
                        'prefix': prefix,
                        'bot': self.channel_name,
                        'channel_id': channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': message_id,
                    }
                return variables

            msg = Message(
                id=message_id,
                log=log,
                channel_id=channel_id,
                txt=text,
                event=EventType.message,
                prefix=prefix,
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
            logging.error(f'[event_message] {e}\n{traceback.format_exc()}')

    async def event_channel_points_redemption_add(self, payload) -> None:
        try:
            logging.info(f'[redemption] {payload}')
            channel_name: str = payload.broadcaster.name.lower()
            info: Optional[ChannelInfo] = self.channels.get(channel_name)
            if not info:
                logging.info(f'[redemption] unknown channel {channel_name!r}')
                return

            author_raw = payload.user.name
            if not author_raw:
                return
            author: str = author_raw
            text: str = payload.user_input or ''
            reward_title: str = payload.reward.title
            channel_id = info.channel_id
            log = InvocationLog(f"twitch channel {channel_name} ({channel_id})")
            log.info(f'reward "{reward_title}" for user {author}')

            is_mod = False
            message_id = str(time.time_ns())
            variables: Optional[Dict] = None

            def get_vars():
                nonlocal variables
                if not variables:
                    variables = {
                        'author': author,
                        'author_name': author,
                        'mention': Lazy(lambda: self.any_mention(text, info, author)),
                        'direct_mention': Lazy(lambda: self.mentions(text)),
                        'random_mention': Lazy(lambda: self.random_mention(info, author), stick=False),
                        'any_mention': Lazy(lambda: self.random_mention(info, ''), stick=False),
                        'media': 'twitch',
                        'text': text,
                        'is_mod': is_mod,
                        'prefix': info.prefix,
                        'bot': self.channel_name,
                        'channel_id': channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': message_id,
                    }
                return variables

            msg = Message(
                id=message_id,
                log=log,
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
            logging.error(f'[redemption] {e}\n{traceback.format_exc()}')

    async def event_channel_hype_train_end(self, payload) -> None:
        try:
            logging.debug(f'[hype_train_end] {payload}')
            channel_name: str = payload.broadcaster.name.lower()
            info: Optional[ChannelInfo] = self.channels.get(channel_name)
            if not info:
                logging.info(f'[hype_train_end] unknown channel {channel_name!r}')
                return

            level: str = str(payload.level)
            contributors: str = ', '.join(
                '@' + c.user.name
                for c in (payload.top_contributions or [])
            )
            channel_id = info.channel_id
            log = InvocationLog(f"twitch channel {channel_name} ({channel_id})")

            is_mod = False
            message_id = str(time.time_ns())
            variables: Optional[Dict] = None

            def get_vars():
                nonlocal variables
                if not variables:
                    variables = {
                        'author': '',
                        'author_name': '',
                        'mention': Lazy(lambda: contributors),
                        'direct_mention': Lazy(lambda: contributors),
                        'random_mention': Lazy(lambda: contributors),
                        'any_mention': Lazy(lambda: contributors),
                        'media': 'twitch',
                        'text': level,
                        'is_mod': is_mod,
                        'prefix': info.prefix,
                        'bot': self.channel_name,
                        'channel_id': channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': message_id,
                    }
                return variables

            msg = Message(
                id=message_id,
                log=log,
                channel_id=channel_id,
                txt=level,
                event=EventType.twitch_hype_train,
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
            logging.error(f'[hype_train_end] {e}\n{traceback.format_exc()}')

    async def send_message(self, info: ChannelInfo, txt: str) -> None:
        if not info.twitch_user_id:
            logging.warning(f'send_message: no broadcaster user ID for channel {info.channel_id}')
            return
        if len(txt) > 500:
            txt = txt[:497] + '...'
        if self.user is None:
            logging.warning('send_message: bot not logged in')
            return
        async with self.throttler:
            logging.info(f'> {txt!r}')
            try:
                broadcaster = self.create_partialuser(
                    user_id=info.twitch_user_id,
                    user_login='',
                )
                await broadcaster.send_message(
                    sender=self.user,
                    message=txt,
                )
            except Exception as e:
                logging.error(f'[send_message] failed: {e}')

    def any_mention(self, txt: str, info: ChannelInfo, author: str) -> str:
        direct = self.mentions(txt)
        return direct if direct else self.random_mention(info, author)

    def mentions(self, txt: str) -> str:
        result = re.findall(r'@\S+', txt)
        return ' '.join(result) if result else ''

    def random_mention(self, info: ChannelInfo, author: str) -> str:
        users = [x for x in info.active_users.keys() if x != author]
        return '@' + (random.choice(users) if users else author)

    async def on_cron(self) -> None:
        for channel_name, info in self.channels.items():
            if info.last_activity < time.time() - 1800.0:
                continue
            text = info.prefix + '_cron'
            log = InvocationLog(f"twitch channel {channel_name} ({info.channel_id})")
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
                        'bot': self.channel_name,
                        'channel_id': info.channel_id,
                        '_log': log,
                        '_private': False,
                        '_id': 'cron',
                    }
                return variables

            msg = Message(
                id='cron',
                log=log,
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

