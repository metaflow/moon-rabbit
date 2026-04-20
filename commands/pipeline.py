"""
Copyright 2021 Google LLC

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
import dataclasses
import json
import logging
import re
import time
import traceback
from typing import Protocol

import ttldict2

from data import (
    Action,
    ActionKind,
    CommandData,
    InvocationLog,
    Message,
    is_dev,
    render,
)
from storage import db

# id: str -> Message
messages = ttldict2.TTLDict(ttl_seconds=600.0)

# channel_id -> timestamp of the last "error occurred" chat reply
_ERROR_REPLY_COOLDOWN_SECS = 30 * 60
_last_error_reply: dict[int, float] = {}


async def process_message(msg: Message) -> list[Action]:
    logging.debug(f'process message "{msg.txt}" type {msg.event}')
    messages[msg.id] = msg
    actions: list[Action] = []
    try:
        cmds = await asyncio.to_thread(get_commands, msg.channel_id, msg.prefix)
        for cmd in cmds:
            if cmd.mod_only() and not msg.is_mod:
                continue
            if cmd.private_mod_only() and not (msg.is_mod and msg.private):
                continue
            if msg.is_discord and not cmd.for_discord():
                continue
            if (not msg.is_discord) and not cmd.for_twitch():
                continue
            a, next = await asyncio.to_thread(cmd.run, msg)
            actions.extend(a)
            if not next:
                break
        actions.extend(msg.additionalActions)
        log_actions = [a for a in actions if a.attachment == ""]
        msg.log.debug(f"actions (except download) {log_actions}")
    except Exception as e:
        msg.log.error(f"{e}\n{traceback.format_exc()}")
        now = time.monotonic()
        if now - _last_error_reply.get(msg.channel_id, 0.0) >= _ERROR_REPLY_COOLDOWN_SECS:
            _last_error_reply[msg.channel_id] = now
            actions.append(Action(kind=ActionKind.REPLY, text="error occurred"))
        if is_dev():
            raise
    return actions


def command_prefix(txt: str, prefix: str, s: list[str]) -> str:
    for x in s:
        if txt.startswith(prefix + x):
            # TODO: don't append an empty string and return additional bool instead.
            return txt[len(prefix + x) :] + " "
        if txt.startswith(prefix + " " + x):
            return txt[len(prefix + " " + x) :] + " "
    return ""


class Command(Protocol):
    # Note that command run is not async, for perfom
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        return [], True

    def help(self, prefix: str):
        return ""

    def help_full(self, prefix: str):
        return self.help(prefix)

    def mod_only(self):
        return True

    def private_mod_only(self):
        return False

    def for_discord(self):
        return True

    def for_twitch(self):
        return True

    def hidden_help(self):
        return True


# str -> List[Command]
commands_cache = ttldict2.TTLDict(ttl_seconds=600.0)


def get_commands(channel_id: int, prefix: str) -> list[Command]:
    key = f"commands_{channel_id}_{prefix}"
    r = commands_cache.get(key)
    if not r:
        from commands.builtins import (
            Debug,
            Eval,
            HelpCommand,
            InvalidateCache,
            Multiline,
            SetCommand,
            SetPrefix,
        )
        from commands.text import (
            TagDelete,
            TagList,
            TextDescribe,
            TextDownload,
            TextNew,
            TextRemove,
            TextSearch,
            TextSet,
            TextSetNew,
            TextUpload,
        )

        commands: list[Command] = [
            HelpCommand(),
            Eval(),
            Debug(),
            Multiline(),
            SetCommand(),
            SetPrefix(),
            TagList(),
            TagDelete(),
            TextSet(),
            TextUpload(),
            TextDownload(),
            TextSearch(),
            TextRemove(),
            TextDescribe(),
            TextNew(),
            TextSetNew(),
            InvalidateCache(),
        ]
        commands.extend(
            [PersistentCommand(x, prefix) for x in db().get_commands(channel_id, prefix)]
        )
        commands_cache[key] = commands
        r = commands
    return r


class PersistentCommand(Command):
    regex: re.Pattern
    data: CommandData

    def __init__(self, data, prefix):
        self.data = data
        p = data.pattern.replace("!prefix", re.escape(prefix) + " ?")
        logging.debug(f"regex {p}")
        self.regex = re.compile(p, re.IGNORECASE)

    def for_discord(self):
        return self.data.discord

    def for_twitch(self):
        return self.data.twitch

    def run(self, msg: Message) -> tuple[list[Action], bool]:
        if msg.event != self.data.event_type or not re.search(self.regex, msg.txt):
            return [], True
        variables = msg.get_variables()
        if self.data.mod and not variables["is_mod"]:
            logging.debug("non mod called persistent")
            return [], True
        log: InvocationLog = variables["_log"]
        log.info(f"matched command {json.dumps(dataclasses.asdict(self.data), ensure_ascii=False)}")
        actions: list[Action] = []
        try:
            for e in self.data.actions:
                variables["_render_depth"] = 0
                a = Action(kind=e.kind, text=render(e.text, variables))
                if a.text:
                    actions.append(a)
            return actions, True
        except Exception as e:
            log.error(f"failed to render '{self.data.name}': {str(e)}")
            log.error(traceback.format_exc())
            return [], True

    def help(self, prefix: str):
        if self.data.help:
            return self.data.help.replace("!prefix", prefix)
        return prefix + self.data.name

    def help_full(self, prefix: str):
        if self.data.help_full:
            return self.data.help_full.replace("!prefix", prefix)
        return self.help(prefix)

    def mod_only(self):
        return self.data.mod

    def hidden_help(self):
        return self.data.hidden
