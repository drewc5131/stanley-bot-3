from discord.ext import commands
import discord
import asyncio
from io import TextIOWrapper

import os
import sys
sys.path.insert(0, "lib")
import logging
import logging.handlers
import traceback
import datetime
import subprocess

class StanleyBot(commands.Bot):
    def __init__(self, *args, **kwargs):

        super().__init__(*args, command_prefix="p#", **kwargs);

def main(bot):
    #load_cogs(bot)

    print("Logging into Discord...");
    tokenfile = open("token.dbot");
    token = tokenfile.read();
    tokenfile.close();
    yield from bot.login(token = token);
    yield from bot.connect();

class Formatter(commands.HelpFormatter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs);

    def _add_subcommands_to_page(self, max_width, commands):
        for name, command in sorted(commands, key=lambda t: t[0]):
            if name in command.aliases:
                # skip aliases
                continue

            entry = '  {0:<{width}} {1}'.format(name, command.short_doc,
                                                width=max_width);
            shortened = self.shorten(entry);
            self._paginator.add_line(shortened);

def load_cogs(bot: StanleyBot):
    cogs = ("moderation", "meme");
    for cog in cogs:
        bot.load_extension("cogs." + cog);
        print("Loading Extension " + cog)

def initialize(bot_class=StanleyBot, formatter_class=Formatter):
    formatter = formatter_class(show_check_failure=False)

    bot = bot_class(formatter=formatter, description="lol", pm_help=None)

    import __main__

    @bot.event
    async def on_ready():
        print('------')

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
    return bot

if __name__ == '__main__':
    sys.stdout = TextIOWrapper(sys.stdout.detach(),
                               encoding=sys.stdout.encoding,
                               errors="replace",
                               line_buffering=True)
    bot = initialize()
    load_cogs(bot)
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main(bot))
    except discord.LoginFailure:
        bot.logger.error(traceback.format_exc())
