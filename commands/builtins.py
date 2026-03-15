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

import dataclasses
import json
import logging
import re

import discord

from commands.pipeline import (
    Command,
    PersistentCommand,
    command_prefix,
    commands_cache,
    get_commands,
)
from data import Action, ActionKind, CommandData, Message, dictToCommandData, render
from storage import cursor, db


class Eval(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["eval"])
        if not text:
            return [], True
        text = text.strip()
        v = msg.get_variables()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        # v['_log'].info(f'eval "{text}"')
        v["_render_depth"] = 0
        s = render(text, v)
        if not s:
            s = "<empty>"
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f"{prefix}eval"

    def help_full(self, prefix: str):
        return f'{prefix}eval <expression> (see "set")'


class SetCommand(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["command"])
        if not text:
            return [], True
        text = text.strip()
        log = msg.log
        channel_id = msg.channel_id
        commands_cache.pop(f"commands_{channel_id}_{msg.prefix}", None)
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        parts = text.split(" ", 1)
        name = parts[0]
        if len(parts) == 1:
            cursor().execute(
                "DELETE FROM commands WHERE channel_id = %s AND name = %s", (channel_id, name)
            )
            return [Action(kind=ActionKind.REPLY, text=f"Deleted command '{name}'")], False
        command_text = parts[1]
        cmd = CommandData(pattern="!prefix" + re.escape(name) + "\\b")
        try:
            cmd = dictToCommandData(json.loads(command_text))
        except Exception:
            log.info("failed to parse command as JSON, assuming literal text")
            cmd.actions.append(Action(text=command_text, kind=ActionKind.NEW_MESSAGE))
        cmd.name = name
        log.info(f"parsed command {cmd}")
        v = msg.get_variables()
        id = db().set_command(cursor(), channel_id, v["author_name"], cmd)
        log.info(f"channel={channel_id} author={v['author_name']} added new command '{name}' #{id}")
        return [Action(kind=ActionKind.REPLY, text=f"Added new command '{name}' #{id}")], False

    def help(self, prefix: str):
        return f"{prefix}command"

    def help_full(self, prefix: str):
        return f'''{prefix}command [<name> <template>|<JSON>]
Missing value deletes command.
See https://jinja.palletsprojects.com/en/3.0.x/templates/ for the general template syntax.

Variables available:
- "author" - author of original message
- "bot" - bot mention
- "direct_mention" (if there is a direct mention)
- "random_mention" (always random)
- "any_mention" (same as random_mention but includes author)
- "is_mod" - author is moderator
- "media" - "discord" or "twitch"
- "mention" - direct_mention if set, otherwise random_mention
- "prefix" - command prefix ({prefix})
- "text" - full message text

Additional functions:
- randint(from = 0, to = 100) - random integer in [from, to] range;
- timestamp() - current timestamp in seconds, integer
- dt(<text for discord>, <text for twitch>) - different fixed text for discord or twitch;
- get(<name>[, <category = ''>, <default value = ''>]) - get variable value;
- set(<name>[, <value = ''>, <category = ''>, <expires in seconds = 32400 (9h)>]) - set variable that will expire after some time. Empty value deletes the variable;
- category_size(<category>) - number of set variables in category;
- delete_category(<category>) - delete all variables in category;
- txt(<tags filter>, <inflect>) - get a random text fragment.
Tags filter selects subset of all texts. From simple "my-tag" to complex "tag1 or (tag2 and tag3) except tag4 and tag5".
If <inflect> is set, text will not be rendered and instead inflected according to russian language (possible options: им, nomn, рд, gent, дт, datv, вн, accs, тв, ablt, пр, loct, ед, sing, мн, plur, СУЩ, NOUN, ПРИЛ, ADJF).
For example txt('morph & adj & good', 'тв').

JSON format is ever changing, use "{prefix}debug <command>" to get a command representation.
It is the only way to customize a command to match a different regex, allow only for mods, hide it.
'''


class SetPrefix(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["prefix-set"])
        if not text:
            return [], True
        text = text.strip()
        v = msg.get_variables()
        log = msg.log
        channel_id = msg.channel_id
        if v["bot"] not in str(v["direct_mention"]):
            log.info("this bot is not mentioned directly")
            return [], True
        if not text:
            return [], True
        if msg.is_discord:
            db().set_discord_prefix(channel_id, text)
        else:
            db().set_twitch_prefix(channel_id, text)
        return [
            Action(kind=ActionKind.REPLY, text=f'set new prefix for {v["media"]} to "{text}"')
        ], False

    def help(self, prefix: str):
        return f"{prefix}prefix-set"

    def help_full(self, prefix: str):
        return f"{prefix}prefix-set <new prefix> <bot>"


class Multiline(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["multiline"])
        if not text:
            return [], True
        text = text.strip()
        channel_id = msg.channel_id
        lines = [x.strip() for x in text.split("\n")]
        is_mod = msg.is_mod
        private = msg.private
        actions: list[Action] = []
        cmds = get_commands(channel_id, msg.prefix)
        for line in lines:
            if not line:
                continue
            logging.debug(f"executing line {line}")
            for cmd in cmds:
                if cmd.private_mod_only() and not (is_mod and private):
                    continue
                if cmd.mod_only() and not is_mod:
                    continue
                if msg.is_discord and not cmd.for_discord():
                    continue
                if (not msg.is_discord) and not cmd.for_twitch():
                    continue
                cp = dataclasses.replace(msg)
                cp.txt = line
                a, next = cmd.run(cp)
                actions.extend(a)
                if not next:
                    break
        return [Action(kind=ActionKind.REPLY, text=f"Executed {len(lines)} lines")], False

    def help(self, prefix: str):
        return f"{prefix}multiline"

    def help_full(self, prefix: str):
        return f"{prefix}multiline\n{prefix}command1\n{prefix}command2\n..."


class Debug(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["debug"])
        if not text:
            return [], True
        text = text.strip()
        results: list[Action] = []
        msg.get_variables()  # TODO: drop? I don't think we need this side effect.
        channel_id = msg.channel_id
        if not text:
            for e in db().get_logs(channel_id):
                s = (
                    "\n".join([discord.utils.escape_mentions(x[1]) for x in e.messages])
                    + "\n-----------------------------\n"
                )
                results.append(Action(kind=ActionKind.PRIVATE_MESSAGE, text=s))
            return results, False
        commands = db().get_commands(channel_id, msg.prefix)
        for cmd in commands:
            if cmd.name == text:
                results.append(
                    Action(
                        ActionKind.PRIVATE_MESSAGE,
                        f"{msg.prefix}command {cmd.name} "
                        + discord.utils.escape_markdown(
                            discord.utils.escape_mentions(
                                json.dumps(dataclasses.asdict(cmd), ensure_ascii=False)
                            )
                        ),
                    )
                )
        return results, False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f"{prefix}debug"

    def help_full(self, prefix: str):
        return f'"{prefix}debug" OR "{prefix}debug <command name>"'

    def private_mod_only(self):
        return True


class HelpCommand(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["commands", "help"])
        if not text:
            return [], True
        text = text.strip()
        is_mod = msg.is_mod
        private = msg.private
        channel_id = msg.channel_id
        if not text:
            hidden_commands = []
            names = []
            s = []
            for c in get_commands(channel_id, msg.prefix):
                if isinstance(c, PersistentCommand):
                    names.append(c.data.name)
                if msg.is_discord and not c.for_discord():
                    hidden_commands.append(c.help(msg.prefix))
                    continue
                if (not msg.is_discord) and not c.for_twitch():
                    hidden_commands.append(c.help(msg.prefix))
                    continue
                if c.mod_only() and not is_mod:
                    continue
                if c.private_mod_only() and not (is_mod and private):
                    continue
                if c.hidden_help():
                    hidden_commands.append(c.help(msg.prefix))
                    continue
                s.append(c.help(msg.prefix))
            reply = "commands: " + ", ".join(s)
            if is_mod:
                if private:
                    reply += (
                        "\ncommand names: "
                        + ", ".join(names)
                        + "\n"
                        + "hidden commands: "
                        + ", ".join(hidden_commands)
                    )
                elif msg.is_discord:
                    reply += " (some commands are only available in private messages)"
            actions = [Action(kind=ActionKind.REPLY, text=reply)]
            return actions, False
        s = []
        for c in get_commands(channel_id, msg.prefix):
            if c.mod_only() and not is_mod:
                continue
            if c.private_mod_only() and not (is_mod and private):
                continue
            if msg.is_discord and not c.for_discord():
                continue
            if (not msg.is_discord) and not c.for_twitch():
                continue
            hf = c.help_full(msg.prefix)
            h = c.help(msg.prefix)
            if (
                h == text
                or h == msg.prefix + text
                or h.startswith(text + " ")
                or h.startswith(msg.prefix + text + " ")
            ):
                s.append(hf)
        if not s:
            return [], False
        return [Action(kind=ActionKind.REPLY, text="\n".join(s))], False

    def help(self, prefix: str):
        return f"{prefix}help [<command name>]"

    def mod_only(self):
        return False

    def hidden_help(self):
        return False


class InvalidateCache(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["invalidate_cache"])
        if not text:
            return [], True
        commands_cache.pop(f"commands_{msg.channel_id}_{msg.prefix}", None)
        return [Action(kind=ActionKind.REPLY, text="cleared commands cache")], False
