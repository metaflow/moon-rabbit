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

import argparse
import asyncio
import datetime
import logging
import logging.handlers
import os
import sys
import traceback

import discord
import twitchio
from dotenv import load_dotenv

import templates
import twitch_client
from data import set_is_dev
from discord_client import DiscordClient
from storage import DB, db, set_db


async def expireVariables():
    while True:
        db().expire_variables()
        db().expire_old_queries()
        await asyncio.sleep(300)


async def shutdown(
    discord_client: DiscordClient | None, twitch_bot: twitch_client.TwitchClient | None
):
    """Gracefully close all client sessions and cancel background tasks."""
    shutdown_tasks = []
    if discord_client:
        logging.info("Closing Discord client...")
        shutdown_tasks.append(discord_client.close())
    if twitch_bot:
        logging.info("Closing Twitch client...")
        shutdown_tasks.append(twitch_bot.close())

    if shutdown_tasks:
        try:
            # Wait for up to 10 seconds for clients to close
            await asyncio.wait_for(asyncio.gather(*shutdown_tasks), timeout=10.0)
        except TimeoutError:
            logging.warning("Shutdown timed out after 10 seconds.")
        except Exception as e:
            logging.error(f"Error during shutdown: {e}\n{traceback.format_exc()}")

    # Cancel all other tasks (like cron and expireVariables)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        logging.info(f"Canceling {len(tasks)} remaining background tasks...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def run_loop(
    loop: asyncio.AbstractEventLoop,
    discord_client: DiscordClient | None,
    twitch_bot: twitch_client.TwitchClient | None,
    cron_interval_s: int,
):
    """Run the main event loop and handle graceful shutdown."""
    try:
        logging.info("running the async loop")
        loop.create_task(expireVariables())
        loop.run_forever()
    except KeyboardInterrupt:
        logging.info("Caught KeyboardInterrupt, shutting down...")
    except Exception as e:
        logging.error(f"Caught unexpected exception: {e}\n{traceback.format_exc()}")
    finally:
        logging.info("Commencing shutdown...")
        # Run the shutdown tasks until complete
        loop.run_until_complete(shutdown(discord_client, twitch_bot))
        loop.close()
        logging.info("Shutdown complete.")


async def cron(client: DiscordClient | twitch_client.TwitchClient, cron_interval_s: int):
    while True:
        await client.on_cron()
        await asyncio.sleep(cron_interval_s)


def setup_logging(log_prefix: str, also_log_to_stdout: bool):
    """Configure multi-level file logging with automatic size-based rotation.

    Creates three log files:
      {log_prefix}.debug.log  — all levels (50MB, 8 backups)
      {log_prefix}.info.log   — INFO+ (20MB, 8 backups)
      {log_prefix}.errors.log — ERROR+ (10MB, 8 backups)
    """
    log_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    debugHandler = logging.handlers.RotatingFileHandler(
        f"{log_prefix}.debug.log", encoding="utf-8", maxBytes=50_000_000, backupCount=8
    )
    debugHandler.setLevel(logging.DEBUG)
    debugHandler.setFormatter(log_fmt)
    infoHandler = logging.handlers.RotatingFileHandler(
        f"{log_prefix}.info.log", encoding="utf-8", maxBytes=20_000_000, backupCount=8
    )
    infoHandler.setLevel(logging.INFO)
    infoHandler.setFormatter(log_fmt)
    errHandler = logging.handlers.RotatingFileHandler(
        f"{log_prefix}.errors.log", encoding="utf-8", maxBytes=10_000_000, backupCount=8
    )
    errHandler.setLevel(logging.ERROR)
    errHandler.setFormatter(log_fmt)
    logging.basicConfig(
        handlers=[debugHandler, infoHandler, errHandler],
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.DEBUG,
    )
    if also_log_to_stdout:
        stdoutHandler = logging.StreamHandler()
        stdoutHandler.setFormatter(log_fmt)
        logging.getLogger().addHandler(stdoutHandler)


def require_env(name: str) -> str:
    val = os.getenv(name)
    if val is None:
        logging.error(f"Missing required environment variable: {name}")
        sys.exit(1)
    return val


def main():
    templates.register_template_globals()
    parser = argparse.ArgumentParser(description="moon rabbit")
    parser.add_argument("--twitch")
    parser.add_argument("--discord", action="store_true")
    parser.add_argument("--twitch_channel_name")
    parser.add_argument("--twitch_command_prefix", default="+")
    parser.add_argument("--channel_id")
    parser.add_argument("--also_log_to_stdout", action="store_true")
    parser.add_argument("--log", default="bot")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--cron_interval_s", default="600")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: send a smoke-test message to all channels on connect",
    )
    args = parser.parse_args()
    setup_logging(args.log, args.also_log_to_stdout)
    load_dotenv()
    db_connection = require_env("DB_CONNECTION")
    logging.info(f"connecting to {db_connection}")
    set_db(DB(db_connection))
    db().check_database()
    logging.info(f"args {args}")
    loop = asyncio.new_event_loop()
    # loop = asyncio.get_running_loop()
    dev_msg = None
    if args.dev:
        set_is_dev(True)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dev_msg = f"\U0001f407 moon-rabbit dev mode — connected at {now}"
        logging.info(f"dev mode enabled, smoke-test message: {dev_msg}")
    discordClient = None
    twitch_bot = None
    if args.discord:
        try:
            logging.info("starting Discord Bot")
            intents = discord.Intents.default()
            intents.message_content = True
            discordClient = DiscordClient(
                intents=intents, loop=loop, profile=args.profile, dev_message=dev_msg
            )
            discord_token = require_env("DISCORD_TOKEN")
            loop.create_task(discordClient.start(discord_token))
            loop.create_task(cron(discordClient, int(args.cron_interval_s)))
        except Exception as e:
            logging.error(f"{e}\n{traceback.format_exc()}")
    if args.twitch:
        try:
            twitch_bot = twitch_client.TwitchClient(
                twitch_bot=args.twitch,
                dev_message=dev_msg,
                domain=require_env("TWITCH_OAUTH_DOMAIN").removesuffix("/"),
            )
            logging.info(
                f"Channel Owner Authorization URL {twitch_bot.adapter.get_authorization_url(scopes=twitchio.Scopes(channel_bot=True, channel_read_redemptions=True, channel_read_hype_train=True), force_verify=True)}"  # type: ignore[attr-defined]
            )
            logging.info(
                f"Bot Account Authorization URL {twitch_bot.adapter.get_authorization_url(scopes=twitchio.Scopes(user_read_chat=True, user_write_chat=True, user_bot=True), force_verify=True)}"  # type: ignore[attr-defined]
            )
            loop.create_task(twitch_bot.start())
            loop.create_task(cron(twitch_bot, int(args.cron_interval_s)))
        except Exception as e:
            logging.error(f"{e}\n{traceback.format_exc()}")
    if args.twitch or args.discord:
        run_loop(loop, discordClient, twitch_bot, int(args.cron_interval_s))
        sys.exit(0)
    print("add --twitch or --discord argument to run bot")
    sys.exit(1)


if __name__ == "__main__":
    main()
