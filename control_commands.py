import discord
from discord.utils import escape_markdown
import psycopg2.extensions
from data import *
import dacite
import json
from data import Action, Command, ActionKind
import json
from jinja2.sandbox import SandboxedEnvironment
import psycopg2
import psycopg2.extensions
import logging
import re
from typing import Callable, Dict, List, Type
import storage
import dacite
from storage import DB


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
    log.info(
        f"channel={channel_id} author={variables['author_name']} added new command '{name}' #{id}")
    return [Action(kind=ActionKind.REPLY, text=f"Added new command '{name}' #{id}")]


async def fn_add_list(db: DB,
                      cur: psycopg2.extensions.cursor,
                      log: InvocationLog,
                      channel_id: int,
                      variables: Dict,
                      txt: str) -> List[Action]:
    name, text = txt.split(' ', 1)
    cur.execute('INSERT INTO lists (channel_id, author, list_name, text) VALUES (%s, %s, %s, %s) RETURNING id;',
                (channel_id, variables['author_name'], name, text))
    id = cur.fetchone()[0]
    log.info(f"added new list item '{name}' '{text}' #{id}")
    # TODO cache clear
    return [Action(kind=ActionKind.REPLY, text=f"Added new list '{name}' item '{text}' #{id}")]


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
    if variables['bot'] not in variables['direct_mention']:
        log.info('this bot is not mentioned directly')
        return []
    result: List[Action] = []
    new_prefix = txt.split(' ')[0]
    if variables['media'] == 'discord':
        db.set_discord_prefix(channel_id, new_prefix)
    if variables['media'] == 'twitch':
        db.set_twitch_prefix(channel_id, new_prefix)
    result.append(Action(kind=ActionKind.REPLY, text=f'set new prefix for {variables["media"]} to "{new_prefix}"'))
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
        commands = db.get_commands(channel_id, variables['prefix'])
        for cmd in commands:
            if cmd.data.name == txt:
                results.append(Action(ActionKind.PRIVATE_MESSAGE, discord.utils.escape_mentions(json.dumps(dataclasses.asdict(cmd.data)))))
        return results
    logging.info(f'logs {db.get_logs(channel_id)}')
    for e in db.get_logs(channel_id):
        s = '\n'.join([discord.utils.escape_mentions(x[1]) for x in e.messages])+ '\n-----------------------------\n'
        s = s[:1900]
        results.append(Action(kind=ActionKind.PRIVATE_MESSAGE, text=s))
    return results

all_commands = {
    'set': fn_cmd_set,
    'list-add': fn_add_list,
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
        r = await all_commands[cmd](storage.db, storage.db.conn.cursor(), log, channel_id, variables, t)
        log.info(f"command result '{r}'")
        actions.extend(r)
        log.info(f'actions {actions}')
    if not actions:
        # Add an empty message if no actions are set.
        actions.append(Action(ActionKind.NOOP, ''))
    return actions