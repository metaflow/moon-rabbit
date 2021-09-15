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
from storage import DB, db
import traceback

commands_cache = {}


class Command(Protocol):
    async def run(self, prefix: str, text: str, discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        return [], True


def get_commands(channel_id: int, prefix: str) -> List[Command]:
    key = f'commands_{channel_id}_{prefix}'
    if not key in commands_cache:
        z: List[Command] = [ListAddBulk(), ListNames()]
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
            return [Action(kind=ActionKind.REPLY, text=f"format {v['prefix']}list-add-bulk LIST_NAME[ item1[<new line>item2...")], False
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


class ListNames:
    async def run(self, prefix: str, text: str, is_discord: bool, get_variables: Callable[[], Dict]) -> Tuple[List[Action], bool]:
        if text != prefix + "lists":
            return [], True
        v = get_variables()
        if not v['is_mod']:
            return [], True
        return [Action(kind=ActionKind.REPLY, text='\n'.join(db().get_list_names(v['channel_id'])))], False


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
    parts = txt.split(' ', 1)
    logging.info(f'parts {parts}')
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
    commands_cache.pop(f'commands_{channel_id}_{variables["prefix"]}', None)
    log.info(
        f"channel={channel_id} author={variables['author_name']} added new command '{name}' #{id}")
    return [Action(kind=ActionKind.REPLY, text=f"Added new command '{name}' #{id}")]


async def fn_add_list_item(db: DB,
                           cur: psycopg2.extensions.cursor,
                           log: InvocationLog,
                           channel_id: int,
                           variables: Dict,
                           txt: str) -> List[Action]:
    parts = txt.split(' ', 1)
    if len(parts) < 2:
        return []
    list_name, value = parts
    id, b = db.add_list_item(channel_id, list_name, value)
    return [Action(kind=ActionKind.REPLY, text=f"Added new list '{list_name}' item '{value}' #{id}")]


async def fn_list_search(db: DB,
                         cur: psycopg2.extensions.cursor,
                         log: InvocationLog,
                         channel_id: int,
                         variables: Dict,
                         txt: str) -> List[Action]:
    parts = txt.split(' ', 1)
    if len(parts) > 1:
        q = '%' + \
            parts[1].replace('=', '==').replace(
                '%', '=%').replace('_', '=_') + '%'
        cur.execute("select id, text from lists where (channel_id = %s) AND (list_name = %s) AND (text LIKE %s)",
                    (channel_id, parts[0], q))
    else:
        cur.execute("select id, text from lists where (channel_id = %s) AND (list_name = %s)",
                    (channel_id, parts[0]))
    rr = []
    for row in cur.fetchall():
        rr.append(f"#{row[0]}: {row[1]}")
    # TODO: clear cache
    if not rr:
        return [Action(kind=ActionKind.REPLY, text="no results")]
    else:
        return [Action(kind=ActionKind.REPLY, text='\n'.join(rr))]


async def fn_delete_list_item(db: DB,
                              cur: psycopg2.extensions.cursor,
                              log: InvocationLog,
                              channel_id: int,
                              variables: Dict,
                              txt: str) -> List[Action]:
    if txt.isnumeric():
        cur.execute(
            'DELETE FROM lists WHERE channel_id = %s AND id = %s', (channel_id, txt))
        return [Action(kind=ActionKind.REPLY, text=f"Deleted list item #{id}")]
    parts = txt.split(' ', 1)
    if len(parts) < 2 or parts[0] != 'all':
        return [Action(kind=ActionKind.REPLY, text="command format is <number> or 'all <list name>'")]
    cur.execute(
        'DELETE FROM lists WHERE channel_id = %s AND list_name = %s', (channel_id, parts[1]))
    return [Action(kind=ActionKind.REPLY, text=f"Deleted all items in list '{parts[1]}'")]


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


async def fn_debug(db: DB,
                   cur: psycopg2.extensions.cursor,
                   log: InvocationLog,
                   channel_id: int,
                   variables: Dict,
                   txt: str) -> List[Action]:
    if variables['media'] != 'discord' or not variables['is_mod']:
        return []
    results: List[Action] = []
    if txt:
        prefix = variables["prefix"]
        commands = db.get_commands(channel_id, prefix)
        for cmd in commands:
            if cmd.name == txt:
                results.append(Action(ActionKind.PRIVATE_MESSAGE, discord.utils.escape_markdown(discord.utils.escape_mentions(
                    json.dumps(dataclasses.asdict(cmd), ensure_ascii=False)))))
        return results
    logging.info(f'logs {db().get_logs(channel_id)}')
    for e in db.get_logs(channel_id):
        s = '\n'.join([discord.utils.escape_mentions(x[1])
                      for x in e.messages]) + '\n-----------------------------\n'
        results.append(Action(kind=ActionKind.PRIVATE_MESSAGE, text=s))
    return results

all_commands = {
    'set': fn_cmd_set,
    'list-add': fn_add_list_item,
    'list-rm': fn_delete_list_item,
    'list-search': fn_list_search,
    'prefix-set': fn_set_prefix,
    'debug': fn_debug,
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
