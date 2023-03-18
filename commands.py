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
from typing import Dict, List, Set
from storage import cursor, db
import traceback
import query
import csv
import words
import io
import ttldict2

# id: str -> Message 
messages = ttldict2.TTLDict(ttl_seconds=600.0)

def str_to_tags(s: str) -> Tuple[Dict[str, Optional[str]], bool]:
    z: Dict[str, Optional[str]] = {}
    if s.strip() == '':
        return (z, True)
    for line in s.split('\n'):
        x = line.strip()
        if not x:
            continue
        parts = x.split('=', 1)
        value: Optional[str] = None
        name = parts[0].strip()
        if not query.good_tag_name(name):
            logging.warn(f'tag "{name}" is invalid')
            return (z, False) 
        if len(parts) > 1:
            value = parts[1].strip()
        z[name] = value
    return (z, True)


def tag_values_to_str(tags: Dict[str, Optional[str]]) -> str:
    z = []
    for n, v in tags.items():
        if not v:
            z.append(n)
            continue
        z.append(f'{n}={v}')
    return '\n'.join(z)


async def process_message(msg: Message) -> List[Action]:
    logging.debug(f'process message "{msg.txt}" type {msg.event}')
    messages[msg.id] = msg
    actions: List[Action] = []
    try:
        cmds = get_commands(msg.channel_id, msg.prefix)
        for cmd in cmds:
            if cmd.mod_only() and not msg.is_mod:
                continue
            if cmd.private_mod_only() and not (msg.is_mod and msg.private):
                continue
            if msg.is_discord and not cmd.for_discord():
                continue
            if (not msg.is_discord) and not cmd.for_twitch():
                continue
            a, next = await cmd.run(msg)
            actions.extend(a)
            if not next:
                break
        actions.extend(msg.additionalActions)
        log_actions = [a for a in actions if a.attachment == '']
        msg.log.debug(f'actions (except download) {log_actions}')
    except Exception as e:
        actions.append(Action(kind=ActionKind.REPLY, text='error ocurred'))
        msg.log.error(f'{e}\n{traceback.format_exc()}')
    return actions


def command_prefix(txt: str, prefix: str, s: List[str]) -> str:
    for x in s:
        if txt.startswith(prefix + x):
            # TODO: don't append an empty string and return additional bool instead.
            return txt[len(prefix + x):] + ' '
        if txt.startswith(prefix + ' ' + x):
            return txt[len(prefix + ' ' + x):] + ' '
    return ''


class Command(Protocol):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        return [], True

    def help(self, prefix: str):
        return ''

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


def get_commands(channel_id: int, prefix: str) -> List[Command]:
    key = f'commands_{channel_id}_{prefix}'
    r = commands_cache.get(key)
    if (not r):
        commands: List[Command] = [HelpCommand(),
                            Eval(), Debug(), Multiline(),
                            SetCommand(), SetPrefix(),
                            TagList(), TagDelete(),
                            TextSet(), TextUpload(), TextDownload(),
                            TextSearch(), TextRemove(), TextDescribe(), TextNew(), TextSetNew(),
                            InvalidateCache()
                            ]
        commands.extend([PersistentCommand(x, prefix)
                 for x in db().get_commands(channel_id, prefix)])
        commands_cache[key] = commands
        r = commands
    return r


class PersistentCommand(Command):
    regex: re.Pattern
    data: CommandData

    def __init__(self, data, prefix):
        self.data = data
        p = data.pattern.replace('!prefix', re.escape(prefix) + ' ?')
        logging.info(f'regex {p}')
        self.regex = re.compile(p, re.IGNORECASE)

    def for_discord(self):
        return self.data.discord

    def for_twitch(self):
        return self.data.twitch

    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        if msg.event != self.data.event_type or not re.search(self.regex, msg.txt):
            return [], True
        variables = msg.get_variables()
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

    def hidden_help(self):
        return self.data.hidden


class Eval(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['eval'])
        if not text:
            return [], True
        text = text.strip()
        v = msg.get_variables()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        # v['_log'].info(f'eval "{text}"')
        v['_render_depth'] = 0
        s = render(text, v)
        if not s:
            s = "<empty>"
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}eval'

    def help_full(self, prefix: str):
        return f'{prefix}eval <expression> (see "set")'


class SetCommand(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['command'])
        if not text:
            return [], True
        text = text.strip()
        log = msg.log
        channel_id = msg.channel_id
        commands_cache.pop(f'commands_{channel_id}_{msg.prefix}', None)
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        parts = text.split(' ', 1)
        name = parts[0]
        if len(parts) == 1:
            cursor().execute(
                'DELETE FROM commands WHERE channel_id = %s AND name = %s', (channel_id, name))
            return [Action(kind=ActionKind.REPLY, text=f"Deleted command '{name}'")], False
        command_text = parts[1]
        cmd = CommandData(pattern="!prefix" + re.escape(name) + "\\b")
        try:
            cmd = dictToCommandData(json.loads(command_text))
        except Exception as e:
            log.info('failed to parse command as JSON, assuming literal text')
            cmd.actions.append(
                Action(text=command_text, kind=ActionKind.NEW_MESSAGE))
        cmd.name = name
        log.info(f'parsed command {cmd}')
        v = msg.get_variables()
        id = db().set_command(cursor(), channel_id, v['author_name'], cmd)
        log.info(
            f"channel={channel_id} author={v['author_name']} added new command '{name}' #{id}")
        return [Action(kind=ActionKind.REPLY, text=f"Added new command '{name}' #{id}")], False

    def help(self, prefix: str):
        return f'{prefix}command'

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
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['prefix-set'])
        if not text:
            return [], True
        text = text.strip()
        v = msg.get_variables()
        log = msg.log
        channel_id = msg.channel_id
        if v['bot'] not in str(v['direct_mention']):
            log.info('this bot is not mentioned directly')
            return [], True
        if not text:
            return [], True
        if msg.is_discord:
            db().set_discord_prefix(channel_id, text)
        else:
            db().set_twitch_prefix(channel_id, text)
        return [Action(kind=ActionKind.REPLY, text=f'set new prefix for {v["media"]} to "{text}"')], False

    def help(self, prefix: str):
        return f'{prefix}prefix-set'

    def help_full(self, prefix: str):
        return f'{prefix}prefix-set <new prefix> <bot>'


class TagDelete(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['tag-rm'])
        if not text:
            return [], True
        text = text.strip()
        v = msg.get_variables()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        channel_id = msg.channel_id
        tag_id: int
        if text.isdigit():
            tag_id = str_to_int(text)
        else:
            tag_id = db().tag_by_value(channel_id)[text]
        deleted = db().delete_tag(channel_id, tag_id)
        return [Action(kind=ActionKind.REPLY, text=(f'removed tag {text}' if deleted == 1 else 'no such tag'))], False

    def help(self, prefix: str):
        return f'{prefix}tag-rm'

    def help_full(self, prefix: str):
        return f'{prefix}tag-rm <tag id>'


class TagList(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['tags'])
        if not text:
            return [], True
        channel_id = msg.channel_id
        tags = db().tag_by_value(channel_id)
        s = 'no tags'
        if tags:
            t: List[str] = []
            for tag in sorted(tags):
                t.append(f'{tags[tag]} {tag}')
            s = '\n'.join(t)
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}tags'


class TextSet(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['add'])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        row = [r for r in csv.reader(io.StringIO(text), delimiter=';')][0]
        channel_id = msg.channel_id
        prev: Optional[str] = None
        if row:
            if len(row) >= 2 and row[1].isdigit():
                prev = text_to_row(channel_id, str_to_int(row[1]))
            else:
                text_id = db().find_text(channel_id, row[0].strip())
                if text_id:
                    prev = text_to_row(channel_id, text_id)
        updated, _, text_id = import_text_row(
            channel_id, row, db().tag_by_value(channel_id))
        if text_id:
            s = f'{"Updated" if updated else "Added"} text (text;id;tags):\n{text_to_row(channel_id, text_id)}'
            if prev:
                s += f'\nPrevious value:\n{prev}'
        else:
            raise Exception("failed to insert new text")
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}add'

    def help_full(self, prefix: str):
        return f'{prefix}add "some text";id;"tag1<newline>tag2<newline>tag3..."'


def text_to_row(channel_id: int, text_id: int) -> Optional[str]:
    text_value = db().get_text(channel_id, text_id)
    if not text_value:
        return None
    tags = db().get_text_tag_values(channel_id, text_id)
    tag_by_id = db().tag_by_id(channel_id)
    name_value: Dict[str, Optional[str]] = {}
    for id, value in tags.items():
        name = tag_by_id[id]
        name_value[name] = value
    sbuf = io.StringIO()
    csv.writer(sbuf, delimiter=';').writerow(
        [text_value, text_id, tag_values_to_str(name_value)])
    return sbuf.getvalue().strip()


class TextDescribe(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['describe'])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        channel_id = msg.channel_id
        if not text.isdigit():
            return [Action(kind=ActionKind.REPLY, text=f'Please provide a text id')], False
        s = text_to_row(channel_id, str_to_int(text))
        if not s:
            return [Action(kind=ActionKind.REPLY, text=f'Text #{text} is not found')], False
        return [Action(kind=ActionKind.REPLY, text=f'{msg.prefix}add {s}')], False

    def help(self, prefix: str):
        return f'{prefix}describe'

    def help_full(self, prefix: str):
        return f'{prefix}describe <text id>\nPrint full info about text.'

def morph_text(text_value: str) -> Dict[str, str]:
    name_value: Dict[str, str] = {}
    parts = re.split(r'(\s+)', text_value.strip())
    ww = [parts[i] for i in range(0, len(parts), 2)]
    parses = [words.morph.parse(w) for w in ww]
    for i in range(len(parses)):
        parses[i] = [p for p in parses[i]
                        if 'nomn' in list(p.tag.grammemes)]
    logging.info(f'morph parses for parts {parses}')
    if any(parses):
        for inf in words.case_tags:
            ss = set(words.morph.cyr2lat(inf).split(','))
            t = ''
            for i in range(len(parts)):
                j = i // 2
                if i % 2 != 0 or not parses[j]:
                    t += parts[i]
                    continue
                x = parses[j][0].inflect(ss)
                if not x:
                    t += parts[i]
                else:
                    t += x.word
            name_value[inf] = t
    return name_value

class TextNew(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['new'])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        tt = text.strip().split(';', 1)
        text_value = tt[0].strip()
        name_value: Dict[str, Optional[str]] = morph_text(text_value)
        if len(tt) >= 2:
            for s in tt[1].split(' '):
                if query.good_tag_name(s):
                    name_value[s] = None
        str_buf = io.StringIO()
        csv.writer(str_buf, delimiter=';').writerow(
            [text_value, "", tag_values_to_str(name_value)])
        return [Action(kind=ActionKind.REPLY, text=f'{msg.prefix}add {str_buf.getvalue()}')], False

    def help(self, prefix: str):
        return f'{prefix}new'

    def help_full(self, prefix: str):
        return f'{prefix}new <text>;tag1 tag2 tag3\nTries to morphologically analyze text and prints new "add" command.'

class TextSetNew(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['setnew'])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        tt = text.strip().split(';', 1)
        text_value = tt[0].strip()
        name_value: Dict[str, Optional[str]] = morph_text(text_value)
        if len(tt) >= 2:
            for s in tt[1].split(' '):
                if query.good_tag_name(s):
                    name_value[s] = None
        channel_id = msg.channel_id
        updated, _, text_id = import_text_row(
            channel_id, [text_value, "", tag_values_to_str(name_value)], db().tag_by_value(channel_id))
        if text_id:
            s = f'{"Updated" if updated else "Added"} text (text;id;tags):\n{text_to_row(channel_id, text_id)}'
        else:
            raise Exception("failed to insert new text")
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}setnew'

    def help_full(self, prefix: str):
        return f'{prefix}setnew <text>;tag1 tag2 tag3\nSame as running command returned by !new.'


def import_text_row(channel_id: int, row: List[str], tag_by_name: Dict[str, int]) -> Tuple[int, int, int]:
    updated = 0
    added = 0
    if not row:
        return (updated, added, 0)
    txt = row[0].strip()
    if not txt:
        return (updated, added, 0)
    text_id = 0
    if len(row) >= 2:
        s = row[1].strip()
        if s:
            text_id = str_to_int(s)
            if not text_id:
                logging.warn('failed to convert "{s}" to number')
                return (0, 0, 0)
    tags_info: Optional[Dict[str, Optional[str]]] = None
    if len(row) >= 3:
        tags_info, ok = str_to_tags(row[2])
        if not ok:
            logging.warn(f'failed to get tags from "{row[2]}"')
            return (0, 0, 0)
    if text_id:
        if db().set_text(channel_id, txt, text_id):
            updated += 1
        else:
            return (0, 0, 0)
    else:
        existing = db().find_text(channel_id, txt)
        if existing:
            text_id = existing
            updated += 1
        else:
            text_id = db().add_text(channel_id, txt)
            added += 1
    if tags_info:
        tag_values: Dict[int, Optional[str]] = {}
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            logging.debug(
                f'#{text_id} {txt} tags dict {tags_info}')
        for name, value in tags_info.items():
            if name not in tag_by_name:
                db().add_tag(channel_id, name)
                tag_by_name = db().tag_by_value(channel_id)
            tag_values[tag_by_name[name]] = value
        db().set_text_tags(channel_id, text_id, tag_values)
    return (updated, added, text_id)


class TextUpload(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        if msg.txt.strip() != msg.prefix + "upload":
            return [], True
        v = msg.get_variables()
        log = msg.log
        content = ''
        discord_msg: discord.Message = v['_discord_message']
        log.info('looking for attachments')
        for att in discord_msg.attachments:
            log.info(
                f'attachment {att.filename} {att.size} {att.content_type}')
            content = (await att.read()).decode('utf-8')
            break
        channel_id = msg.channel_id
        all_tags: Set[str] = set()
        for row in csv.reader(io.StringIO(content)):
            if len(row) < 3:
                continue
            tags, ok = str_to_tags(row[2])
            if ok:
                all_tags.update(tags.keys())
        logging.info(f'all tags in file: {all_tags}')
        for t in all_tags:
            db().add_tag(channel_id, t)
        tag_by_name = db().tag_by_value(channel_id)
        total = 0
        total_added = 0
        total_updated = 0
        bad_rows = []
        i = 0
        for row in csv.reader(io.StringIO(content)):
            i += 1
            if not row:
                continue
            updated, added, text_id = import_text_row(channel_id, row, tag_by_name)
            if not text_id:
                bad_rows.append(i)
            total_updated += updated
            total_added += added
            total += 1
        s = f"Added {total_added} and updated {total_updated} texts from non-empty {total} row with tags {all_tags}."
        if bad_rows:
            s += f'\nBad rows numbers: {",".join([str(i) for i in bad_rows])}'
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}upload'

    def for_twitch(self):
        return False


class TextDownload(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['download'])
        if not text:
            return [], True
        text = text.strip()
        channel_id = msg.channel_id
        items: List[Tuple[int, str, Set[int]]] = []
        if not text:
            items = db().all_texts(channel_id)
        else:
            # TODO: also use newline and regex
            query_parts = text.split(';', 1)
            substring = query_parts[0]
            tag_query = ''
            if len(query_parts) > 1:
                tag_query = query_parts[1]
            items = db().text_search(channel_id, substring, tag_query)
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        tag_by_id = db().tag_by_id(channel_id)
        output = io.StringIO()
        writer = csv.writer(output)
        for ii in items:
            text_id = ii[0]
            txt = ii[1]
            tags = db().get_text_tag_values(channel_id, text_id)
            name_value: Dict[str, Optional[str]] = {}
            for id, value in tags.items():
                name = tag_by_id[id]
                # TODO: remove when morph tag is migrated to tag values
                if name == 'morph':
                    filter = []
                    if tags:
                        for tag_id in tags.keys():
                            name = tag_by_id[tag_id]
                            if name in words.morph_tags:
                                filter.append(words.morph_tags[name])
                    for inf in words.case_tags:
                        name_value[inf] = words.inflect_word(txt, inf, filter)
                    continue
                name_value[name] = value
            writer.writerow([txt, text_id, tag_values_to_str(name_value)])
        return [Action(kind=ActionKind.REPLY,
                       text='texts',
                       attachment=output.getvalue(),
                       attachment_name=f'texts.csv')], False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f'{prefix}download'

    def help_full(self, prefix: str):
        return f'{prefix}download [<substring>[;<tag query>]]'


class TextSearch(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['search'])
        if not text:
            return [], True
        text = text.strip()
        v = msg.get_variables()
        channel_id = msg.channel_id
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        query_parts = text.split(';', 1)
        substring = query_parts[0]
        tag_query = ''
        if len(query_parts) > 1:
            tag_query = query_parts[1]
        items = db().text_search(channel_id, substring, tag_query)
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        tag_by_id = db().tag_by_id(channel_id)
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        sbuf = io.StringIO()
        for ii in items:
            tag_names = [tag_by_id[x] for x in ii[2]]
            tag_names = [x for x in tag_names if not x in words.case_tags]
            csv.writer(sbuf, delimiter=';').writerow(
                [ii[1], ii[0], " ".join(tag_names)])
        return [Action(kind=ActionKind.REPLY, text=sbuf.getvalue())], False

    def help(self, prefix: str):
        return f'{prefix}search'

    def help_full(self, prefix: str):
        return f'{prefix}search <substring><tag query>]'


class TextRemove(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['rm'])
        if not text:
            return [], True
        text = text.strip()
        v = msg.get_variables()
        channel_id = msg.channel_id
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        text_id: Optional[int]
        if text.isdigit():
            text_id = str_to_int(text)
        else:
            query_parts = text.split(';', 1)
            substring = query_parts[0]
            tag_query = ''
            if len(query_parts) > 1:
                tag_query = query_parts[1]
            items = db().text_search(channel_id, substring, tag_query)
            if not items:
                return [Action(kind=ActionKind.REPLY, text=f'No matches found')], False
            if len(items) == 1:
                text_id, text, _ = items[0]
            else:
                return [Action(kind=ActionKind.REPLY, text=f'Multiple matches for that query')], False
        if not text_id:
            return [Action(kind=ActionKind.REPLY, text=f'No text is found')], False
        s = text_to_row(channel_id, text_id)
        t = db().delete_text(channel_id, text_id)
        if t:
            return [Action(kind=ActionKind.REPLY, text=f'Deleted text\n{s}')], False
        return [Action(kind=ActionKind.REPLY, text=f'Text #{text_id} is not found')], False

    def help(self, prefix: str):
        return f'{prefix}rm'

    def help_full(self, prefix: str):
        return f'{prefix}rm <id|substring;tag query>'


class Multiline(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['multiline'])
        if not text:
            return [], True
        text = text.strip()
        channel_id = msg.channel_id
        lines = [x.strip() for x in text.split('\n')]
        is_mod = msg.is_mod
        private = msg.private
        actions: List[Action] = []
        cmds = get_commands(channel_id, msg.prefix)
        for line in lines:
            if not line:
                continue
            logging.info(f'executing line {line}')
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
                a, next = await cmd.run(cp)
                actions.extend(a)
                if not next:
                    break
        return [Action(kind=ActionKind.REPLY, text=f'Executed {len(lines)} lines')], False

    def help(self, prefix: str):
        return f'{prefix}multiline'

    def help_full(self, prefix: str):
        return f'{prefix}multiline\n{prefix}command1\n{prefix}command2\n...'


class Debug(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['debug'])
        if not text:
            return [], True
        text = text.strip()
        results: List[Action] = []
        v = msg.get_variables()
        channel_id = msg.channel_id
        if not text:
            for e in db().get_logs(channel_id):
                s = '\n'.join([discord.utils.escape_mentions(x[1])
                               for x in e.messages]) + '\n-----------------------------\n'
                results.append(Action(kind=ActionKind.PRIVATE_MESSAGE, text=s))
            return results, False
        commands = db().get_commands(channel_id, msg.prefix)
        for cmd in commands:
            if cmd.name == text:
                results.append(Action(ActionKind.PRIVATE_MESSAGE, f'{msg.prefix}command {cmd.name} ' + discord.utils.escape_markdown(discord.utils.escape_mentions(
                    json.dumps(dataclasses.asdict(cmd), ensure_ascii=False)))))
        return results, False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f'{prefix}debug'

    def help_full(self, prefix: str):
        return f'"{prefix}debug" OR "{prefix}debug <command name>"'

    def private_mod_only(self):
        return True


class HelpCommand(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['commands', 'help'])
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
            reply = 'commands: ' + ', '.join(s)
            if is_mod:
                if private:
                    reply += '\ncommand names: ' + \
                        ', '.join(names) + '\n' + 'hidden commands: ' + \
                        ', '.join(hidden_commands)
                elif msg.is_discord:
                    reply += ' (some commands are only available in private messages)'
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
            if h == text or h == msg.prefix + text or h.startswith(text + ' ') or h.startswith(msg.prefix + text + ' '):
                s.append(hf)
        if not s:
            return [], False
        return [Action(kind=ActionKind.REPLY, text='\n'.join(s))], False

    def help(self, prefix: str):
        return f'{prefix}help [<command name>]'

    def mod_only(self):
        return False

    def hidden_help(self):
        return False


class InvalidateCache(Command):
    async def run(self, msg: Message) -> Tuple[List[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ['invalidate_cache'])
        if not text:
            return [], True
        commands_cache.pop(f'commands_{msg.channel_id}_{msg.prefix}', None)
        return [], False