#!/usr/bin/python
#
# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# TODO allow commands w/o prefix in private bot conversation
# TODO check sandbox settings
# TODO test perf of compiled template VS from_string
# TODO bingo or anagramms?
# TODO DB indexes
"""Bot entry point."""

import asyncio
import traceback
from io import StringIO
from data import *
from twitchio.ext import commands as twitchCommands  # type: ignore
import argparse
import discord  # type: ignore
import jinja2
import logging
import os
import sys
import random
from storage import DB, db, set_db, cursor
from typing import Any, Callable, List, Set, Union
import commands
import time
import logging.handlers
import twitch_api
import numpy as np
from discord_client import DiscordClient, discord_literal

@jinja2.pass_context
def render_text_item(ctx, q: Union[str, int, List[Union[str, float]]], inf: str = ''):
    v = ctx.get_all()
    v['_render_depth'] += 1
    if v['_render_depth'] > 50:
        v['_log'].error('rendering depth is > 50')
        return ''
    text_id: Optional[int] = None
    channel_id = v['channel_id']
    if isinstance(q, int):
        text_id = q
    elif isinstance(q, str):
        if inf:
            q = f'({q}) and {inf}'
        text_id = db().get_random_text_id(channel_id, q)
    else:
        queries = q[::2]
        weights = np.array([abs(float(x)) for x in q[1::2]])
        weights /= np.sum(weights)
        query_text: str = db().rng.choice(queries, p=weights)
        if inf:
            query_text = f'({query_text}) and {inf}'
        text_id = db().get_random_text_id(channel_id, query_text)
    if not text_id:
        v['_log'].info(f'no matching text is found')
        return ''
    if inf:
        tag_id = db().tag_by_value(channel_id)[inf]
        return db().get_text_tag_value(channel_id, text_id, tag_id)
    txt = db().get_text(channel_id, text_id)
    if not txt:
        v['_log'].info(f'failed to get text {text_id}')
        return ''
    return render(txt, v)


def randint(a=0, b=100):
    return random.randint(a, b)

@jinja2.pass_context
def get_variable(ctx, name: str, category: str = '', default_value: str = ''):
    channel_id = ctx.get('channel_id')
    return db().get_variable(channel_id, name, category, default_value)


@jinja2.pass_context
def set_variable(ctx, name: str, value: str = '', category: str = '', expires: int = 9 * 3600):
    channel_id = ctx.get('channel_id')
    db().set_variable(channel_id, name, value, category, expires + int(time.time()))
    return ''


@jinja2.pass_context
def get_variables_category_size(ctx, name: str) -> int:
    channel_id = ctx.get('channel_id')
    return db().count_variables_in_category(channel_id, name)


@jinja2.pass_context
def delete_category(ctx, name: str):
    channel_id = ctx.get('channel_id')
    db().delete_category(channel_id, name)
    return ''

@jinja2.pass_context
def list_category(ctx, name: str) -> List[Tuple[str,str]]:
    channel_id = ctx.get('channel_id')
    return db().list_variables(channel_id, name)

@jinja2.pass_context
def discord_or_twitch(ctx, vd: str, vt: str):
    return vd if ctx.get('media') == 'discord' else vt

@jinja2.pass_context
def new_message(ctx, s: str):
    msg: Message = commands.messages[ctx.get('_id')]
    msg.additionalActions.append(Action(kind=ActionKind.NEW_MESSAGE, text=s))
    return ''


# templates.globals['list'] = render_list_item
templates.globals['txt'] = render_text_item
templates.globals['randint'] = randint
templates.globals['discord_literal'] = discord_literal
templates.globals['get'] = get_variable
templates.globals['set'] = set_variable
templates.globals['category_size'] = get_variables_category_size
templates.globals['list_category'] = list_category
templates.globals['delete_category'] = delete_category
templates.globals['message'] = new_message
templates.globals['timestamp'] = lambda: int(time.time())
templates.globals['dt'] = discord_or_twitch
templates.globals['discord_name'] = discord_literal
# templates.globals['echo'] = lambda x: x
# templates.globals['log'] = lambda x: logging.info(x)


async def expireVariables():
    while True:
        db().expire_variables()
        db().expire_old_queries()
        await asyncio.sleep(300)

async def cron(client: Union[DiscordClient, twitch_api.Twitch3], cron_interval_s: int):
    while True:
        await client.on_cron()
        await asyncio.sleep(cron_interval_s)

def main():
    parser = argparse.ArgumentParser(description='moon rabbit')
    parser.add_argument('--twitch')
    parser.add_argument('--discord', action='store_true')
    parser.add_argument('--twitch_channel_name')
    parser.add_argument('--twitch_command_prefix', default='+')
    parser.add_argument('--channel_id')
    parser.add_argument('--also_log_to_stdout', action='store_true')
    parser.add_argument('--log', default='bot')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--log_level', default='INFO')
    parser.add_argument('--cron_interval_s', default='600')
    args = parser.parse_args()
    errHandler = logging.FileHandler( 
        f'{args.log}.errors.log', encoding='utf-8',)
    errHandler.setLevel(logging.ERROR)
    rotatingHandler = logging.handlers.TimedRotatingFileHandler(
        f'{args.log}.log', when='D', encoding='utf-8', backupCount=8)
    logging.basicConfig(
        handlers=[rotatingHandler, errHandler],
        format='%(asctime)s %(levelname)s %(message)s',
        level=args.log_level)
    if args.also_log_to_stdout:
        stdoutHandler = logging.StreamHandler()
        stdoutHandler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(message)s'))
        logging.getLogger().addHandler(stdoutHandler)
    logging.info(f"connecting to {os.getenv('DB_CONNECTION')}")
    set_db(DB(os.getenv('DB_CONNECTION')))
    db().check_database()
    logging.info(f'args {args}')
    loop = asyncio.new_event_loop()
    # loop = asyncio.get_running_loop()
    discordClient = None
    if args.discord:
        try:
            logging.info('starting Discord Bot')
            discordClient = DiscordClient(
                intents=discord.Intents.all(), loop=loop, profile=args.profile)
            loop.create_task(discordClient.start(os.getenv('DISCORD_TOKEN')))
            loop.create_task(cron(discordClient, int(args.cron_interval_s)))
        except Exception as e:
            logging.error(f'{e}\n{traceback.format_exc()}')
    if args.twitch:
        with cursor() as cur:
            try:
                t = twitch_api.Twitch3(twitch_bot=args.twitch, loop=loop)
                loop.create_task(t.connect())
                loop.create_task(cron(t, int(args.cron_interval_s)))
            except Exception as e:
                logging.error(f'{e}\n{traceback.format_exc()}')
    if args.twitch or args.discord:
        logging.info('running the async loop')
        loop.create_task(expireVariables())
        loop.run_forever()
        sys.exit(0)    
    print('add --twitch or --discord argument to run bot')
    sys.exit(1)

if __name__ == "__main__":
    main()
