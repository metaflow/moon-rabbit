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

import discord  # type: ignore
from data import *
import json
import logging
import re
from typing import Callable, Dict, List
from storage import cursor, db
import traceback


class Command(Protocol):
    async def run(self, prefix: str, text: str, discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        return [], True

    def help(self, prefix: str):
        return ''

    def help_full(self, prefix: str):
        return self.help(prefix)

    def mod_only(self):
        return True

    def for_discord(self):
        return True

    def for_twitch(self):
        return True


commands_cache: Dict[str, List[Command]] = {}


def get_commands(channel_id: int, prefix: str) -> List[Command]:
    key = f'commands_{channel_id}_{prefix}'
    if not key in commands_cache:
        z: List[Command] = [HelpCmd(),
                            ListSearch(), ListAddBulk(), ListNames(), ListRemove(), ListAddItem(), ListDownload(),
                            Eval(), Debug(), 
                            SetCommand(), SetPrefix(),
                            TagList(), TagAdd(), TagDelete(),
                            TextAddTags(), TextAdd(), TextAddBulk(), TextDownload(), TextSearch(), TextRemove()]
        z.extend([PersistentCommand(x, prefix)
                 for x in db().get_commands(channel_id, prefix)])
        commands_cache[key] = z
    return commands_cache[key]


class PersistentCommand(Command):
    regex: re.Pattern
    data: CommandData

    def __init__(self, data, prefix):
        self.data = data
        p = data.pattern.replace('!prefix', re.escape(prefix))
        logging.info(f'regex {p}')
        self.regex = re.compile(p, re.IGNORECASE)

    def for_discord(self):
        return self.data.discord

    def for_twitch(self):
        return self.data.twitch

    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not re.search(self.regex, text):
            return [], True
        variables = get_variables()
        if self.data.mod and not variables['is_mod']:
            logging.info('non mod called persistent')
            return [], True
        log: InvocationLog = variables['_log']
        log.info(
            f'matched command {json.dumps(dataclasses.asdict(self.data), ensure_ascii=False)}')
        actions: List[Action] = []
        try:
            for e in self.data.actions:
                variables['_render_depth'] = 0
                a = Action(
                    kind=e.kind,
                    text=render(e.text, variables))
                if a.text:
                    actions.append(a)
            return actions, True
        except Exception as e:
            log.error(f"failed to render '{self.data.name}': {str(e)}")
            log.error(traceback.format_exc())
            return [], True

    def help(self, prefix: str):
        if self.data.help:
            return self.data.help.replace('!prefix', prefix)
        return prefix + self.data.name

    def help_full(self, prefix: str):
        if self.data.help_full:
            return self.data.help_full.replace('!prefix', prefix)
        return self.help(prefix)

    def mod_only(self):
        return self.data.mod


class ListAddBulk(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-add-bulk "):
            return [], True
        v = get_variables()
        log = v['_log']
        parts = text.split(' ', 2)
        list_name = ''
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        list_name = parts[1]
        content = ''
        if len(parts) == 3:
            content = parts[2]
        msg: discord.Message = v['_discord_message']
        log.info('looking for attachments')
        for att in msg.attachments:
            log.info(
                f'attachment {att.filename} {att.size} {att.content_type}')
            content += '\n' + (await att.read()).decode('utf-8')
        channel_id = v['channel_id']
        values = [x.strip() for x in content.split('\n')]
        added = 0
        total = 0
        for s in values:
            if s:
                total += 1
                _, b = db().add_list_item(channel_id, list_name, s)
                if b:
                    added += 1
        return [Action(kind=ActionKind.REPLY, text=f"Added {added} items out of {total}")], False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f'{prefix}list-add-bulk'

    def help_full(self, prefix: str):
        return f'{prefix}list-add-bulk <list name> + <attach a file>'


class ListDownload(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-download "):
            return [], True
        v = get_variables()
        parts = text.split(' ', 2)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        list_name = parts[1]
        msg: discord.Message = v['_discord_message']
        channel_id = v['channel_id']
        id_text = db().get_all_list_items(channel_id, list_name)
        att = '\n'.join([f'{x[0]}\t{x[1]}' for x in id_text])
        return [Action(kind=ActionKind.REPLY,
        text=list_name,
        attachment=att,
        attachment_name=f'{list_name}.csv')], False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f'{prefix}list-download'

    def help_full(self, prefix: str):
        return f'{prefix}list-download <list name>'


class ListNames(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if text != prefix + "lists":
            return [], True
        v = get_variables()
        return [Action(kind=ActionKind.REPLY, text='\n'.join(db().get_list_names(v['channel_id'])))], False

    def help(self, prefix: str):
        return f'{prefix}lists'


def escape_like(t):
    return t.replace('=', '==').replace('%', '=%').replace('_', '=_')


# TODO: move to DB
def list_search(channel_id: int, txt: str, list_name: str) -> List[Tuple[int, str, str]]:
    matched_rows: List[Tuple[int, str, str]] = []
    with cursor() as cur:
        q = '%' + escape_like(txt) + '%'
        if list_name:
            cur.execute("select id, list_name, text from lists where (channel_id = %s) AND (list_name = %s) AND (text LIKE %s)",
                        (channel_id, list_name, q))
        else:
            cur.execute("select id, list_name, text from lists where (channel_id = %s) AND (text LIKE %s)",
                        (channel_id, q))
        for row in cur.fetchall():
            matched_rows.append((row[0], row[1], row[2]))
    return matched_rows


class ListRemove(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-rm"):
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
        parts = text.split(' ', 3)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        txt = parts[1]
        if txt.isnumeric():
            t = db().delete_list_item(channel_id, int(txt))
            if not t:
                return [Action(kind=ActionKind.REPLY, text=f'No item #{txt} is found')], False
            text, list_name = t
            return [Action(kind=ActionKind.REPLY, text=f'Deleted list {list_name} item #{txt} "{text}"')], False
        list_name = ''
        if len(parts) >= 3:
            list_name = parts[2]
        items = list_search(channel_id, txt, list_name)
        if not items:
            if txt == 'all' and list_name:
                n = db().delete_list(channel_id, list_name)
                return [Action(kind=ActionKind.REPLY, text=f'Deleted all {n} items in list "{list_name}"')], False
            return [Action(kind=ActionKind.REPLY, text=f'No matches found')], False
        if len(items) == 1:
            i, list_name, text = items[0]
            db().delete_list_item(channel_id, i)
            return [Action(kind=ActionKind.REPLY, text=f'Deleted list {list_name} item #{i} "{text}"')], False
        rr = []
        for ii in items:
            rr.append(f'#{ii[0]} {ii[1]} "{ii[2]}"')
        s = '\n'.join(rr)
        return [Action(kind=ActionKind.REPLY, text=f'Multiple items match query: \n{s}')], False

    def help_full(self, prefix: str):
        return f'{prefix}list-rm <number> OR {prefix}list-rm <substring> [<list name>] OR {prefix}list-rm all <list name>'

    def help(self, prefix: str):
        return f"{prefix}list-rm"


class ListSearch(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-search"):
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
        parts = text.split(' ', 3)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        txt = parts[1]
        list_name = ''
        if len(parts) >= 3:
            list_name = parts[2]
        items = list_search(channel_id, txt, list_name)
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        rr = []
        for ii in items:
            rr.append(f'#{ii[0]} {ii[1]} "{ii[2]}"')
        return [Action(kind=ActionKind.REPLY, text='\n'.join(rr))], False

    def help(self, prefix: str):
        return f'{prefix}list-search'

    def help_full(self, prefix: str):
        return f'{prefix}list-search <substring> [<list name>]'


class Eval(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "eval"):
            return [], True
        v = get_variables()
        parts = text.split(' ', 1)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        v['_log'].info(f'eval "{parts[1]}"')
        v['_render_depth'] = 0
        s = render(parts[1], v)
        if not s:
            s = "<empty>"
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}eval'

    def help_full(self, prefix: str):
        return f'{prefix}eval <expression> (see "set")'


class SetCommand(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "set"):
            return [], True
        v = get_variables()
        log = v['_log']
        channel_id = v['channel_id']
        commands_cache.pop(f'commands_{channel_id}_{prefix}', None)
        parts = text.split(' ', 2)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        name = parts[1]
        if len(parts) == 2:
            cursor().execute(
                'DELETE FROM commands WHERE channel_id = %s AND name = %s', (channel_id, name))
            return [Action(kind=ActionKind.REPLY, text=f"Deleted command '{name}'")], False
        command_text = parts[2]
        cmd = CommandData(pattern="!prefix" + re.escape(name) + "\\b")
        try:
            cmd = dictToCommandData(json.loads(command_text))
        except Exception as e:
            log.info('failed to parse command as JSON, assuming literal text')
            cmd.actions.append(
                Action(text=command_text, kind=ActionKind.NEW_MESSAGE))
        cmd.name = name
        log.info(f'parsed command {cmd}')
        id = db().set_command(cursor(), channel_id, v['author_name'], cmd)
        log.info(
            f"channel={channel_id} author={v['author_name']} added new command '{name}' #{id}")
        return [Action(kind=ActionKind.REPLY, text=f"Added new command '{name}' #{id}")], False

    def help(self, prefix: str):
        return f'{prefix}set'

    def help_full(self, prefix: str):
        return f'''{prefix}set [<template>|<JSON>]
Missing value deletes command.
See https://jinja.palletsprojects.com/en/3.0.x/templates/ for the general template syntax.

Variables available:
- "author" - author of original message
- "bot" - bot mention
- "direct_mention" (if there is a direct action)
- "is_mod" - author is moderator
- "media" - "discord" or "twitch"
- "mention" - direct_mention if set, otherwise random_mention
- "prefix" - command prefix ({prefix})
- "random_mention" (always random)
- "text" - full message text

Additional functions:
- list(<list name>) - random item from the list, item is treated as template too;
- randint(from = 0, to = 100) - random integer in [from, to] range;
- timestamp() - current timestamp in seconds, integer
- dt(<text for discord>, <text for twitch>) - different fixed text for discord or twitch;
- get(<name>[, <category = ''>, <default value = ''>]) - get variable value;
- set(<name>[, <value = ''>, <category = ''>, <expires in seconds = 32400 (9h)>]) - set variable that will expire after some time. Empty value deletes the variable;
- category_size(<category>) - number of set variables in category;
- delete_category(<category>) - delete all variables in category;
- inflect(<category>, <list of iflections>, <semantic filters>, <agree with number>) - inflect russian sentence and agree with number (possible options: им, nomn, рд, gent, дт, datv, вн, accs, тв, ablt, пр, loct, ед, sing, мн, plur, СУЩ, NOUN, ПРИЛ, ADJF). E.g. inflect('лучший приятель', 'тв', ['мр;ПРИЛ', 'мр;СУЩ'], 4).
Semantic filter has form "a,b;c;d,e" - it will filter words that has tags (a or b) AND (c) AND (d OR e). See https://pymorphy2.readthedocs.io/en/latest/user/grammemes.html for the full list of grammemes.

JSON format is ever changing, use "{prefix}debug <command>" to get a command representation.
It is the only way to customize a command to match a different regex, allow only for mods, hide it.
'''


class SetPrefix(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "prefix-set"):
            return [], True
        v = get_variables()
        log = v['_log']
        channel_id = v['channel_id']
        if v['bot'] not in str(v['direct_mention']):
            log.info('this bot is not mentioned directly')
            return [], True
        parts = text.split(' ')
        if len(parts) < 2:
            return [], True
        new_prefix = parts[1]
        if is_discord:
            db().set_discord_prefix(channel_id, new_prefix)
        else:
            db().set_twitch_prefix(channel_id, new_prefix)
        return [Action(kind=ActionKind.REPLY, text=f'set new prefix for {v["media"]} to "{new_prefix}"')], False

    def help(self, prefix: str):
        return f'{prefix}prefix-set'

    def help_full(self, prefix: str):
        return f'{prefix}prefix-set <new prefix> <bot>'


class ListAddItem(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-add"):
            return [], True
        v = get_variables()
        parts = text.split(' ', 2)
        if len(parts) < 3:
            return [], False
        _, list_name, value = parts
        channel_id = v['channel_id']
        id, b = db().add_list_item(channel_id, list_name, value)
        if b:
            return [Action(kind=ActionKind.REPLY, text=f"Added new list '{list_name}' item '{value}' #{id}")], False
        return [Action(kind=ActionKind.REPLY, text=f'List "{list_name}" item "{value}" #{id} already exists')], False

    def help(self, prefix: str):
        return f'{prefix}list-add'

    def help_full(self, prefix: str):
        return f'{prefix}list-add <list name> <value>'

class TagAdd(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "tag-add"):
            return [], True
        v = get_variables()
        parts = text.split(' ', 1)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        _, value = parts
        channel_id = v['channel_id']
        db().add_tag(channel_id, value)
        return [Action(kind=ActionKind.REPLY, text='OK')], False

    def help(self, prefix: str):
        return f'{prefix}tag-add'

    def help_full(self, prefix: str):
        return f'{prefix}tag-add <value>'

class TagDelete(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "tag-rm"):
            return [], True
        v = get_variables()
        parts = text.split(' ', 1)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        _, value = parts
        value = value.strip()
        channel_id = v['channel_id']
        deleted = db().delete_tag(channel_id, value)
        return [Action(kind=ActionKind.REPLY, text=('OK' if deleted == 1 else 'no such tag'))], False

    def help(self, prefix: str):
        return f'{prefix}tag-rm'

    def help_full(self, prefix: str):
        return f'{prefix}tag-rm <value>'

class TagList(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "tags"):
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
        tags = db().get_tags(channel_id)
        s = 'no tags'
        if tags:
            s = ', '.join(tags.keys())
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}tags'

class TextAdd(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "text-add "):
            return [], True
        v = get_variables()
        parts = text.split(' ', 1)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        _, value = parts
        value = value.strip()
        if not value:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        channel_id = v['channel_id']
        _, added = db().add_text(channel_id, value)
        return [Action(kind=ActionKind.REPLY, text='added new text' if added else 'already exist')], False

    def help(self, prefix: str):
        return f'{prefix}text-add'

    def help_full(self, prefix: str):
        return f'{prefix}text-add <value>'

class TextAddTags(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "tag-text"):
            return [], True
        v = get_variables()
        parts = text.split(' ')
        if len(parts) < 3:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        value = int(parts[1])
        channel_id = v['channel_id']
        if not db().get_text(channel_id, value):
            return [Action(kind=ActionKind.REPLY, text=f'No text with id {value} found')], False
        set_tags = parts[2:]
        for t in set_tags:
            db().add_tag(channel_id, t.strip())
        tags = db().get_tags(channel_id)
        channel_id = v['channel_id']
        db().delete_text_tags(value)
        for t in set_tags:
            t = t.strip()
            db().add_text_tag(channel_id, value, tags[t])
        return [Action(kind=ActionKind.REPLY, text='OK')], False

    def help(self, prefix: str):
        return f'{prefix}tag-text'

    def help_full(self, prefix: str):
        return f'{prefix}tag-text <id> <tag> [<tag> [...]]\nexisting tags will be removed'


class TextAddBulk(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if text.strip() != prefix + "text-add-bulk":
            return [], True
        v = get_variables()
        log = v['_log']
        content = ''
        msg: discord.Message = v['_discord_message']
        log.info('looking for attachments')
        for att in msg.attachments:
            log.info(
                f'attachment {att.filename} {att.size} {att.content_type}')
            content += '\n' + (await att.read()).decode('utf-8')
        channel_id = v['channel_id']
        values = [x.strip() for x in content.split('\n')]
        lines = [x.split('\t') for x in values if x]
        all_tags = set()
        for s in lines:
            if len(s) < 2:
                continue
            for t in s[1].split(' '):
                t = t.strip()
                all_tags.add(t)
        for t in all_tags:
            db().add_tag(channel_id, t)
        tags = db().get_tags(channel_id)
        total = 0
        total_added = 0
        for s in lines:
            txt = s[0].strip()
            if not txt:
                continue
            total += 1
            text_id, added = db().add_text(channel_id, txt)
            if added:
                total_added += 1
            else:
                db().delete_text_tags(text_id)
            if len(s) < 2:
                continue
            for t in s[1].split(' '): 
                db().add_text_tag(channel_id, text_id, tags[t.strip()])
        return [Action(kind=ActionKind.REPLY, text=f"Added {total_added} texts from non-empty {total} lines with tags {all_tags}")], False

    def help(self, prefix: str):
        return f'{prefix}text-add-bulk'

class TextDownload(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if text.strip() != prefix + "text-download":
            return [], True
        v = get_variables()
        msg: discord.Message = v['_discord_message']
        channel_id = v['channel_id']
        items = db().all_texts(channel_id)
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        rr = []
        for ii in items:
            rr.append(f'{ii[1]}\t{" ".join(ii[2])}')
        att = '\n'.join(rr)
        return [Action(kind=ActionKind.REPLY,
        text='texts',
        attachment=att,
        attachment_name=f'texts.csv')], False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f'{prefix}text-download'

class TextSearch(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "search"):
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
        parts = text.split(' ', 3)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        # txt = parts[1]
        items = db().text_search(channel_id, parts[1])
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        rr = []
        for ii in items:
            rr.append(f'{ii[0]} "{ii[1]}" {" ".join(ii[2])}')
        return [Action(kind=ActionKind.REPLY, text='\n'.join(rr))], False

    def help(self, prefix: str):
        return f'{prefix}search'

    def help_full(self, prefix: str):
        return f'{prefix}search <substring>'

class TextRemove(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "rm"):
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
        parts = text.split(' ', 1)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        txt = parts[1]
        if txt.isnumeric():
            t = db().delete_text(channel_id, int(txt))
            if not t:
                return [Action(kind=ActionKind.REPLY, text=f'No text with id {txt} found')], False
            return [Action(kind=ActionKind.REPLY, text=f'Deleted text #{txt}')], False
        items = db().text_search(channel_id, txt)
        if not items:
            return [Action(kind=ActionKind.REPLY, text=f'No matches found')], False
        if len(items) == 1:
            id, text, _ = items[0]
            db().delete_list_item(channel_id, id)
            return [Action(kind=ActionKind.REPLY, text=f'Deleted text "{text}"')], False
        rr = []
        for ii in items:
            rr.append(f'{ii[1]} "{", ".join(ii[2])}"')
        s = '\n'.join(rr)
        return [Action(kind=ActionKind.REPLY, text=f'Multiple matches: \n{s}')], False

class Debug(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "debug"):
            return [], True
        results: List[Action] = []
        v = get_variables()
        channel_id = v['channel_id']
        parts = text.split(' ', 1)
        if len(parts) < 2:
            for e in db().get_logs(channel_id):
                s = '\n'.join([discord.utils.escape_mentions(x[1])
                               for x in e.messages]) + '\n-----------------------------\n'
                results.append(Action(kind=ActionKind.PRIVATE_MESSAGE, text=s))
            return results, False
        txt = parts[1]
        commands = db().get_commands(channel_id, prefix)
        for cmd in commands:
            if cmd.name == txt:
                results.append(Action(ActionKind.PRIVATE_MESSAGE, f'{prefix}set {cmd.name} ' + discord.utils.escape_markdown(discord.utils.escape_mentions(
                    json.dumps(dataclasses.asdict(cmd), ensure_ascii=False)))))
        return results, False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f'{prefix}debug'

    def help_full(self, prefix: str):
        return f'"{prefix}debug" OR "{prefix}debug <command name>"'


class HelpCmd(Command):
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "commands") and not text.startswith(prefix + "help"):
            return [], True
        v = get_variables()
        is_mod = v['is_mod']
        channel_id = v['channel_id']
        parts = text.split(' ', 1)
        if len(parts) < 2:
            s = []
            for c in get_commands(channel_id, prefix):
                if is_discord and not c.for_discord():
                    continue
                if (not is_discord) and not c.for_twitch():
                    continue
                if c.mod_only() and not is_mod:
                    continue
                if isinstance(c, PersistentCommand):
                    if c.data.hidden:
                        continue
                s.append(c.help(prefix))
            return [Action(kind=ActionKind.REPLY, text='commands: ' + ', '.join(s))], False
        sub = parts[1].strip()
        if not sub:
            return [], False
        s = []
        for c in get_commands(channel_id, prefix):
            if c.mod_only() and not is_mod:
                continue
            if is_discord and not c.for_discord():
                continue
            if (not is_discord) and not c.for_twitch():
                continue
            hf = c.help_full(prefix)
            h = c.help(prefix)
            logging.info(f'sub "{sub}", h "{hf}"')
            if h == sub or h == prefix + sub or h.startswith(sub + ' ') or h.startswith(prefix + sub + ' '):
                s.append(hf)
        if not s:
            return [], False
        return [Action(kind=ActionKind.REPLY, text='\n'.join(s))], False

    def help(self, prefix: str):
        return f'{prefix}help [<command name>]'

    def mod_only(self):
        return False
