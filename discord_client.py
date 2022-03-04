from io import StringIO, BytesIO

from urllib.parse import urlparse
from data import *
import discord  # type: ignore
import logging
import random
import ttldict2  # type: ignore
from storage import DB, db, set_db, cursor
from typing import Any, Callable, List, Set, Union
import traceback
import commands
import time
import logging.handlers
from PIL import Image, ImageFont, ImageDraw 
import os
import hashlib
import requests

def discord_literal(t):
    return t.replace('<@!', '<@')

def download_file(url: str) -> str:
    url_parts = urlparse(url)
    name, ext = os.path.splitext(url_parts.path)
    h = hashlib.new('SHA1')
    h.update(url.encode('utf8'))
    url_hash = h.hexdigest()
    file_name = f'{h.hexdigest()}{ext}'
    logging.info(f'parts {url_parts.path} hash {url_hash} name {name} ext {ext}')
    if os.path.isfile(file_name):
        logging.info(f'"{file_name}" already exist')
        return file_name
    r = requests.get(url, allow_redirects=True)
    open(file_name, 'wb').write(r.content)
    return file_name

# https://discordpy.readthedocs.io/en/latest/api.html
class DiscordClient(discord.Client):
    def __init__(self, profile: bool, *args, **kwargs):
        self.guild_data: Dict[str, Any] = {}
        self.channels: Dict[str, Any] = {}
        self.mods: Dict[str, str] = {}
        self.profile = profile
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print('We have logged in as {0.user}'.format(self))

    async def on_message(self, message: discord.Message):
        # Don't react to own messages.
        if message.author == self.user:
            return
        # logging.info(f'channel {message.channel} {message.channel.type}')
        guild_id = ''
        is_mod = False
        private = False
        if message.channel.type == discord.ChannelType.private:
            g = self.mods.get(str(message.author.id))
            if not g:
                await message.channel.send('You are not a moderator. First send message in your discord and come back here.')
                return
            guild_id = g
            is_mod = True
            private = True
        else:
            permissions = message.author.guild_permissions
            guild_id = str(message.guild.id)
            is_mod = permissions.ban_members or permissions.administrator
            if is_mod:
                self.mods[str(message.author.id)] = guild_id
        try:
            channel_id, prefix = db().discord_channel_info(
                db().conn.cursor(), guild_id)
        except Exception as e:
            logging.error(
                f"'discord_channel_info': {e}\n{traceback.format_exc()}")
            return
        log = InvocationLog(
            f'guild={guild_id} message_channel={message.channel.id} channel={channel_id} author={message.author.id}')
        if channel_id not in self.channels:
            self.channels[channel_id] = {
                'active_users': ttldict2.TTLDict(ttl_seconds=3600.0 * 2),
                'allowed_channels': db().get_discord_allowed_channels(channel_id)}
        text = commands.command_prefix(message.content, prefix, ['allow_here'])
        discord_channel = str(message.channel.id)
        if text:
            self.channels[channel_id]['allowed_channels'].add(discord_channel)
            db().set_discord_allowed_channels(channel_id, self.channels[channel_id]['allowed_channels'])
            log.info(f'discord channel {message.channel.id} is allowed')
            await message.reply('this channel is now allowed')
            return
        text = commands.command_prefix(message.content, prefix, ['disallow_here'])
        if text:
            self.channels[channel_id]['allowed_channels'].discard(discord_channel)
            db().set_discord_allowed_channels(channel_id, self.channels[channel_id]['allowed_channels'])
            log.info(f'discord channel {message.channel.id} is allowed')
            await message.reply('this channel is now disallowed')
            return
        if (message.channel.type != discord.ChannelType.private) and self.channels[channel_id]['allowed_channels'] and (discord_channel not in self.channels[channel_id]['allowed_channels']):
            return
        text = commands.command_prefix(message.content, prefix, ['cron'])
        if text:
            await self.on_cron()
            return
        if not message.author.bot:
            self.channels[channel_id]['active_users'][discord_literal(
                message.author.mention)] = '+'
        self.channels[channel_id]['active_users'].drop_old_items()
        log.info(f'message "{message.content}"')
        variables: Optional[Dict] = None
        # postpone variable calculations as much as possible
        message_id = str(message.id)
        def get_vars():
            nonlocal variables
            if not variables:
                bot = discord_literal(self.user.mention)
                author = discord_literal(message.author.mention)
                exclude = [bot, author]
                variables = {
                    'author': author,
                    'author_name': discord_literal(str(message.author.display_name)),
                    'mention': Lazy(lambda: self.any_mention(message, self.channels[channel_id]['active_users'].keys(), exclude)),
                    'direct_mention': Lazy(lambda: self.mentions(message)),
                    'random_mention': Lazy(lambda: self.random_mention(message, self.channels[channel_id]['active_users'].keys(), exclude), stick=False),
                    'any_mention': Lazy(lambda: self.any_mention(message, self.channels[channel_id]['active_users'].keys(), [bot]), stick=False),
                    'media': 'discord',
                    'text': message.content,
                    'is_mod': is_mod,
                    'prefix': prefix,
                    'bot': bot,
                    'channel_id': channel_id,
                    '_log': log,
                    '_discord_message': message,
                    '_private': private,
                    '_id': message_id,
                }
            return variables
        msg = Message(
            id = message_id,
            log = log,
            channel_id=channel_id,
            txt=message.content,
            event=EventType.message,
            prefix=prefix,
            is_discord=True,
            is_mod=is_mod,
            private=private,
            get_variables=get_vars)
        if self.profile:
            start = time.time_ns()
            i = 0
            ns = 1000_000_000
            while (time.time_ns() - start < ns):
                i += 1
                actions = await commands.process_message(msg)
            actions.append(
                Action(ActionKind.REPLY, text=f'{i} iterations in {time.time_ns() - start} ns'))
        else:
            actions = await commands.process_message(msg)
        db().add_log(channel_id, log)
        for a in actions:
            if len(a.text) > 2000:
                a.text = a.text[:1997] + "..."
            if a.kind == ActionKind.NEW_MESSAGE:
                await message.channel.send(a.text)
            if a.kind == ActionKind.REPLY:
                if a.attachment:
                    await message.reply(a.text, file=discord.File(StringIO(a.attachment), filename=a.attachment_name))
                else:
                    await message.reply(a.text)
            if a.kind == ActionKind.PRIVATE_MESSAGE:
                await message.author.send(a.text)
            if a.kind == ActionKind.REACT_EMOJI:
                await message.add_reaction(a.text)

    def random_mention(self, msg, users: List[str], exclude: List[str]):
        users = [x for x in users if x not in exclude]
        if users:
            return random.choice(users)
        return discord_literal(msg.author.mention)

    def mentions(self, msg):
        if msg.mentions:
            return ' '.join([discord_literal(x.mention) for x in msg.mentions])
        return ''

    def any_mention(self, msg, users: List[str], exclude: List[str]):
        direct = self.mentions(msg)
        return direct if direct else self.random_mention(msg, users, exclude)

    def count_active_voice_channels(self, guild: discord.Guild) -> int:
        count = 0
        for v in guild.voice_channels:
            if len(v.members) > 0:
                count += 1
        return count

    async def on_cron(self):
        logging.info(f'running discord cron')
        g: discord.Guild
        for g in self.guilds:
            try:
                id = f'{g.id}'
                if 'BANNER' not in g.features:
                    continue
                channel_id, prefix = db().discord_channel_info(db().conn.cursor(), id)
                banner_template = db().get_variable(channel_id, 'banner_template', 'admin', '')
                log = InvocationLog(f'guild={id} banner update')
                log.debug(f'banner template "{banner_template}"')
                if not banner_template:
                    continue
                if id not in self.guild_data:
                    self.guild_data[id] = {'banner_text': ''}
                variables: Optional[Dict] = None
                def get_vars():
                    nonlocal variables
                    if not variables:
                        bot = discord_literal(self.user.mention)
                        variables = {
                            'media': 'discord',
                            'text': banner_template,
                            'is_mod': False,
                            'prefix': prefix,
                            'bot': bot,
                            'channel_id': channel_id,
                            'member_count': g.member_count,
                            'description': g.description,
                            'premium_subscription_count': g.premium_subscription_count,
                            'premium_tier': g.premium_tier,
                            'active_voice_channels_count': Lazy(lambda: str(self.count_active_voice_channels(g))),
                            '_log': log,
                            '_private': False,
                        }
                    return variables
                msg = Message(
                    id = 'banner',
                    log = log,
                    channel_id=channel_id,
                    txt=banner_template,
                    event=EventType.message,
                    prefix=prefix,
                    is_discord=True,
                    is_mod=True,
                    private=False,
                    get_variables=get_vars)
                actions = await commands.process_message(msg)
                if not actions:
                    continue
                txt = actions[0].text
                if self.guild_data[id]['banner_text'] == txt:
                    log.debug('banner text is the same')
                    continue
                parts = txt.split(';;')
                log.debug(parts[0])
                url = parts[0].strip()
                img = download_file(url)
                image = Image.open(img)
                image_editable = ImageDraw.Draw(image)
                for p in parts[1:]:
                    x, y, s, cr, cg, cb, text = p.split(',', 6)
                    title_font = ImageFont.truetype('arial.ttf', size=int(s))
                    image_editable.text((int(x),int(y)), text, (int(cr), int(cg), int(cb)), font=title_font)
                h = hashlib.new('SHA1')
                h.update(txt.encode('utf8'))
                resultFile = f'{h.hexdigest()}.png'
                log.info(f'result {resultFile}')
                image.save(resultFile)
                bb = BytesIO()
                image.save(bb, format='png')
                bb.seek(0)
                await g.edit(banner=bb.read())
                self.guild_data[id]['banner_text'] = txt
            except Exception as e:
                logging.error(f"'cron update': {e}\n{traceback.format_exc()}")
                if id not in self.guild_data:
                    self.guild_data[id] = {'banner_text': ''}
                self.guild_data[id].banner_text = ''