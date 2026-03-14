import jinja2
import random
import time
import numpy as np
import commands
from typing import Union, List, Optional, Tuple
from storage import db
from data import templates, render, Action, ActionKind, Message
from discord_client import discord_literal

@jinja2.pass_context
def render_text_item(ctx, q: Union[str, int, List[Union[str, float]]], inf: str = ''):
    v = ctx.get_all()
    v['_render_depth'] += 1
    if v['_render_depth'] > 50:
        v['_log'].error('rendering depth is > 50')
        return ''
    text_id: Optional[int] = None
    channel_id = v['channel_id']
    if isinstance(q, int):
        text_id = q
    elif isinstance(q, str):
        if inf:
            q = f'({q}) and {inf}'
        text_id = db().get_random_text_id(channel_id, q)
    else:
        queries = q[::2]
        weights = np.array([abs(float(x)) for x in q[1::2]])
        weights /= np.sum(weights)
        query_text: str = db().rng.choice(queries, p=weights)
        if inf:
            query_text = f'({query_text}) and {inf}'
        text_id = db().get_random_text_id(channel_id, query_text)
    if not text_id:
        v['_log'].info(f'no matching text is found')
        return ''
    if inf:
        tag_id = db().tag_by_value(channel_id)[inf]
        return db().get_text_tag_value(channel_id, text_id, tag_id)
    txt = db().get_text(channel_id, text_id)
    if not txt:
        v['_log'].info(f'failed to get text {text_id}')
        return ''
    return render(txt, v)

def randint(a=0, b=100):
    return random.randint(a, b)

@jinja2.pass_context
def get_variable(ctx, name: str, category: str = '', default_value: str = ''):
    channel_id = ctx.get('channel_id')
    return db().get_variable(channel_id, name, category, default_value)

@jinja2.pass_context
def set_variable(ctx, name: str, value: str = '', category: str = '', expires: int = 9 * 3600):
    channel_id = ctx.get('channel_id')
    db().set_variable(channel_id, name, value, category, expires + int(time.time()))
    return ''

@jinja2.pass_context
def get_variables_category_size(ctx, name: str) -> int:
    channel_id = ctx.get('channel_id')
    return db().count_variables_in_category(channel_id, name)

@jinja2.pass_context
def delete_category(ctx, name: str):
    channel_id = ctx.get('channel_id')
    db().delete_category(channel_id, name)
    return ''

@jinja2.pass_context
def list_category(ctx, name: str) -> List[Tuple[str,str]]:
    channel_id = ctx.get('channel_id')
    return db().list_variables(channel_id, name)

@jinja2.pass_context
def discord_or_twitch(ctx, vd: str, vt: str):
    return vd if ctx.get('media') == 'discord' else vt

@jinja2.pass_context
def new_message(ctx, s: str):
    msg: Message = commands.messages[ctx.get('_id')]
    msg.additionalActions.append(Action(kind=ActionKind.NEW_MESSAGE, text=s))
    return ''

def register_template_globals():
    """Register all globally available functions to the Jinja SandboxedEnvironment."""
    # templates.globals['list'] = render_list_item
    templates.globals['txt'] = render_text_item
    templates.globals['randint'] = randint
    templates.globals['discord_literal'] = discord_literal
    templates.globals['get'] = get_variable
    templates.globals['set'] = set_variable
    templates.globals['category_size'] = get_variables_category_size
    templates.globals['list_category'] = list_category
    templates.globals['delete_category'] = delete_category
    templates.globals['message'] = new_message
    templates.globals['timestamp'] = lambda: int(time.time())
    templates.globals['dt'] = discord_or_twitch
    templates.globals['discord_name'] = discord_literal
    # templates.globals['echo'] = lambda x: x
    # templates.globals['log'] = lambda x: logging.info(x)
