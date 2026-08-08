#!/usr/bin/env python3
"""Apply sub-bot changes to discord_borda_poll.py"""

with open('discord_borda_poll.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ═══ 1. Add sub-bot creation + helper functions after module loading ═══
old_section = '''print(f"\u2503 Loaded {_loaded_mods} feature modules from modules/")

# Register setup_hook so discord.py calls it before connecting
# Register persistent views for AI Chat Room buttons (survives bot restarts)'''

new_section = '''print(f"\u2503 Loaded {_loaded_mods} feature modules from modules/")

# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# Sub-bot (Entertainment Bot) \u2014 separate Discord bot for other servers
# Only registers entertainment + AI-free commands. Shares all state
# (economy, WW1, AI API, data) with the main bot since same process.
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
sub_bot = None
SUB_BOT_TOKEN_ENV = os.getenv("SUB_BOT_TOKEN")

# Helper: cross-bot channel/guild lookup
def get_channel_any(ch_id):
    """Look up a channel across both bots (main + sub)."""
    ch = bot.get_channel(ch_id)
    if ch is None and sub_bot is not None:
        ch = sub_bot.get_channel(ch_id)
    return ch

def get_guild_any(gid):
    """Look up a guild across both bots (main + sub)."""
    g = bot.get_guild(gid)
    if g is None and sub_bot is not None:
        g = sub_bot.get_guild(gid)
    return g

if SUB_BOT_TOKEN_ENV:
    _sub_intents = discord.Intents.default()
    _sub_intents.message_content = True
    _sub_intents.members = True
    sub_bot = commands.Bot(command_prefix="!", intents=_sub_intents)

    # \u2500\u2500 Sub-bot setup_hook: register only entertainment + AI-free commands \u2500\u2500
    async def _sub_setup_hook():
        _sub_groups = [
            PollGroup(), MeetingGroup(), ScheduleGroup(), SystemGroup(), EconomyGroup(),
            QuizGroup(), TurtleSoupGroup(), WerewolfGroup(), StockGroup(),
            HorseRacingGroup(), SiegeGroup(), CyberWarGroup(), GalgameGroup(),
        ]
        for grp in _sub_groups:
            try:
                sub_bot.tree.add_command(grp)
            except Exception as e:
                print(f"\u26a0\ufe0f Sub-bot \u7121\u6cd5\u8a3b\u518a\u6307\u4ee4\u7fa4\u7d44 {type(grp).__name__}: {e}")
        # /draw standalone command
        try:
            sub_bot.tree.add_command(draw_command)
        except Exception as e:
            print(f"\u26a0\ufe0f Sub-bot \u7121\u6cd5\u8a3b\u518a /draw: {e}")
        print(f"\u2133 Sub-bot \u5df2\u8a3b\u518a {len(_sub_groups)} \u500b\u6307\u4ee4\u7fa4\u7d44 + /draw")

    # \u2500\u2500 Sub-bot interaction check: only blacklist, no tier gating \u2500\u2500
    async def _sub_tree_interaction_check(interaction: discord.Interaction) -> bool:
        if interaction.user and is_blacklisted(interaction.user.id):
            try:
                await interaction.response.send_message(
                    "\U0001f6ab \u4f60\u5df2\u88ab\u5217\u5165\u9ed1\u540d\u55ae\uff0c\u7121\u6cd5\u4f7f\u7528\u6b64\u6a5f\u5668\u4eba\u7684\u4efb\u4f55\u529f\u80fd\u3002",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    sub_bot.setup_hook = _sub_setup_hook
    sub_bot.tree.interaction_check = _sub_tree_interaction_check
    print("\U0001f916 Sub-bot (Entertainment) \u5df2\u521d\u59cb\u5316\uff0c\u7b49\u5f85\u9023\u7dda\u2026")
else:
    print("\u2133 SUB_BOT_TOKEN \u672a\u8a2d\u5b9a\uff0c\u8df3\u904e\u5b50\u6a5f\u5668\u4eba\u521d\u59cb\u5316\u3002")

# Register setup_hook so discord.py calls it before connecting
# Register persistent views for AI Chat Room buttons (survives bot restarts)'''

assert old_section in content, "Could not find module loading section"
content = content.replace(old_section, new_section, 1)

# ═══ 2. Add sub-bot event handlers after persistent views ═══
old_views_end = '''if os.getenv("HOI4_ENABLED", "true").lower() not in ("false", "0", "no", "off"):
    bot.add_view(HOI4PanelView())  # HOI4 \u6230\u7565\u6307\u63ee\u90e8\u9762\u677f\u6309\u9215\u6301\u4e45\u5316

bot.setup_hook = setup_hook'''

new_views_end = '''if os.getenv("HOI4_ENABLED", "true").lower() not in ("false", "0", "no", "off"):
    bot.add_view(HOI4PanelView())  # HOI4 \u6230\u7565\u6307\u63ee\u90e8\u9762\u677f\u6309\u9215\u6301\u4e45\u5316

bot.setup_hook = setup_hook

# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# Sub-bot event handlers (only if sub_bot exists)
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
if sub_bot is not None:
    # Register persistent views on sub-bot too (so buttons work)
    for _view_cls in [TurtleSoupStartView, WerewolfSignupView, EconomyPanelButtonsView,
                      GalgamePanelView, HorseBettingView, _WerewolfResumeView, SiegePanelView]:
        try:
            sub_bot.add_view(_view_cls())
        except Exception as e:
            print(f"\u26a0\ufe0f Sub-bot persistent view {type(_view_cls()).__name__} \u8a3b\u518a\u5931\u6557: {e}")

    @sub_bot.event
    async def on_ready():
        """Sub-bot on_ready: sync commands and register guilds."""
        if not getattr(on_ready, "_sub_done", False):
            on_ready._sub_done = True
            print(f"\u2705 Sub-bot \u4e0a\u7dda\uff1a{sub_bot.user}")
            try:
                synced = await sub_bot.tree.sync()
                print(f"\u2705 Sub-bot \u5df2\u540c\u6b65 {len(synced)} \u500b slash commands")
            except Exception as e:
                print(f"\u274c Sub-bot \u540c\u6b65\u6307\u4ee4\u5931\u6557\uff1a{e}")
            for g in sub_bot.guilds:
                _is_owner = (str(g.id) == ICEA_GUILD_ID)
                register_server(g.id, g.name, is_owner_server=_is_owner)

    @sub_bot.event
    async def on_guild_join(guild):
        """Sub-bot \u52a0\u5165\u65b0\u4f3a\u670d\u5668\u6642\u81ea\u52d5\u8a3b\u518a\u3002"""
        _is_owner = (str(guild.id) == ICEA_GUILD_ID)
        register_server(guild.id, guild.name, is_owner_server=_is_owner)
        print(f"\u2133 Sub-bot \u52a0\u5165\u4f3a\u670d\u5668\uff1a{guild.name} ({guild.id})")
        try:
            ch = guild.system_channel or next(
                (c for c in guild.text_channels
                 if c.permissions_for(guild.me).send_messages), None)
            if ch:
                await ch.send(
                    "\U0001f389 **\u5a1b\u6a02\u6a5f\u5668\u4eba\u5df2\u52a0\u5165\uff01**\\n\\n"
                    "\u53ef\u7528\u529f\u80fd\uff1a\\n"
                    "\U0001f3ad \u62b6\u7b54 \u2022 \U0001f422 \u6d77\u9f9c\u6e6f \u2022 \U0001f43a \u72fc\u4eba\u6bba \u2022 \U0001f4c8 \u80a1\u5e02 \u2022 \U0001f3c7 \u8cfd\u99ac\\n"
                    "\U0001f3f0 \u653b\u57ce\u6230 \u2022 \u2694\ufe0f WW1 \u4e16\u754c\u5927\u6230 \u2022 \U0001f3ae Galgame \u2022 \U0001f3a8 \u6587\u751f\u5716\\n"
                    "\U0001f4ca \u6295\u7968 \u2022 \U0001f4c5 \u6703\u8b70/\u6392\u7a0b \u2022 \U0001f4b0 \u7d93\u6fdf\u7cfb\u7d71\\n\\n"
                    "\u8f38\u5165 `/` \u958b\u59cb\u4f7f\u7528\uff01"
                )
        except Exception:
            pass

    @sub_bot.event
    async def on_guild_remove(guild):
        """Sub-bot \u88ab\u79fb\u9664\u4f3a\u670d\u5668\u6642\u53d6\u6d88\u8a3b\u518a\u3002"""
        unregister_server(guild.id)
        print(f"\u2133 Sub-bot \u96e2\u958b\u4f3a\u670d\u5668\uff1a{guild.name} ({guild.id})")

    @sub_bot.event
    async def on_message(message):
        """Sub-bot on_message: only handle turtle soup (entertainment)."""
        if message.author.bot or not message.guild:
            return
        try:
            handled = await _handle_turtle_soup_message(message)
        except Exception as e:
            print(f"\u26a0\ufe0f Sub-bot turtle soup on_message error: {e}")'''

assert old_views_end in content, "Could not find views end section"
content = content.replace(old_views_end, new_views_end, 1)

# ═══ 3. Modify main() to run both bots ═══
old_main = '''def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("\u26a0\ufe0f  \u8acb\u8a2d\u5b9a\u74b0\u5883\u8b8a\u6578 DISCORD_BOT_TOKEN")
        return

    async def runner():
        discord.utils.setup_logging()  # preserve discord.py's default logging (normally set up by bot.run)
        loop = asyncio.get_event_loop()
        _install_shutdown_handler(loop)
        async with bot:
            await bot.start(token)

    try:
        asyncio.run(runner())
    except (KeyboardInterrupt, SystemExit):
        pass'''

new_main = '''def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("\u26a0\ufe0f  \u8acb\u8a2d\u5b9a\u74b0\u5883\u8b8a\u6578 DISCORD_BOT_TOKEN")
        return

    async def runner():
        discord.utils.setup_logging()  # preserve discord.py's default logging (normally set up by bot.run)
        loop = asyncio.get_event_loop()
        _install_shutdown_handler(loop)
        if sub_bot is not None and SUB_BOT_TOKEN_ENV:
            print("\U0001f680 \u555f\u52d5\u4e3b\u6a5f\u5668\u4eba + \u5b50\u6a5f\u5668\u4eba\u2026")
            await asyncio.gather(
                bot.start(token),
                sub_bot.start(SUB_BOT_TOKEN_ENV),
            )
        else:
            async with bot:
                await bot.start(token)

    try:
        asyncio.run(runner())
    except (KeyboardInterrupt, SystemExit):
        pass'''

assert old_main in content, "Could not find main() function"
content = content.replace(old_main, new_main, 1)

with open('discord_borda_poll.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("OK: Sub-bot architecture implemented")
