"""
 Copyright 2021 Goncharov Mikhail

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

import discord
from discord import channel
from discord.utils import escape_markdown
import psycopg2.extensions
from data import *
import json
import json
from jinja2.sandbox import SandboxedEnvironment
import psycopg2
import psycopg2.extensions
import logging
import re
from typing import Callable, Dict, List, Type
import storage
from storage import DB, cursor, db
import traceback

commands_cache = {}


class Command(Protocol):
    async def run(self, prefix: str, text: str, discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        return [], True
    
    def help(self, prefix: str):
        return ''


def get_commands(channel_id: int, prefix: str) -> List[Command]:
    key = f'commands_{channel_id}_{prefix}'
    if not key in commands_cache:
        z: List[Command] = [ListAddBulk(), ListNames(), ListRemove(),
                            ListSearch(), Eval(), Debug(), ListAddItem(), CommandsList()]
        z.extend([PersistentCommand(x, prefix)
                 for x in db().get_commands(channel_id, prefix)])
        commands_cache[key] = z
    return commands_cache[key]


class PersistentCommand:
    regex: Optional[re.Pattern]
    data: CommandData

    def __init__(self, data, prefix):
        self.data = data
        p = data.pattern.replace('!prefix', re.escape(prefix))
        logging.info(f'regex {p}')
        self.regex = re.compile(p, re.IGNORECASE)

    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if (not self.data.discord) and is_discord:
            return [], True
        if (not self.data.twitch) and (not is_discord):
            return [], True
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
            return self.data.help
        return self.data.name + ' ' + self.regex.pattern


class ListAddBulk:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-add-bulk "):
            return [], True
        v = get_variables()
        if not v['is_mod']:
            return [], True
        log = v['_log']
        parts = text.split(' ', 2)
        list_name = ''
        content = ''
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(v['prefix']))], False
        list_name = parts[1]
        if len(parts) == 3:
            content = parts[2]
        if is_discord:
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
        for v in values:
            if v:
                total += 1
                _, b = db().add_list_item(channel_id, list_name, v)
                if b:
                    added += 1
        return [Action(kind=ActionKind.REPLY, text=f"Added {added} items out of {total}")], False

    def help(self, prefix: str):
        return f'"{prefix}list-add-bulk <list name> + <attach a file>'

class ListNames:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if text != prefix + "lists":
            return [], True
        v = get_variables()
        if not v['is_mod']:
            return [], True
        return [Action(kind=ActionKind.REPLY, text='\n'.join(db().get_list_names(v['channel_id'])))], False

    def help(self, prefix: str):
        return f'{prefix}lists'

def escape_like(t):
    return t.replace('=', '==').replace('%', '=%').replace('_', '=_')


def list_search(channel_id: int, txt: str, list_name: str) -> List[Tuple[int, str, str]]:
    matched_rows: List[Tuple[int, str]] = []
    with cursor() as cur:
        q = '%' + escape_like(txt) + '%'
        if list_name:
            cur.execute("select id, list_name, text from lists where (channel_id = %s) AND (list_name = %s) AND (text LIKE %s)",
                        (channel_id, list_name, q))
        else:
            cur.execute("select id, list_name, text from lists where (channel_id = %s) AND (text LIKE %s)",
                        (channel_id, q))
        for row in cur.fetchall():
            matched_rows.append([row[0], row[1], row[2]])
    return matched_rows


class ListRemove:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-rm"):
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
        if not v['is_mod']:
            return [], True
        parts = text.split(' ', 3)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(v['prefix']))], False
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


    def help(self, prefix: str):
        return f'"{prefix}list-rm <number>" OR "{prefix}list-rm <substring>" [<list name>]" OR "{prefix}list-rm all <list name>"'


class ListSearch:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "list-search"):
            return [], True
        v = get_variables()
        channel_id = v['channel_id']
        if not v['is_mod']:
            return [], True
        parts = text.split(' ', 3)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(v['prefix']))], False            
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
        return f'{prefix}list-search <substring> [<list name>]'


class Eval:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not text.startswith(prefix + "eval"):
            return [], True
        v = get_variables()
        if not v['is_mod']:
            return [], True
        parts = text.split(' ', 1)
        if len(parts) < 2:
            return [Action(kind=ActionKind.REPLY, text=self.help(v['prefix']))], False
        v['_log'].info(f'eval "{parts[1]}"')
        v['_render_depth'] = 0
        s = render(parts[1], v)
        if not s:
            s = "<empty>"
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f'{prefix}eval <expression>'

async def fn_cmd_set(db: DB,
                     cur: psycopg2.extensions.cursor,
                     log: InvocationLog,
                     channel_id: int,
                     variables: Dict,
                     txt: str) -> List[Action]:
    '''
    txt format '<name> [<json|text>]'
    missing value will drop the command
    '''
    commands_cache.pop(f'commands_{channel_id}_{variables["prefix"]}', None)
    parts = txt.split(' ', 1)
    name = parts[0]
    if len(parts) == 1:
        cur.execute(
            'DELETE FROM commands WHERE channel_id = %s AND name = %s', (channel_id, name))
        return [Action(kind=ActionKind.REPLY, text=f"Deleted command '{name}'")]
    text = parts[1]
    cmd = CommandData(pattern="!prefix" + re.escape(name) + "\\b")
    try:
        cmd = dictToCommandData(json.loads(text))
    except Exception as e:
        log.info('failed to parse command as JSON, assuming literal text')
        cmd.actions.append(Action(text=text, kind=ActionKind.NEW_MESSAGE))
    cmd.name = name
    log.info(f'parsed command {cmd}')
    id = db.set_command(cur, channel_id, variables['author_name'], cmd)
    log.info(
        f"channel={channel_id} author={variables['author_name']} added new command '{name}' #{id}")
    return [Action(kind=ActionKind.REPLY, text=f"Added new command '{name}' #{id}")]


async def fn_set_prefix(db: DB,
                        cur: psycopg2.extensions.cursor,
                        log: InvocationLog,
                        channel_id: int,
                        variables: Dict,
                        txt: str) -> List[Action]:
    logging.info(f"set new prefix '{txt}'")
    if variables['bot'] not in str(variables['direct_mention']):
        log.info('this bot is not mentioned directly')
        return []
    result: List[Action] = []
    new_prefix = txt.split(' ')[0]
    if variables['media'] == 'discord':
        db.set_discord_prefix(channel_id, new_prefix)
    if variables['media'] == 'twitch':
        db.set_twitch_prefix(channel_id, new_prefix)
    result.append(Action(kind=ActionKind.REPLY,
                  text=f'set new prefix for {variables["media"]} to "{new_prefix}"'))
    return result


class ListAddItem:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not is_discord:
            return [], True
        if not text.startswith(prefix + "list-add"):
            return [], True
        v = get_variables()
        if not v['is_mod']:
            return [], True
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
        return f'{prefix}list-add <list name> <value>'


class Debug:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not is_discord:
            return [], True
        if not text.startswith(prefix + "debug"):
            return [], True
        results: List[Action] = []
        v = get_variables()
        if not v['is_mod']:
            return [], True
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
                results.append(Action(ActionKind.PRIVATE_MESSAGE, discord.utils.escape_markdown(discord.utils.escape_mentions(
                    json.dumps(dataclasses.asdict(cmd), ensure_ascii=False)))))
        return results, False

    def help(self, prefix: str):
        return f'"{prefix}debug" OR "{prefix}debug <command name>"'

all_commands = {
    'set': fn_cmd_set,
    'prefix-set': fn_set_prefix,
}

async def process_control_message(log: InvocationLog, channel_id: int, txt: str, prefix: str, get_variables: Callable[[], Dict]) -> List[Action]:
    admin_command = False
    for c in all_commands:
        if txt.startswith(prefix + c + ' ') or txt == prefix + c:
            admin_command = True
            break
    if not admin_command:
        return []
    actions: List[Action] = []
    variables = get_variables()
    log.info('running control commands')
    if not variables['is_mod']:
        log.warning(f'non mod called an admin command')
        return [Action(ActionKind.REPLY, '')]
    txt = txt[len(prefix):]
    for p in txt.split('\n' + prefix):
        parts = p.split(' ', 1)
        cmd = parts[0]
        t = ''
        if len(parts) > 1:
            t = parts[1]
        if cmd not in all_commands:
            log.info(f'unknown command {cmd}')
            continue
        log.info(f"running cmd {cmd} '{t}'")
        r = await all_commands[cmd](storage.db(), storage.db().conn.cursor(), log, channel_id, variables, t)
        log.info(f"command result '{r}'")
        actions.extend(r)
    actions = fold_actions(actions)
    log.info(f'actions {actions}')
    if not actions:
        # Add an empty message if no actions are set.
        actions.append(Action(ActionKind.NOOP, ''))
    return actions

class CommandsList:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if not is_discord:
            return [], True
        if not text.startswith(prefix + "commands"):
            return [], True
        v = get_variables()
        if not v['is_mod']:
            return [], True
        channel_id = v['channel_id']
        s = []
        for c in get_commands(channel_id, v['prefix']):
            s.append(c.help(v['prefix']))
        return [Action(kind=ActionKind.PRIVATE_MESSAGE, text='\n'.join(s))], False
    
    def help(self, prefix: str):
        return f'{prefix}commands'