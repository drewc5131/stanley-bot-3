import discord
from discord.ext import commands
from datetime import datetime
from collections import deque, defaultdict, OrderedDict
import os
import re
import logging
import asyncio

class Moderation():
    """Moderation Commands"""
    def __init__(self, bot):
        self.bot = bot;

    @commands.command(no_pm = True, pass_context = True)
    async def ban(self, ctx, user: discord.Member, days: str = None):
        """Bans `user` and deletes last `days` of msgs from `user`"""
        await ctx.send('lol')
        return
        try:
            await self.bot.ban(user, days);

            embed = discord.Embed(color = red());
            embed.author = ctx.message.author;
            embed.title = "Banned a user";
            embed.timestamp = datetime.now();
            embed.add_field("Username", "user.username");
            embed.add_field("User ID", "user.id");
            await self.bot.say("lol bye", embed = embed);

        except discord.errors.Forbidden:
            await self.bot.say("lol i dont have permissions");

def setup(bot):
    bot.add_cog(Moderation(bot))
