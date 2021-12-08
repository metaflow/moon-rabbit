from io import StringIO
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

def discord_literal(t):
    return t.replace('<@!', '<@')

# https://discordpy.readthedocs.io/en/latest/api.html
class DiscordClient(discord.Client):
    def __init__(self, profile: bool, *args, **kwargs):
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