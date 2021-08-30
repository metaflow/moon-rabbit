#!/usr/bin/python
#
# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Discord bot entry point."""

import sys


def main(argv):
    pass


if __name__ == '__main__':
    main(sys.argv)
import discord
import os
import random

client = discord.Client()

@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))

@client.event
async def on_message(message):
    if message.author == client.user:
        return
    print(message.content)
    if '+мж' in message.content:
        await message.channel.send(random.choice(['фонарь', 'собака', 'мракус']))

if __name__ == "__main__":
  client.run(os.getenv('TOKEN'))
