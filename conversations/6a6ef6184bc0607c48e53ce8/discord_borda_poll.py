"""
投票系統 — Discord Bot (discord.py)

支援兩種投票模式：
  - borda  波達計數法（排序偏好投票）
  - simple 一般投票（單選，點按鈕投票）

部署到 Render 免費方案：
  - 建議使用 Background Worker（24/7 不休眠，不需 keep-alive）
  - 也支援 Web Service 模式（內建 HTTP keep-alive server）

環境變數：
  DISCORD_BOT_TOKEN  - Discord bot token（必須）
  PORT               - HTTP server port（Render 自動注入，預設 10000）
  RENDER_EXTERNAL_URL - Render 自動注入的公開 URL（用於 self-ping）
  OAUTH_CLIENT_ID     - Discord App 的 Client ID（OAuth2 用）
  OAUTH_CLIENT_SECRET - Discord App 的 Client Secret（OAuth2 用）
  OAUTH_REDIRECT_URI  - OAuth 回調 URL，例如 https://你的服務.onrender.com/callback
  AI_API_URL    - AI API 端點（預設 OpenAI: https://api.openai.com/v1/chat/completions）
  AI_API_KEY    - AI API 金鑰（也可在 Dashboard 中設定）
  AI_MODEL      - AI 模型名稱（預設 gpt-4o-mini，也可在 Dashboard 中設定）
  AI_SYSTEM_PROMPT - AI 系統提示詞（預設為會議紀錄整理格式）
  COOKIE_SECRET   - Session 簽名密鑰（不設則每次重啟隨機生成，建議固定設定）

  快報/公報設定存於 data/briefing_settings.json
  快報/公報指令：
    /briefing daily_set <time> <channel>  - 設定每日自動快報
    /briefing daily_off                    - 關閉每日自動快報
    /briefing daily_now [channel]          - 立即生成每日快報
    /briefing weekly_set <day> <time> <channel> - 設定每週自動公報
    /briefing weekly_off                   - 關閉每週自動公報
    /briefing weekly_now [channel]         - 立即生成每週公報
    /briefing status                       - 查看設定

  注意：message_content intent 必須在 Discord Developer Portal 中啟用
  持久化：投票資料存於 data/polls_data.json，建議在 Render 掛載 Persistent Disk 以跨部署保留

指令一覽：
  /poll create <title> [mode]     建立新投票（管理員限定）
  /poll add <poll_id> <option>    新增選項到指定投票（管理員限定）
  /poll list [poll_id]            查看投票清單或指定投票的選項
  /poll start <poll_id>           啟動指定投票（管理員限定）
  /poll end <poll_id>             結束指定投票並顯示結果（管理員限定）
  /poll delete <poll_id>          刪除指定投票（管理員限定）
  /poll vote <poll_id>            投票（一般成員，依投票模式自動判斷）
  /poll manage                    開啟投票管理面板（管理員限定）
                                  管理面板內可設定身分組限制

波達計數法：n 個選項中，第 1 名得 n-1 分，…，最後一名得 0 分。
一般投票：每人一票，最高票獲勝。
"""

import discord
from discord import app_commands
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import asyncio
import os
import urllib.request
import urllib.parse
import secrets as py_secrets
import aiohttp
import random
import string
import re
import hmac
import hashlib
import json as json_module
from datetime import datetime, timedelta
from aiohttp import web


# ──────────────────────────────────────────────
# Keep-Alive HTTP Server
# ──────────────────────────────────────────────

async def self_ping_loop():
    """每 5 分鐘 self-ping 一次，防止 Render 休眠。"""
    await asyncio.sleep(30)  # 等 bot 完全上線後再開始
    # 支援多種環境變數名稱（SELF_URL 優先，其次 RENDER_EXTERNAL_URL）
    base_url = (
        os.getenv("SELF_URL") or
        os.getenv("RENDER_EXTERNAL_URL") or
        ""
    )
    if not base_url:
        print("ℹ️  未設定 SELF_URL，self-ping 停用。")
        print("ℹ️  請到 Render Environment 新增：SELF_URL = https://你的服務名.onrender.com")
        return
    health_url = base_url.rstrip("/") + "/health"
    print(f"🔁 Self-ping 已啟動，目標：{health_url}")
    while True:
        try:
            req = urllib.request.Request(health_url, headers={"User-Agent": "SelfPing/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"🏓 Self-ping OK ({resp.status})")
        except Exception as e:
            print(f"⚠️  Self-ping 失敗：{e}")
        await asyncio.sleep(270)  # 每 4.5 分鐘 ping 一次（Render 15分鐘休眠，保留充裕緩衝）


async def keep_alive_server():
    """啟動 HTTP keep-alive server（Render Web Service 用）。"""
    port = int(os.getenv("PORT", 10000))

    async def health(request):
        return web.Response(text="Bot is running ✅", status=200)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    # AI settings API
    app.router.add_get("/api/ai-settings", api_get_ai_settings)
    app.router.add_put("/api/ai-settings", api_set_ai_settings)
    # Dashboard routes
    app.router.add_get("/dashboard", dashboard_index)
    app.router.add_get("/login", dashboard_login)
    app.router.add_get("/callback", dashboard_callback)
    app.router.add_post("/logout", dashboard_logout)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/guilds", api_guilds)
    app.router.add_get("/api/guilds/{gid}/polls", api_polls)
    app.router.add_get("/api/guilds/{gid}/polls/{pid}", api_poll_detail)
    app.router.add_post("/api/guilds/{gid}/polls", api_create_poll)
    app.router.add_post("/api/guilds/{gid}/polls/{pid}/start", api_start_poll)
    app.router.add_post("/api/guilds/{gid}/polls/{pid}/end", api_end_poll)
    app.router.add_delete("/api/guilds/{gid}/polls/{pid}", api_delete_poll)
    app.router.add_post("/api/guilds/{gid}/polls/{pid}/options", api_add_option)
    app.router.add_put("/api/guilds/{gid}/polls/{pid}/roles", api_set_roles)
    print(f"📊 Dashboard routes registered")


    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Keep-alive HTTP server started on port {port}")



# ──────────────────────────────────────────────
# Dashboard: OAuth2 & Session
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# Dashboard: OAuth2 & Session (signed cookies - survives restarts/redeploys)
# ──────────────────────────────────────────────

COOKIE_SECRET = os.getenv("COOKIE_SECRET", py_secrets.token_urlsafe(32))

OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")


def _create_signed_cookie(data: dict) -> str:
    """Create an HMAC-signed cookie containing user data."""
    payload = __import__("base64").b64encode(json_module.dumps(data).encode()).decode()
    sig = hmac.new(COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_signed_cookie(cookie: str) -> dict:
    """Verify and decode a signed cookie. Returns None if invalid."""
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = hmac.new(COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json_module.loads(__import__("base64").b64decode(payload))
    except Exception:
        return None


def _read_dashboard_html():
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>dashboard.html not found</h1>"


async def _get_session_user(request):
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    return _verify_signed_cookie(cookie)


def _is_guild_admin(guild_entry):
    perms = int(guild_entry.get("permissions", 0))
    return bool(perms & 0x8) or bool(perms & 0x20)


def _poll_to_dict(poll):
    return {
        "poll_id": poll.poll_id,
        "title": poll.title,
        "mode": poll.mode,
        "status": poll.status,
        "description": getattr(poll, "description", ""),
        "vote_count": poll.vote_count(),
        "option_count": poll.option_count(),
        "allowed_roles": poll.allowed_roles,
        "options": [{"text": opt.text} for opt in poll.options],
        "votes": {str(uid): v for uid, v in poll.votes.items()},
    }


async def dashboard_index(request):
    return web.Response(text=_read_dashboard_html(), content_type="text/html")


async def dashboard_login(request):
    if not OAUTH_CLIENT_ID or not OAUTH_REDIRECT_URI:
        return web.Response(text="OAuth not configured. Set OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI", status=500)
    url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={OAUTH_CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(OAUTH_REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20guilds"
    )
    return web.HTTPFound(url)


async def dashboard_callback(request):
    code = request.query.get("code")
    if not code:
        return web.Response(text="Missing code", status=400)
    data = {
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI,
    }
    async with aiohttp.ClientSession() as sess:
        async with sess.post("https://discord.com/api/oauth2/token", data=data) as resp:
            td = await resp.json()
        if "access_token" not in td:
            return web.Response(text="OAuth failed", status=400)
        tk = td["access_token"]
        h = {"Authorization": f"Bearer {tk}"}
        async with sess.get("https://discord.com/api/users/@me", headers=h) as resp:
            u = await resp.json()
        async with sess.get("https://discord.com/api/users/@me/guilds", headers=h) as resp:
            g = await resp.json()
    # Store only essential data in cookie (keep it small - browsers limit ~4KB)
    user_data = {
        "user_id": u.get("id", ""),
        "username": u.get("username", "unknown"),
        "avatar": u.get("avatar"),
        "access_token": tk,
    }
    signed = _create_signed_cookie(user_data)
    r = web.HTTPFound("/dashboard")
    r.set_cookie("session", signed, httponly=True, samesite="Lax", max_age=86400 * 7)
    return r


async def dashboard_logout(request):
    r = web.HTTPFound("/dashboard")
    r.del_cookie("session")
    return r


async def _fetch_guilds(access_token):
    """Fetch user's guilds from Discord API."""
    try:
        h = {"Authorization": f"Bearer {access_token}"}
        async with aiohttp.ClientSession() as sess:
            async with sess.get("https://discord.com/api/users/@me/guilds", headers=h) as resp:
                g = await resp.json()
                return g if isinstance(g, list) else []
    except Exception:
        return []


async def api_me(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    av = f"https://cdn.discordapp.com/avatars/{user['user_id']}/{user['avatar']}.png" if user.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"
    guilds = await _fetch_guilds(user["access_token"])
    ag = [g for g in guilds if _is_guild_admin(g)]
    return web.json_response({"username": user["username"], "avatar_url": av, "admin_guild_count": len(ag)})


async def api_guilds(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    guilds = await _fetch_guilds(user["access_token"])
    out = []
    for g in guilds:
        if _is_guild_admin(g):
            ic = f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g.get("icon") else None
            out.append({"id": g["id"], "name": g["name"], "icon_url": ic})
    return web.json_response(out)


async def api_polls(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    polls = guild_polls.get(gid, {})
    return web.json_response([_poll_to_dict(p) for p in polls.values()])


async def api_poll_detail(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    poll = get_poll(gid, request.match_info["pid"])
    if not poll:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_poll_to_dict(poll))


async def api_create_poll(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    body = await request.json()
    import uuid
    pid = str(uuid.uuid4())[:8]
    polls = guild_polls.setdefault(gid, {})
    poll = Poll(poll_id=pid, title=body.get("title", ""), mode=body.get("mode", "borda"))
    poll.options = [PollOption(text=o.strip()) for o in body.get("options", []) if o.strip()]
    poll.status = "drafting"
    polls[pid] = poll
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))


async def api_start_poll(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    poll = get_poll(gid, request.match_info["pid"])
    if not poll:
        return web.json_response({"error": "not found"}, status=404)
    if poll.status != "drafting":
        return web.json_response({"error": "not drafting"}, status=400)
    if poll.option_count() < 2:
        return web.json_response({"error": "need 2+ options"}, status=400)
    poll.status = "active"
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))


async def api_end_poll(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    poll = get_poll(gid, request.match_info["pid"])
    if not poll:
        return web.json_response({"error": "not found"}, status=404)
    if poll.status != "active":
        return web.json_response({"error": "not active"}, status=400)
    poll.status = "ended"
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))


async def api_delete_poll(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    polls = guild_polls.get(gid, {})
    if request.match_info["pid"] not in polls:
        return web.json_response({"error": "not found"}, status=404)
    del polls[request.match_info["pid"]]
    save_polls_to_disk()
    return web.json_response({"ok": True})


async def api_add_option(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    poll = get_poll(gid, request.match_info["pid"])
    if not poll:
        return web.json_response({"error": "not found"}, status=404)
    if poll.status != "drafting":
        return web.json_response({"error": "not drafting"}, status=400)
    if poll.option_count() >= 25:
        return web.json_response({"error": "max 25"}, status=400)
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "empty text"}, status=400)
    poll.options.append(PollOption(text=text))
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))


async def api_set_roles(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid = int(request.match_info["gid"])
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ge or not _is_guild_admin(ge):
        return web.json_response({"error": "forbidden"}, status=403)
    poll = get_poll(gid, request.match_info["pid"])
    if not poll:
        return web.json_response({"error": "not found"}, status=404)
    body = await request.json()
    poll.allowed_roles = [int(r) for r in body.get("role_ids", [])]
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))


# ──────────────────────────────────────────────
# AI 會議紀錄設定
# ──────────────────────────────────────────────

DEFAULT_AI_SYSTEM_PROMPT = """你是一個專業的議會會議紀錄整理助手。請根據以下 Discord 頻道的對話紀錄，整理出結構化的會議紀錄。

格式要求：
## 會議資訊
- 日期時間
- 頻道
- 出席人員（列出所有發言者）

## 討論議題
按時間順序列出討論的議題，每個議題用標題標示

## 各議題重點
每個議題下列出各成員的發言摘要，標明發言者用戶名

## 動議與提案
列出所有提出的動議

## 投票結果（如有）
列出投票的項目和結果

## 結論與決議
列出會議的結論和後續事項

請用繁體中文輸出，保持客觀中立的語氣。如果對話中沒有明確的議題分界，請根據內容自動歸類。只整理有意義的討論內容，忽略閒聊和系統訊息。"""

DAILY_BRIEFING_PROMPT = """你是一個微國家組織的每日快報整理助手。請根據以下 Discord 伺服器過去 24 小時所有頻道的對話紀錄，整理出一份簡潔的每日快報。

格式：
## 📰 每日快報

### 📋 重要事項
列出最重要的 2-3 件事

### 💬 各頻道摘要
每個有討論的頻道 1-2 句摘要

### 📊 投票動態
進行中或剛結束的投票（如有）

### 📌 待辦事項
提到的待辦或未完成事項（如有）

用繁體中文，簡潔有力。只整理有意義的討論，忽略閒聊和系統訊息。如果某個區塊沒有內容就省略。"""

WEEKLY_BULLETIN_PROMPT = """你是一個微國家組織的每週公報整理助手。請根據以下 Discord 伺服器過去 7 天所有頻道的對話紀錄，整理出一份結構化的每週公報。

格式：
## 📰 每週公報

### 📋 本週大事
本週最重要的 3-5 件事

### 💬 各頻道摘要
每個頻道的主要討論和決策

### 📊 投票與決議
本週所有投票結果（如有）

### 📌 下週待辦
預計討論或處理的事項（如有）

用繁體中文，正式但不失親切。只整理有意義的內容，忽略閒聊。如果某個區塊沒有內容就省略。"""

WEEKDAY_NAMES = {0: "週一", 1: "週二", 2: "週三", 3: "週四", 4: "週五", 5: "週六", 6: "週日"}

# 快報/公報設定（持久化到磁碟）
briefing_settings = {
    "daily_enabled": False,
    "daily_time": "23:00",
    "daily_channel_id": None,
    "weekly_enabled": False,
    "weekly_day": 6,  # 0=週一, 6=週日
    "weekly_time": "23:00",
    "weekly_channel_id": None,
}

BRIEFING_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "briefing_settings.json")


def save_briefing_settings():
    try:
        os.makedirs(os.path.dirname(BRIEFING_DATA_FILE), exist_ok=True)
        with open(BRIEFING_DATA_FILE, "w", encoding="utf-8") as f:
            json_module.dump(briefing_settings, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Failed to save briefing settings: {e}")


def load_briefing_settings():
    global briefing_settings
    try:
        if os.path.exists(BRIEFING_DATA_FILE):
            with open(BRIEFING_DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
            briefing_settings.update(loaded)
            print("✅ 載入快報設定")
    except Exception as e:
        print(f"⚠️ Failed to load briefing settings: {e}")


async def collect_all_messages(hours: int, max_per_channel: int = 80) -> str:
    """Collect messages from all text channels in all guilds."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    sections = []

    for guild in bot.guilds:
        for channel in guild.text_channels:
            try:
                channel_msgs = []
                async for msg in channel.history(after=cutoff, limit=max_per_channel):
                    if msg.author.bot:
                        continue
                    text = msg.content.strip()
                    if not text:
                        if msg.attachments:
                            text = f"[附件 x{len(msg.attachments)}]"
                        elif msg.embeds:
                            text = f"[嵌入訊息]"
                        else:
                            continue
                    if len(text) > 300:
                        text = text[:300] + "..."
                    time_str = msg.created_at.strftime("%m/%d %H:%M")
                    name = msg.author.display_name
                    channel_msgs.append(f"[{time_str}] {name}: {text}")

                if channel_msgs:
                    sections.append(f"### #{channel.name} ({len(channel_msgs)} 則)")
                    sections.extend(channel_msgs)
            except (discord.Forbidden, discord.HTTPException):
                continue

    return "\n".join(sections)


async def run_briefing(target_channel: discord.TextChannel, hours: int, mode: str):
    """Collect all server messages and generate a briefing with AI streaming."""
    is_daily = mode == "daily"
    title = "📰 每日快報" if is_daily else "📰 每週公報"
    prompt = DAILY_BRIEFING_PROMPT if is_daily else WEEKLY_BULLETIN_PROMPT

    # Send initial message
    live_msg = await target_channel.send(f"{title}\n📝 正在收集各頻道訊息...")

    # Collect messages
    log_text = await collect_all_messages(hours=hours, max_per_channel=100 if is_daily else 150)

    if not log_text.strip():
        await live_msg.edit(content=f"{title}\n📭 指定時間內未找到任何訊息。")
        return

    if len(log_text) > 30000:
        log_text = log_text[:30000] + "\n...（後續訊息已截斷）"

    # Use a custom system prompt for briefings
    settings = dict(ai_settings)
    settings["system_prompt"] = prompt

    await live_msg.edit(content=f"{title}\n📝 收集完成，AI 開始生成{'每日快報' if is_daily else '每週公報'}...")

    accumulated = ""
    last_edit = 0.0
    import time as _time

    try:
        async for chunk in call_ai_api_stream(log_text, settings):
            accumulated += chunk
            now = _time.time()
            if now - last_edit >= 1.5:
                last_edit = now
                header = f"{title}\n"
                display = header + accumulated
                if len(display) > 1950:
                    max_body = 1950 - len(header) - 5
                    display = header + accumulated[:max_body] + "\n⏳..."
                try:
                    await live_msg.edit(content=display)
                except Exception:
                    pass

        # Final output
        full_text = f"{title}\n" + accumulated
        if len(full_text) <= 2000:
            await live_msg.edit(content=full_text)
        else:
            import io
            await live_msg.edit(content=f"{title}\n✅ 已生成（完整內容見下方附件）")
            file_content = f"# {title}\n# 生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n# 涵蓋範圍：過去 {hours} 小時\n\n---\n\n{accumulated}"
            file = discord.File(
                io.BytesIO(file_content.encode("utf-8")),
                filename=f"{'daily_briefing' if is_daily else 'weekly_bulletin'}_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
            )
            await target_channel.send(file=file)

    except Exception as e:
        try:
            await live_msg.edit(content=f"{title}\n❌ AI 整理失敗：{e}")
        except Exception:
            await target_channel.send(f"{title}\n❌ AI 整理失敗：{e}")


async def daily_briefing_scheduler():
    """Background task: run daily briefing at scheduled time."""
    last_run_date = None
    while True:
        await asyncio.sleep(60)
        if not briefing_settings["daily_enabled"]:
            last_run_date = None
            continue
        now = datetime.now()
        if now.strftime("%H:%M") == briefing_settings.get("daily_time", "23:00"):
            today_key = now.date().isoformat()
            if today_key != last_run_date:
                last_run_date = today_key
                channel_id = briefing_settings.get("daily_channel_id")
                if channel_id:
                    channel = bot.get_channel(channel_id)
                    if channel:
                        print(f"⏰ 自動執行每日快報...")
                        await run_briefing(channel, hours=24, mode="daily")
                        print(f"✅ 每日快報完成")


async def weekly_briefing_scheduler():
    """Background task: run weekly bulletin at scheduled day+time."""
    last_run_date = None
    while True:
        await asyncio.sleep(60)
        if not briefing_settings["weekly_enabled"]:
            last_run_date = None
            continue
        now = datetime.now()
        target_day = int(briefing_settings.get("weekly_day", 6))
        if now.weekday() == target_day and now.strftime("%H:%M") == briefing_settings.get("weekly_time", "23:00"):
            today_key = now.date().isoformat()
            if today_key != last_run_date:
                last_run_date = today_key
                channel_id = briefing_settings.get("weekly_channel_id")
                if channel_id:
                    channel = bot.get_channel(channel_id)
                    if channel:
                        print(f"⏰ 自動執行每週公報...")
                        await run_briefing(channel, hours=168, mode="weekly")
                        print(f"✅ 每週公報完成")


ai_settings = {
    "api_url": os.getenv("AI_API_URL", "https://api.openai.com/v1/chat/completions"),
    "api_key": os.getenv("AI_API_KEY", ""),
    "model": os.getenv("AI_MODEL", "gpt-4o-mini"),
    "system_prompt": os.getenv("AI_SYSTEM_PROMPT", DEFAULT_AI_SYSTEM_PROMPT),
}


def parse_since(since_str: str):
    """Parse a time string and return UTC datetime."""
    since_str = since_str.strip().lower()
    now_utc = datetime.utcnow()

    # "Nh" or "Nhours"
    m = re.match(r'^(\d+(?:\.\d+)?)\s*h(?:ours?)?$', since_str)
    if m:
        return now_utc - timedelta(hours=float(m.group(1)))

    # "Nm" or "Nmin" or "Nminutes"
    m = re.match(r'^(\d+(?:\.\d+)?)\s*m(?:in(?:utes?)?)?$', since_str)
    if m:
        return now_utc - timedelta(minutes=float(m.group(1)))

    # "NhNm" like "1h30m"
    m = re.match(r'^(?:(\d+)h)?(?:(\d+)m)?$', since_str)
    if m and (m.group(1) or m.group(2)):
        h = int(m.group(1) or 0)
        mi = int(m.group(2) or 0)
        return now_utc - timedelta(hours=h, minutes=mi)

    # "HH:MM" (assume UTC+8)
    try:
        t = datetime.strptime(since_str, "%H:%M")
        target = now_utc.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        target -= timedelta(hours=8)
        if target > now_utc:
            target -= timedelta(days=1)
        return target
    except ValueError:
        pass

    # "YYYY-MM-DD"
    try:
        d = datetime.strptime(since_str, "%Y-%m-%d")
        return d - timedelta(hours=8)
    except ValueError:
        pass

    # "YYYY-MM-DD HH:MM"
    try:
        d = datetime.strptime(since_str, "%Y-%m-%d %H:%M")
        return d - timedelta(hours=8)
    except ValueError:
        pass

    return None


async def call_ai_api(conversation: str, settings: dict) -> str:
    """Call an OpenAI-compatible API to summarize the conversation (streaming)."""
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.get("model", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": settings.get("system_prompt", DEFAULT_AI_SYSTEM_PROMPT)},
            {"role": "user", "content": conversation},
        ],
        "temperature": 0.3,
        "stream": True,
    }
    # Use streaming to avoid long silent waits — collect chunks as they arrive
    timeout = aiohttp.ClientTimeout(total=300, connect=15, sock_read=60)
    result_chunks = []
    async with aiohttp.ClientSession() as session:
        async with session.post(settings["api_url"], json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(f"AI API returned {resp.status}: {error_text[:500]}")
            # Read SSE stream
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json_module.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        result_chunks.append(delta["content"])
                except Exception:
                    continue
    if not result_chunks:
        raise Exception("AI API returned empty response")
    return "".join(result_chunks)


async def call_ai_api_stream(conversation: str, settings: dict):
    """Async generator: yields text chunks from AI API as they stream in."""
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.get("model", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": settings.get("system_prompt", DEFAULT_AI_SYSTEM_PROMPT)},
            {"role": "user", "content": conversation},
        ],
        "temperature": 0.3,
        "stream": True,
    }
    timeout = aiohttp.ClientTimeout(total=300, connect=15, sock_read=90)
    async with aiohttp.ClientSession() as session:
        async with session.post(settings["api_url"], json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(f"AI API returned {resp.status}: {error_text[:500]}")
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json_module.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        yield delta["content"]
                except Exception:
                    continue


async def api_get_ai_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    key = ai_settings["api_key"]
    masked = key[:6] + "..." + key[-4:] if len(key) > 10 else ("***" if key else "")
    return web.json_response({
        "api_url": ai_settings["api_url"],
        "api_key_masked": masked,
        "has_key": bool(key),
        "model": ai_settings["model"],
        "system_prompt": ai_settings["system_prompt"],
    })


async def api_set_ai_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    if "api_url" in body and body["api_url"]:
        ai_settings["api_url"] = body["api_url"]
    if "api_key" in body and body["api_key"]:
        ai_settings["api_key"] = body["api_key"]
    if "model" in body and body["model"]:
        ai_settings["model"] = body["model"]
    if "system_prompt" in body:
        ai_settings["system_prompt"] = body["system_prompt"]
    return web.json_response({"ok": True})


# ──────────────────────────────────────────────
# 資料結構
# ──────────────────────────────────────────────

@dataclass
class PollOption:
    text: str

@dataclass
class Poll:
    poll_id: str
    title: str
    mode: str = "borda"  # "borda" | "simple"
    options: List[PollOption] = field(default_factory=list)
    status: str = "drafting"  # "drafting" | "active" | "ended"
    # borda mode: Dict[int, List[int]]  (user_id -> ranked option indices)
    # simple mode: Dict[int, int]       (user_id -> option index)
    votes: dict = field(default_factory=dict)
    message_id: Optional[int] = None
    created_by: int = 0
    allowed_roles: List[int] = field(default_factory=list)  # empty = everyone

    def option_count(self) -> int:
        return len(self.options)

    def add_option(self, text: str):
        self.options.append(PollOption(text=text))

    def tally_borda(self) -> Dict[str, int]:
        n = self.option_count()
        scores: Dict[str, int] = {opt.text: 0 for opt in self.options}
        for ranking in self.votes.values():
            for rank_pos, opt_idx in enumerate(ranking):
                if 0 <= opt_idx < n:
                    scores[self.options[opt_idx].text] += n - 1 - rank_pos
        return scores

    def tally_simple(self) -> Dict[str, int]:
        counts: Dict[str, int] = {opt.text: 0 for opt in self.options}
        for opt_idx in self.votes.values():
            if 0 <= opt_idx < len(self.options):
                counts[self.options[opt_idx].text] += 1
        return counts

    def tally(self) -> Dict[str, int]:
        if self.mode == "simple":
            return self.tally_simple()
        return self.tally_borda()

    def vote_count(self) -> int:
        return len(self.votes)


# guild_id -> { poll_id -> Poll }
guild_polls: Dict[int, Dict[str, Poll]] = {}


DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "polls_data.json")


def save_polls_to_disk():
    """Save all polls to disk for persistence across restarts."""
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        serializable = {}
        for gid, polls in guild_polls.items():
            serializable[str(gid)] = {}
            for pid, poll in polls.items():
                serializable[str(gid)][pid] = {
                    "poll_id": poll.poll_id,
                    "title": poll.title,
                    "mode": poll.mode,
                    "status": poll.status,
                    "options": [{"text": o.text} for o in poll.options],
                    "votes": {str(k): v for k, v in poll.votes.items()},
                    "message_id": poll.message_id,
                    "created_by": poll.created_by,
                    "allowed_roles": poll.allowed_roles,
                    "description": getattr(poll, "description", ""),
                }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json_module.dump(serializable, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Failed to save polls: {e}")


def load_polls_from_disk():
    """Load polls from disk on startup."""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            total = 0
            for gid_str, polls in data.items():
                gid = int(gid_str)
                guild_polls[gid] = {}
                for pid, p in polls.items():
                    poll = Poll(
                        poll_id=p["poll_id"],
                        title=p["title"],
                        mode=p.get("mode", "borda"),
                    )
                    poll.status = p.get("status", "drafting")
                    poll.message_id = p.get("message_id")
                    poll.created_by = p.get("created_by", 0)
                    poll.allowed_roles = p.get("allowed_roles", [])
                    if p.get("description"):
                        poll.description = p["description"]
                    for o in p.get("options", []):
                        poll.add_option(o["text"])
                    poll.votes = {int(k): v for k, v in p.get("votes", {}).items()}
                    guild_polls[gid][pid] = poll
                    total += 1
            print(f"✅ 從磁碟載入 {total} 個投票")
    except Exception as e:
        print(f"⚠️ Failed to load polls: {e}")


async def auto_save_loop():
    """Background task: save polls every 30 seconds."""
    while True:
        await asyncio.sleep(30)
        save_polls_to_disk()


def get_poll(guild_id: int, poll_id: str) -> Optional[Poll]:
    return guild_polls.get(guild_id, {}).get(poll_id)


def gen_poll_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return interaction.user.guild_permissions.manage_guild


def check_role_permission(interaction: discord.Interaction, poll: Poll) -> bool:
    """檢查使用者是否有權限投票（基於身分組限制）。"""
    if not poll.allowed_roles:
        return True
    user_role_ids = {r.id for r in interaction.user.roles}
    return bool(user_role_ids & set(poll.allowed_roles))


def status_emoji(status: str) -> str:
    return {"drafting": "📝 準備中", "active": "🗳️ 進行中", "ended": "✅ 已結束"}.get(status, status)


def mode_name(mode: str) -> str:
    return {"borda": "波達計數法", "simple": "一般投票"}.get(mode, mode)


# ──────────────────────────────────────────────
# Bot
# ──────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # Needed to read message content from channel history
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    bot.tree.add_command(PollGroup())
    bot.tree.add_command(MeetingGroup())
    bot.tree.add_command(BriefingGroup())
    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot 上線：{bot.user}（已同步 {len(synced)} 個 slash commands）")
    except Exception as e:
        print(f"❌ 同步指令失敗：{e}")


@bot.event
async def setup_hook():
    load_polls_from_disk()
    save_polls_to_disk()  # Create file if not exists
    load_briefing_settings()
    save_briefing_settings()  # Create file if not exists
    await keep_alive_server()
    asyncio.ensure_future(self_ping_loop())
    asyncio.ensure_future(auto_save_loop())
    asyncio.ensure_future(daily_briefing_scheduler())
    asyncio.ensure_future(weekly_briefing_scheduler())


# ──────────────────────────────────────────────
# 波達計數法投票 View
# ──────────────────────────────────────────────

class RankVoteView(discord.ui.View):
    def __init__(self, poll: Poll, voter_id: int = 0):
        super().__init__(timeout=600)
        self.poll = poll
        self.voter_id = voter_id
        self._current_rank: List[int] = []

        options_for_select = [
            discord.SelectOption(
                label=f"{i+1}. {opt.text[:90]}",
                value=str(i),
                description=f"選項 {i+1}",
            )
            for i, opt in enumerate(poll.options)
        ]

        select = discord.ui.Select(
            placeholder="選擇你的第 1 偏好 👑",
            min_values=1, max_values=1,
            options=options_for_select,
            custom_id=f"borda_rank:{poll.poll_id}",
        )
        select.callback = self.on_rank_select
        self.add_item(select)

    async def on_rank_select(self, interaction: discord.Interaction):
        if self.voter_id and interaction.user.id != self.voter_id:
            await interaction.response.send_message("❌ 這不是你的投票面板。請用 /poll vote 取得你自己的投票面板。", ephemeral=True)
            return
        if self.poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
            return

        if not check_role_permission(interaction, self.poll):
            await interaction.response.send_message("❌ 你沒有參與此投票的身分組權限。", ephemeral=True)
            return

        opt_idx = int(interaction.data["values"][0])
        if opt_idx in self._current_rank:
            await interaction.response.send_message("⚠️ 你已經排過這個選項了。", ephemeral=True)
            return

        self._current_rank.append(opt_idx)
        rank_num = len(self._current_rank)
        n = self.poll.option_count()

        if rank_num >= n:
            self.poll.votes[interaction.user.id] = list(self._current_rank)
            save_polls_to_disk()
            ranking_text = "\n".join(
                f"{i+1}. {self.poll.options[idx].text}"
                for i, idx in enumerate(self._current_rank)
            )
            await interaction.response.edit_message(
                content=f"✅ **投票完成！** 你的排序：\n{ranking_text}\n\n謝謝投票，結果將在管理員結束投票後公布。",
                ephemeral=True,
                view=None,
            )
            return

        remaining = [
            discord.SelectOption(
                label=f"{i+1}. {self.poll.options[i].text[:90]}",
                value=str(i),
                description=f"選項 {i+1}",
            )
            for i in range(n) if i not in self._current_rank
        ]
        self.children[0].options = remaining
        self.children[0].placeholder = f"選擇你的第 {rank_num + 1} 偏好"

        progress = "\n".join(
            f"{i+1}. {self.poll.options[idx].text}"
            for i, idx in enumerate(self._current_rank)
        )
        await interaction.response.edit_message(
            content=f"📊 **{self.poll.title}** — 排序你的偏好\n\n目前已排：\n{progress}\n\n請選擇第 **{rank_num + 1}** 偏好：",
            view=self,
        )


# ──────────────────────────────────────────────
# 一般投票 View（按鈕單選）
# ──────────────────────────────────────────────

class SimpleVoteView(discord.ui.View):
    def __init__(self, poll: Poll, voter_id: int):
        super().__init__(timeout=600)  # 10 分鐘逾時
        self.poll = poll
        self.voter_id = voter_id

        for i, opt in enumerate(poll.options):
            btn = discord.ui.Button(
                label=opt.text[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"simple_vote:{poll.poll_id}:{i}",
                row=i // 5,
            )
            btn.callback = self.make_callback(i)
            self.add_item(btn)

    def make_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            if self.voter_id and interaction.user.id != self.voter_id:
                await interaction.response.send_message("❌ 這不是你的投票面板。請用 /poll vote 取得你自己的投票面板。", ephemeral=True)
                return
            if self.poll.status != "active":
                await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
                return

            if not check_role_permission(interaction, self.poll):
                await interaction.response.send_message("❌ 你沒有參與此投票的身分組權限。", ephemeral=True)
                return
            if interaction.user.id in self.poll.votes:
                await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
                return
            self.poll.votes[interaction.user.id] = idx
            save_polls_to_disk()
            await interaction.response.send_message(
                f"✅ 已投票給 **{self.poll.options[idx].text}**！謝謝參與。",
                ephemeral=True,
            )
        return callback


# ──────────────────────────────────────────────
# 投票管理面板 View
# ──────────────────────────────────────────────



# ──────────────────────────────────────────────
# 身分組選擇 View
# ──────────────────────────────────────────────

class RoleSelectView(discord.ui.View):
    def __init__(self, manage_view: "ManagePanelView", poll: Poll):
        super().__init__(timeout=120)
        self.manage_view = manage_view
        self.poll = poll

        role_select = discord.ui.RoleSelect(
            placeholder="選擇允許投票的身分組（可多選）…",
            min_values=0,
            max_values=25,
        )
        role_select.callback = self.on_roles_selected
        self.add_item(role_select)

        btn_clear = discord.ui.Button(label="清除限制（所有人可投）", style=discord.ButtonStyle.secondary, emoji="🔓")
        btn_clear.callback = self.on_clear
        self.add_item(btn_clear)

        btn_back = discord.ui.Button(label="返回管理面板", style=discord.ButtonStyle.primary, emoji="⬅️")
        btn_back.callback = self.on_back
        self.add_item(btn_back)

    async def on_roles_selected(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        selected = interaction.data.get("values", [])
        self.poll.allowed_roles = [int(r) for r in selected]
        if self.poll.allowed_roles:
            role_names = []
            for rid in self.poll.allowed_roles:
                role = interaction.guild.get_role(rid)
                if role:
                    role_names.append(role.name)
            await interaction.response.send_message(
                f"✅ 已設定身分組限制：{', '.join(role_names)}", ephemeral=True
            )
        else:
            await interaction.response.send_message("⚠️ 沒有選擇任何身分組，將不設限制。", ephemeral=True)
        self.manage_view._refresh_select()
        embed = self.manage_view._poll_detail_embed(self.poll)
        await interaction.message.edit(embed=embed, view=self.manage_view)

    async def on_clear(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        self.poll.allowed_roles = []
        save_polls_to_disk()
        await interaction.response.send_message("🔓 已清除身分組限制，所有人皆可投票。", ephemeral=True)
        self.manage_view._refresh_select()
        embed = self.manage_view._poll_detail_embed(self.poll)
        await interaction.message.edit(embed=embed, view=self.manage_view)

    async def on_back(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        self.manage_view._refresh_select()
        if self.manage_view.selected_poll_id:
            embed = self.manage_view._poll_detail_embed(self.poll)
        else:
            embed = self.manage_view._guild_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self.manage_view)


class ManagePanelView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.selected_poll_id: Optional[str] = None
        self._refresh_select()

    def _refresh_select(self):
        self.clear_items()
        polls = guild_polls.get(self.guild_id, {})
        if not polls:
            select = discord.ui.Select(
                placeholder="沒有投票",
                min_values=1, max_values=1,
                options=[discord.SelectOption(label="（尚無投票）", value="none")],
                disabled=True,
            )
            self.add_item(select)
            return

        options = []
        for pid, p in polls.items():
            status_text = {"drafting": "準備中", "active": "進行中", "ended": "已結束"}.get(p.status, p.status)
            label = f"[{pid}] {p.title[:80]}"
            options.append(discord.SelectOption(
                label=label,
                value=pid,
                description=f"{mode_name(p.mode)} · {status_text} · {p.vote_count()} 票 · {p.option_count()} 選項",
            ))

        select = discord.ui.Select(
            placeholder="選擇要管理的投票…",
            min_values=1, max_values=1,
            options=options,
        )
        select.callback = self.on_select
        self.add_item(select)

        if self.selected_poll_id:
            poll = get_poll(self.guild_id, self.selected_poll_id)
            if poll:
                if poll.status == "drafting":
                    btn_start = discord.ui.Button(label="啟動投票", style=discord.ButtonStyle.success, emoji="▶️")
                    btn_start.callback = self.on_start
                    self.add_item(btn_start)

                if poll.status == "active":
                    btn_end = discord.ui.Button(label="結束投票", style=discord.ButtonStyle.danger, emoji="⏹️")
                    btn_end.callback = self.on_end
                    self.add_item(btn_end)

                btn_view = discord.ui.Button(label="查看詳情", style=discord.ButtonStyle.secondary, emoji="📋")
                btn_view.callback = self.on_view
                self.add_item(btn_view)

                btn_roles = discord.ui.Button(label="身分組限制", style=discord.ButtonStyle.secondary, emoji="🔐")
                btn_roles.callback = self.on_roles
                self.add_item(btn_roles)

                btn_delete = discord.ui.Button(label="刪除投票", style=discord.ButtonStyle.danger, emoji="🗑️")
                btn_delete.callback = self.on_delete
                self.add_item(btn_delete)

    async def on_select(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        self.selected_poll_id = interaction.data["values"][0]
        self._refresh_select()
        await self._update_message(interaction)

    async def _update_message(self, interaction: discord.Interaction):
        poll = get_poll(self.guild_id, self.selected_poll_id) if self.selected_poll_id else None
        if poll:
            embed = self._poll_detail_embed(poll)
        else:
            embed = self._guild_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def _poll_detail_embed(self, poll: Poll) -> discord.Embed:
        opt_lines = []
        for i, opt in enumerate(poll.options):
            if poll.status in ("active", "ended"):
                tally = poll.tally()
                score = tally.get(opt.text, 0)
                unit = "分" if poll.mode == "borda" else "票"
                opt_lines.append(f"{i+1}. {opt.text} — **{score}** {unit}")
            else:
                opt_lines.append(f"{i+1}. {opt.text}")

        embed = discord.Embed(
            title=f"🔧 管理投票：{poll.title}",
            description=(
                f"**ID：** `{poll.poll_id}`\n"
                f"**模式：** {mode_name(poll.mode)}\n"
                f"**狀態：** {status_emoji(poll.status)}\n"
                f"**投票人數：** {poll.vote_count()}\n"
                f"**選項數：** {poll.option_count()}\n\n"
                f"**\u8eab\u4efd\u7d44\u9650\u5236\uff1a** " + (", ".join("<@&{}>".format(rid) for rid in poll.allowed_roles) if poll.allowed_roles else "\u6240\u6709\u4eba") + "\n"
                f"**選項清單：**\n" + "\n".join(opt_lines)
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="使用下方按鈕管理此投票")
        return embed

    def _guild_overview_embed(self) -> discord.Embed:
        polls = guild_polls.get(self.guild_id, {})
        if not polls:
            return discord.Embed(
                title="🔧 投票管理面板",
                description="目前沒有任何投票。\n使用 `/poll create` 建立新投票。",
                color=discord.Color.blurple(),
            )

        lines = []
        for pid, p in polls.items():
            lines.append(f"`{pid}`  {p.title} — {status_emoji(p.status)} · {mode_name(p.mode)} · {p.vote_count()} 票")

        embed = discord.Embed(
            title="🔧 投票管理面板",
            description=f"本伺服器共有 {len(polls)} 個投票：\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="從下拉選單選擇一個投票進行管理")
        return embed

    async def on_start(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束。", ephemeral=True)
            return
        if poll.option_count() < 2:
            await interaction.response.send_message("❌ 至少需要 2 個選項才能啟動投票。", ephemeral=True)
            return

        poll.status = "active"
        save_polls_to_disk()

        if poll.mode == "borda":
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：波達計數法\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方下拉選單，依偏好排序所有選項（第 1 名最偏好）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 波達計數法投票 · 排序所有選項即可投票")
        else:
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：一般投票\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方按鈕投給你支持的選項（每人一票）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 一般投票 · 每人一票")

        # 公告訊息不帶 View — 成員請使用 /poll vote <id> 投票
        embed.set_footer(text=f"投票 ID: {poll.poll_id} · 請使用 /poll vote {poll.poll_id} 投票")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        poll.message_id = msg.id

        self._refresh_select()
        await interaction.followup.edit_message(interaction.message.id, embed=self._poll_detail_embed(poll), view=self)

    async def on_end(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未啟動。", ephemeral=True)
            return

        poll.status = "ended"
        save_polls_to_disk()
        scores = poll.tally()
        total_votes = poll.vote_count()
        n = poll.option_count()

        if not scores or total_votes == 0:
            await interaction.response.send_message(f"📊 投票「{poll.title}」已結束，但沒有收到任何投票。")
        else:
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for rank_pos, (opt_text, score) in enumerate(ranked):
                medal = medals[rank_pos] if rank_pos < 3 else f"`{rank_pos+1}`"
                unit = "分" if poll.mode == "borda" else "票"
                lines.append(f"{medal}  **{opt_text}** — {score} {unit}")

            scoring_desc = (
                f"計分方式：波達計數法（第 1 名得 {n-1} 分，最後一名得 0 分）"
                if poll.mode == "borda"
                else "計分方式：一般投票（最高票獲勝）"
            )
            embed = discord.Embed(
                title=f"📊 投票結果：{poll.title}",
                description=(
                    f"🗳️ 共 {total_votes} 人投票 · {n} 個選項\n"
                    f"{scoring_desc}\n\n"
                    + "\n".join(lines)
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 投票已結束")
            await interaction.response.send_message(embed=embed)

        self._refresh_select()
        await interaction.followup.edit_message(interaction.message.id, embed=self._poll_detail_embed(poll), view=self)

    async def on_view(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return

        # Build per-user vote detail (ephemeral followup)
        await interaction.response.defer(ephemeral=True)

        if not poll.votes:
            await interaction.followup.send("📭 目前還沒有人投票。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📋 投票明細：{poll.title}",
            color=discord.Color.blurple(),
        )

        lines = []
        for user_id, vote_data in poll.votes.items():
            user = interaction.guild.get_member(user_id)
            name = user.display_name if user else f"用戶 {user_id}"
            if poll.mode == "borda":
                # vote_data = [opt_idx, opt_idx, ...]
                ranked = []
                for rank_pos, opt_idx in enumerate(vote_data, 1):
                    opt_text = poll.options[opt_idx].text if opt_idx < len(poll.options) else f"#{opt_idx}"
                    ranked.append(f"{rank_pos}. {opt_text}")
                lines.append(f"**{name}**\n" + "  ".join(ranked))
            else:
                # vote_data = single opt_idx
                opt_text = poll.options[vote_data].text if vote_data < len(poll.options) else f"#{vote_data}"
                lines.append(f"**{name}**：{opt_text}")

        # Discord embed description limit = 4096 chars; chunk into fields if needed
        chunk = []
        chunk_len = 0
        field_count = 0
        for line in lines:
            if chunk_len + len(line) + 2 > 1000 or len(chunk) >= 10:
                embed.add_field(
                    name=f"投票者 ({field_count * 10 + 1}–{field_count * 10 + len(chunk)})" if field_count > 0 or len(lines) > 10 else "投票者",
                    value="\n".join(chunk),
                    inline=False,
                )
                chunk = []
                chunk_len = 0
                field_count += 1
            chunk.append(line)
            chunk_len += len(line) + 2
        if chunk:
            embed.add_field(
                name=f"投票者 ({field_count * 10 + 1}–{field_count * 10 + len(chunk)})" if field_count > 0 else "投票者",
                value="\n".join(chunk),
                inline=False,
            )

        embed.set_footer(text=f"共 {len(poll.votes)} 人投票 | 僅你可見")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_roles(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        role_view = RoleSelectView(self, poll)
        embed = discord.Embed(
            title=f"🔐 身份組限制：{poll.title}",
            description=(
                f"投票 ID：`{poll.poll_id}`\n"
                f"從下方選擇允許投票的身份組（可多選）。\n"
                f"不選任何身份組 = 所有人皆可投票。"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=role_view)

    async def on_delete(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        del guild_polls[self.guild_id][self.selected_poll_id]
        save_polls_to_disk()
        self.selected_poll_id = None
        self._refresh_select()
        embed = self._guild_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)


# ──────────────────────────────────────────────
# Slash Command Group
# ──────────────────────────────────────────────

class PollGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="poll", description="投票系統（波達計數法 + 一般投票）")

    @app_commands.command(name="create", description="建立新投票（管理員限定）")
    @app_commands.describe(
        title="投票標題",
        mode="投票模式：borda（波達計數法）或 simple（一般投票）",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="波達計數法（排序偏好）", value="borda"),
        app_commands.Choice(name="一般投票（單選）", value="simple"),
    ])
    async def create(self, interaction: discord.Interaction, title: str, mode: app_commands.Choice[str] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        if guild_id not in guild_polls:
            guild_polls[guild_id] = {}

        poll_mode = mode.value if mode else "borda"
        poll_id = gen_poll_id()
        poll = Poll(
            poll_id=poll_id,
            title=title,
            mode=poll_mode,
            created_by=interaction.user.id,
        )
        guild_polls[guild_id][poll_id] = poll
        save_polls_to_disk()

        await interaction.response.send_message(
            f"📝 投票「**{title}**」已建立！\n"
            f"**ID：** `{poll_id}`\n"
            f"**模式：** {mode_name(poll_mode)}\n\n"
            f"使用 `/poll add <poll_id> <option>` 新增選項，或用 `/poll manage` 開啟管理面板。"
        )

    @app_commands.command(name="add", description="新增選項到指定投票（管理員限定）")
    @app_commands.describe(poll_id="投票 ID", option="選項內容")
    async def add(self, interaction: discord.Interaction, poll_id: str, option: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束，無法新增選項。", ephemeral=True)
            return
        if len(poll.options) >= 25:
            await interaction.response.send_message("❌ Discord 上限 25 個選項。", ephemeral=True)
            return
        poll.add_option(option)
        save_polls_to_disk()
        await interaction.response.send_message(
            f"✅ 已新增選項 **{option}**（目前共 {poll.option_count()} 個選項）\n"
            f"投票 ID：`{poll_id}`\n"
            f"繼續用 `/poll add` 新增，或用 `/poll start {poll_id}` 啟動投票。"
        )

    @app_commands.command(name="list", description="查看投票清單或指定投票的選項")
    @app_commands.describe(poll_id="投票 ID（不填則列出所有投票）")
    async def list(self, interaction: discord.Interaction, poll_id: Optional[str] = None):
        polls = guild_polls.get(interaction.guild.id, {})

        if poll_id:
            poll = polls.get(poll_id)
            if not poll:
                await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
                return
            if not poll.options:
                await interaction.response.send_message(f"📭 投票「{poll.title}」目前沒有選項。", ephemeral=True)
                return
            lines = [f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options)]
            embed = discord.Embed(
                title=f"📊 {poll.title}",
                description=(
                    f"ID：`{poll.poll_id}`\n"
                    f"模式：{mode_name(poll.mode)}\n"
                    f"狀態：{status_emoji(poll.status)}\n\n"
                    + "\n".join(lines)
                ),
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed)
        else:
            if not polls:
                await interaction.response.send_message("❌ 目前沒有任何投票。", ephemeral=True)
                return
            lines = []
            for pid, p in polls.items():
                lines.append(f"`{pid}`  {p.title} — {status_emoji(p.status)} · {mode_name(p.mode)} · {p.vote_count()} 票 · {p.option_count()} 選項")
            embed = discord.Embed(
                title="📋 所有投票",
                description=f"共 {len(polls)} 個投票：\n\n" + "\n".join(lines),
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="start", description="啟動指定投票（管理員限定）")
    @app_commands.describe(poll_id="投票 ID")
    async def start(self, interaction: discord.Interaction, poll_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束。", ephemeral=True)
            return
        if poll.option_count() < 2:
            await interaction.response.send_message("❌ 至少需要 2 個選項才能啟動投票。", ephemeral=True)
            return

        poll.status = "active"
        save_polls_to_disk()

        if poll.mode == "borda":
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：波達計數法\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方下拉選單，依偏好排序所有選項（第 1 名最偏好）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 波達計數法投票 · 排序所有選項即可投票")
        else:
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：一般投票\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方按鈕投給你支持的選項（每人一票）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 一般投票 · 每人一票")

        # 公告訊息不帶 View — 成員請使用 /poll vote <id> 投票
        embed.set_footer(text=f"投票 ID: {poll.poll_id} · 請使用 /poll vote {poll.poll_id} 投票")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        poll.message_id = msg.id

    @app_commands.command(name="end", description="結束指定投票並顯示結果（管理員限定）")
    @app_commands.describe(poll_id="投票 ID")
    async def end(self, interaction: discord.Interaction, poll_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未啟動。", ephemeral=True)
            return

        poll.status = "ended"
        save_polls_to_disk()
        scores = poll.tally()
        total_votes = poll.vote_count()
        n = poll.option_count()

        if not scores or total_votes == 0:
            await interaction.response.send_message(f"📊 投票「{poll.title}」已結束，但沒有收到任何投票。")
            return

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank_pos, (opt_text, score) in enumerate(ranked):
            medal = medals[rank_pos] if rank_pos < 3 else f"`{rank_pos+1}`"
            unit = "分" if poll.mode == "borda" else "票"
            lines.append(f"{medal}  **{opt_text}** — {score} {unit}")

        scoring_desc = (
            f"計分方式：波達計數法（第 1 名得 {n-1} 分，最後一名得 0 分）"
            if poll.mode == "borda"
            else "計分方式：一般投票（最高票獲勝）"
        )
        embed = discord.Embed(
            title=f"📊 投票結果：{poll.title}",
            description=(
                f"🗳️ 共 {total_votes} 人投票 · {n} 個選項\n"
                f"{scoring_desc}\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"投票 ID: {poll.poll_id} · 投票已結束")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delete", description="刪除指定投票（管理員限定）")
    @app_commands.describe(poll_id="投票 ID")
    async def delete(self, interaction: discord.Interaction, poll_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        polls = guild_polls.get(interaction.guild.id, {})
        if poll_id not in polls:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        title = polls[poll_id].title
        del polls[poll_id]
        await interaction.response.send_message(f"🗑️ 投票「**{title}**」（`{poll_id}`）已刪除。")

    @app_commands.command(name="vote", description="投票（一般成員，依投票模式自動判斷）")
    @app_commands.describe(poll_id="投票 ID")
    async def vote(self, interaction: discord.Interaction, poll_id: str):
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
            return
        if interaction.user.id in poll.votes:
            await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
            return
        if not check_role_permission(interaction, poll):
            await interaction.response.send_message("❌ 你沒有參與此投票的身分組權限。", ephemeral=True)
            return

        if poll.mode == "borda":
            view = RankVoteView(poll, voter_id=interaction.user.id)
            await interaction.response.send_message(
                content=f"📊 **{poll.title}** — 排序你的偏好\n\n請選擇第 **1** 偏好：",
                view=view, ephemeral=True,
            )
        else:
            view = SimpleVoteView(poll, voter_id=interaction.user.id)
            await interaction.response.send_message(
                content=f"📊 **{poll.title}** — 點擊按鈕投給你支持的選項：",
                view=view, ephemeral=True,
            )

    @app_commands.command(name="test", description="投票系統測試（管理員限定）")
    async def test(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        poll_id = "test-" + str(interaction.guild.id)
        polls = guild_polls.setdefault(interaction.guild.id, {})

        # 如果已有測試投票，先刪除
        if poll_id in polls:
            del polls[poll_id]

        # 建立測試投票（波達計數法，3 個選項）
        poll = Poll(
            poll_id=poll_id,
            title="投票系統測試",
            mode="borda",
        )
        poll.options = [
            PollOption(text="選項 A"),
            PollOption(text="選項 B"),
            PollOption(text="選項 C"),
        ]
        poll.status = "active"
        save_polls_to_disk()  # 直接啟動，方便立即測試
        polls[poll_id] = poll

        embed = discord.Embed(
            title="🧪 投票系統測試",
            description=(
                f"已建立測試投票並自動啟動。\n\n"
                f"**投票 ID：** `{poll_id}`\n"
                f"**模式：** 波達計數法\n"
                f"**選項：**\n"
                f"1. 選項 A\n"
                f"2. 選項 B\n"
                f"3. 選項 C\n\n"
                f"👉 請使用 `/poll vote {poll_id}` 開始投票測試\n"
                f"👉 管理員可使用 `/poll end {poll_id}` 結束並查看結果\n"
                f"👉 測試完畢可用 `/poll delete {poll_id}` 刪除"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="manage", description="開啟投票管理面板（管理員限定）")
    async def manage(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        view = ManagePanelView(interaction.guild.id)
        embed = view._guild_overview_embed()
        await interaction.response.send_message(embed=embed, view=view)


# ──────────────────────────────────────────────
# 會議指令群組
# ──────────────────────────────────────────────


class MeetingGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="meeting", description="會議相關指令")

    @app_commands.command(name="adjourn", description="整理會議紀錄（管理員限定）")
    @app_commands.describe(
        channel="要整理的頻道",
        since="起始時間（例如：2h=2小時前、1h30m、14:00、2026-08-02）",
    )
    async def adjourn(self, interaction: discord.Interaction, channel: discord.TextChannel, since: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        if not ai_settings["api_key"]:
            await interaction.response.send_message(
                "❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定 中設定。", ephemeral=True
            )
            return

        after_time = parse_since(since)
        if not after_time:
            await interaction.response.send_message(
                "❌ 無法解析時間。支援格式：`2h`（2小時前）、`1h30m`、`14:00`、`2026-08-02`、`2026-08-02 14:00`",
                ephemeral=True,
            )
            return

        await interaction.response.defer()  # public — meeting minutes should be visible

        # Collect messages
        formatted = []
        count = 0
        try:
            async for msg in channel.history(after=after_time, limit=500):
                if msg.author.bot:
                    continue
                content = msg.content.strip()
                if not content:
                    if msg.attachments:
                        content = f"[傳送了 {len(msg.attachments)} 個附件]"
                    elif msg.embeds:
                        content = f"[傳送了嵌入訊息: {msg.embeds[0].title or '無標題'}]"
                    else:
                        continue
                if len(content) > 500:
                    content = content[:500] + "..."
                time_str = msg.created_at.strftime("%H:%M")
                name = msg.author.display_name
                formatted.append(f"[{time_str}] {name}: {content}")
                count += 1
        except discord.Forbidden:
            await interaction.followup.send("❌ 沒有權限讀取該頻道的訊息。")
            return

        if not formatted:
            await interaction.followup.send(
                f"❌ 在指定時間後未找到任何訊息（頻道：{channel.mention}，起始：{after_time.strftime('%Y-%m-%d %H:%M UTC')}）"
            )
            return

        # Build conversation log
        log_text = f"頻道: #{channel.name}\n時間範圍: {after_time.strftime('%Y-%m-%d %H:%M')} UTC ~ 整理時間\n訊息數: {count}\n\n"
        log_text += "\n".join(reversed(formatted))

        if len(log_text) > 30000:
            log_text = log_text[:30000] + "\n...（後續訊息已截斷）"

        # Send live message — will be edited as AI streams
        live_msg = await interaction.followup.send(
            f"📋 **會議紀錄 — #{channel.name}**\n📝 正在整理 {count} 則訊息，AI 開始生成...",
            wait=True
        )

        # Stream AI response, edit message live
        import time as _time
        accumulated = ""
        last_edit = 0
        edit_interval = 1.5  # seconds between edits (Discord rate limit safe)
        header = f"📋 **會議紀錄 — #{channel.name}**\n"

        try:
            async for chunk in call_ai_api_stream(log_text, ai_settings):
                accumulated += chunk
                now = _time.time()
                if now - last_edit >= edit_interval:
                    last_edit = now
                    # Truncate to fit Discord 2000 char limit
                    display = header + accumulated
                    if len(display) > 1950:
                        max_body = 1950 - len(header) - 5
                        display = header + accumulated[:max_body] + "\n⏳..."
                    try:
                        await live_msg.edit(content=display)
                    except Exception:
                        pass

            # Final edit with complete content
            full_text = header + accumulated
            if len(full_text) <= 2000:
                try:
                    await live_msg.edit(content=full_text)
                except Exception:
                    pass
            else:
                # Too long for one message — send as file
                import io
                try:
                    await live_msg.edit(content=header + "✅ 會議紀錄已生成（完整內容見下方附件）")
                except Exception:
                    pass
                file_content = f"# 會議紀錄 — #{channel.name}\n# 整理範圍：{after_time.strftime('%Y-%m-%d %H:%M')} UTC 起\n# 共 {count} 則訊息\n# 由 {interaction.user.display_name} 整理\n# AI 模型：{ai_settings['model']}\n\n---\n\n{accumulated}"
                file = discord.File(
                    io.BytesIO(file_content.encode("utf-8")),
                    filename=f"meeting_minutes_{channel.name}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.md"
                )
                embed = discord.Embed(
                    title=f"📋 會議紀錄 — {channel.name}",
                    description=f"整理範圍：{after_time.strftime('%Y-%m-%d %H:%M')} UTC 起\n共 {count} 則訊息\nAI 模型：{ai_settings['model']}",
                    color=discord.Color.blue(),
                )
                embed.set_footer(text=f"由 {interaction.user.display_name} 整理")
                await interaction.followup.send(embed=embed, file=file)

        except Exception as e:
            try:
                await live_msg.edit(content=f"❌ AI 整理失敗：{e}")
            except Exception:
                await interaction.followup.send(f"❌ AI 整理失敗：{e}")

    @app_commands.command(name="test", description="測試 AI API 連線（管理員限定）")
    async def test_ai(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not ai_settings["api_key"]:
            await interaction.response.send_message("❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定 中設定。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            result = await call_ai_api("請回覆：AI 連線測試成功！只需一句話。", ai_settings)
            await interaction.followup.send(f"✅ AI API 連線成功！\n模型：{ai_settings['model']}\n回覆：{result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ AI API 連線失敗：{e}", ephemeral=True)


# ──────────────────────────────────────────────
# 快報與公報指令
# ──────────────────────────────────────────────

class BriefingGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="briefing", description="每日快報與每週公報")

    @app_commands.command(name="daily_set", description="設定每日自動快報時間（管理員限定）")
    @app_commands.describe(time="執行時間 HH:MM（例如：23:00）", channel="發佈快報的頻道")
    async def daily_set(self, interaction: discord.Interaction, time: str, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        # Validate time format
        try:
            h, m = time.strip().split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ 時間格式錯誤。請用 HH:MM 格式，例如 `23:00`。", ephemeral=True)
            return
        briefing_settings["daily_enabled"] = True
        briefing_settings["daily_time"] = time.strip()
        briefing_settings["daily_channel_id"] = channel.id
        save_briefing_settings()
        await interaction.response.send_message(
            f"✅ 每日快報已設定\n⏰ 每天 `{time.strip()}` 自動發佈到 {channel.mention}",
            ephemeral=True
        )

    @app_commands.command(name="daily_off", description="關閉每日自動快報（管理員限定）")
    async def daily_off(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        briefing_settings["daily_enabled"] = False
        save_briefing_settings()
        await interaction.response.send_message("✅ 每日自動快報已關閉。可用 `/briefing daily_now` 手動執行。", ephemeral=True)

    @app_commands.command(name="daily_now", description="立即生成每日快報（管理員限定）")
    @app_commands.describe(channel="發佈快報的頻道（預設：當前頻道）")
    async def daily_now(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not ai_settings["api_key"]:
            await interaction.response.send_message("❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定。", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.send_message(f"📝 每日快報開始生成，請到 {target.mention} 查看。", ephemeral=True)
        await run_briefing(target, hours=24, mode="daily")

    @app_commands.command(name="weekly_set", description="設定每週自動公報時間（管理員限定）")
    @app_commands.describe(
        day="星期幾",
        time="執行時間 HH:MM",
        channel="發佈公報的頻道",
    )
    @app_commands.choices(day=[
        app_commands.Choice(name="週一", value="0"),
        app_commands.Choice(name="週二", value="1"),
        app_commands.Choice(name="週三", value="2"),
        app_commands.Choice(name="週四", value="3"),
        app_commands.Choice(name="週五", value="4"),
        app_commands.Choice(name="週六", value="5"),
        app_commands.Choice(name="週日", value="6"),
    ])
    async def weekly_set(self, interaction: discord.Interaction, day: app_commands.Choice[str], time: str, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        try:
            h, m = time.strip().split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ 時間格式錯誤。請用 HH:MM 格式，例如 `23:00`。", ephemeral=True)
            return
        briefing_settings["weekly_enabled"] = True
        briefing_settings["weekly_day"] = int(day.value)
        briefing_settings["weekly_time"] = time.strip()
        briefing_settings["weekly_channel_id"] = channel.id
        save_briefing_settings()
        day_name = WEEKDAY_NAMES.get(int(day.value), day.name)
        await interaction.response.send_message(
            f"✅ 每週公報已設定\n⏰ 每{day_name} `{time.strip()}` 自動發佈到 {channel.mention}",
            ephemeral=True
        )

    @app_commands.command(name="weekly_off", description="關閉每週自動公報（管理員限定）")
    async def weekly_off(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        briefing_settings["weekly_enabled"] = False
        save_briefing_settings()
        await interaction.response.send_message("✅ 每週自動公報已關閉。可用 `/briefing weekly_now` 手動執行。", ephemeral=True)

    @app_commands.command(name="weekly_now", description="立即生成每週公報（管理員限定）")
    @app_commands.describe(channel="發佈公報的頻道（預設：當前頻道）")
    async def weekly_now(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not ai_settings["api_key"]:
            await interaction.response.send_message("❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定。", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.send_message(f"📝 每週公報開始生成，請到 {target.mention} 查看。", ephemeral=True)
        await run_briefing(target, hours=168, mode="weekly")

    @app_commands.command(name="status", description="查看快報與公報設定")
    async def briefing_status(self, interaction: discord.Interaction):
        daily_on = "✅ 開啟" if briefing_settings["daily_enabled"] else "❌ 關閉"
        weekly_on = "✅ 開啟" if briefing_settings["weekly_enabled"] else "❌ 關閉"
        daily_time = briefing_settings.get("daily_time", "23:00")
        daily_ch = f"<#{briefing_settings['daily_channel_id']}>" if briefing_settings.get("daily_channel_id") else "未設定"
        weekly_day_name = WEEKDAY_NAMES.get(int(briefing_settings.get("weekly_day", 6)), "週日")
        weekly_time = briefing_settings.get("weekly_time", "23:00")
        weekly_ch = f"<#{briefing_settings['weekly_channel_id']}>" if briefing_settings.get("weekly_channel_id") else "未設定"

        embed = discord.Embed(title="📰 快報與公報設定", color=discord.Color.blue())
        embed.add_field(
            name="📊 每日快報",
            value=f"狀態：{daily_on}\n時間：每天 `{daily_time}`\n頻道：{daily_ch}",
            inline=False
        )
        embed.add_field(
            name="📋 每週公報",
            value=f"狀態：{weekly_on}\n時間：每{weekly_day_name} `{weekly_time}`\n頻道：{weekly_ch}",
            inline=False
        )
        embed.set_footer(text="使用 /briefing daily_set, weekly_set 設定 | daily_off, weekly_off 關閉")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────
# 啟動
# ──────────────────────────────────────────────

def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("⚠️  請設定環境變數 DISCORD_BOT_TOKEN")
        return
    bot.run(token)


if __name__ == "__main__":
    main()
