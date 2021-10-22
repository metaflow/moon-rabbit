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
from typing import Callable, Dict, List, Set
from storage import cursor, db
import traceback
import query
import lark
import words


async def process_message(log: InvocationLog, channel_id: int, txt: str, event: EventType, prefix: str, is_discord: bool, is_mod: bool, private: bool, get_variables: Callable[[], Dict]) -> List[Action]:
    logging.debug(f'process message "{txt}" type {event}')
    actions: List[Action] = []
    try:
        cmds = get_commands(channel_id, prefix)
        for cmd in cmds:
            if cmd.mod_only() and not is_mod:
                continue
            if cmd.private_mod_only() and not (is_mod and private):
                continue
            if is_discord and not cmd.for_discord():
                continue
            if (not is_discord) and not cmd.for_twitch():
                continue
            a, next = await cmd.run(prefix, txt, event, is_discord, get_variables)
            actions.extend(a)
            if not next:
                break
        log_actions = [a for a in actions if a.attachment == '']
        log.debug(f'actions (except download) {log_actions}')
    except Exception as e:
        actions.append(
            Action(kind=ActionKind.REPLY, text='error ocurred'))
        log.error(f'{e}\n{traceback.format_exc()}')
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
    async def run(self, prefix: str, text: str, event: EventType, discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
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


commands_cache: Dict[str, List[Command]] = {}


def get_commands(channel_id: int, prefix: str) -> List[Command]:
    key = f'commands_{channel_id}_{prefix}'
    if not key in commands_cache:
        z: List[Command] = [HelpCommand(),
                            Eval(), Debug(), Multiline(),
                            SetCommand(), SetPrefix(),
                            TagList(), TagAdd(), TagDelete(),
                            TextSetTags(), TextAdd(), TextUpload(), TextDownload(), TextSearch(), TextRemove(), TextMorph(),
                            ]
        z.extend([PersistentCommand(x, prefix)
                 for x in db().get_commands(channel_id, prefix)])
        commands_cache[key] = z
    return commands_cache[key]


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

    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if event != self.data.event_type or not re.search(self.regex, text):
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

    def hidden_help(self):
        return self.data.hidden


class Eval(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['eval'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
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
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['command'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        log = v['_log']
        channel_id = v['channel_id']
        commands_cache.pop(f'commands_{channel_id}_{prefix}', None)
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
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
- "direct_mention" (if there is a direct action)
- "is_mod" - author is moderator
- "media" - "discord" or "twitch"
- "mention" - direct_mention if set, otherwise random_mention
- "prefix" - command prefix ({prefix})
- "random_mention" (always random)
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
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['prefix-set'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        log = v['_log']
        channel_id = v['channel_id']
        if v['bot'] not in str(v['direct_mention']):
            log.info('this bot is not mentioned directly')
            return [], True
        if not text:
            return [], True
        if is_discord:
            db().set_discord_prefix(channel_id, text)
        else:
            db().set_twitch_prefix(channel_id, text)
        return [Action(kind=ActionKind.REPLY, text=f'set new prefix for {v["media"]} to "{text}"')], False

    def help(self, prefix: str):
        return f'{prefix}prefix-set'

    def help_full(self, prefix: str):
        return f'{prefix}prefix-set <new prefix> <bot>'


class TagAdd(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['tag-add'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        channel_id = v['channel_id']
        if not query.tag_re.match(text):
            return [Action(kind=ActionKind.REPLY, text='tag name might consist of latin letters, digits, "_" and "-" characters')], False
        db().add_tag(channel_id, text)
        return [Action(kind=ActionKind.REPLY, text='OK')], False

    def help(self, prefix: str):
        return f'{prefix}tag-add'

    def help_full(self, prefix: str):
        return f'{prefix}tag-add <value>'


class TagDelete(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['tag-rm'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        channel_id = v['channel_id']
        deleted = db().delete_tag(channel_id, str_to_int(text))
        return [Action(kind=ActionKind.REPLY, text=('OK' if deleted == 1 else 'no such tag'))], False

    def help(self, prefix: str):
        return f'{prefix}tag-rm'

    def help_full(self, prefix: str):
        return f'{prefix}tag-rm <tag id>'


class TagList(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['tags'])
        if not text:
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
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


class TextAdd(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['add'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        text_id: Optional[int] = None
        tag_names: List[str] = []
        if ';' in text and not text.startswith('"'):
            parts = text.split(';')
            if len(parts) >= 2:
                text = parts[0].strip()
                tag_names = parts[1].split(' ')
                tag_names = [x.strip() for x in tag_names if x.strip()]
                logging.info(f'new tags {tag_names}')
            if len(parts) >= 3:
                text_id = str_to_int(parts[2])
        elif text.startswith('"') and text.endswith('"') and len(text) > 2:
            text = text[1:-1]  # strip quotes
        channel_id = v['channel_id']
        s = ''
        if text_id:
            old_txt = db().set_text(channel_id, text, text_id)
            if not old_txt:
                return [Action(kind=ActionKind.REPLY, text=f'Text {text_id} does not exist')], False
            s = f'Updated text #{text_id} from/to\n"{old_txt}"\n"{text}"'
        else:
            text_id, added = db().add_text(channel_id, text)
            s = f'Added new text {text_id}' if added else f'Text {text_id} already exist'
            s += f'\n"{text}"'
            if ' ' not in text:
                tag_options = words.suggest_tags(text)
                if tag_options:
                    s += '. Suggested tags:\n' + tag_options
        if tag_names:
            for t in tag_names:
                if not query.tag_re.match(t):
                    return [Action(kind=ActionKind.REPLY, text=f'tag name might consist of latin letters, digits, "_" and "-" characters, not "{t}"')], False
                db().add_tag(channel_id, t)
            tags_by_value = db().tag_by_value(channel_id)
            tag_by_id = db().tag_by_id(channel_id)
            new_tags = set([tags_by_value[s.strip()] for s in tag_names])
            old_tags, updated = db().set_text_tags(channel_id, text_id, new_tags)
            if updated:
                s += f'\nNew tags: {", ".join([tag_by_id[x] for x in new_tags])}'
                if old_tags:
                    s += f'\nPrevious tags: {", ".join([tag_by_id[x] for x in old_tags])}'
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}add'

    def help_full(self, prefix: str):
        return f'{prefix}add <value>[;tag1 tag2 tag3[;id]] or add "<literal value>"'

def print_morphs(text: str) -> List[str]:
    parts = re.split(r'(\s+)', text.strip())
    ww = [parts[i] for i in range(0, len(parts), 2)]
    parses = [words.morph.parse(w) for w in ww]
    # Remove non 'nomn' parses.
    for i in range(len(parses)):
        parses[i] = [p for p in parses[i] if 'nomn' in list(p.tag.grammemes)]
    idx = [0] * len(parses)
    # logging.info(f'parses {[len(p) for p in parses]} {parses}')
    ok = True
    z = []
    while ok:
        s = f'tag: **morph'
        if any([i for i in idx if i > 0]):
             s += f' morph_{"-".join([str(i) for i in idx])}'
        s += '**\n'
        g = []
        for i in range(len(idx)):
            if idx[i] < len(parses[i]):
                g.append(' '.join(parses[i][idx[i]].tag.grammemes))
            else:
                g.append('X')
        s += ', '.join(g) + '\n'
        for c in words.cases:
            t = c + ': '
            for i in range(len(parts)):
                j = i // 2
                if i % 2 != 0 or idx[j] >= len(parses[j]):
                    t += parts[i]
                    continue
                x = parses[j][idx[j]].inflect({c})
                if not x:
                    t += '?'
                else:
                    t += x.word
            s += t + '\n'
        s += 'plur:' + '\n'
        for c in words.cases:
            t = c + ': '
            for i in range(len(parts)):
                j = i // 2
                if i % 2 != 0 or idx[j] >= len(parses[j]):
                    t += parts[i]
                    continue
                x = parses[j][idx[j]].inflect({c, 'plur'})
                if not x:
                    t += '?'
                else:
                    t += x.word
            s += t + '\n'
        s += '\n'
        idx[0] += 1
        for i in range(len(idx)):
            if idx[i] < len(parses[i]):
                break
            ok = i < len(idx) - 1
            if ok:
                idx[i] = 0
                idx[i + 1] += 1
        z.append(s)
    return z

class TextMorph(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['morph'])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        return [Action(kind=ActionKind.REPLY, text=x) for x in print_morphs(text)], False

    def help(self, prefix: str):
        return f'{prefix}morph'

    def help_full(self, prefix: str):
        return f'{prefix}morph <value>[;tag1 tag2...]'

class TextSetTags(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['tag'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        parts = text.split(' ')
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(prefix))], False
        text_id = str_to_int(parts[0])
        channel_id = v['channel_id']
        txt_value = db().get_text(channel_id, text_id)
        if not txt_value:
            return [Action(kind=ActionKind.REPLY, text=f'No text with id {text_id} found')], False
        set_tags = [x.strip() for x in parts[1:] if x.strip()]
        for t in set_tags:
            if not query.tag_re.match(t):
                return [Action(kind=ActionKind.REPLY, text='tag name might consist of latin letters, digits, "_" and "-" characters')], False
            db().add_tag(channel_id, t.strip())
        s = f'Set tags for text {text_id} "{txt_value}": {", ".join(set_tags)}'
        channel_id = v['channel_id']
        tags_by_value = db().tag_by_value(channel_id)
        tags_by_id = db().tag_by_id(channel_id)
        current_tags = db().get_text_tags(channel_id, text_id)
        if current_tags:
            s += '\nPrevious tags: ' + \
                ', '.join([tags_by_id[x] for x in current_tags])
        new_tags: Set[int] = set()
        for t in set_tags:
            new_tags.add(tags_by_value[t])
        _, ok = db().set_text_tags(channel_id, text_id, new_tags)
        if ok:
            return [Action(kind=ActionKind.REPLY, text=s)], False
        return [Action(kind=ActionKind.REPLY, text=f'Failed to set tags {text_id} {new_tags}')], False

    def help(self, prefix: str):
        return f'{prefix}tag'

    def help_full(self, prefix: str):
        return f'{prefix}tag <id> <tag> [<tag> [...]]\nexisting tags will be removed'


class TextUpload(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if text.strip() != prefix + "upload":
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
            all_tags.update([x.strip() for x in s[1].split(' ') if x.strip()])
        for t in all_tags:
            if not query.tag_re.match(t):
                return [Action(kind=ActionKind.REPLY, text='tag name might consist of latin letters, digits, "_" and "-" characters')], False
            db().add_tag(channel_id, t)
        tag_by_value = db().tag_by_value(channel_id)
        total = 0
        total_added = 0
        total_updated = 0
        for s in lines:
            txt = s[0].strip()
            if not txt:
                continue
            total += 1
            text_id = 0
            if len(s) >= 3:
                text_id = str_to_int(s[2])
            if text_id:
                if db().set_text(channel_id, txt, text_id):
                    total_updated += 1
            else:
                text_id, added = db().add_text(channel_id, txt)
                if added:
                    total_added += 1
            if len(s) < 2:
                continue
            tag_names = s[1].split(' ')
            tag_names = [x.strip() for x in tag_names if x.strip()]
            db().set_text_tags(channel_id, text_id, set(
                [tag_by_value[t] for t in tag_names]))
        return [Action(kind=ActionKind.REPLY, text=f"Added {total_added} and updated {total_updated} texts from non-empty {total} lines with tags {all_tags}")], False

    def help(self, prefix: str):
        return f'{prefix}upload'

    def for_twitch(self):
        return False


class TextDownload(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['download'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        channel_id = v['channel_id']
        items: List[Tuple[int, str, Set[int]]] = []
        if not text:
            items = db().all_texts(channel_id)
        else:
            query_parts = text.split(';', 1)
            substring = query_parts[0]
            tag_query = ''
            if len(query_parts) > 1:
                tag_query = query_parts[1]
            items = db().text_search(channel_id, substring, tag_query)
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        rr = []
        tag_by_id = db().tag_by_id(channel_id)
        for ii in items:
            tags = [tag_by_id[x] for x in ii[2]]
            rr.append(f'{ii[1]}\t{" ".join(tags)}\t{ii[0]}')
        att = '\n'.join(rr)
        return [Action(kind=ActionKind.REPLY,
                       text='texts',
                       attachment=att,
                       attachment_name=f'texts.tsv')], False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f'{prefix}download'

    def help_full(self, prefix: str):
        return f'{prefix}download [<substring>[;<tag query>]]'


class TextSearch(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['search'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        channel_id = v['channel_id']
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        query_parts = text.split(';', 1)
        substring = query_parts[0]
        tag_query = ''
        if len(query_parts) > 1:
            tag_query = query_parts[1]
        items = db().text_search(channel_id, substring, tag_query)
        if not items:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        tag_by_id = db().tag_by_id(channel_id)
        rr = []
        for ii in items:
            tag_names = [tag_by_id[x] for x in ii[2]]
            rr.append(f'{ii[1]};{" ".join(tag_names)};{ii[0]}')
        if not rr:
            return [Action(kind=ActionKind.REPLY, text='no results')], False
        return [Action(kind=ActionKind.REPLY, text='\n'.join(rr))], False

    def help(self, prefix: str):
        return f'{prefix}search'

    def help_full(self, prefix: str):
        return f'{prefix}search <substring>[;<tag query>]'


class TextRemove(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['rm'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        channel_id = v['channel_id']
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(prefix))], False
        if text.isdigit():
            t = db().delete_text(channel_id, int(text))
            if not t:
                return [Action(kind=ActionKind.REPLY, text=f'No text with id {text} found')], False
            return [Action(kind=ActionKind.REPLY, text=f'Deleted text #{text}')], False
        query_parts = text.split(';', 1)
        substring = query_parts[0]
        tag_query = ''
        if len(query_parts) > 1:
            tag_query = query_parts[1]
        items = db().text_search(channel_id, substring, tag_query)
        if not items:
            return [Action(kind=ActionKind.REPLY, text=f'No matches found')], False
        if len(items) == 1:
            id, text, _ = items[0]
            db().delete_text(channel_id, id)
            return [Action(kind=ActionKind.REPLY, text=f'Deleted text "{text}"')], False
        rr = []
        tag_by_id = db().tag_by_id(channel_id)
        for ii in items:
            tag_names = [tag_by_id[x] for x in ii[2]]
            rr.append(f'{ii[0]} {ii[1]} "{", ".join(tag_names)}"')
        s = '\n'.join(rr)
        return [Action(kind=ActionKind.REPLY, text=f'Multiple matches: \n{s}')], False

    def help(self, prefix: str):
        return f'{prefix}rm'

    def help_full(self, prefix: str):
        return f'{prefix}rm <id|substring;tag query>'


class Multiline(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['multiline'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        channel_id = v['channel_id']
        lines = [x.strip() for x in text.split('\n')]
        is_mod = v['is_mod']
        private = v['_private']
        actions: List[Action] = []
        cmds = get_commands(channel_id, prefix)
        for line in lines:
            if not line:
                continue
            logging.info(f'executing line {line}')
            for cmd in cmds:
                if cmd.private_mod_only() and not (is_mod and private):
                    continue
                if cmd.mod_only() and not is_mod:
                    continue
                if is_discord and not cmd.for_discord():
                    continue
                if (not is_discord) and not cmd.for_twitch():
                    continue
                a, next = await cmd.run(prefix, line, event, is_discord, get_variables)
                actions.extend(a)
                if not next:
                    break
        return [Action(kind=ActionKind.REPLY, text=f'Executed {len(lines)} lines')], False

    def help(self, prefix: str):
        return f'{prefix}multiline'

    def help_full(self, prefix: str):
        return f'{prefix}multiline\n{prefix}command1\n{prefix}command2\n...'


class Debug(Command):
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['debug'])
        if not text:
            return [], True
        text = text.strip()
        results: List[Action] = []
        v = get_variables()
        channel_id = v['channel_id']
        if not text:
            for e in db().get_logs(channel_id):
                s = '\n'.join([discord.utils.escape_mentions(x[1])
                               for x in e.messages]) + '\n-----------------------------\n'
                results.append(Action(kind=ActionKind.PRIVATE_MESSAGE, text=s))
            return results, False
        commands = db().get_commands(channel_id, prefix)
        for cmd in commands:
            if cmd.name == text:
                results.append(Action(ActionKind.PRIVATE_MESSAGE, f'{prefix}set {cmd.name} ' + discord.utils.escape_markdown(discord.utils.escape_mentions(
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
    async def run(self, prefix: str, text: str, event: EventType, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        text = command_prefix(text, prefix, ['commands', 'help'])
        if not text:
            return [], True
        text = text.strip()
        v = get_variables()
        is_mod = v['is_mod']
        private = v['_private']
        channel_id = v['channel_id']
        if not text:
            hidden_commands = []
            names = []
            s = []
            for c in get_commands(channel_id, prefix):
                if isinstance(c, PersistentCommand):
                    names.append(c.data.name)
                if is_discord and not c.for_discord():
                    hidden_commands.append(c.help(prefix))
                    continue
                if (not is_discord) and not c.for_twitch():
                    hidden_commands.append(c.help(prefix))
                    continue
                if c.mod_only() and not is_mod:
                    continue
                if c.private_mod_only() and not (is_mod and private):
                    continue
                if c.hidden_help():
                    hidden_commands.append(c.help(prefix))
                    continue
                s.append(c.help(prefix))
            reply = 'commands: ' + ', '.join(s)
            if is_mod:
                if private:
                    reply += '\ncommand names: ' + \
                        ', '.join(names) + '\n' + 'hidden commands: ' + \
                        ', '.join(hidden_commands)
                elif is_discord:
                    reply += ' (some commands are only available in private messages)'
            actions = [Action(kind=ActionKind.REPLY, text=reply)]
            return actions, False
        s = []
        for c in get_commands(channel_id, prefix):
            if c.mod_only() and not is_mod:
                continue
            if c.private_mod_only() and not (is_mod and private):
                continue
            if is_discord and not c.for_discord():
                continue
            if (not is_discord) and not c.for_twitch():
                continue
            hf = c.help_full(prefix)
            h = c.help(prefix)
            if h == text or h == prefix + text or h.startswith(text + ' ') or h.startswith(prefix + text + ' '):
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
