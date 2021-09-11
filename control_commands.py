import discord
from discord.utils import escape_markdown
import psycopg2.extensions
from data import *
import dacite
import json
from data import Action, Command, ActionKind, Effect
import json
from jinja2.sandbox import SandboxedEnvironment
import psycopg2
import psycopg2.extensions
import logging
import re
from typing import Dict, List, Type
import traceback
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
    cmd = PersistentCommand(pattern="!prefix" + re.escape(name) + "\\b")
    try:
        cmd = dacite.from_dict(PersistentCommand, json.loads(text))
    except Exception as e:
        log.info('failed to parse command as JSON, assuming literal text')
        cmd.effects.append(Effect(text=text, kind=ActionKind.NEW_MESSAGE))
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
        return [Action(kind=ActionKind.REPLY, text="ignored")]
    new_prefix = txt.split(' ')[0]
    if variables['media'] == 'discord':
        db.set_discord_prefix(channel_id, new_prefix)
    if variables['media'] == 'twitch':
        db.set_twitch_prefix(channel_id, new_prefix)
    return [Action(kind=ActionKind.REPLY, text=f'set new prefix for {variables["media"]} to "{new_prefix}"')]


async def fn_debug(db: DB,
                   cur: psycopg2.extensions.cursor,
                   log: InvocationLog,
                   channel_id: int,
                   variables: Dict,
                   txt: str) -> List[Action]:
    if variables['media'] != 'discord' or not variables['is_mod']:
        return
    results: List[Action] = []
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
