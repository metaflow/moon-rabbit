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