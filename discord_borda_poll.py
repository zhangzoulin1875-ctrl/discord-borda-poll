#!/usr/bin/env python3
"""
ICEA Discord Bot — Base Framework
===================================
A clean starting point for the rewritten bot.

Architecture:
  - Single main file (discord_borda_poll.py) — bot init, event loop, shared state
  - modules/ — feature modules loaded at startup (empty for now)
  - data/ — JSON persistence (auto-created)
  - Dashboard (dashboard.html) — web management panel (TODO)

Design Principles:
  1. Every async handler has try/except — no silent crashes
  2. All data persists to data/*.json with atomic writes
  3. All times are GMT+8 (Asia/Taipei)
  4. ephemeral messages for personal interactions
  5. Owner-only management commands (Discord ID in env or hardcoded)
  6. AI calls use full fallback chain — never fail silently
"""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands

# ─── Line-buffered stdout (critical on Render — non-TTY blocks by default) ───
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# ─── Constants ────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "1482256878334640209"))
TZ_TAIPEI = timezone(timedelta(hours=8))
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─── Bot Instance ──────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ─── Utilities ──────────────────────────────────────────────────────────────
def now_str() -> str:
    """Current time in GMT+8, human-readable."""
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")


def save_json(filename: str, data) -> None:
    """Atomic JSON write to data/ directory."""
    path = DATA_DIR / filename
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_json(filename: str, default=None):
    """Load JSON from data/ directory, return default if not found."""
    path = DATA_DIR / filename
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 載入 {filename} 失敗：{e}")
        return default


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID


# ─── Module Loader ──────────────────────────────────────────────────────────
def load_modules():
    """Execute all modules/NNN_*.py files into the bot's namespace.
    Each module can register commands, event handlers, etc.
    Modules are loaded in alphabetical order.
    """
    modules_dir = Path(__file__).parent / "modules"
    if not modules_dir.exists():
        print("ℹ️ modules/ 目錄不存在，略過模組載入")
        return

    for mod_file in sorted(modules_dir.glob("[0-9][0-9][0-9]_*.py")):
        try:
            with open(mod_file, "r", encoding="utf-8") as f:
                code = f.read()
            exec(compile(code, str(mod_file), "exec"), bot._bot_globals)
            print(f"✅ 已載入模組：{mod_file.name}")
        except Exception as e:
            print(f"❌ 載入模組 {mod_file.name} 失敗：{e}")
            traceback.print_exc()


# ─── Basic Slash Commands ───────────────────────────────────────────────────
@tree.command(name="ping", description="檢查機器人是否在線")
async def ping_command(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(
        f"🏓 Pong！延遲 {latency_ms}ms\n🕒 {now_str()}",
        ephemeral=True,
    )


@tree.command(name="status", description="查看機器人狀態（機器人擁有者限定）")
async def status_command(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
        return

    embed = discord.Embed(
        title="🤖 機器人狀態",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="延遲", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="伺服器數", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="時間", value=now_str(), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── Event Handlers ─────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"═══════════════════════════════════════════════════")
    print(f"✅ 機器人已上線：{bot.user} (ID: {bot.user.id})")
    print(f"🕒 啟動時間：{now_str()} (GMT+8)")
    print(f"📋 已加入 {len(bot.guilds)} 個伺服器")
    print(f"═══════════════════════════════════════════════════")

    # Sync slash commands
    try:
        synced = await tree.sync()
        print(f"🔄 已同步 {len(synced)} 個指令")
    except Exception as e:
        print(f"⚠️ 指令同步失敗：{e}")

    # Set nickname to ICEA official
    for guild in bot.guilds:
        try:
            member = guild.get_member(bot.user.id)
            if member and member.nick != "ICEA official":
                await member.edit(nick="ICEA official")
        except Exception:
            pass


@bot.event
async def on_message(message: discord.Message):
    """Central message handler — modules can hook into this via bot._event_hooks."""
    if message.author.bot or message.author == bot.user:
        return
    # Modules will register their own message handlers here
    # For now this is a placeholder


@bot.event
async def on_error(event_name: str, *args, **kwargs):
    print(f"⚠️ 事件 '{event_name}' 發生例外：")
    traceback.print_exc()


# ─── Main Entry ──────────────────────────────────────────────────────────────
async def main():
    if not BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN 未設定")
        sys.exit(1)

    # Store globals for module access
    bot._bot_globals = {
        "bot": bot,
        "tree": tree,
        "save_json": save_json,
        "load_json": load_json,
        "is_owner": is_owner,
        "now_str": now_str,
        "OWNER_ID": OWNER_ID,
        "TZ_TAIPEI": TZ_TAIPEI,
        "DATA_DIR": DATA_DIR,
        "discord": discord,
        "app_commands": app_commands,
    }

    # Load feature modules
    load_modules()

    # Start bot
    async with bot:
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
