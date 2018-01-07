import discord
from discord.ext import commands
from datetime import datetime
from collections import deque, defaultdict, OrderedDict
import os
import re
import logging
import asyncio

class Meme():
    """Meme Commands"""
    def __init__(self, bot):
        self.bot = bot;

    @commands.command(no_pm = True, pass_context = True)
    async def Test(self, ctx, msg:str):
        """test msg"""
        embed = discord.Embed();
        embed.set_author(name=ctx.message.author.display_name);
        embed.title = "Test";
        embed.timestamp = datetime.now();
        embed.add_field(name = "Msg", value = msg);
        await ctx.send(embed = embed);
        print(ctx.message.author.permissions_in(ctx.message.channel))


def setup(bot):
    bot.add_cog(Meme(bot))
