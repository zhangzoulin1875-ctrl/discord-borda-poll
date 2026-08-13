#!/usr/bin/env python3
"""
ICEA Discord Bot — Base Framework
===================================
A clean starting point for the rewritten bot.

Architecture:
  - Single main file (discord_borda_poll.py) — bot init, event loop, shared state
  - modules/ — feature modules loaded at startup
  - data/ — JSON persistence (auto-created)

Design Principles:
  1. Every async handler has try/except — no silent crashes
  2. All data persists to data/*.json with atomic writes
  3. All times are GMT+8 (Asia/Taipei)
  4. ephemeral messages for personal interactions
  5. Owner-only management commands (Discord ID in env or hardcoded)
  6. Supports Render Web Service mode (built-in HTTP keep-alive server)
"""

import asyncio
import json
import os
import sys
import base64
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

import discord
from discord import app_commands
from aiohttp import web
import aiohttp

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

# ─── GitHub Persistence Config ───────────────────────────────────────────
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "zhangzoulin1875-ctrl/discord-borda-poll")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
_github_file_shas = {}
# Files to persist to GitHub (settings only - records are rebuildable from Discord)
_PERSIST_FILES = {
    "proposal_settings.json",
    "application_settings.json",
    "polls.json",
    "ticket_settings.json",
    "tickets.json",
}

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
    """Atomic JSON write to data/ directory + GitHub sync."""
    path = DATA_DIR / filename
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    # Async push to GitHub (non-blocking)
    if GITHUB_TOKEN and filename in _PERSIST_FILES:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(github_push_json(filename, data))
        except Exception:
            pass


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


# ─── GitHub Persistence (replaces Google Drive) ──────────────────────────
def _github_api_url(filename: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{filename}"


def _github_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_pull_json(filename: str):
    """Sync read JSON settings from GitHub (called at startup)."""
    if not GITHUB_TOKEN:
        return None
    try:
        url = _github_api_url(filename) + f"?ref={GITHUB_BRANCH}"
        req = urllib.request.Request(url, headers=_github_headers(), method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
            sha = data.get("sha", "")
            content_b64 = data.get("content", "")
            if sha:
                _github_file_shas[filename] = sha
            if not content_b64:
                return None
            decoded = base64.b64decode(content_b64).decode("utf-8")
            return json.loads(decoded)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"\U0001F4C2 GitHub: {filename} not found (first deploy)")
        else:
            print(f"\u26a0\ufe0f GitHub pull {filename} failed (HTTP {e.code})")
        return None
    except Exception as e:
        print(f"\u26a0\ufe0f GitHub pull {filename} failed: {e}")
        return None


def github_pull_all():
    """Pull all persisted settings from GitHub at startup."""
    if not GITHUB_TOKEN:
        print("\u2139\ufe0f GITHUB_TOKEN not set, skipping GitHub persistence")
        return
    print(f"\U0001F504 Syncing settings from GitHub ({GITHUB_REPO})...")
    pulled = 0
    for filename in _PERSIST_FILES:
        data = github_pull_json(filename)
        if data is not None:
            path = DATA_DIR / filename
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  \u2705 Pulled {filename} from GitHub")
            pulled += 1
    print(f"\U0001F504 GitHub sync complete: {pulled} files")


async def github_push_json(filename: str, data) -> None:
    """Async push JSON settings to GitHub (called after save_json)."""
    if not GITHUB_TOKEN or filename not in _PERSIST_FILES:
        return
    try:
        content_str = json.dumps(data, ensure_ascii=False, indent=2)
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("ascii")
        body_obj = {
            "message": f"Auto-sync {filename}",
            "content": content_b64,
            "branch": GITHUB_BRANCH,
        }
        sha = _github_file_shas.get(filename)
        if sha:
            body_obj["sha"] = sha
        body = json.dumps(body_obj).encode("utf-8")

        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.put(
                _github_api_url(filename),
                data=body,
                headers=_github_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (200, 201):
                    resp_data = await resp.json()
                    new_sha = resp_data.get("content", {}).get("sha", "")
                    if new_sha:
                        _github_file_shas[filename] = new_sha
                    print(f"\u2705 GitHub push {filename} success")
                else:
                    err_text = await resp.text()
                    print(f"\u26a0\ufe0f GitHub push {filename} failed (HTTP {resp.status}): {err_text[:200]}")
    except Exception as e:
        print(f"\u26a0\ufe0f GitHub push {filename} failed: {e}")


# ─── Keep-Alive HTTP Server (Render Web Service mode) ────────────────────────
async def keep_alive_server():
    """啟動 HTTP keep-alive server（Render Web Service 用）。
    Render 的 Web Service 要求綁定一個 port，否則會一直掃描。
    這個小 server 只回 200 OK，讓 Render 認為服務正常。
    """
    port = int(os.getenv("PORT", 10000))

    async def health(request):
        return web.Response(text="Bot is running ✅", status=200)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Keep-alive HTTP server started on port {port}")


# ─── Self-Ping Loop (prevent Render free tier sleep) ────────────────────────
async def self_ping_loop():
    """Every ~4.5 min, ping our own /health endpoint to prevent Render from
    spinning down the free-tier Web Service after 15 min of inactivity.

    Uses SELF_URL or RENDER_EXTERNAL_URL env var (Render auto-injects the latter).
    """
    await asyncio.sleep(30)  # wait for bot to be fully online
    base_url = os.getenv("SELF_URL") or os.getenv("RENDER_EXTERNAL_URL") or ""
    if not base_url:
        print("ℹ️ SELF_URL not set, self-ping disabled")
        print("ℹ️ Add SELF_URL=https://your-service.onrender.com in Render env vars")
        return
    health_url = base_url.rstrip("/") + "/health"
    print(f"🔁 Self-ping started: {health_url}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    health_url,
                    headers={"User-Agent": "SelfPing/1.0"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    pass  # status doesn't matter, just need to wake it
            except Exception:
                pass  # silently ignore, will retry
            await asyncio.sleep(270)  # 4.5 min


# ─── Module Loader ──────────────────────────────────────────────────────────
_bot_globals = {}


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
            exec(compile(code, str(mod_file), "exec"), _bot_globals)
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

    # Start self-ping loop (prevent Render free tier sleep)
    asyncio.ensure_future(self_ping_loop())

    # ── 模組 on_ready 掛鉤：每個模組可各自註冊一個啟動後要跑的函式，
    # 全部收集在 _bot_ready_hooks 清單裡（避免多個模組用同名函式互相覆蓋）──
    for hook in _bot_globals.get("_bot_ready_hooks", []):
        try:
            asyncio.ensure_future(hook())
        except Exception as e:
            print(f"⚠️ on_ready 掛鉤 {getattr(hook, '__name__', hook)} 錯誤：{e}")


@bot.event
async def on_message(message: discord.Message):
    """Central message handler — dispatches to module hooks."""
    if message.author.bot or message.author == bot.user:
        return

    # ── 提案區偵測 ──
    handler = _bot_globals.get("handle_proposal_message")
    if handler:
        try:
            await handler(message)
        except Exception as e:
            print(f"⚠️ handle_proposal_message 錯誤：{e}")

    # ── 入盟申請區偵測 ──
    handler = _bot_globals.get("handle_application_message")
    if handler:
        try:
            await handler(message)
        except Exception as e:
            print(f"⚠️ handle_application_message 錯誤：{e}")

    # ── 投票回覆偵測 ──
    handler = _bot_globals.get("handle_poll_message")
    if handler:
        try:
            await handler(message)
        except Exception as e:
            print(f"⚠️ handle_poll_message 錯誤：{e}")


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Detect edits to application posts and re-check them."""
    if after.author.bot:
        return
    handler = _bot_globals.get("handle_application_edit")
    if handler:
        try:
            await handler(before, after)
        except Exception as e:
            print(f"⚠️ handle_application_edit 錯誤：{e}")


@bot.event
async def on_thread_create(thread: discord.Thread):
    """Detect new forum threads in proposal/application channels."""
    handler = _bot_globals.get("handle_thread_create")
    if handler:
        try:
            await handler(thread)
        except Exception as e:
            print(f"⚠️ handle_thread_create 錯誤：{e}")


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
    _bot_globals.update({
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
        "asyncio": asyncio,
        "github_push_json": github_push_json,
        "_bot_ready_hooks": [],
    })

    # Pull persisted settings from GitHub (replaces Google Drive)
    github_pull_all()

    # Load feature modules
    load_modules()

    # Register any command groups that modules created
    for name, obj in list(_bot_globals.items()):
        if isinstance(obj, app_commands.Group):
            try:
                tree.add_command(obj)
                print(f"📝 已註冊指令群組：/{obj.name}")
            except Exception as e:
                print(f"⚠️ 註冊指令群組 {name} 失敗：{e}")

    # Start keep-alive HTTP server (for Render Web Service)
    try:
        await keep_alive_server()
    except Exception as e:
        print(f"⚠️ Keep-alive server 啟動失敗（不影響 bot 運行）：{e}")

    # Start bot
    async with bot:
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
