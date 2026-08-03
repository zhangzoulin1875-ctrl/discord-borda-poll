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

  AI 聊天設定存於 data/chat_ai_settings.json
  AI 聊天指令（/chat ...）：
    /chat toggle               - 開啟/關閉 AI 聊天
    /chat model <model>        - 設定模型
    /chat prompt <prompt>      - 設定人設
    /chat cooldown <seconds>   - 設定冷卻時間
    /chat channel <action> [channel] - 管理頻道白名單
    /chat test <message>       - 測試 AI 回覆
    /chat status               - 查看設定

  AI 聊天環境變數（與會議紀錄/快報使用不同 API）：
    CHAT_AI_API_URL  - 聊天 AI API 端點
    CHAT_AI_API_KEY  - 聊天 AI API Key
    CHAT_AI_MODEL    - 聊天 AI 模型
    CHAT_AI_SYSTEM_PROMPT - 聊天 AI 人設

  Google Drive 檔案儲存（免費，替代 Render 付費硬碟）：
    GOOGLE_SERVICE_ACCOUNT_B64 - Base64 編碼的 Google 服務帳號 JSON
    GOOGLE_DRIVE_FOLDER_ID      - Drive 資料夾 ID（選填，建議設定）
    設定後：啟動時從 Drive 載入資料，每 60 秒同步到 Drive
    未設定：使用本地檔案（重部署後資料會遺失）
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

import sys
import functools

# ═══ CRITICAL: force unbuffered/line-buffered stdout ═══
# Python defaults to BLOCK buffering for stdout when it's not attached to a
# TTY (which is always the case under Render / Docker / any process
# supervisor). This means print() output sits in an internal buffer and is
# NOT sent to Render's log collector until the buffer fills (~8KB) or the
# process exits. The discord.py / aiohttp logging module output you DO see
# in Render logs goes through logging.StreamHandler, which flushes per
# record — that's a completely separate mechanism from print(). This is why
# every diagnostic print() we added was invisible in the logs: it wasn't
# lost, it just hadn't been flushed yet. Fix: force line buffering so every
# print() is flushed immediately, same as the logging output.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass
print = functools.partial(print, flush=True)

import discord
from discord import app_commands
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import asyncio
import os
import base64
import urllib.request
import urllib.parse
import secrets as py_secrets
import aiohttp
import random
import string
import re
import time as _time

try:
    import jwt as pyjwt  # PyJWT for Google Drive service account auth
except ImportError:
    pyjwt = None
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
    # Chat AI settings API
    app.router.add_get("/api/chat-ai-settings", api_get_chat_ai_settings)
    app.router.add_put("/api/chat-ai-settings", api_set_chat_ai_settings)
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
    app.router.add_get("/oauth/drive/callback", oauth_drive_callback)
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


# ──────────────────────────────────────────────
# Google Drive OAuth（個人帳號授權，取得有儲存配額的 refresh token）
# ──────────────────────────────────────────────

def _sign_drive_oauth_state(admin_id: int) -> str:
    payload = f"{admin_id}:{int(_time.time())}"
    sig = hmac.new(COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_drive_oauth_state(state: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        admin_id, ts, sig = decoded.rsplit(":", 2)
        payload = f"{admin_id}:{ts}"
        expected = hmac.new(COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return False
        # State valid for 10 minutes
        return (_time.time() - int(ts)) < 600
    except Exception:
        return False


def _drive_oauth_redirect_uri() -> str:
    base_url = os.getenv("SELF_URL") or os.getenv("RENDER_EXTERNAL_URL") or ""
    return base_url.rstrip("/") + "/oauth/drive/callback"


async def oauth_drive_callback(request):
    code = request.query.get("code")
    state = request.query.get("state", "")
    error = request.query.get("error")

    if error:
        return web.Response(
            text=f"<h2>❌ 授權被拒絕或取消</h2><p>{error}</p>",
            content_type="text/html", status=400
        )
    if not code:
        return web.Response(text="<h2>❌ 缺少 code 參數</h2>", content_type="text/html", status=400)
    if not _verify_drive_oauth_state(state):
        return web.Response(
            text="<h2>❌ state 驗證失敗或已過期（10 分鐘內有效）</h2><p>請重新執行 /system drive_authorize 取得新連結。</p>",
            content_type="text/html", status=400
        )

    client_id = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "") or os.getenv("OAUTH_CLIENT_SECRET", "")
    redirect_uri = _drive_oauth_redirect_uri()

    if not client_id or not client_secret:
        return web.Response(
            text="<h2>❌ 伺服器未設定 OAuth Client ID / Secret（GOOGLE_CLIENT_ID 或 OAUTH_CLIENT_ID）</h2>",
            content_type="text/html", status=500
        )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                return web.Response(
                    text=f"<h2>❌ 換取 token 失敗（HTTP {resp.status}）</h2><pre>{text[:1000]}</pre>",
                    content_type="text/html", status=400
                )
            result = json_module.loads(text)

    refresh_token = result.get("refresh_token")
    if not refresh_token:
        return web.Response(
            text=(
                "<h2>⚠️ 沒有拿到 refresh_token</h2>"
                "<p>通常是因為你之前已經授權過這個應用程式。"
                "請到 <a href='https://myaccount.google.com/permissions' target='_blank'>"
                "Google 帳號權限頁面</a> 移除這個應用程式的授權，然後重新執行 /system drive_authorize。</p>"
            ),
            content_type="text/html", status=400
        )

    html = f"""
    <html><body style="font-family: sans-serif; max-width: 700px; margin: 40px auto; line-height: 1.6;">
    <h2>✅ 授權成功！</h2>
    <p>把下面這串值複製起來，到 Render → Environment 新增一個變數：</p>
    <p><b>GOOGLE_DRIVE_REFRESH_TOKEN</b> = </p>
    <textarea readonly style="width:100%; height:80px; font-family: monospace; padding:8px;">{refresh_token}</textarea>
    <p>⚠️ 這是敏感資訊，請勿分享給他人。設定完成後 Render 會自動重新部署，之後可以關閉這個分頁。</p>
    <p>設定完後，記得也要確認 OAUTH_CLIENT_ID 和 OAUTH_CLIENT_SECRET（或 GOOGLE_CLIENT_ID 和 GOOGLE_CLIENT_SECRET）這兩個變數也已經加到 Render。</p>
    </body></html>
    """
    return web.Response(text=html, content_type="text/html")





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

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BRIEFING_DATA_FILE = os.path.join(DATA_DIR, "briefing_settings.json")


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


# ──────────────────────────────────────────────
# AI 聊天設定（獨立 API，與會議紀錄/快報分開）
# ──────────────────────────────────────────────

DEFAULT_CHAT_AI_PROMPT = """你是一個微國家組織的 Discord 成員，也是一個 AI 助手。你會參與頻道中的討論，提供有建設性的意見。

規則：
- 用繁體中文回覆
- 保持簡潔，通常 1-3 句話，最多不超過 5 句
- 有自己的觀點，不要只是附和或重複別人說的話
- 可以討論政策、法案、投票、組織運作等話題
- 如果不確定的事實，直接說不確定，不要編造
- 語氣自然輕鬆，像群組裡的一個朋友
- 不要使用 markdown 標題（## ###）
- 不要每次都長篇大論，有時候一句話就夠了
- 可以開玩笑，但不要冒犯別人
- 如果對話涉及微國家相關知識，你會收到微國家百科的查詢資料，請優先參考這些資料回答"""

chat_ai_settings = {
    "api_url": os.getenv("CHAT_AI_API_URL", "https://api.openai.com/v1/chat/completions"),
    "api_key": os.getenv("CHAT_AI_API_KEY", ""),
    "model": os.getenv("CHAT_AI_MODEL", "gpt-4o-mini"),
    "system_prompt": os.getenv("CHAT_AI_SYSTEM_PROMPT", DEFAULT_CHAT_AI_PROMPT),
    "enabled": False,
    "cooldown_seconds": 60,
    "channels_whitelist": [],  # empty = all channels
    "filter_strength": "mention",  # mention / off / low / medium / high
    "abuse_detection_enabled": False,
    "abuse_detection_strictness": "medium",  # low / medium / high
    "abuse_mute_admins": False,
    "log_channel_id": None,  # channel ID for AI action logs (mute, warnings, etc.)
    "micropedia_enabled": True,  # auto-lookup micropedia.site for micronation questions
    "micropedia_max_results": 5,  # max articles to fetch per query
}

CHAT_AI_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chat_ai_settings.json")

# Per-channel cooldown tracking
chat_cooldowns: dict = {}  # channel_id -> timestamp
# ──────────────────────────────────────────────
# Token 使用量追蹤（Token Usage Tracking）
# ──────────────────────────────────────────────

TOKEN_USAGE_FILE = os.path.join(DATA_DIR, "token_usage.json")
token_usage = {
    "total_tokens": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "api_calls": 0,
    "today_tokens": 0,
    "today_prompt": 0,
    "today_completion": 0,
    "today_calls": 0,
    "today_date": "",
    "started_at": 0,
}

def save_token_usage():
    try:
        with open(TOKEN_USAGE_FILE, "w", encoding="utf-8") as f:
            json_module.dump(token_usage, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Token usage save failed: {e}")

def load_token_usage():
    global token_usage
    try:
        if os.path.exists(TOKEN_USAGE_FILE):
            with open(TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
                started_at = loaded.get("started_at", 0)
                token_usage.update(loaded)
                if started_at:
                    token_usage["started_at"] = started_at
        if not token_usage.get("started_at"):
            token_usage["started_at"] = _time.time()
        today = _time.strftime("%Y-%m-%d")
        if token_usage.get("today_date") != today:
            token_usage["today_date"] = today
            token_usage["today_tokens"] = 0
            token_usage["today_prompt"] = 0
            token_usage["today_completion"] = 0
            token_usage["today_calls"] = 0
        print(f"✅ Token 使用量載入：累計 {token_usage['total_tokens']:,} tokens, 今日 {token_usage['today_tokens']:,} tokens")
    except Exception as e:
        print(f"⚠️ Token usage load failed: {e}")
        token_usage["started_at"] = _time.time()

def _track_token_usage(data: dict):
    usage = data.get("usage")
    if not usage or not isinstance(usage, dict):
        return
    total = usage.get("total_tokens", 0)
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    if not total and not prompt and not completion:
        return
    today = _time.strftime("%Y-%m-%d")
    if token_usage.get("today_date") != today:
        token_usage["today_date"] = today
        token_usage["today_tokens"] = 0
        token_usage["today_prompt"] = 0
        token_usage["today_completion"] = 0
        token_usage["today_calls"] = 0
    token_usage["total_tokens"] += total
    token_usage["prompt_tokens"] += prompt
    token_usage["completion_tokens"] += completion
    token_usage["api_calls"] += 1
    token_usage["today_tokens"] += total
    token_usage["today_prompt"] += prompt
    token_usage["today_completion"] += completion
    token_usage["today_calls"] += 1

# Per-USER generating lock (not per-channel) — different people in the same
# channel can get replies simultaneously; only the same user is blocked from
# having two in-flight requests at once (which would be redundant).
_user_generating: set = set()  # user_ids currently generating a reply
# Global concurrency cap: at most this many AI API calls in flight at the same
# time. Prevents API rate-limiting / connection exhaustion when many users hit
# the bot simultaneously. Extra requests wait their turn (don't get dropped).
_chat_semaphore: asyncio.Semaphore = None  # initialised in on_ready
# Shared aiohttp session for ALL outgoing HTTP calls (chat API + micropedia).
# Creating a new session per request is wasteful and exhausts connections under
# concurrency; one reusable session with connection pooling handles load far
# better.
_shared_session: aiohttp.ClientSession = None  # initialised in on_ready

# ──────────────────────────────────────────────
# 每用戶記憶系統（per-user memory）
# ──────────────────────────────────────────────

USER_MEMORIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "user_memories.json")
user_memories: dict = {}  # {user_id_str: {facts: [...], name: str, interaction_count: int, last_seen: float}}


def save_user_memories():
    try:
        os.makedirs(os.path.dirname(USER_MEMORIES_FILE), exist_ok=True)
        with open(USER_MEMORIES_FILE, "w", encoding="utf-8") as f:
            json_module.dump(user_memories, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Failed to save user memories: {e}")


def load_user_memories():
    global user_memories
    try:
        if os.path.exists(USER_MEMORIES_FILE):
            with open(USER_MEMORIES_FILE, "r", encoding="utf-8") as f:
                user_memories = json_module.load(f)
            # One-time purge of cross-contaminated memories (v1 fix)
            purge_flag = os.path.join(os.path.dirname(USER_MEMORIES_FILE), ".memory_purge_v2.done")
            if not os.path.exists(purge_flag):
                if user_memories:
                    print(f"🧹 記憶系統 v2 修復：清除所有舊記憶（可能已串號）")
                    user_memories = {}
                    save_user_memories()
                    with open(purge_flag, "w") as pf:
                        pf.write("done")
                else:
                    with open(purge_flag, "w") as pf:
                        pf.write("done")
            print(f"✅ 載入 {len(user_memories)} 位使用者記憶")
    except Exception as e:
        print(f"⚠️ Failed to load user memories: {e}")


def _update_user_memory(user_id: str, user_name: str, new_facts: list):
    """Update a user's memory with new facts (deduped, capped at 20)."""
    mem = user_memories.get(user_id, {
        "facts": [], "name": user_name,
        "interaction_count": 0, "last_seen": 0.0,
    })
    existing_lower = set(f.lower().strip() for f in mem.get("facts", []))
    for f in new_facts:
        f = f.strip()[:200]  # cap individual fact length
        if f and f.lower() not in existing_lower:
            mem["facts"].append(f)
            existing_lower.add(f.lower())
    # Cap at 20 facts (keep most recent)
    if len(mem["facts"]) > 20:
        mem["facts"] = mem["facts"][-20:]
    mem["name"] = user_name
    mem["interaction_count"] = mem.get("interaction_count", 0) + 1
    mem["last_seen"] = _time.time()
    user_memories[user_id] = mem
    save_user_memories()


def save_chat_ai_settings():
    try:
        os.makedirs(os.path.dirname(CHAT_AI_DATA_FILE), exist_ok=True)
        with open(CHAT_AI_DATA_FILE, "w", encoding="utf-8") as f:
            json_module.dump(chat_ai_settings, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Failed to save chat AI settings: {e}")


def load_chat_ai_settings():
    global chat_ai_settings
    try:
        if os.path.exists(CHAT_AI_DATA_FILE):
            with open(CHAT_AI_DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
            # Ensure filter_strength exists (migration for older saves)
            if "filter_strength" not in loaded:
                loaded["filter_strength"] = "mention"
            if "abuse_detection_enabled" not in loaded:
                loaded["abuse_detection_enabled"] = False
            if "abuse_detection_strictness" not in loaded:
                loaded["abuse_detection_strictness"] = "medium"
            if "abuse_mute_admins" not in loaded:
                loaded["abuse_mute_admins"] = False
            if "log_channel_id" not in loaded:
                loaded["log_channel_id"] = None
            if "micropedia_enabled" not in loaded:
                loaded["micropedia_enabled"] = True
            if "micropedia_max_results" not in loaded:
                loaded["micropedia_max_results"] = 5
            chat_ai_settings.update(loaded)
            print("✅ 載入 AI 聊天設定")
    except Exception as e:
        print(f"⚠️ Failed to load chat AI settings: {e}")


def _is_worth_replying(content: str, is_mentioned: bool, bot_id: int, strength: str = "low", is_reply_to_bot: bool = False) -> tuple:
    """Heuristic check: is this message worth an AI reply? Returns (worth, clean_content).
    Strength levels:
      mention — ONLY reply when @mentioned or replying to the bot (no random chime-in)
      off    — reply to everything (except empty)
      low    — only block pure greetings/empty/very short
      medium — block greetings + require questions/keywords for non-mentions
      high   — strict: long messages only, keywords required, no random
    """
    # Remove bot mention
    clean = content.replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "").strip()

    # Always: empty or whitespace-only
    if not clean:
        return False, clean

    # ── MENTION: ONLY reply when explicitly @mentioned ──
    if strength == "mention":
        if is_mentioned:
            return True, clean
        return False, clean

    # ── OFF: reply to everything ──
    if strength == "off":
        return True, clean

    # Common greeting / low-value sets
    greetings = {"hi", "hello", "hey", "yo", "ok", "okay", "lol", "haha", "nice",
                 "你好", "哈囉", "嗨", "測試", "test", "ping", "pong", "好的", "嗯",
                 "喔", "啊", "喔喔", "哈哈", "推", "+1", "ss", "早安", "晚安", "晚安",
                 "安安", "hihi", "哈囉哈囉", "好喔", "不要", "好啦"}
    normalized = clean.lower().strip("!.?？。！，,~～")
    is_greeting = normalized in greetings
    is_link_only = clean.startswith("http") and len(clean.split()) == 1
    is_emoji_only = not any(c.isalnum() for c in clean)

    # ── LOW: block only pure greetings, links, emoji-only, and <3 chars ──
    if strength == "low":
        if len(clean) < 3:
            return False, clean
        if is_greeting or is_link_only or is_emoji_only:
            return False, clean
        # @mention: almost anything goes
        if is_mentioned:
            return True, clean
        # Not mentioned: reply to anything that's not a greeting/link/emoji
        # This is the relaxed mode — normal conversation flows through
        return True, clean

    # ── MEDIUM: block greetings, require some substance ──
    if strength == "medium":
        if len(clean) < 5:
            return False, clean
        if is_greeting or is_link_only or is_emoji_only:
            return False, clean
        if is_mentioned:
            if len(clean) < 8:
                return False, clean
            return True, clean
        # Not mentioned: questions, keywords, or 15+ char messages
        if "?" in clean or "？" in clean:
            return True, clean
        keywords = ["投票", "議", "法案", "政策", "選舉", "建議", "想法", "討論",
                    "如何", "為什麼", "怎麼", "認為", "覺得", "提案", "決議", "規定",
                    "憲法", "入籍", "公民", "政府", "國家", "問題", " help", "幫忙"]
        if any(kw in clean for kw in keywords):
            return True, clean
        if len(clean) >= 15:
            return True, clean
        return False, clean

    # ── HIGH: strict, token-saving mode ──
    if strength == "high":
        if len(clean) < 5:
            return False, clean
        if is_greeting or is_link_only or is_emoji_only:
            return False, clean
        if is_mentioned:
            if len(clean) < 10:
                return False, clean
            return True, clean
        # Not mentioned: questions and keywords only, no random chance
        if "?" in clean or "？" in clean:
            return True, clean
        keywords = ["投票", "議", "法案", "政策", "選舉", "建議", "想法", "討論",
                    "如何", "為什麼", "怎麼", "認為", "覺得", "提案", "決議", "規定",
                    "憲法", "入籍", "公民", "政府", "國家"]
        if any(kw in clean for kw in keywords):
            return True, clean
        return False, clean

    # Fallback: same as low
    return True, clean


async def call_chat_api(messages: list, settings: dict, tools: list = None) -> dict:
    """Call the chat AI API (non-streaming, short replies).
    Returns the raw assistant message dict (content + possible tool_calls),
    so the caller can drive a tool-calling loop when `tools` is provided.
    Automatically degrades to a plain (no-tools) call if this endpoint has
    already been observed to reject the `tools` field, or if this specific
    request with tools fails for ANY reason — different OpenAI-compatible
    proxies report an unsupported `tools` param with wildly different status
    codes (400, 422, 500, or even a 200 with an error payload instead of
    `choices`), so we don't try to guess which one and instead just retry
    plain whenever a tools-enabled call doesn't come back clean."""
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }
    api_url = settings["api_url"].rstrip("/")
    if not api_url.endswith("/chat/completions"):
        if api_url.endswith("/v1"):
            api_url += "/chat/completions"
        elif api_url.endswith("/v2"):
            api_url += "/chat/completions"
        else:
            api_url += "/v1/chat/completions"

    use_tools = tools if (tools and api_url not in _tools_unsupported_apis) else None

    async def _post(payload, timeout_total=300, timeout_read=120):
        """Streaming-aware POST: always uses stream=True to keep the
        connection alive on slow endpoints. Creates a FRESH session per
        call (like the briefing function does) to avoid any session-level
        timeout interference from _shared_session. The sock_read timeout
        only applies BETWEEN chunks — once the first chunk arrives, the
        timer resets."""
        payload = {**payload, "stream": True}
        t = aiohttp.ClientTimeout(total=timeout_total, connect=15, sock_read=timeout_read)
        # Always create a fresh session — no session-level timeout to interfere
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=t) as resp:
                return await _read_stream(resp)

    async def _read_stream(resp):
        """Read an SSE stream and return (status, json_body) where json_body
        is a synthetic non-stream response containing the accumulated message."""
        if resp.status != 200:
            error_text = await resp.text()
            print(f"⚠️ API returned status {resp.status}: {error_text[:200]}")
            return resp.status, error_text

        content_parts = []
        tool_calls_acc = {}  # index -> {id, name, arguments}
        finish_reason = None
        _first_chunk_time = None
        _chunk_count = 0

        async for raw_line in resp.content:
            _chunk_count += 1
            if _first_chunk_time is None:
                _first_chunk_time = _time.time()
                print(f"📦 第一個 chunk 到達")
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json_module.loads(data_str)
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr
                # Accumulate content
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
                # Accumulate tool_calls (they come as deltas across chunks)
                if "tool_calls" in delta:
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.get("id"):
                            tool_calls_acc[idx]["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            tool_calls_acc[idx]["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            tool_calls_acc[idx]["function"]["arguments"] += fn["arguments"]
            except Exception:
                continue

        # Build a synthetic non-stream response body
        message = {"role": "assistant", "content": "".join(content_parts)}
        if tool_calls_acc:
            message["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        body = json_module.dumps({"choices": [{"message": message, "finish_reason": finish_reason or "stop"}]})
        _elapsed = _first_chunk_time - _time.time() if _first_chunk_time else 0
        _total_chars = sum(len(p) for p in content_parts)
        print(f"📦 串流完成：{_chunk_count} chunks, content={_total_chars} chars, tool_calls={len(tool_calls_acc)}")
        return 200, body

    payload = {
        "model": settings.get("model", "gpt-4o-mini"),
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 300,
    }
    if use_tools:
        payload["tools"] = use_tools
        payload["tool_choice"] = "auto"

    # When trying with tools, use a SHORTER timeout (12s) — if the endpoint
    # hangs on the tools param (common with non-OpenAI proxies), we fail fast
    # and retry without tools at the normal timeout, instead of burning 30s.
    ok = False
    data = None
    _t0 = _time.time()
    try:
        status, body_text = await _post(payload)
        print(f"⏱️ call_chat_api: _post 耗時 {_time.time()-_t0:.1f}s (status={status}, tools={'yes' if use_tools else 'no'})")
    except (asyncio.TimeoutError, Exception) as e:
        if use_tools:
            # The endpoint HUNG on the tools param (didn't return an error,
            # just sat there until our timeout fired). Mark it as unsupported
            # and retry immediately WITHOUT tools at the normal timeout.
            print(f"⚠️ Chat AI 端點帶 tools 參數逾時/錯誤（{type(e).__name__}: {e}），判定不支援 tools，立即重試...")
            _tools_unsupported_apis.add(api_url)
            save_tools_unsupported()
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
            status, body_text = await _post(payload)
        else:
            raise

    if status == 200:
        try:
            data = json_module.loads(body_text)
            if "choices" in data:
                # Check if streaming returned empty content — API may not
                # support streaming and returned a regular JSON response that
                # _read_stream couldn't parse as SSE chunks.
                _msg = data["choices"][0].get("message", {})
                _content = _msg.get("content", "")
                _tc = _msg.get("tool_calls")
                if not _content and not _tc:
                    print(f"⚠️ 串流回應為空（API 可能不支援 streaming），回退為非串流模式重試...")
                    payload_ns = {k: v for k, v in payload.items() if k != "stream"}
                    # Actually we need to rebuild without stream=True
                    payload_ns = dict(payload)
                    payload_ns.pop("stream", None)
                    t2 = aiohttp.ClientTimeout(total=300, connect=15, sock_read=120)
                    async with aiohttp.ClientSession() as sess:
                        async with sess.post(api_url, json=payload_ns, headers=headers, timeout=t2) as resp2:
                            if resp2.status == 200:
                                body_text = await resp2.text()
                                data = json_module.loads(body_text)
                                if "choices" in data:
                                    ok = True
                                    if use_tools:
                                        _tools_supported_apis.add(api_url)
                                        save_tools_supported()
                                    print(f"✅ 非串流回退成功")
                            else:
                                print(f"⚠️ 非串流回退也失敗：status={resp2.status}")
                else:
                    ok = True
                    if use_tools:
                        _tools_supported_apis.add(api_url)
                        save_tools_supported()
        except Exception as e:
            print(f"⚠️ 解析回應失敗：{e}")

    if not ok and use_tools:
        # Endpoint returned a non-200 or malformed response WITH tools —
        # assume it doesn't support function calling and never try again.
        print(f"⚠️ Chat AI 端點帶 tools 參數呼叫失敗（status={status}），之後略過 tools：{body_text[:200]}")
        _tools_unsupported_apis.add(api_url)
        save_tools_unsupported()
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        status, body_text = await _post(payload)
        if status == 200:
            try:
                data = json_module.loads(body_text)
                ok = "choices" in data
            except Exception:
                ok = False

    if not ok:
        raise Exception(f"Chat AI API returned {status}: {body_text[:300]}")

    _track_token_usage(data)

    return data["choices"][0]["message"]


# ──────────────────────────────────────────────
# 伺服器結構感知（Server Context Cache）
# ──────────────────────────────────────────────

# {guild_id: {"data": {...}, "updated": timestamp}}
_server_context_cache: dict = {}
_SERVER_CONTEXT_TTL = 600  # 10 分鐘快取


async def _refresh_server_context(guild):
    """Fetch and cache server structure: channels, roles, emojis, members."""
    try:
        # Channels
        channels = []
        for ch in guild.text_channels:
            cat = ch.category.name if ch.category else "無分類"
            channels.append({"name": ch.name, "category": cat, "topic": (ch.topic or "")[:80]})
        channels.sort(key=lambda c: (c["category"], c["name"]))

        # Roles (exclude @everyone and managed/integration roles)
        roles = []
        for r in sorted(guild.roles, key=lambda x: -x.position):
            if r.name == "@everyone" or r.managed:
                continue
            roles.append({"name": r.name, "color": str(r.color), "member_count": len(r.members)})

        # Custom emojis
        emojis = []
        for e in guild.emojis:
            emojis.append({"name": e.name, "animated": e.animated, "id": str(e.id)})
        # Limit to 50 to save tokens
        emojis = emojis[:50]

        # Members (only those with roles or online, max 80)
        members = []
        for m in guild.members:
            if m.bot:
                continue
            member_roles = [r.name for r in m.roles if r.name != "@everyone" and not r.managed]
            members.append({
                "name": m.display_name,
                "roles": member_roles[:5],  # top 5 roles
                "nick": m.nick or "",
                "is_admin": m.guild_permissions.administrator or m.guild_permissions.manage_guild,
            })
        members.sort(key=lambda m: (-m["is_admin"], m["name"]))
        members = members[:80]

        _server_context_cache[guild.id] = {
            "data": {
                "guild_name": guild.name,
                "member_count": guild.member_count,
                "channels": channels,
                "roles": roles,
                "emojis": emojis,
                "members": members,
            },
            "updated": _time.time(),
        }
        print(f"🔄 伺服器結構已更新：{guild.name}（{len(channels)} 頻道, {len(roles)} 身分組, {len(emojis)} emoji, {len(members)} 成員）")
    except Exception as e:
        print(f"⚠️ 伺服器結構更新失敗（{guild.name}）：{e}")


async def _get_server_context(guild, user) -> str:
    """Get compact server context string for the AI prompt."""
    # Refresh if stale or not cached
    cached = _server_context_cache.get(guild.id)
    if not cached or (_time.time() - cached["updated"] > _SERVER_CONTEXT_TTL):
        await _refresh_server_context(guild)
        cached = _server_context_cache.get(guild.id)

    if not cached:
        return ""

    d = cached["data"]
    lines = []
    lines.append(f"─── 伺服器結構：{d['guild_name']}（{d['member_count']} 成員）───")

    # Channels — just names, max 30, no category grouping (saves ~2000 chars)
    ch_names = [f"#{ch['name']}" for ch in d["channels"][:30]]
    if len(d["channels"]) > 30:
        ch_names.append(f"...（共 {len(d['channels'])} 個）")
    lines.append(f"\n📁 頻道（{len(d['channels'])} 個）：{', '.join(ch_names)}")

    # Roles (compact)
    lines.append(f"\n🏷️ 身分組：{', '.join(r['name'] for r in d['roles'][:20])}")

    # Emojis — ONLY show ones with aliases (others are useless to AI and waste tokens)
    if d["emojis"]:
        emoji_tokens = []
        for e in d["emojis"]:
            alias = emoji_aliases.get(e["name"])
            if alias:
                prefix = "a" if e.get("animated") else ""
                token = f"<{prefix}:{e['name']}:{e['id']}>"
                label = alias.get("alias", "")
                emoji_tokens.append(f"{token}（={label}）")
        if emoji_tokens:
            lines.append(
                f"\n😀 伺服器自訂 Emoji（使用時必須完整照抄 <...> 文字）：\n"
                f"{' '.join(emoji_tokens)}"
            )

    # Current user's identity in this server
    user_roles = [r.name for r in user.roles if r.name != "@everyone" and not r.managed]
    is_admin = user.guild_permissions.administrator or user.guild_permissions.manage_guild
    lines.append(f"\n👤 當前使用者：「{user.display_name}」")
    if user.nick:
        lines.append(f"  暱稱：{user.nick}")
    lines.append(f"  身分組：{', '.join(user_roles) if user_roles else '（無）'}")
    lines.append(f"  權限：{'管理員' if is_admin else '一般成員'}")

    # Other members — only admins + role-bearing members, max 15
    other_members = [m for m in d["members"] if m["name"] != user.display_name]
    notable = [m for m in other_members if m["is_admin"] or m.get("roles")]
    if notable:
        member_summary = []
        for m in notable[:15]:
            tag = "★" if m["is_admin"] else (f"({m['roles'][0]})" if m.get("roles") else "")
            member_summary.append(f"{m['name']}{tag}")
        lines.append(f"\n👥 成員（★=管理員）：{', '.join(member_summary)}")

    return "\n".join(lines)


async def server_context_refresh_loop():
    """Background task: refresh server context every 10 minutes for all guilds."""
    await asyncio.sleep(60)  # Wait for bot to be ready
    while True:
        try:
            for guild in bot.guilds:
                await _refresh_server_context(guild)
                await asyncio.sleep(1)  # stagger to avoid rate limits
        except Exception as e:
            print(f"⚠️ 伺服器結構背景更新失敗：{e}")
        await asyncio.sleep(_SERVER_CONTEXT_TTL)


async def forum_index_refresh_loop():
    """Background task: refresh the forum-post search index every 15 minutes.
    Runs fully decoupled from any single user query's time budget — a query
    just reads whatever is cached (bigram matching only, no network calls),
    so this refresh can take as long as it needs without ever risking a
    reply-pipeline timeout."""
    await asyncio.sleep(90)  # Wait for bot to be ready, after server context
    while True:
        try:
            for guild in bot.guilds:
                await _refresh_forum_index(guild)
                await asyncio.sleep(1)  # stagger to avoid rate limits
        except Exception as e:
            print(f"⚠️ Forum 索引背景更新失敗：{e}")
        await asyncio.sleep(_FORUM_INDEX_TTL)


# ──────────────────────────────────────────────
# 濫用偵測系統（Abuse Detection）
# ──────────────────────────────────────────────

# Per-user abuse tracking: {user_id_str: {message_times: [...], warnings: int, total_mutes: int, last_mute_time: float}}
abuse_tracker: dict = {}
# Mod action log: [{user_id, user_name, action, duration, reason, timestamp, channel}]
mod_action_log: list = []
MOD_LOG_MAX = 50

# Severe slur / hate keywords (fast path, no AI needed)
_SEVERE_KEYWORDS = [
    "死gay", "死Gay", "死GAY",
    "nigger", "nigga", "Nigger", "NIGGA",
    "chink", "Chink", "CHINK",
    "retard", "Retard", "RETARD",
    "faggot", "Faggot", "FAGGOT",
    "反人類", "種族滅絕",
]

# Flood thresholds: (messages_in_window, window_seconds)
_FLOOD_THRESHOLDS = {
    "low":    (12, 30),   # 12 msgs in 30s
    "medium": (8, 30),    # 8 msgs in 30s
    "high":   (5, 20),    # 5 msgs in 20s
}

# Escalating mute durations (seconds): [1st, 2nd, 3rd, 4th+]
_MUTE_ESCALATION = {
    "low":    [300, 600, 1800, 3600],       # 5m, 10m, 30m, 1h
    "medium": [600, 1800, 3600, 21600],      # 10m, 30m, 1h, 6h
    "high":   [1800, 3600, 21600, 86400],    # 30m, 1h, 6h, 24h
}


def _track_flood(user_id: str, strictness: str) -> bool:
    """Track message frequency. Returns True if flooding detected."""
    now = _time.time()
    threshold_count, window_secs = _FLOOD_THRESHOLDS.get(strictness, _FLOOD_THRESHOLDS["medium"])

    tracker = abuse_tracker.setdefault(user_id, {"message_times": [], "warnings": 0, "total_mutes": 0, "last_mute_time": 0})
    times = tracker["message_times"]
    times.append(now)
    # Prune old entries outside the window
    cutoff = now - window_secs
    tracker["message_times"] = [t for t in times if t > cutoff]

    return len(tracker["message_times"]) >= threshold_count


def _check_severe_keywords(content: str) -> str | None:
    """Fast-path check for severe slurs. Returns the matched keyword or None."""
    lower = content.lower()
    for kw in _SEVERE_KEYWORDS:
        if kw.lower() in lower:
            return kw
    return None


def _get_mute_duration(user_id: str, strictness: str, severity_override: int = 0) -> int:
    """Get mute duration based on offense count and strictness.
    severity_override: if >0, use this duration directly (from AI judgment)."""
    if severity_override > 0:
        # Cap at 86400 (24h)
        return min(severity_override, 86400)

    tracker = abuse_tracker.get(user_id, {"warnings": 0, "total_mutes": 0, "last_mute_time": 0})
    offense = tracker["total_mutes"]
    escalation = _MUTE_ESCALATION.get(strictness, _MUTE_ESCALATION["medium"])
    idx = min(offense, len(escalation) - 1)
    return escalation[idx]


async def _resolve_log_channel(guild):
    """Resolve the configured log channel, with cache-miss fallback to a live fetch.
    Returns (channel_or_None, error_reason_or_None)."""
    log_ch_id = chat_ai_settings.get("log_channel_id")
    if not log_ch_id:
        return None, "未設定 log_channel_id"
    if not guild:
        return None, "沒有 guild 物件"

    log_ch = guild.get_channel(log_ch_id)
    if log_ch:
        return log_ch, None

    # Cache miss — try a live fetch (covers freshly-created channels or cache gaps)
    try:
        log_ch = await guild.fetch_channel(log_ch_id)
        return log_ch, None
    except discord.NotFound:
        return None, f"頻道 ID {log_ch_id} 不存在（可能已被刪除，請重新設定 /chat log_channel）"
    except discord.Forbidden:
        return None, f"Bot 沒有權限查看頻道 ID {log_ch_id}（請確認 Bot 在該頻道有 View Channel 權限）"
    except Exception as e:
        return None, f"取得頻道失敗：{e}"


async def _send_chat_log(message, user_content: str, ai_reply: str, channel_name: str = ""):
    """Send a conversation log to the designated log channel."""
    if not chat_ai_settings.get("log_channel_id"):
        return  # not configured, nothing to do
    if not message.guild:
        print("⚠️ 對話紀錄：訊息沒有 guild（私訊？），略過")
        return

    try:
        log_ch, err = await _resolve_log_channel(message.guild)
    except Exception as e:
        print(f"⚠️ 對話紀錄發送失敗（_resolve_log_channel 例外）：{e}")
        return
    if not log_ch:
        print(f"⚠️ 對話紀錄發送失敗：{err}")
        return
    print(f"📝 已解析紀錄頻道：#{getattr(log_ch, 'name', '?')} ({log_ch.id})")

    try:
        author = message.author
        user_text = user_content[:300]
        if len(user_content) > 300:
            user_text += "..."
        ai_text = ai_reply[:1000]
        if len(ai_reply) > 1000:
            ai_text += "..."

        embed = discord.Embed(
            title="💬 AI 對話紀錄",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=f"👤 {author.display_name}",
            value=f"> {user_text}",
            inline=False
        )
        embed.add_field(
            name="🤖 AI 回覆",
            value=f"> {ai_text}",
            inline=False
        )
        ch_name = channel_name or (message.channel.name if hasattr(message.channel, "name") else "?")
        embed.set_footer(text=f"#{ch_name} | User ID: {author.id}")
        await log_ch.send(embed=embed)
        print(f"📝 對話紀錄已發送到 #{log_ch.name}")
    except discord.Forbidden:
        print(f"⚠️ 對話紀錄發送失敗：Bot 沒有在 #{getattr(log_ch, 'name', '?')} 發送訊息/嵌入的權限")
    except Exception as e:
        print(f"⚠️ 對話紀錄發送失敗：{e}")


async def _execute_mute(message, duration: int, reason: str):
    """Execute Discord timeout on the message author."""
    member = message.author
    guild = message.guild
    if not guild or not member:
        return False

    # Skip admins unless abuse_mute_admins is True
    if not chat_ai_settings.get("abuse_mute_admins", False):
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            print(f"🛡️ 濫用偵測：跳過管理員 {member.display_name}（abuse_mute_admins=False）")
            return False

    # Check bot permissions
    bot_member = guild.get_member(bot.user.id)
    if not bot_member or not bot_member.guild_permissions.moderate_members:
        print(f"🛡️ 濫用偵測：Bot 沒有 Moderate Members 權限，無法禁言")
        try:
            await message.channel.send(f"⚠️ 偵測到濫用行為但 Bot 缺少「禁言成員」權限。請給 Bot「Moderate Members」權限。")
        except Exception:
            pass
        return False

    try:
        until = discord.utils.utcnow() + datetime.timedelta(seconds=duration)
        await member.timeout(until, reason=reason)

        tracker = abuse_tracker.setdefault(str(member.id), {"message_times": [], "warnings": 0, "total_mutes": 0, "last_mute_time": 0})
        tracker["total_mutes"] += 1
        tracker["last_mute_time"] = _time.time()

        mod_action_log.append({
            "user_id": str(member.id),
            "user_name": member.display_name,
            "action": "mute",
            "duration": duration,
            "reason": reason,
            "timestamp": _time.time(),
            "channel": str(message.channel.name) if hasattr(message.channel, "name") else "?",
        })
        if len(mod_action_log) > MOD_LOG_MAX:
            mod_action_log[:] = mod_action_log[-MOD_LOG_MAX:]

        print(f"🛡️ 濫用偵測：已禁言 {member.display_name} {duration}秒，原因：{reason}")

        # Send log to designated channel if configured
        if chat_ai_settings.get("log_channel_id"):
            try:
                log_ch, log_err = await _resolve_log_channel(guild)
                if not log_ch:
                    print(f"⚠️ 禁言紀錄發送失敗：{log_err}")
                if log_ch:
                    log_embed = discord.Embed(
                        title="🛡️ AI 自動禁言",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    log_embed.add_field(name="使用者", value=f"{member.mention} ({member.display_name})", inline=False)
                    log_embed.add_field(name="禁言時長", value=f"{duration // 60} 分鐘 ({duration} 秒)", inline=True)
                    log_embed.add_field(name="頻道", value=f"#{message.channel.name}" if hasattr(message.channel, "name") else "?", inline=True)
                    log_embed.add_field(name="原因", value=reason, inline=False)
                    log_embed.add_field(name="違規次數", value=f"第 {tracker['total_mutes']} 次", inline=True)
                    offense_text = message.content[:200]
                    if len(message.content) > 200:
                        offense_text += "..."
                    log_embed.add_field(name="觸發訊息", value=f"> {offense_text}", inline=False)
                    log_embed.set_footer(text=f"User ID: {member.id}")
                    await log_ch.send(embed=log_embed)
            except Exception as log_err:
                print(f"⚠️ Log 頻道發送失敗：{log_err}")

        return True
    except Exception as e:
        print(f"🛡️ 濫用偵測：禁言失敗：{e}")
        return False


# ──────────────────────────────────────────────
# 微國家百科 (micropedia.site) 整合 — AI 工具呼叫 (function calling)
#
# 設計：不再由我們自己猜測「該不該查」、「查什麼字」。改成把搜尋能力包成一個
# 工具（search_micropedia）交給 AI，讓 AI 自己決定何時查、查什麼關鍵字，
# 查不到還能自己換關鍵字重試 —— 這比我們寫死的關鍵字/去除問句語尾的heuristic
# 準確得多（micropedia.site 用的是陽春 MySQL 全文搜尋，不支援中文斷詞，所以
# 「山海事件」查不到「山海密謀事件」，但「山海」可以 —— 這種細節只有 AI
# 自己嘗試不同關鍵字才能摸索出來）。
# 同時直接呼叫 MediaWiki 官方 API（action=query&list=search / prop=revisions），
# 全部是正式 JSON API，不是抓取渲染後的 HTML 頁面。
# ──────────────────────────────────────────────

_micropedia_cache: dict = {}  # query -> (timestamp, content)
_MICROPEDIA_CACHE_TTL = 600  # 10 minutes

# Per api_url: whether the endpoint has confirmed to reject the "tools" field.
# Once we learn a given endpoint doesn't support function calling, we stop
# paying the cost of trying (and failing) on every single message.
# Persisted to disk — without this, EVERY hot restart re-pays the double
# network round-trip (try-with-tools, fail, retry-without-tools) on the very
# next message, which is a major source of the reply pipeline timing out.
_tools_unsupported_apis: set = set()
_tools_supported_apis: set = set()
_TOOLS_SUPPORTED_FILE = os.path.join(DATA_DIR, "tools_supported_apis.json")


def save_tools_supported():
    """Persist the set of API URLs known to support tool calling."""
    try:
        with open(_TOOLS_SUPPORTED_FILE, "w") as f:
            json_module.dump(list(_tools_supported_apis), f)
    except Exception as e:
        print(f"⚠️ save_tools_supported failed: {e}")


def load_tools_supported():
    """Load known tool-supporting API URLs from disk."""
    global _tools_supported_apis
    try:
        if os.path.exists(_TOOLS_SUPPORTED_FILE):
            with open(_TOOLS_SUPPORTED_FILE, "r") as f:
                _tools_supported_apis = set(json_module.load(f))
            print(f"🔧 已載入 tools 支援白名單：{len(_tools_supported_apis)} 個端點")
    except Exception as e:
        print(f"⚠️ load_tools_supported failed: {e}")
TOOLS_UNSUPPORTED_FILE = os.path.join(DATA_DIR, "tools_unsupported_apis.json")

def save_tools_unsupported():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(TOOLS_UNSUPPORTED_FILE, "w", encoding="utf-8") as f:
            json_module.dump(list(_tools_unsupported_apis), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ tools_unsupported_apis save failed: {e}")

def load_tools_unsupported():
    global _tools_unsupported_apis
    try:
        if os.path.exists(TOOLS_UNSUPPORTED_FILE):
            with open(TOOLS_UNSUPPORTED_FILE, "r", encoding="utf-8") as f:
                _tools_unsupported_apis = set(json_module.load(f))
            if _tools_unsupported_apis:
                print(f"✅ tools_unsupported_apis 載入：{_tools_unsupported_apis}（略過 tools 參數，直接用純文字呼叫）")
    except Exception as e:
        print(f"⚠️ tools_unsupported_apis load failed: {e}")

_MICROPEDIA_SKIP_PREFIXES = ("特殊:", "File:", "Category:", "Template:", "Help:",
                             "Special:", "MediaWiki:", "User:", "Talk:", "Project:", "分類:")

# ── Bigram title-matching: robust auto-search that doesn't depend on the AI's
# tool-calling ability or judgment ──
#
# Real-world finding: micropedia.site's own search (list=search) does basic
# MySQL matching with NO Chinese word segmentation. A full phrase like
# "山海事件" or "琉璃是誰" returns ZERO hits even though the real articles
# ("山海密謀事件", "琉璃") plainly exist — only a bare substring/short term
# reliably matches. Relying on the AI to guess the "right" short term (via
# tool calling) only works if the LLM provider actually supports tool calling
# (many self-hosted / third-party OpenAI-compatible proxies quietly don't).
#
# So instead: cache the wiki's full list of page titles (a few thousand — cheap
# to hold in memory) and do our OWN fuzzy matching in Python using Chinese
# character bigrams (2-char sliding windows). This runs automatically on every
# message, for free, with no dependency on tool-calling support at all — it
# directly fixes the "琉璃是誰" / "山海事件" style failures we saw in production.
_micropedia_titles_cache = {"titles": [], "fetched_at": 0.0}
_MICROPEDIA_TITLES_TTL = 6 * 3600  # refresh the title list every 6 hours


def _bigrams(s: str) -> set:
    """2-char sliding-window bigrams of a string (with whitespace stripped).
    Single-character strings fall back to the character itself."""
    import re as _re
    s = _re.sub(r"\s+", "", s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _fuzzy_match_titles(message: str, titles: list, top_n: int = 5) -> list:
    """Score every cached wiki title against the message by bigram containment
    (what fraction of the TITLE's bigrams also appear in the message) and
    return the best matching titles. Tuned against real production queries:
    - short titles (<=3 chars / <=2 bigrams) require FULL containment, to
      avoid one common bigram matching tons of unrelated short titles.
    - longer titles require >=60% containment AND >=2 overlapping bigrams,
      to avoid weak, coincidental single-bigram matches.
    This deliberately favors precision — silently finding nothing is fine
    (no context gets injected); a bad/irrelevant match is not."""
    msg_bg = _bigrams(message)
    if not msg_bg:
        return []
    scored = []
    for t in titles:
        tbg = _bigrams(t)
        if not tbg:
            continue
        overlap = msg_bg & tbg
        if not overlap:
            continue
        containment = len(overlap) / len(tbg)
        if len(tbg) <= 2:
            if containment < 1.0:
                continue
        else:
            if containment < 0.4 or len(overlap) < 2:
                continue
        scored.append((t, containment, len(overlap), len(t)))
    # Best containment first, then more overlap, then shorter/more specific title
    scored.sort(key=lambda x: (-x[1], -x[2], x[3]))
    return [t for t, _, _, _ in scored[:top_n]]


async def _fetch_all_micropedia_titles(session) -> list:
    """Fetch every page title on the wiki via the MediaWiki allpages API
    (paginated, ~4000+ pages as of writing — small enough to hold in memory).
    JSON API only, no scraping."""
    import urllib.parse as _up
    all_titles = []
    apfrom = ""
    timeout = aiohttp.ClientTimeout(total=8, connect=3)
    for _ in range(30):  # hard cap on pagination loops as a safety net
        url = f"https://www.micropedia.site/api.php?action=query&list=allpages&aplimit=500&format=json"
        if apfrom:
            url += f"&apfrom={_up.quote(apfrom)}"
        async with session.get(url, headers={"User-Agent": "DiscordBot (micropedia-integration/1.0)"}, timeout=timeout) as resp:
            if resp.status != 200:
                break
            data = await resp.json()
        pages = data.get("query", {}).get("allpages", [])
        all_titles.extend(p["title"] for p in pages)
        apcontinue = data.get("continue", {}).get("apcontinue")
        if not apcontinue:
            break
        apfrom = apcontinue
    return [t for t in all_titles if not any(t.startswith(p) for p in _MICROPEDIA_SKIP_PREFIXES)]


async def _get_micropedia_titles(session) -> list:
    """Cached accessor for the full title list — refreshes every 6h."""
    now = _time.time()
    if _micropedia_titles_cache["titles"] and (now - _micropedia_titles_cache["fetched_at"] < _MICROPEDIA_TITLES_TTL):
        return _micropedia_titles_cache["titles"]
    try:
        titles = await _fetch_all_micropedia_titles(session)
        if titles:
            _micropedia_titles_cache["titles"] = titles
            _micropedia_titles_cache["fetched_at"] = now
            print(f"📚 Micropedia: 已快取 {len(titles)} 個頁面標題")
        return _micropedia_titles_cache["titles"]
    except Exception as e:
        print(f"📚 Micropedia: 標題清單取得失敗：{e}")
        return _micropedia_titles_cache["titles"]  # stale cache (possibly empty) is still safe to use


async def _micropedia_auto_context(message_text: str, max_results: int = 5) -> str:
    """Automatically find and fetch relevant micropedia articles for a chat
    message — runs on EVERY message (cheap: cached titles + in-memory bigram
    scoring), with NO dependency on the AI deciding to search or on tool-calling
    support. This is what actually fixes real-world misses like '琉璃是誰' /
    '山海事件' where the wiki's own search returns nothing for the full phrase."""
    if not message_text or len(message_text.strip()) < 2:
        return ""
    try:
        own_session = False
        session = _shared_session if (_shared_session and not _shared_session.closed) else None
        if session is None:
            session = aiohttp.ClientSession()
            own_session = True
        try:
            titles = await _get_micropedia_titles(session)
            if not titles:
                return ""
            matched = _fuzzy_match_titles(message_text, titles, top_n=max_results)
            if not matched:
                return ""
            print(f"📚 Micropedia: 自動比對到 {len(matched)} 篇文章: {matched}")
            content = await _micropedia_fetch_content(session, matched)
            return content
        finally:
            if own_session:
                await session.close()
    except Exception as e:
        print(f"📚 Micropedia: 自動比對錯誤：{e}")
        return ""


def _clean_wikitext(text: str) -> str:
    """Remove MediaWiki markup to get clean text."""
    import re as _re
    # Remove templates {{...}} (non-nested)
    text = _re.sub(r"\{\{[^}]*\}\}", "", text)
    # Remove wiki links [[link|display]] -> display
    text = _re.sub(r"\[\[[^]]*?\|([^]]*)\]\]", r"\1", text)
    text = _re.sub(r"\[\[([^]]*)\]\]", r"\1", text)
    # Remove external links [url text] -> text
    text = _re.sub(r"\[https?://\S+\s+([^]]*)\]", r"\1", text)
    text = _re.sub(r"\[https?://\S+\]", "", text)
    # Remove HTML tags
    text = _re.sub(r"<[^>]+>", "", text)
    # Remove wiki tables {| ... |}
    text = _re.sub(r"\{\|.*?\|\}", "", text, flags=_re.DOTALL)
    # Remove headings markup =...=
    text = _re.sub(r"^=+\s*(.*?)\s*=+$", r"\1", text, flags=_re.MULTILINE)
    # Remove list markers
    text = _re.sub(r"^[\*#:]+\s*", "", text, flags=_re.MULTILINE)
    # Remove excess whitespace
    text = _re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


async def _micropedia_search_api(session, query: str, max_results: int) -> list:
    """Search micropedia.site via the official MediaWiki Search API (JSON,
    action=query&list=search) — NOT html scraping. Returns a list of matching
    article titles (empty if no hits)."""
    import urllib.parse as _up
    timeout = aiohttp.ClientTimeout(total=6, connect=3)
    url = (
        f"https://www.micropedia.site/api.php?action=query&list=search"
        f"&srsearch={_up.quote(query)}&format=json&srlimit={max_results}&utf8=1"
    )
    async with session.get(
        url, headers={"User-Agent": "DiscordBot (micropedia-integration/1.0)"}, timeout=timeout
    ) as resp:
        if resp.status != 200:
            print(f"📚 Micropedia: 搜尋 API 回傳 {resp.status}")
            return []
        data = await resp.json()
    hits = data.get("query", {}).get("search", [])
    titles = []
    for h in hits:
        title = h.get("title", "")
        if title and not any(title.startswith(p) for p in _MICROPEDIA_SKIP_PREFIXES):
            titles.append(title)
    return titles


async def _micropedia_fetch_content(session, titles: list) -> str:
    """Fetch article content for the given titles via the MediaWiki content API
    (action=query&prop=revisions&rvprop=content) — JSON API, not scraping."""
    import urllib.parse as _up
    if not titles:
        return ""
    timeout = aiohttp.ClientTimeout(total=6, connect=3)
    titles_param = "|".join(_up.quote(t) for t in titles)
    api_url = (
        f"https://www.micropedia.site/api.php?action=query"
        f"&titles={titles_param}"
        f"&prop=revisions&rvprop=content&format=json&redirects=1"
    )
    async with session.get(
        api_url, headers={"User-Agent": "DiscordBot (micropedia-integration/1.0)"}, timeout=timeout
    ) as resp:
        if resp.status != 200:
            print(f"📚 Micropedia: 內容 API 回傳 {resp.status}")
            return ""
        data = await resp.json()

    content_parts = []
    pages = data.get("query", {}).get("pages", {})
    for pid, page in pages.items():
        if pid == "-1" or "missing" in page:
            continue
        revs = page.get("revisions", [])
        if not revs:
            continue
        wikitext = revs[0].get("*", "")
        if not wikitext or len(wikitext) < 10:
            continue
        clean = _clean_wikitext(wikitext)
        if clean and len(clean) > 10:
            title = page.get("title", "?")
            if len(clean) > 2000:
                clean = clean[:2000] + "..."
            content_parts.append(f"【{title}】\n{clean}")

    return "\n\n".join(content_parts)


async def _fetch_micropedia_inner(query: str, max_results: int = 5) -> str:
    """Single search + content-fetch attempt (one query, no internal retries —
    the AI itself decides whether/how to retry with a different query via the
    search_micropedia tool). Returns formatted article content, or empty string."""
    if not query:
        return ""

    cache_key = query.lower()
    if cache_key in _micropedia_cache:
        cached_time, cached_content = _micropedia_cache[cache_key]
        if _time.time() - cached_time < _MICROPEDIA_CACHE_TTL:
            print(f"📚 Micropedia: 使用快取結果 for '{query}'")
            return cached_content

    try:
        own_session = False
        session = _shared_session if (_shared_session and not _shared_session.closed) else None
        if session is None:
            session = aiohttp.ClientSession()
            own_session = True
        try:
            print(f"📚 Micropedia: 搜尋 '{query}'")
            titles = await _micropedia_search_api(session, query, max_results)
            if not titles:
                print(f"📚 Micropedia: 搜尋 '{query}' 沒有結果")
                _micropedia_cache[cache_key] = (_time.time(), "")
                return ""
            print(f"📚 Micropedia: 找到 {len(titles)} 篇相關文章: {titles[:5]}")
            result = await _micropedia_fetch_content(session, titles)
            _micropedia_cache[cache_key] = (_time.time(), result)
            print(f"📚 Micropedia: 取得內容 ({len(result)} chars)")
            return result
        finally:
            if own_session:
                await session.close()
    except asyncio.TimeoutError:
        print(f"📚 Micropedia: 搜尋逾時 for '{query}'")
        return ""
    except Exception as e:
        print(f"📚 Micropedia: 錯誤 for '{query}': {e}")
        return ""


async def _fetch_micropedia(query: str, max_results: int = 5) -> str:
    """Thin wrapper enforcing a hard overall time budget (8s) on a single
    micropedia lookup, regardless of network conditions — guarantees a tool
    call the AI makes never meaningfully stalls the reply pipeline."""
    try:
        return await asyncio.wait_for(_fetch_micropedia_inner(query, max_results), timeout=8)
    except asyncio.TimeoutError:
        print(f"📚 Micropedia: 整體查詢逾時（>8s），放棄 for '{query}'")
        return ""


# ── Discord 歷史搜尋工具 ──
# 兩個資料來源，平行查詢後合併結果：
# 1. 論壇貼文本地索引 — Discord 論壇頻道的貼文（例如提案、罷免案）常常是用
#    表單/webhook 送出，內文其實是空的，真正的文字內容全部塞在 Embed 裡。
#    Discord 官方 guild search API 只比對純文字 content 欄位，比對不到
#    embed 內容，所以這類貼文永遠搜不到——這正是黃綠燈罷免案搜不到的原因。
#    我們自己把每個論壇貼文的標題、標籤、內文、embed 全部串起來做本地索引，
#    用跟 micropedia 一樣的 bigram 比對法搜尋，快取在記憶體，即時比對零延遲。
# 2. Discord guild search API — 一般文字頻道的純文字訊息歷史搜尋。
# 不受時間限制，有史以來的訊息/貼文都能搜到。

_forum_index_cache: dict = {}  # {guild_id: {"posts": [...], "updated": ts}}
_FORUM_INDEX_TTL = 900  # 15 分鐘快取


async def _refresh_forum_index(guild) -> list:
    """Scan every Forum channel in the guild and build a searchable index of
    every post (thread): title + tags + OP text/embed content, PLUS the
    thread's REPLY messages (up to 50 most recent). This matters a lot:
    status updates like "案子已撤回" / "新秘書長已選出" are almost always
    posted as REPLIES within the discussion thread, not edits to the
    original post. Without reading replies, the AI only ever sees the
    proposal as it looked at creation time and thinks it's still ongoing
    long after it was resolved/withdrawn. Embeds are also critical — many
    proposal/policy forums are submitted via a bot/webhook that posts a
    pure embed with EMPTY message content."""
    posts = []
    try:
        for forum in guild.forums:
            threads = list(forum.threads)  # active posts (already cached)
            try:
                async for t in forum.archived_threads(limit=100):
                    threads.append(t)
            except Exception as e:
                print(f"⚠️ 無法取得「{forum.name}」的封存貼文：{e}")

            for thread in threads:
                try:
                    starter = thread.starter_message
                    if starter is None:
                        try:
                            starter = await thread.fetch_message(thread.id)
                        except Exception:
                            starter = None

                    tags = [t.name for t in thread.applied_tags]
                    text_parts = [thread.name]
                    if tags:
                        text_parts.append(" ".join(tags))
                    if starter:
                        if starter.content:
                            text_parts.append(starter.content)
                        for embed in starter.embeds:
                            if embed.title:
                                text_parts.append(str(embed.title))
                            if embed.description:
                                text_parts.append(str(embed.description))
                            for field in embed.fields:
                                text_parts.append(f"{field.name} {field.value}")

                    # Fetch reply messages — this is what captures status
                    # updates (withdrawals, resolutions, follow-ups) that
                    # happen AFTER the original post.
                    reply_lines = []
                    try:
                        async for msg in thread.history(limit=50, oldest_first=True):
                            if starter and msg.id == starter.id:
                                continue
                            body = msg.content.strip()
                            if not body:
                                for embed in msg.embeds:
                                    if embed.title:
                                        body += str(embed.title) + " "
                                    if embed.description:
                                        body += str(embed.description) + " "
                                body = body.strip()
                            if not body:
                                continue
                            date_str = msg.created_at.strftime("%Y-%m-%d") if msg.created_at else "?"
                            author = msg.author.display_name if msg.author else "未知"
                            reply_lines.append(f"[{date_str}] {author}: {body[:200]}")
                    except Exception:
                        pass

                    if reply_lines:
                        text_parts.append("─── 討論串回覆（含後續進展/狀態更新）───")
                        text_parts.extend(reply_lines)

                    last_activity = thread.created_at
                    if reply_lines:
                        try:
                            last_activity = [m async for m in thread.history(limit=1)][0].created_at
                        except Exception:
                            pass

                    posts.append({
                        "title": thread.name,
                        "tags": tags,
                        "channel_name": forum.name,
                        "text": "\n".join(str(p) for p in text_parts if p),
                        "reply_lines": reply_lines,  # kept separate so we can always surface the LATEST update
                        "url": thread.jump_url,
                        "author": (starter.author.display_name if starter and starter.author else "未知"),
                        "created_at": thread.created_at.strftime("%Y-%m-%d") if thread.created_at else "",
                        "last_activity": last_activity.strftime("%Y-%m-%d") if last_activity else "",
                    })
                except Exception as e:
                    print(f"⚠️ 索引貼文失敗（{getattr(thread, 'name', '?')}）：{e}")
                    continue
    except Exception as e:
        print(f"⚠️ Forum 索引刷新失敗：{e}")

    _forum_index_cache[guild.id] = {"posts": posts, "updated": _time.time()}
    forum_count = len(list(guild.forums)) if hasattr(guild, "forums") else 0
    total_replies = sum(len(p.get("reply_lines", [])) for p in posts)
    print(f"🗂️ Forum 索引已更新：{guild.name} — {len(posts)} 篇貼文（{forum_count} 個論壇頻道，共索引 {total_replies} 則回覆）")
    return posts


async def _get_forum_index(guild) -> list:
    """Read the cached forum index. The background forum_index_refresh_loop
    keeps this warm every 15 minutes, so this is just a cheap dict read in
    the common case. Only does a live (blocking) refresh if the cache is
    completely empty — e.g. a query arrives before the bot's first
    background refresh pass has completed after startup."""
    cached = _forum_index_cache.get(guild.id)
    if cached is not None:
        return cached["posts"]
    return await _refresh_forum_index(guild)


def _search_forum_posts(query: str, posts: list, top_n: int = 5) -> list:
    """Fuzzy-match a query against indexed forum posts using bigram
    containment (same technique as micropedia title matching), scored by how
    much of the QUERY's bigrams are found in the post's combined text —
    plus a direct substring check, since forum text is long/noisy and a
    short exact term (e.g. a proper noun) should always count as a hit."""
    query = query.strip()
    query_bg = _bigrams(query)
    if not query_bg or not posts:
        return []
    scored = []
    for p in posts:
        text = p["text"]
        text_bg = _bigrams(text)
        if not text_bg:
            continue
        overlap = query_bg & text_bg
        substring_hit = bool(query) and query in text
        if not overlap and not substring_hit:
            continue
        containment = len(overlap) / len(query_bg) if query_bg else 0
        if not substring_hit and containment < 0.5:
            continue
        scored.append((p, containment, substring_hit))
    scored.sort(key=lambda x: (-x[2], -x[1]))
    return [p for p, _, _ in scored[:top_n]]


async def _live_guild_message_search(guild, query: str, limit: int = 10) -> str:
    """Search Discord guild message history (plain-text content only) via
    Discord's guild search API endpoint. No time limit — searches all of
    history. Does NOT see embed content — that's what the forum index above
    covers separately."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token or not guild or not query.strip():
        return ""

    query = query.strip()
    headers = {"Authorization": f"Bot {token}"}
    timeout = aiohttp.ClientTimeout(total=7, connect=3)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            search_url = (
                f"https://discord.com/api/v9/guilds/{guild.id}/messages/search"
                f"?content={urllib.parse.quote(query)}&limit={limit}"
            )
            async with session.get(search_url, headers=headers) as resp:
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "5")
                    print(f"⏳ Discord search rate limited, retry after {retry_after}s")
                    return ""
                if resp.status != 200:
                    err = await resp.text()
                    print(f"⚠️ Discord search failed ({resp.status}): {err[:300]}")
                    return ""
                data = await resp.json()

            messages = []
            for group in data.get("messages", []):
                for msg in group:
                    messages.append(msg)
            if not messages:
                return ""

            lines = [f"🔍 頻道訊息搜尋「{query}」的結果（{len(messages)} 則）："]
            for i, msg in enumerate(messages[:limit], 1):
                author = msg.get("author", {}).get("username", "未知")
                content = msg.get("content", "").strip()
                timestamp = msg.get("timestamp", "")
                content = re.sub(r"<@!?\d+>", "@用戶", content)[:300]
                lines.append(f"\n[{i}] {author} ({timestamp[:10] if timestamp else '?'}): {content}")
            return "\n".join(lines)[:2000]
    except asyncio.TimeoutError:
        print(f"⚠️ Discord 訊息搜尋逾時 for '{query}'")
        return ""
    except Exception as e:
        print(f"⚠️ Discord search error: {e}")
        return ""


async def _search_discord_history_inner(guild, query: str, limit: int = 10) -> str:
    """Runs the forum-index search and the live message search CONCURRENTLY
    and merges both into one result string for the AI."""
    if not guild or not query.strip():
        return "搜尋條件不足"
    query = query.strip()

    async def _forum_part():
        try:
            posts = await _get_forum_index(guild)
            matched = _search_forum_posts(query, posts, top_n=5)
            if not matched:
                return ""
            parts = [
                f"📋 論壇貼文搜尋「{query}」的結果（{len(matched)} 篇）：\n"
                f"⚠️ 重要：每篇貼文下面若有「最新進展」，那才是目前的真實狀態"
                f"（可能已撤案/已通過/已否決/已有後續結果），絕對不要只看原始貼文內容就下結論，"
                f"原始提案內容可能早就過時了。"
            ]
            for p in matched:
                # OP snippet stays short — the important part is the LATEST
                # updates, which are shown in full below regardless of match
                # score (a naive text[:500] truncation would show only the
                # stale original post and cut off exactly the status update
                # that matters).
                op_text = p["text"].split("─── 討論串回覆", 1)[0]
                snippet = op_text[:350]
                tag_str = f"［{'/'.join(p['tags'])}］" if p["tags"] else ""
                block = (
                    f"\n【{p['channel_name']}】{tag_str}《{p['title']}》"
                    f"— {p['author']}, 發起於 {p['created_at']}\n原始內容：{snippet}"
                )
                reply_lines = p.get("reply_lines") or []
                if reply_lines:
                    last_activity = p.get("last_activity", "")
                    recent = reply_lines[-5:]  # last 5 replies = latest status
                    block += (
                        f"\n📌 最新進展（最後活動：{last_activity}）：\n" + "\n".join(recent)
                    )
                else:
                    block += "\n（此貼文下沒有任何回覆，狀態可能從未更新過）"
                # Deliberately NOT including the raw jump_url here — some
                # weak models see a URL and hallucinate a refusal like "I
                # can't access Discord links directly", even when nothing
                # in the user's message was a link at all.
                parts.append(block)
            return "\n".join(parts)
        except Exception as e:
            print(f"⚠️ Forum 索引搜尋失敗：{e}")
            return ""

    forum_result, live_result = await asyncio.gather(
        asyncio.wait_for(_forum_part(), timeout=3),  # cache read + in-memory bigram match — should be instant
        asyncio.wait_for(_live_guild_message_search(guild, query, limit), timeout=8),
        return_exceptions=True,
    )
    if isinstance(forum_result, Exception):
        forum_result = ""
    if isinstance(live_result, Exception):
        live_result = ""

    combined = "\n\n".join(r for r in (forum_result, live_result) if r)
    if not combined:
        return f"沒有找到包含「{query}」的訊息或論壇貼文"
    return combined[:3500]


async def _search_discord_history(guild, query: str, limit: int = 10) -> str:
    """Thin wrapper enforcing a hard overall time budget (10s) regardless of
    network conditions — guarantees this tool call never meaningfully stalls
    the reply pipeline (same pattern as _fetch_micropedia)."""
    try:
        return await asyncio.wait_for(_search_discord_history_inner(guild, query, limit), timeout=10)
    except asyncio.TimeoutError:
        print(f"⚠️ Discord 搜尋整體逾時（>10s），放棄 for '{query}'")
        return "搜尋逾時，換個關鍵字試試看"


_DISCORD_SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_discord",
        "description": (
            "搜尋這個 Discord 伺服器的歷史紀錄，找出包含指定關鍵字的內容，包含兩種來源："
            "(1) 論壇頻道的貼文（提案、罷免案、政策討論等——含標題、標籤、內文），"
            "(2) 一般文字頻道的訊息歷史。可以搜尋所有頻道、有史以來的內容，沒有時間限制。"
            "當使用者問到過去發生的事、歷史事件、某個提案/罷免案、某人說過什麼、"
            "之前的決定或討論時，呼叫這個工具搜尋。"
            "建議用簡短的核心關鍵字（人名、事件名、提案名稱關鍵詞），不要太長的句子。"
            "可以呼叫多次嘗試不同關鍵字（例如完整名稱查不到就拆成更短的核心詞）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵字，建議用簡短的核心詞（人名、事件核心詞、提案關鍵詞等），不要整句問句"
                }
            },
            "required": ["query"],
        },
    },
}


_MICROPEDIA_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_micropedia",
        "description": (
            "搜尋微國家百科 (micropedia.site)，取得關於微國家歷史、人物、事件、組織、"
            "條約等的正式資料。當使用者問到任何你不確定、可能是組織內部術語或專有名詞的"
            "人事時地物時，呼叫這個工具查證，不要憑印象亂猜或編造。"
            "這個 wiki 的搜尋是陽春的全文比對，不支援中文斷詞——如果完整片語查不到，"
            "試試看拆成更短的核心詞（例如「山海事件」查不到就試「山海」）。"
            "可以呼叫多次嘗試不同關鍵字。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵字，建議用簡短的核心詞（人名、事件核心詞、國名等），不要整句問句"
                }
            },
            "required": ["query"],
        },
    },
}




def _fix_emoji_shortcodes(text: str, guild) -> str:
    """Safety net: convert any bare ':name:' shortcode in the AI's reply into
    the actual Discord custom-emoji render syntax '<:name:id>' (or
    '<a:name:id>' for animated). Checks three sources in order:
    1. Direct match against real emoji names in the guild
    2. Match against alias names (e.g. AI writes ':偉廷微笑:' → becomes the
       real emoji <:emoji7:123456789012345678>)
    3. If no match, leave as-is (could be a normal word or time like 10:30)"""
    if not text or not guild or not guild.emojis:
        return text

    emoji_by_name = {e.name: e for e in guild.emojis}
    # Also build a lookup by alias name → emoji object
    emoji_by_alias = {}
    for orig_name, alias_data in emoji_aliases.items():
        alias_label = alias_data.get("alias", "")
        if alias_label:
            eid = alias_data.get("emoji_id", "")
            for e in guild.emojis:
                if str(e.id) == eid:
                    emoji_by_alias[alias_label] = e
                    break

    if not emoji_by_name and not emoji_by_alias:
        return text

    # Skip anything that's already a proper Discord emoji tag: <:name:id> or <a:name:id>
    already_tagged_spans = set()
    for m in re.finditer(r"<a?:\w+:\d+>", text):
        already_tagged_spans.add((m.start(), m.end()))

    def _replace(m):
        start, end = m.span()
        for s, e in already_tagged_spans:
            if s <= start and end <= e:
                return m.group(0)
        name = m.group(1)
        emoji = emoji_by_name.get(name)
        if not emoji:
            emoji = emoji_by_alias.get(name)
        if not emoji:
            return m.group(0)
        prefix = "a" if emoji.animated else ""
        return f"<{prefix}:{emoji.name}:{emoji.id}>"

    return re.sub(r":(\w+):", _replace, text)


async def generate_chat_reply(message, settings: dict) -> tuple:
    """Generate a reply for a chat message with brief context, server awareness, and per-user memory.
    Returns (reply_text, new_facts_or_None, mod_action_or_None)."""
    user_id = str(message.author.id)
    user_name = message.author.display_name

    # Load user memory
    mem = user_memories.get(user_id, {})
    facts = mem.get("facts", [])

    # Build system prompt with memory — STRICTLY scoped to current user
    system_prompt = settings["system_prompt"]

    # Inject server context (channels, roles, emojis, members, current user identity)
    if message.guild:
        try:
            server_ctx = await _get_server_context(message.guild, message.author)
            if server_ctx:
                system_prompt += f"\n\n{server_ctx}"
        except Exception as e:
            print(f"⚠️ 伺服器結構取得失敗：{e}")

    system_prompt += f"\n\n你現在正在和「{user_name}」對話，請直接針對這句話回答。"

    if facts:
        memory_text = "\n".join(f"- {f}" for f in facts)
        system_prompt += f"\n\n你對「{user_name}」的記憶：\n{memory_text}"
    else:
        system_prompt += f"\n\n你目前對「{user_name}」沒有記憶。"

    system_prompt += (
        f"\n\n─── 記憶提取規則 ───\n"
        f"回覆最後加一行 [MEMORY: ...]，只記住「{user_name}」本人說的關於自己的資訊。"
        f"\n- 只記 {user_name} 親口說的事（身分、偏好、近況等）"
        f"\n- 如果 {user_name} 沒有提到關於自己的新資訊，寫 [MEMORY: none]"
        f"\n- 這行不會顯示給用戶看"
    )

    # Add abuse detection instruction if enabled
    if settings.get("abuse_detection_enabled", False):
        strictness = settings.get("abuse_detection_strictness", "medium")
        strict_desc = {"low": "寬容", "medium": "標準", "high": "嚴格"}.get(strictness, "標準")
        system_prompt += (
            f"\n\n─── 濫用偵測（已啟用，{strict_desc}）───\n"
            f"你有權判斷使用者是否濫用，並建議禁言時長。\n"
            f"判定標準：\n"
            f"- 辱罵、歧視、仇恨言論 → 嚴重（建議 1800-86400 秒）\n"
            f"- 瘋狂廢話刷屏、無意義騷擾 → 中等（建議 300-1800 秒）\n"
            f"- 輕微挑釁、態度不佳 → 輕微（建議 60-300 秒）\n"
            f"- 正常玩笑、抱怨、討論 → 不禁言\n"
            f"如果需要禁言，在回覆最後加一行 [MOD: 秒數]\n"
            f"例如：[MOD: 600] 表示禁言 10 分鐘\n"
            f"正常對話不加 [MOD:]。這行不會顯示給用戶看。\n"
            f"⚠️ 請謹慎使用，只在真正濫用時才建議禁言。"
        )

    # NOTE: deliberately NOT including other users' recent messages as
    # "context" anymore — this was causing the AI to answer a completely
    # different topic that other people happened to be chatting about,
    # instead of the actual question being asked. Only the current user's
    # own message is sent now. Also reduces prompt size (helps latency).
    bot_id = bot.user.id
    clean_content = message.content.replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "").strip()

    full_prompt = f"{user_name}: {clean_content}"

    # ── Micropedia ──
    # Two layers, so this works regardless of whether the AI provider even
    # supports tool calling:
    # 1. AUTO context injection (always runs, no AI judgment needed): fuzzy
    #    bigram-match the raw message against the wiki's full cached title
    #    list and inject any matched articles' content directly. This is what
    #    actually fixes real misses like "琉璃是誰" / "山海事件" where the
    #    wiki's own search returns zero hits for the full phrase.
    # 2. The search_micropedia TOOL, for models that support tool calling, to
    #    dig further with a different term if the auto context isn't enough.
    micropedia_enabled = settings.get("micropedia_enabled", True)
    max_results = settings.get("micropedia_max_results", 5)

    if micropedia_enabled and len(clean_content) >= 4:
        # Skip micropedia for very short messages (emoji-only, "好", etc.)
        # to avoid inflating the system prompt with 2000+ chars of wiki content
        # that the AI has to process on a slow free API
        try:
            auto_context = await asyncio.wait_for(
                _micropedia_auto_context(clean_content, max_results), timeout=10
            )
        except asyncio.TimeoutError:
            print(f"📚 Micropedia: 自動比對逾時（>10s），跳過")
            auto_context = ""
        if auto_context:
            system_prompt += (
                f"\n\n─── 微國家百科資料（已自動比對到相關文章）───\n"
                f"以下是根據使用者訊息，自動從微國家百科 (micropedia.site) 比對到的相關文章。"
                f"請優先參考這些資料來回答問題；如果資料裡沒有直接答案，可以合理推論，"
                f"但不要無中生有捏造細節。\n{auto_context}"
            )
            print(f"📚 Micropedia: 已自動注入 {len(auto_context)} chars 到 AI 上下文")

        system_prompt += (
            f"\n\n─── search_micropedia 工具 ───\n"
            f"你還有一個 search_micropedia 工具，可以再查詢微國家百科的其他資料"
            f"（例如上面自動比對到的資料不夠、或你懷疑還有其他相關文章時）。"
            f"當使用者問到任何組織內部的人事時地物、事件、專有名詞，而你不確定或沒印象時，"
            f"呼叫這個工具查證，不要憑印象亂猜或編造內容。"
            f"如果查詢的關鍵字找不到結果，試試看換成更短的核心詞再查一次"
            f"（這個 wiki 的搜尋不支援中文斷詞，完整片語常常查不到，但拆開的核心詞可以）。"
            f"如果自動比對和你的查詢都找不到資料，才誠實告知使用者你沒有找到相關資料。"
        )

    # Build tool list FIRST so we know whether search_discord is available
    # before constructing the system prompt (avoids adding ~500 chars of
    # search_discord instructions when the tool won't even be sent).
    _norm = settings.get("api_url", "").rstrip("/")
    if not _norm.endswith("/chat/completions"):
        if _norm.endswith("/v1") or _norm.endswith("/v2"):
            _norm += "/chat/completions"
        else:
            _norm += "/v1/chat/completions"
    # WHITELIST approach: only send tools if we've CONFIRMED the endpoint
    # supports them (via startup probe or previous successful tools call).
    _tools_supported = _norm in _tools_supported_apis
    _tools_unsup = _norm in _tools_unsupported_apis
    tools_ok = _tools_supported and not _tools_unsup

    tools = []
    _search_discord_available = False
    if tools_ok:
        if micropedia_enabled:
            tools.append(_MICROPEDIA_TOOL_SCHEMA)
        tools.append(_DISCORD_SEARCH_TOOL_SCHEMA)
        _search_discord_available = True
    elif micropedia_enabled and not _tools_unsup and not _tools_supported:
        # Endpoint not yet tested — send micropedia tool only (known to work
        # in the past) but NOT search_discord (new, might slow things down).
        tools.append(_MICROPEDIA_TOOL_SCHEMA)
    tools = tools if tools else None

    # Only add search_discord instructions to system prompt when the tool
    # is actually available — avoids inflating the payload with instructions
    # for a tool the AI can't even call (which slows down the API response
    # on heavily-loaded free endpoints).
    if _search_discord_available:
        system_prompt += (
            f"\n\n─── search_discord 工具（搜尋伺服器歷史：論壇貼文 + 訊息）───\n"
            f"你有一個 search_discord 工具，會同時搜尋兩種內容：\n"
            f"1. 論壇頻道的貼文（提案、罷免案、政策/規範討論等）——這些內容微國家百科"
            f"通常不會記載，只存在於 Discord 伺服器裡\n"
            f"2. 一般文字頻道的訊息歷史\n"
            f"搜尋沒有時間限制，有史以來的內容都能找到。"
            f"當使用者問到：\n"
            f"- 過去發生過的事、歷史事件\n"
            f"- 任何提案、罷免案、政策討論（這些很可能只存在論壇貼文，百科查不到）\n"
            f"- 某人之前說過什麼、做過什麼\n"
            f"- 之前的決定、投票、討論\n"
            f"- 任何「以前」、「之前」、「上次」相關的問題\n"
            f"請呼叫這個工具搜尋相關關鍵字。如果完整名稱查不到，試試看拆成更短的核心詞再查一次"
            f"（例如「黃綠燈罷免案」查不到就試「黃綠燈」或「罷免」）。"
            f"找到的內容會標示來源、發言者、日期和內容，"
            f"你可以據此回答使用者的問題；如果找不到，才誠實告知使用者。\n"
            f"⚠️ 極重要：論壇貼文的「原始內容」只是提案剛發起時的樣子，事情很可能早就有後續發展"
            f"（撤案、通過、否決、換人等）。一定要看「最新進展」欄位裡最後幾則回覆的日期和內容，"
            f"那才是目前真正的狀態。如果最新進展顯示案子已經結束/撤回/有結果，"
            f"就不要再用原始提案的角度回答，要以最新狀態為準。\n"
            f"⚠️ 工具回傳的文字內容本身就是完整答案來源，不是網址或連結，"
            f"你不需要、也沒有能力瀏覽任何網頁。絕對不要回覆「我無法查看連結」"
            f"「Discord 訊息需要透過客戶端存取」之類的話——你收到的搜尋結果已經是純文字內容，"
            f"直接根據內容回答使用者的問題就好，不要提到「連結」或「網址」這件事。"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": full_prompt},
    ]

    async def _run_tool_loop():
        """Drive the tool-calling round-trip — capped at EXACTLY 2 LLM calls
        total, no matter what. Round 0 gets tools (the model can request
        several tool_calls at once in a single response — this still lets it
        "try a few queries", just not across multiple separate round-trips).
        Round 1 is always forced to a plain text answer with no tools.
        This hard cap matters because each individual call_chat_api call
        already has its own timeout/retry safety net — but if the endpoint is
        merely SLOW (not hung) at ~20-30s per call, allowing 3 total round-trips
        (as the previous version did, with tools available on 2 of them) could
        by itself exceed the outer time budget. Capping at 2 keeps worst-case
        total latency bounded and predictable."""
        t0 = _time.time()
        msgs = messages
        assistant_msg = await call_chat_api(msgs, settings, tools=tools)
        print(f"⏱️ Round 1（{'含 tools' if tools else '無 tools'}）耗時 {_time.time()-t0:.1f}s")
        tool_calls = assistant_msg.get("tool_calls")
        if not tool_calls:
            return assistant_msg.get("content") or ""

        # Model wants to search — execute each requested call (possibly
        # several at once), then let it see all results and give a final answer.
        t1 = _time.time()
        msgs = msgs + [assistant_msg]
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            call_id = tc.get("id")
            if name == "search_micropedia":
                try:
                    args = json_module.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                query = (args.get("query") or "").strip()
                print(f"🔧 AI 呼叫 search_micropedia('{query}')")
                result = await _fetch_micropedia(query, max_results) if query else ""
                tool_content = result if result else "沒有找到相關資料，試試看換一個更短或不同的關鍵字。"
            elif name == "search_discord":
                try:
                    args = json_module.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                query = (args.get("query") or "").strip()
                print(f"🔧 AI 呼叫 search_discord('{query}')")
                if query and message.guild:
                    result = await _search_discord_history(message.guild, query, limit=10)
                else:
                    result = "無法搜尋（沒有 guild 或搜尋詞為空）"
                tool_content = result if result else "沒有找到相關訊息，試試看換一個不同的關鍵字。"
            else:
                tool_content = f"未知工具：{name}"
            msgs = msgs + [{
                "role": "tool",
                "tool_call_id": call_id,
                "content": tool_content[:3000],
            }]
        print(f"⏱️ 工具執行（{len(tool_calls)} 個）耗時 {_time.time()-t1:.1f}s")

        # Final round — ALWAYS plain text, no tools. Capped at 2 calls total.
        t2 = _time.time()
        final_msg = await call_chat_api(msgs, settings, tools=None)
        print(f"⏱️ Round 2（最終答案，無 tools）耗時 {_time.time()-t2:.1f}s，總計 {_time.time()-t0:.1f}s")
        return final_msg.get("content") or ""

    # Hard overall cap on the whole AI round-trip (covers all tool rounds),
    # so no matter how many searches happen the reply pipeline never stalls
    # indefinitely — this is the outer safety net on top of each call's own timeout.
    try:
        raw_reply = await asyncio.wait_for(_run_tool_loop(), timeout=90)
    except asyncio.TimeoutError:
        print(f"⚠️ AI 回覆流程整體逾時（>90s）")
        raise
    except Exception as e:
        # Something about the tool-calling machinery itself broke (bad response
        # shape, provider quirk we didn't anticipate, etc.) — never let that take
        # the whole chat feature down. Fall back to one plain, tool-free call.
        print(f"⚠️ 工具呼叫流程失敗，改用純文字模式重試：{e}")
        fallback_msg = await asyncio.wait_for(
            call_chat_api(messages, settings, tools=None), timeout=30
        )
        raw_reply = fallback_msg.get("content") or ""

    # Parse [MEMORY:] and [MOD:] tags from reply
    actual_reply = raw_reply
    new_facts = None
    mod_action = None

    if "[MOD:" in actual_reply:
        parts = actual_reply.rsplit("[MOD:", 1)
        actual_reply = parts[0].strip()
        mod_str = parts[1].rstrip("]").strip()
        try:
            mod_seconds = int(mod_str)
            if mod_seconds > 0:
                mod_action = mod_seconds
        except ValueError:
            pass

    if "[MEMORY:" in actual_reply:
        parts = actual_reply.rsplit("[MEMORY:", 1)
        actual_reply = parts[0].strip()
        memory_str = parts[1].rstrip("]").strip()
        if memory_str.lower() != "none" and memory_str:
            new_facts = [f.strip() for f in memory_str.split(",") if f.strip()]

    # Fix any bare :name: emoji shortcodes into real Discord render syntax
    if actual_reply and message.guild:
        actual_reply = _fix_emoji_shortcodes(actual_reply, message.guild)

    # Final safety: if reply is empty after all parsing, return None so caller can handle
    if not actual_reply:
        actual_reply = None

    return actual_reply, new_facts, mod_action


# ──────────────────────────────────────────────
# Dashboard API: Chat AI settings
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# Google Drive 檔案儲存（替代 Render 付費硬碟）
# 環境變數：
#   GOOGLE_SERVICE_ACCOUNT_B64 - Base64 編碼的服務帳號 JSON
#   GOOGLE_DRIVE_FOLDER_ID      - Drive 資料夾 ID（選填，建議設定）
# ──────────────────────────────────────────────

_drive_token_cache = {"token": None, "expires": 0.0}


async def _get_drive_access_token():
    """Get a Google API access token.
    Prefers OAuth refresh token (personal Drive quota — works with free Gmail).
    Falls back to service account JWT (only works with Shared Drives / Workspace)."""
    if _drive_token_cache["token"] and _time.time() < _drive_token_cache["expires"]:
        return _drive_token_cache["token"]

    # ── Method 1: OAuth refresh token (personal account, has real storage quota) ──
    refresh_token = os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")
    # Use GOOGLE_CLIENT_ID if set, otherwise fall back to OAUTH_CLIENT_ID (Discord OAuth)
    # since users often only have one set of OAuth credentials
    client_id = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "") or os.getenv("OAUTH_CLIENT_SECRET", "")

    if refresh_token and client_id and client_secret:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        result = json_module.loads(text)
                        _drive_token_cache["token"] = result["access_token"]
                        _drive_token_cache["expires"] = _time.time() + result.get("expires_in", 3600) - 60
                        return _drive_token_cache["token"]
                    print(f"⚠️ Drive OAuth refresh 失敗（{resp.status}）：{text[:400]}")
        except Exception as e:
            print(f"⚠️ Drive OAuth refresh 例外：{e}")
        # Don't fall through silently if OAuth was configured but failed —
        # still try service account below in case it's also set up.

    # ── Method 2: Service account JWT (needs Shared Drive / Workspace to actually store files) ──
    if not pyjwt:
        if not (refresh_token and client_id and client_secret):
            print("⚠️ Drive: 未設定 OAuth（GOOGLE_DRIVE_REFRESH_TOKEN）且 PyJWT 未安裝，無法使用服務帳號備援。")
        return None

    creds_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
    if not creds_b64:
        if not (refresh_token and client_id and client_secret):
            print("⚠️ Drive: 未設定 GOOGLE_DRIVE_REFRESH_TOKEN（OAuth）也未設定 GOOGLE_SERVICE_ACCOUNT_B64（服務帳號）。")
        return None

    try:
        creds_info = json_module.loads(base64.b64decode(creds_b64).decode())
    except Exception as e:
        print(f"⚠️ Drive auth 失敗：GOOGLE_SERVICE_ACCOUNT_B64 解碼/解析錯誤：{e}")
        return None

    if creds_info.get("type") != "service_account" or "private_key" not in creds_info:
        print(f"⚠️ Drive auth 失敗：JSON 不是服務帳號金鑰（缺少 type=service_account 或 private_key）。"
              f" 目前的 keys: {list(creds_info.keys())}")
        return None

    try:
        now = int(_time.time())
        payload = {
            "iss": creds_info["client_email"],
            "scope": "https://www.googleapis.com/auth/drive",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        token = pyjwt.encode(payload, creds_info["private_key"], algorithm="RS256")
        if isinstance(token, bytes):
            token = token.decode()

        form_data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": token,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://oauth2.googleapis.com/token",
                data=form_data,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    print(f"⚠️ Drive auth 失敗：token 端點回傳 {resp.status}：{text[:400]}")
                    return None
                result = json_module.loads(text)

        _drive_token_cache["token"] = result["access_token"]
        _drive_token_cache["expires"] = _time.time() + 3000
        return result["access_token"]
    except Exception as e:
        print(f"⚠️ Drive auth failed: {e}")
        return None


async def _drive_upload(filename: str, content: str, return_detail: bool = False):
    """Upload (create or update) a file on Google Drive.
    If return_detail=True, returns (success: bool, detail: str). Otherwise returns bool."""
    def _ret(ok, detail):
        return (ok, detail) if return_detail else ok

    token = await _get_drive_access_token()
    if not token:
        return _ret(False, "無法取得存取權杖（見上方 auth 錯誤）")

    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with aiohttp.ClientSession() as session:
            # Search for existing file
            query = f"name='{filename}' and trashed=false"
            if folder_id:
                query += f" and '{folder_id}' in parents"
            search_url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name)"
            async with session.get(search_url, headers=headers) as resp:
                search_text = await resp.text()
                if resp.status != 200:
                    detail = f"搜尋失敗（HTTP {resp.status}）：{search_text[:400]}"
                    print(f"⚠️ Drive 搜尋 {filename} 失敗（{resp.status}）：{search_text[:400]}")
                    return _ret(False, detail)
                data = json_module.loads(search_text)
            existing = data.get("files", [])

            if existing:
                # Update existing file
                file_id = existing[0]["id"]
                upload_url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media"
                async with session.patch(
                    upload_url,
                    headers={**headers, "Content-Type": "application/json"},
                    data=content.encode("utf-8"),
                ) as resp:
                    if resp.status in (200, 204):
                        print(f"✅ Drive 已更新 {filename}")
                        return _ret(True, "更新成功")
                    err = await resp.text()
                    detail = f"更新失敗（HTTP {resp.status}）：{err[:500]}"
                    print(f"⚠️ Drive 更新 {filename} 失敗（{resp.status}）：{err[:400]}")
                    return _ret(False, detail)
            else:
                # Create new file via multipart upload (RFC 2046 requires CRLF line endings)
                import uuid as _uuid
                boundary = _uuid.uuid4().hex

                metadata = {"name": filename}
                if folder_id:
                    metadata["parents"] = [folder_id]

                body = (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json_module.dumps(metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json\r\n\r\n"
                    f"{content}\r\n"
                    f"--{boundary}--"
                ).encode("utf-8")

                upload_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,parents"
                async with session.post(
                    upload_url,
                    headers={**headers, "Content-Type": f"multipart/related; boundary={boundary}"},
                    data=body,
                ) as resp:
                    resp_text = await resp.text()
                    if resp.status in (200, 201):
                        print(f"✅ Drive 已建立 {filename}：{resp_text[:200]}")
                        return _ret(True, "建立成功")
                    detail = f"建立失敗（HTTP {resp.status}）：{resp_text[:500]}"
                    print(f"⚠️ Drive 建立 {filename} 失敗（{resp.status}）：{resp_text[:400]}")
                    return _ret(False, detail)
    except Exception as e:
        detail = f"例外錯誤：{e}"
        print(f"⚠️ Drive upload failed ({filename}): {e}")
        return _ret(False, detail)


async def _drive_list_files() -> list:
    """List all files currently in the configured Google Drive folder.
    Returns a list of {"id": ..., "name": ...} dicts, or [] on any failure."""
    token = await _get_drive_access_token()
    if not token:
        return []
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            query = "trashed=false"
            if folder_id:
                query += f" and '{folder_id}' in parents"
            list_url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name)&pageSize=200"
            async with session.get(list_url, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    print(f"⚠️ Drive 列出檔案失敗（{resp.status}）：{text[:400]}")
                    return []
                data = json_module.loads(text)
                return data.get("files", [])
    except Exception as e:
        print(f"⚠️ Drive list files failed: {e}")
        return []


async def _drive_download(filename: str) -> str:
    """Download a file from Google Drive. Returns content or None."""
    token = await _get_drive_access_token()
    if not token:
        return None

    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with aiohttp.ClientSession() as session:
            query = f"name='{filename}' and trashed=false"
            if folder_id:
                query += f" and '{folder_id}' in parents"
            search_url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name)"
            async with session.get(search_url, headers=headers) as resp:
                search_text = await resp.text()
                if resp.status != 200:
                    print(f"⚠️ Drive 搜尋 {filename} 失敗（{resp.status}）：{search_text[:400]}")
                    return None
                data = json_module.loads(search_text)
            files = data.get("files", [])
            if not files:
                return None

            file_id = files[0]["id"]
            download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
            async with session.get(download_url, headers=headers) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    print(f"⚠️ Drive 下載 {filename} 失敗（{resp.status}）：{err[:400]}")
                    return None
                return await resp.text()
    except Exception as e:
        print(f"⚠️ Drive download failed ({filename}): {e}")
        return None


async def sync_to_drive():
    """Sync ALL local data files to Google Drive.
    Dynamically scans the local data/ directory for every *.json file —
    no hardcoded filename list — so any new persistent state (present or
    future) is automatically backed up without needing a code change."""
    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_B64") and not os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"):
        return
    has_oauth = bool(os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"))
    has_sa = bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_B64"))
    cid = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
    print(f"🔄 Drive 同步開始：OAuth={'✅' if has_oauth else '❌'} SA={'✅' if has_sa else '❌'} client_id={'✅' if cid else '❌'}")
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if not os.path.isdir(data_dir):
        return
    ok_count = 0
    fail_count = 0
    json_filenames = [f for f in os.listdir(data_dir) if f.endswith(".json")]
    for filename in json_filenames:
        filepath = os.path.join(data_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            success = await _drive_upload(filename, content)
            if success:
                ok_count += 1
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            print(f"⚠️ Sync {filename} failed: {e}")
    if fail_count > 0:
        print(f"⚠️ Drive 同步：{ok_count} 成功，{fail_count} 失敗（見上方詳細錯誤，共 {len(json_filenames)} 個檔案）")
    else:
        print(f"✅ Drive 同步完成：{ok_count}/{len(json_filenames)} 個檔案成功")


async def load_from_drive():
    """Load ALL data files from Google Drive on startup (overwrites local).
    Dynamically discovers every file actually present in the Drive folder —
    no hardcoded filename list — so nothing is ever missed on restart/redeploy,
    even files added by future features."""
    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_B64") and not os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"):
        print("ℹ️ Drive 未設定，略過載入")
        return
    has_oauth = bool(os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"))
    has_sa = bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_B64"))
    cid = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
    print(f"🔄 Drive 載入開始：OAuth={'✅' if has_oauth else '❌'} SA={'✅' if has_sa else '❌'} client_id={'✅' if cid else '❌'}")
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)

    files = await _drive_list_files()
    json_files = [f for f in files if f.get("name", "").endswith(".json")]

    if not json_files:
        print("ℹ️ Drive 資料夾目前沒有任何 .json 檔（首次使用正常）")
        return

    ok_count = 0
    fail_count = 0
    for f in json_files:
        filename = f["name"]
        content = await _drive_download(filename)
        if content:
            try:
                filepath = os.path.join(data_dir, filename)
                with open(filepath, "w", encoding="utf-8") as fh:
                    fh.write(content)
                print(f"✅ 從 Google Drive 載入 {filename}")
                ok_count += 1
            except Exception as e:
                print(f"⚠️ 寫入 {filename} 失敗：{e}")
                fail_count += 1
        else:
            print(f"⚠️ 下載 {filename} 失敗（Drive 上有列出但下載回傳空內容）")
            fail_count += 1
    print(f"🔄 Drive 載入完成：{ok_count} 個成功，{fail_count} 個失敗（共 {len(json_files)} 個檔案）")


async def drive_sync_loop():
    """Background task: sync local data to Google Drive every 20 seconds.
    Kept short so a hot redeploy / crash never loses more than ~20s of state."""
    while True:
        await asyncio.sleep(20)
        await sync_to_drive()


async def api_get_chat_ai_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    key = chat_ai_settings["api_key"]
    masked = key[:6] + "..." + key[-4:] if len(key) > 10 else ("***" if key else "")
    return web.json_response({
        "api_url": chat_ai_settings["api_url"],
        "api_key_masked": masked,
        "model": chat_ai_settings["model"],
        "system_prompt": chat_ai_settings["system_prompt"],
        "enabled": chat_ai_settings["enabled"],
        "cooldown_seconds": chat_ai_settings["cooldown_seconds"],
        "channels_whitelist": chat_ai_settings["channels_whitelist"],
        "filter_strength": chat_ai_settings.get("filter_strength", "mention"),
        "abuse_detection_enabled": chat_ai_settings.get("abuse_detection_enabled", False),
        "abuse_detection_strictness": chat_ai_settings.get("abuse_detection_strictness", "medium"),
        "abuse_mute_admins": chat_ai_settings.get("abuse_mute_admins", False),
        "log_channel_id": chat_ai_settings.get("log_channel_id"),
        "micropedia_enabled": chat_ai_settings.get("micropedia_enabled", True),
        "micropedia_max_results": chat_ai_settings.get("micropedia_max_results", 5),
    })


async def api_set_chat_ai_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    if "api_url" in body:
        chat_ai_settings["api_url"] = body["api_url"]
    if "api_key" in body and body["api_key"]:
        chat_ai_settings["api_key"] = body["api_key"]
    if "model" in body:
        chat_ai_settings["model"] = body["model"]
    if "system_prompt" in body:
        chat_ai_settings["system_prompt"] = body["system_prompt"]
    if "enabled" in body:
        chat_ai_settings["enabled"] = body["enabled"]
    if "cooldown_seconds" in body:
        chat_ai_settings["cooldown_seconds"] = int(body["cooldown_seconds"])
    if "channels_whitelist" in body:
        chat_ai_settings["channels_whitelist"] = body["channels_whitelist"]
    if "micropedia_enabled" in body:
        chat_ai_settings["micropedia_enabled"] = body["micropedia_enabled"]
    if "micropedia_max_results" in body:
        chat_ai_settings["micropedia_max_results"] = int(body["micropedia_max_results"])
    save_chat_ai_settings()
    return web.json_response({"ok": True})


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
    # Auto-append /chat/completions if only base URL is provided
    api_url = settings["api_url"].rstrip("/")
    if not api_url.endswith("/chat/completions"):
        if api_url.endswith("/v1") or api_url.endswith("/v2"):
            api_url += "/chat/completions"
        else:
            api_url += "/v1/chat/completions"
    # Use streaming to avoid long silent waits — collect chunks as they arrive
    timeout = aiohttp.ClientTimeout(total=300, connect=15, sock_read=60)
    result_chunks = []
    async with aiohttp.ClientSession() as session:
        async with session.post(api_url, json=payload, headers=headers, timeout=timeout) as resp:
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
    # Auto-append /chat/completions if only base URL is provided
    api_url = settings["api_url"].rstrip("/")
    if not api_url.endswith("/chat/completions"):
        if api_url.endswith("/v1"):
            api_url += "/chat/completions"
        elif api_url.endswith("/v2"):
            api_url += "/chat/completions"
        else:
            api_url += "/v1/chat/completions"
    timeout = aiohttp.ClientTimeout(total=300, connect=15, sock_read=90)
    async with aiohttp.ClientSession() as session:
        async with session.post(api_url, json=payload, headers=headers, timeout=timeout) as resp:
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
    """Background task: save ALL in-memory state to disk every 30 seconds."""
    while True:
        await asyncio.sleep(30)
        save_polls_to_disk()
        save_quiz_data()
        save_token_usage()


def get_poll(guild_id: int, poll_id: str) -> Optional[Poll]:
    return guild_polls.get(guild_id, {}).get(poll_id)


def gen_poll_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return interaction.user.guild_permissions.manage_guild


BOT_OWNER_ID = 1482256878334640209  # 只有機器人擁有者能更改設定

def is_owner(interaction: discord.Interaction) -> bool:
    """Only the bot owner can change bot settings."""
    return interaction.user.id == BOT_OWNER_ID


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
intents.members = True  # Needed for server structure awareness (member list, roles)
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def _probe_tools_support(settings: dict, api_url: str):
    """Quick startup probe: send a tiny request WITH tools attached to check
    if the chat AI endpoint supports function calling. If it fails, record
    the endpoint in _tools_unsupported_apis and persist it — so the very first
    real user message skips the double-call penalty entirely."""
    if not settings.get("api_key"):
        return
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.get("model", "gpt-4o-mini"),
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
        "temperature": 0,
        "tools": [_DISCORD_SEARCH_TOOL_SCHEMA],
        "tool_choice": "auto",
    }
    try:
        t = aiohttp.ClientTimeout(total=10, connect=5, sock_read=8)
        session = _shared_session
        if session is None or session.closed:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=payload, headers=headers, timeout=t) as resp:
                    status = resp.status
                    body = await resp.text()
        else:
            async with session.post(api_url, json=payload, headers=headers, timeout=t) as resp:
                status = resp.status
                body = await resp.text()
        if status == 200:
            data = json_module.loads(body)
            if "choices" in data:
                print(f"✅ AI 端點支援 tools（probe 成功）— 工具功能可用")
                _tools_supported_apis.add(api_url)
                save_tools_supported()
                return
        # Failed — endpoint doesn't support tools
        print(f"⚠️ AI 端點不支援 tools（probe 失敗 status={status}）— 之後略過 tools 參數")
        _tools_unsupported_apis.add(api_url)
        save_tools_unsupported()
    except asyncio.TimeoutError:
        print(f"⚠️ AI tools probe 逾時 — 判定端點不支援 tools，之後略過 tools 參數")
        _tools_unsupported_apis.add(api_url)
        save_tools_unsupported()
    except Exception as e:
        print(f"⚠️ AI tools probe 錯誤（{e}）— 暫時不判定，等第一則訊息再測試")


async def on_ready():
    global _chat_semaphore, _shared_session
    if _chat_semaphore is None:
        _chat_semaphore = asyncio.Semaphore(5)
    if _shared_session is not None and not _shared_session.closed:
        pass  # reuse existing session
    else:
        if _shared_session is not None and _shared_session.closed:
            print("🔄 舊的 HTTP session 已關閉，建立新的...")
        _shared_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=300, connect=10, sock_read=60),
            connector=aiohttp.TCPConnector(limit=20, limit_per_host=10)
        )
    # Check message_content intent
    if not bot.intents.message_content:
        print("⚠️  message_content intent 未啟用！AI 聊天功能無法讀取訊息內容。")
        print("    請到 Discord Developer Portal → Bot → Privileged Gateway Intents → 開啟 MESSAGE CONTENT INTENT")
    else:
        print("✅ message_content intent 已啟用")
    print(f"📋 Chat AI: enabled={chat_ai_settings.get('enabled')}, "
          f"filter={chat_ai_settings.get('filter_strength', 'mention')}, "
          f"key={'✅' if chat_ai_settings.get('api_key') else '❌'}, "
          f"whitelist={len(chat_ai_settings.get('channels_whitelist', []))} ch")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot 上線：{bot.user}（已同步 {len(synced)} 個 slash commands）")
    except Exception as e:
        print(f"❌ 同步指令失敗：{e}")

    # Warmup probe: if we don't yet know whether the chat AI endpoint supports
    # tools, do a tiny test call NOW (before any user message arrives) so the
    # first real user message doesn't pay the double-call penalty. This probe
    # uses a short 10s timeout and sends a minimal request with tools attached.
    if chat_ai_settings.get("enabled") and chat_ai_settings.get("api_key"):
        _norm = chat_ai_settings.get("api_url", "").rstrip("/")
        if not _norm.endswith("/chat/completions"):
            if _norm.endswith("/v1") or _norm.endswith("/v2"):
                _norm += "/chat/completions"
            else:
                _norm += "/v1/chat/completions"
        if _norm not in _tools_unsupported_apis:
            asyncio.ensure_future(_probe_tools_support(chat_ai_settings, _norm))


@bot.event
async def setup_hook():
    # Register slash command groups (runs once, before bot connects)
    for grp in [PollGroup(), MeetingGroup(), BriefingGroup(), ChatGroup(), SystemGroup(), QuizGroup()]:
        try:
            bot.tree.add_command(grp)
        except Exception:
            pass
    # Load from Google Drive first (if configured), then from local
    await load_from_drive()
    # Load local files (will use Drive-downloaded data if available)
    load_polls_from_disk()
    save_polls_to_disk()  # Create file if not exists
    load_briefing_settings()
    save_briefing_settings()  # Create file if not exists
    load_chat_ai_settings()
    save_chat_ai_settings()  # Create file if not exists
    load_quiz_data()
    save_quiz_data()  # Create files if not exists
    load_token_usage()
    save_token_usage()  # Create file if not exists
    load_user_memories()
    load_emoji_aliases()
    load_tools_unsupported()
    load_tools_supported()
    await keep_alive_server()
    asyncio.ensure_future(self_ping_loop())
    asyncio.ensure_future(auto_save_loop())
    asyncio.ensure_future(daily_briefing_scheduler())
    asyncio.ensure_future(weekly_briefing_scheduler())
    asyncio.ensure_future(drive_sync_loop())
    asyncio.ensure_future(server_context_refresh_loop())
    asyncio.ensure_future(forum_index_refresh_loop())
    asyncio.ensure_future(quiz_question_loop())
    asyncio.ensure_future(quiz_settlement_loop())
    asyncio.ensure_future(token_log_loop())


@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Ignore slash commands (but allow messages starting with "!" which could be normal text)
    if message.content.startswith("/"):
        return

    # Debug: log all human messages
    content_preview = message.content[:80].replace("\n", " ") if message.content else "(empty)"
    is_mentioned = bot.user in message.mentions
    print(f"📩 on_message: #{message.channel} | {message.author.display_name}: {content_preview}")
    print(f"   enabled={chat_ai_settings.get('enabled')}, key={'✅' if chat_ai_settings.get('api_key') else '❌'}, mentioned={is_mentioned}, filter={chat_ai_settings.get('filter_strength', 'mention')}")

    # Check if chat AI is enabled and has API key
    if not chat_ai_settings.get("enabled"):
        print(f"   ⏭️ Chat AI is disabled. Run /chat toggle to enable.")
        return
    if not chat_ai_settings.get("api_key"):
        print(f"   ⏭️ No API key set.")
        return

    # Check if message content is empty (intent not enabled)
    if not message.content or len(message.content.strip()) == 0:
        print(f"   ⚠️ message.content is empty! Message Content Intent may not be enabled in Discord Developer Portal.")
        return

    # Check channel whitelist
    whitelist = chat_ai_settings.get("channels_whitelist", [])
    if whitelist and message.channel.id not in whitelist:
        print(f"   ⏭️ Channel not in whitelist.")
        return

    # Skip if THIS USER already has a reply being generated (prevents one person
    # spamming; different users in the same channel are NOT blocked).
    uid_str = str(message.author.id)
    if uid_str in _user_generating:
        print(f"   ⏭️ Already generating for this user.")
        return

    # Check cooldown — keyed per (channel, user) so one person's question
    # doesn't cool down the entire channel for everyone else.
    if not is_mentioned:
        cooldown_key = (message.channel.id, message.author.id)
        last_reply = chat_cooldowns.get(cooldown_key, 0)
        cooldown = chat_ai_settings.get("cooldown_seconds", 60)
        remaining = cooldown - (_time.time() - last_reply)
        if remaining > 0:
            print(f"   ⏭️ Cooldown for {message.author.display_name}: {remaining:.0f}s remaining.")
            return

    # Detect if this message is a reply to the bot
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
            is_reply_to_bot = ref_msg.author.id == bot.user.id if ref_msg else False
        except Exception:
            pass

    # Worthiness check
    worth, clean = _is_worth_replying(
        message.content, is_mentioned, bot.user.id,
        chat_ai_settings.get("filter_strength", "mention"),
        is_reply_to_bot=is_reply_to_bot,
    )
    if not worth:
        print(f"   ⏭️ Not worth replying (content too short/low-value).")
        return
    print(f"   ✅ Worth replying! Generating...")

    # Check if bot has permission to send messages in this channel
    if message.guild:
        perms = message.channel.permissions_for(message.guild.me)
        if not perms or not perms.send_messages:
            print(f"   ❌ Bot 沒有在 #{message.channel} 發送訊息的權限！請檢查頻道權限設定。")
            try:
                # Try sending via a different method — sometimes read_messages is enough
                # to receive but send_messages is denied
                pass
            except Exception:
                pass
            return

    # ── Abuse detection: fast path (before AI call) ──
    if chat_ai_settings.get("abuse_detection_enabled", False):
        strictness = chat_ai_settings.get("abuse_detection_strictness", "medium")
        uid = str(message.author.id)

        # Check severe keywords
        matched_kw = _check_severe_keywords(message.content)
        if matched_kw:
            duration = _get_mute_duration(uid, strictness, severity_override=3600)
            muted = await _execute_mute(message, duration, f"嚴重違規用語（{matched_kw}）")
            if muted:
                await message.reply(
                    f"🛡️ {message.author.mention} 因使用嚴重違規用語已被禁言 {duration//60} 分鐘。",
                    mention_author=False
                )
                return

        # Check flood
        if _track_flood(uid, strictness):
            duration = _get_mute_duration(uid, strictness)
            tracker = abuse_tracker.get(uid, {})
            tracker["warnings"] = tracker.get("warnings", 0)
            muted = await _execute_mute(message, duration, f"訊息刷屏（{strictness}模式）")
            if muted:
                await message.reply(
                    f"🛡️ {message.author.mention} 因短時間內大量發訊已被禁言 {duration//60} 分鐘。",
                    mention_author=False
                )
                return

    # Generate reply
    _user_generating.add(uid_str)
    try:
        # Wait for a concurrency slot (max 5 simultaneous AI calls). This makes
        # extra requests QUEUE instead of all hitting the API at once and timing
        # out under load. The semaphore is created in on_ready.
        sem = _chat_semaphore or asyncio.Semaphore(5)
        async with sem:
            async with message.channel.typing():
                reply, new_facts, mod_action = await generate_chat_reply(message, chat_ai_settings)
        # Save user memory if AI extracted facts (regardless of reply success)
        if new_facts:
            _update_user_memory(str(message.author.id), message.author.display_name, new_facts)
            print(f"🧠 已更新 {message.author.display_name} 的記憶：{new_facts}")

        # Log conversation to log channel if configured
        log_cfg = chat_ai_settings.get("log_channel_id")
        if log_cfg:
            try:
                await _send_chat_log(message, clean or message.content, reply or "(空回覆)")
            except Exception as log_exc:
                print(f"   ⚠️ _send_chat_log 拋出例外（不影響回覆）：{log_exc}")

        if reply and reply.strip():
            chat_cooldowns[(message.channel.id, message.author.id)] = _time.time()
            print(f"   📤 發送回覆（{len(reply[:2000])} chars）到 #{message.channel}...")
            try:
                await message.reply(reply[:2000], mention_author=False)
                print(f"   ✅ 回覆已發送")
            except discord.Forbidden:
                print(f"   ❌ 發送失敗：Bot 沒有在 #{message.channel} 發送訊息的權限！")
                return
            except Exception as send_err:
                print(f"   ❌ 發送失敗：{send_err}")
                return
        else:
            print(f"⚠️ AI 回覆為空，發送 fallback 訊息")
            try:
                await message.reply("🤔 讓我想想...", mention_author=False)
            except Exception:
                pass
        # ── Abuse detection: AI path (after AI call) ──
        if mod_action and chat_ai_settings.get("abuse_detection_enabled", False):
            duration = min(mod_action, 86400)
            reason = f"AI 判定濫用行為，建議禁言 {duration} 秒"
            muted = await _execute_mute(message, duration, reason)
            if muted:
                await message.reply(
                    f"🛡️ {message.author.mention} AI 偵測到不當行為，已被禁言 {duration//60} 分鐘。",
                    mention_author=False
                )
    except asyncio.TimeoutError:
        print(f"⚠️ Chat AI timeout (API call took too long)")
        try:
            await message.reply("⏰ 回覆逾時，請稍後再試。", mention_author=False)
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ Chat AI error: {e}")
        try:
            await message.reply("⚠️ 發生錯誤，請稍後再試。", mention_author=False)
        except Exception:
            pass
    finally:
        _user_generating.discard(uid_str)


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
# 系統診斷指令群組
# ──────────────────────────────────────────────

class SystemGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="system", description="系統診斷工具")

    @app_commands.command(name="drive_authorize", description="用你的 Google 帳號授權 Drive 存取（機器人擁有者限定）")
    async def drive_authorize(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        client_id = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
        if not client_id:
            await interaction.response.send_message(
                "❌ 尚未設定 OAuth Client ID 環境變數。\n"
                "請先到 Render Environment 新增 `GOOGLE_CLIENT_ID` 和 `GOOGLE_CLIENT_SECRET`（或 `OAUTH_CLIENT_ID` 和 `OAUTH_CLIENT_SECRET`），來自你的 Google Cloud OAuth 用戶端。",
                ephemeral=True
            )
            return

        base_url = os.getenv("SELF_URL") or os.getenv("RENDER_EXTERNAL_URL") or ""
        if not base_url:
            await interaction.response.send_message(
                "❌ 尚未設定 `SELF_URL` 環境變數，無法產生回調網址。",
                ephemeral=True
            )
            return

        redirect_uri = _drive_oauth_redirect_uri()
        state = _sign_drive_oauth_state(interaction.user.id)

        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={urllib.parse.quote(client_id)}"
            f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
            "&response_type=code"
            "&scope=" + urllib.parse.quote("https://www.googleapis.com/auth/drive")
            + "&access_type=offline"
            "&prompt=consent"
            f"&state={urllib.parse.quote(state)}"
        )

        embed = discord.Embed(
            title="🔑 Google Drive 個人帳號授權",
            description=(
                "**點下面連結，用你要拿來存放資料的 Google 帳號登入並同意授權：**\n"
                f"{auth_url}\n\n"
                "**⚠️ 重要：在點擊前，請先確認這個回調網址已加到 Google Cloud Console 的「已授權的重新導向 URI」：**\n"
                f"`{redirect_uri}`\n\n"
                "位置：Google Cloud Console → API 和服務 → 憑證 → 你的 OAuth 用戶端 ID → 編輯\n\n"
                "授權成功後，網頁會顯示一組 `refresh_token`，把它複製到 Render 環境變數 "
                "`GOOGLE_DRIVE_REFRESH_TOKEN`。\n\n"
                "此連結 10 分鐘內有效。"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="drive_test", description="測試 Google Drive 連線並顯示詳細錯誤（機器人擁有者限定）")
    async def drive_test(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        lines = ["**🔍 Google Drive 診斷**", ""]

        # 1. Check env vars
        creds_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "")
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
        refresh_token = os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")
        g_client_id = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
        g_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "") or os.getenv("OAUTH_CLIENT_SECRET", "")

        lines.append(f"**1. 環境變數**")
        lines.append(f"  認證方式：{'🟢 OAuth 個人帳號（推薦）' if refresh_token else ('🟡 服務帳號（需 Shared Drive）' if creds_b64 else '❌ 未設定任何認證')}")
        lines.append(f"  GOOGLE_DRIVE_REFRESH_TOKEN: {'✅ 已設定' if refresh_token else '❌ 未設定'}")
        lines.append(f"  GOOGLE_CLIENT_ID: {'✅ 已設定' if g_client_id else '❌ 未設定'}")
        lines.append(f"  GOOGLE_CLIENT_SECRET: {'✅ 已設定' if g_client_secret else '❌ 未設定'}")
        lines.append(f"  GOOGLE_SERVICE_ACCOUNT_B64: {'✅ 已設定 (' + str(len(creds_b64)) + ' 字元)' if creds_b64 else '❌ 未設定（備援方案）'}")
        lines.append(f"  GOOGLE_DRIVE_FOLDER_ID: {'✅ `' + folder_id + '`' if folder_id else '❌ 未設定'}")

        if not refresh_token and not creds_b64:
            lines.append("")
            lines.append("→ 尚未設定任何認證方式。建議執行 `/system drive_authorize` 用你的個人 Google 帳號授權（有真正的儲存配額）。")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        # 2. Decode and validate service account JSON (only relevant if OAuth isn't set up)
        if refresh_token:
            lines.append("")
            lines.append(f"**2. 服務帳號金鑰解析**")
            lines.append(f"  ⏭️ 已使用 OAuth 個人帳號認證，跳過服務帳號檢查。")
        elif creds_b64:
            lines.append("")
            lines.append(f"**2. 服務帳號金鑰解析**")
            try:
                creds_info = json_module.loads(base64.b64decode(creds_b64).decode())
                lines.append(f"  ✅ Base64 + JSON 解析成功")
                lines.append(f"  type: `{creds_info.get('type', '(缺少)')}`")
                lines.append(f"  client_email: `{creds_info.get('client_email', '(缺少)')}`")
                has_key = "private_key" in creds_info
                lines.append(f"  private_key: {'✅ 存在' if has_key else '❌ 缺少'}")
                if creds_info.get("type") != "service_account":
                    lines.append(f"  ⚠️ type 不是 service_account！你可能上傳了錯誤的金鑰類型（例如 OAuth 用戶端）")
            except Exception as e:
                lines.append(f"  ❌ 解析失敗：{e}")
                lines.append("")
                lines.append("→ GOOGLE_SERVICE_ACCOUNT_B64 內容有誤，請重新 base64 編碼服務帳號 JSON")
                await interaction.followup.send("\n".join(lines), ephemeral=True)
                return

        # 3. Get access token
        lines.append("")
        lines.append(f"**3. 取得存取權杖**")
        _drive_token_cache["token"] = None  # force fresh token for test
        token = await _get_drive_access_token()
        if token:
            lines.append(f"  ✅ 成功取得 token")
        else:
            lines.append(f"  ❌ 取得 token 失敗（詳細錯誤請看 Render logs）")
            lines.append("")
            lines.append("→ 常見原因：JSON 錯誤、Drive API 未啟用、服務帳號被刪除")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        # 4. Try uploading a test file
        lines.append("")
        lines.append(f"**4. 測試上傳**")
        test_content = f'{{"test": true, "time": "{datetime.now().isoformat()}"}}'
        success, detail = await _drive_upload("_connection_test.json", test_content, return_detail=True)
        if success:
            lines.append(f"  ✅ 測試檔案上傳成功！請到 Drive 資料夾確認 `_connection_test.json`")
        else:
            lines.append(f"  ❌ 上傳失敗")
            lines.append(f"  詳細：`{detail}`")
            lines.append("")
            if "storageQuotaExceeded" in detail or "quota" in detail.lower():
                lines.append("→ 這是**服務帳號儲存配額**問題！服務帳號本身沒有 Drive 儲存空間。")
                lines.append("  解法：把資料夾改成「共用雲端硬碟」(Shared Drive)，而非個人「我的雲端硬碟」內的資料夾。")
                lines.append("  （注意：免費 Gmail 帳號可能無法建立共用雲端硬碟，需要 Google Workspace）")
            elif "404" in detail or "File not found" in detail:
                lines.append("→ 常見原因：")
                lines.append("  • GOOGLE_DRIVE_FOLDER_ID 錯誤")
                lines.append("  • 資料夾沒有共用給服務帳號 email")
            elif "403" in detail:
                lines.append("→ 常見原因：")
                lines.append("  • 資料夾沒有共用給服務帳號 email（見上方 client_email）")
                lines.append("  • 服務帳號權限不是「編輯者」")
                lines.append("  • Drive API 沒有在 Google Cloud 專案中啟用")
            else:
                lines.append("→ 請把上面「詳細」的錯誤內容回報，才能進一步排查。")

        await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)


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

    @app_commands.command(name="test", description="測試 AI API 連線（機器人擁有者限定）")
    async def test_ai(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
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
# AI 聊天指令
# ──────────────────────────────────────────────

class ChatGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="chat", description="AI 聊天設定")

    @app_commands.command(name="toggle", description="開啟/關閉 AI 聊天功能（機器人擁有者限定）")
    async def chat_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["enabled"] = not chat_ai_settings["enabled"]
        save_chat_ai_settings()
        status = "✅ 開啟" if chat_ai_settings["enabled"] else "❌ 關閉"
        await interaction.response.send_message(f"AI 聊天功能已{status}", ephemeral=True)

    @app_commands.command(name="model", description="設定 AI 聊天模型（機器人擁有者限定）")
    @app_commands.describe(model="模型名稱（例如：gpt-4o-mini, gemini-1.5-flash）")
    async def chat_model(self, interaction: discord.Interaction, model: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["model"] = model
        save_chat_ai_settings()
        await interaction.response.send_message(f"✅ AI 聊天模型已設為 `{model}`", ephemeral=True)

    @app_commands.command(name="prompt", description="設定 AI 聊天人設（機器人擁有者限定）")
    @app_commands.describe(prompt="系統提示詞（人設描述）")
    async def chat_prompt(self, interaction: discord.Interaction, prompt: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["system_prompt"] = prompt
        save_chat_ai_settings()
        await interaction.response.send_message("✅ AI 聊天人設已更新", ephemeral=True)

    @app_commands.command(name="cooldown", description="設定 AI 聊天冷卻時間（機器人擁有者限定）")
    @app_commands.describe(seconds="冷卻秒數（自動回覆間隔，@提及不受限）")
    async def chat_cooldown(self, interaction: discord.Interaction, seconds: int):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["cooldown_seconds"] = max(0, seconds)
        save_chat_ai_settings()
        await interaction.response.send_message(f"✅ 冷卻時間已設為 {seconds} 秒", ephemeral=True)

    @app_commands.command(name="channel", description="新增/移除頻道白名單（機器人擁有者限定）")
    @app_commands.describe(action="新增或移除", channel="要設定的頻道")
    @app_commands.choices(action=[
        app_commands.Choice(name="新增", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="清空（所有頻道）", value="clear"),
    ])
    async def chat_channel(self, interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        wl = chat_ai_settings.get("channels_whitelist", [])
        act = action.value
        if act == "clear":
            chat_ai_settings["channels_whitelist"] = []
            save_chat_ai_settings()
            await interaction.response.send_message("✅ 頻道白名單已清空（AI 聊天在所有頻道啟用）", ephemeral=True)
        elif channel:
            if act == "add" and channel.id not in wl:
                wl.append(channel.id)
                chat_ai_settings["channels_whitelist"] = wl
                save_chat_ai_settings()
                await interaction.response.send_message(f"✅ 已新增 {channel.mention} 到白名單", ephemeral=True)
            elif act == "remove" and channel.id in wl:
                wl.remove(channel.id)
                chat_ai_settings["channels_whitelist"] = wl
                save_chat_ai_settings()
                await interaction.response.send_message(f"✅ 已從白名單移除 {channel.mention}", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ 頻道已在/不在白名單中", ephemeral=True)

    @app_commands.command(name="filter", description="設定垃圾話過濾強度（機器人擁有者限定）")
    @app_commands.describe(level="過濾強度等級")
    @app_commands.choices(level=[
        app_commands.Choice(name="僅@提及和回覆（推薦）", value="mention"),
        app_commands.Choice(name="關閉（回覆所有訊息）", value="off"),
        app_commands.Choice(name="低（只擋打招呼/連結/emoji）", value="low"),
        app_commands.Choice(name="中（需有實質內容或問題）", value="medium"),
        app_commands.Choice(name="高（嚴格，只回問題和關鍵字）", value="high"),
    ])
    async def chat_filter(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["filter_strength"] = level.value
        save_chat_ai_settings()
        descs = {
            "mention": "僅@提及和回覆：AI 只會在被 @到或被回覆時才說話（不會主動亂接話）",
            "off": "關閉：AI 會回覆所有非空訊息（最自然，但最耗 token）",
            "low": "低：只擋純打招呼、連結、emoji、極短訊息（適合活躍群組）",
            "medium": "中：需要問題、關鍵字、或 15 字以上才回（平衡）",
            "high": "高：只回覆問題和關鍵字（最省 token，但會擋掉很多正常對話）",
        }
        await interaction.response.send_message(
            f"✅ 過濾強度已設為**{level.name}**\n{descs.get(level.value, '')}",
            ephemeral=True
        )

    @app_commands.command(name="server_info", description="查看/更新伺服器結構快取（機器人擁有者限定）")
    async def chat_server_info(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # Force refresh
        await _refresh_server_context(interaction.guild)
        cached = _server_context_cache.get(interaction.guild.id, {})
        d = cached.get("data", {})
        if not d:
            await interaction.followup.send("❌ 無法取得伺服器結構。", ephemeral=True)
            return

        embed = discord.Embed(title=f"🏗️ 伺服器結構：{d['guild_name']}", color=discord.Color.blue())

        ch_list = []
        current_cat = None
        ch_names = []
        for ch in d["channels"]:
            if ch["category"] != current_cat:
                if ch_names:
                    ch_list.append(f"[{current_cat}] {' '.join(ch_names)}")
                    ch_names = []
                current_cat = ch["category"]
            ch_names.append(f"#{ch['name']}")
        if ch_names:
            ch_list.append(f"[{current_cat}] {' '.join(ch_names)}")
        embed.add_field(name=f"📁 頻道（{len(d['channels'])}）", value="\n".join(ch_list)[:1024] or "無", inline=False)

        roles_str = ", ".join(f"{r['name']}({r['member_count']})" for r in d["roles"][:20])
        embed.add_field(name=f"🏷️ 身分組（{len(d['roles'])}）", value=roles_str[:1024] or "無", inline=False)

        emoji_str = " ".join(f":{e['name']}:" for e in d["emojis"][:30])
        embed.add_field(name=f"😀 Emoji（{len(d['emojis'])}）", value=emoji_str[:1024] or "無", inline=False)

        embed.add_field(name="👥 成員", value=f"快取 {len(d['members'])} / {d['member_count']} 總成員", inline=True)
        embed.add_field(name="最後更新", value=f"<t:{int(cached.get('updated', 0))}:R>", inline=True)
        embed.set_footer(text="每 10 分鐘自動更新。此指令可手動刷新。")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="abuse_toggle", description="開關濫用偵測系統（機器人擁有者限定）")
    async def chat_abuse_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["abuse_detection_enabled"] = not chat_ai_settings.get("abuse_detection_enabled", False)
        save_chat_ai_settings()
        status = "✅ 開啟" if chat_ai_settings["abuse_detection_enabled"] else "❌ 關閉"
        await interaction.response.send_message(f"🛡️ 濫用偵測系統已{status}", ephemeral=True)

    @app_commands.command(name="abuse_level", description="設定濫用偵測嚴格度（機器人擁有者限定）")
    @app_commands.describe(level="偵測嚴格度等級")
    @app_commands.choices(level=[
        app_commands.Choice(name="低（寬容，嚴重違規才禁言）", value="low"),
        app_commands.Choice(name="中（標準，刷屏+辱罵都禁）", value="medium"),
        app_commands.Choice(name="高（嚴格，輕微挑釁也禁）", value="high"),
    ])
    async def chat_abuse_level(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["abuse_detection_strictness"] = level.value
        save_chat_ai_settings()
        await interaction.response.send_message(f"✅ 濫用偵測嚴格度已設為**{level.name}**", ephemeral=True)

    @app_commands.command(name="abuse_admins", description="設定是否允許禁言管理員（機器人擁有者限定）")
    @app_commands.describe(enabled="True=可以禁言管理員, False=跳過管理員")
    async def chat_abuse_admins(self, interaction: discord.Interaction, enabled: bool):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["abuse_mute_admins"] = enabled
        save_chat_ai_settings()
        await interaction.response.send_message(
            f"✅ 禁言管理員：{'開啟（管理員也會被禁言）' if enabled else '關閉（管理員不受影響）'}",
            ephemeral=True
        )

    @app_commands.command(name="abuse_log", description="查看最近的禁言記錄（機器人擁有者限定）")
    async def chat_abuse_log(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if not mod_action_log:
            await interaction.response.send_message("📋 目前沒有任何禁言記錄。", ephemeral=True)
            return
        lines = ["📋 **最近禁言記錄**\n"]
        for entry in reversed(mod_action_log[-15:]):
            ts = datetime.datetime.fromtimestamp(entry["timestamp"]).strftime("%m/%d %H:%M")
            mins = entry["duration"] // 60
            lines.append(f"• `{ts}` **{entry['user_name']}** — {mins}分鐘\n  原因：{entry['reason']}（#{entry['channel']}）")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="abuse_unmute", description="手動解除禁言（機器人擁有者限定）")
    @app_commands.describe(user="要解除禁言的用戶")
    async def chat_abuse_unmute(self, interaction: discord.Interaction, user: discord.Member):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        try:
            await user.timeout(None, reason=f"由 {interaction.user.display_name} 手動解除禁言")
            # Reset abuse tracker so they don't get escalated next time
            target_id = str(user.id)
            if target_id in abuse_tracker:
                abuse_tracker[target_id]["message_times"] = []
                abuse_tracker[target_id]["warnings"] = 0
                abuse_tracker[target_id]["total_mutes"] = 0
            await interaction.response.send_message(f"✅ 已解除 {user.mention} 的禁言，並重置濫用追蹤紀錄。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 解除禁言失敗：{e}", ephemeral=True)

    @app_commands.command(name="log_channel", description="設定/清除 AI 紀錄頻道（禁言+對話紀錄，伺服器擁有者限定）")
    @app_commands.describe(action="設定或清除", channel="要設為 log 頻道的頻道（清除時不填）")
    @app_commands.choices(action=[
        app_commands.Choice(name="設定", value="set"),
        app_commands.Choice(name="清除", value="clear"),
    ])
    async def chat_log_channel(self, interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if action.value == "clear":
            chat_ai_settings["log_channel_id"] = None
            save_chat_ai_settings()
            await interaction.response.send_message("✅ AI 紀錄頻道已清除。", ephemeral=True)
        elif action.value == "set" and channel:
            chat_ai_settings["log_channel_id"] = channel.id
            save_chat_ai_settings()
            await interaction.response.send_message(
                f"✅ AI 紀錄頻道已設為 {channel.mention}\n"
                f"AI 對話紀錄 + 自動禁言紀錄將發送到此頻道。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("⚠️ 請選擇動作和頻道。", ephemeral=True)

    @app_commands.command(name="log_test", description="發送測試訊息到 AI 紀錄頻道，驗證設定是否正常（機器人擁有者限定）")
    async def chat_log_test(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        log_ch_id = chat_ai_settings.get("log_channel_id")
        if not log_ch_id:
            await interaction.response.send_message(
                "❌ 尚未設定 AI 紀錄頻道。請先用 `/chat log_channel` 設定。",
                ephemeral=True
            )
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        log_ch, err = await _resolve_log_channel(interaction.guild)
        if not log_ch:
            await interaction.followup.send(f"❌ 找不到紀錄頻道：{err}", ephemeral=True)
            return
        try:
            test_embed = discord.Embed(
                title="🧪 測試訊息",
                description=f"這是 `/chat log_test` 發送的測試訊息，確認 <#{log_ch_id}> 設定正常。",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            test_embed.set_footer(text=f"由 {interaction.user.display_name} 觸發")
            await log_ch.send(embed=test_embed)
            await interaction.followup.send(f"✅ 測試訊息已成功發送到 {log_ch.mention}！", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Bot 沒有在 {log_ch.mention} 發送訊息的權限。\n"
                f"請到該頻道 → 頻道設定 → 權限，確認 Bot 有「查看頻道」「發送訊息」「嵌入連結」權限。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 發送失敗：{e}", ephemeral=True)

    @app_commands.command(name="test", description="測試 AI 聊天回覆（機器人擁有者限定）")
    @app_commands.describe(message="要測試的訊息")
    async def chat_test(self, interaction: discord.Interaction, message: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if not chat_ai_settings.get("api_key"):
            await interaction.response.send_message("❌ 尚未設定 AI 聊天 API Key。請到 Dashboard 設定。", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        try:
            # Use generate_chat_reply for full memory integration
            class FakeMsg:
                pass
            fake = FakeMsg()
            fake.channel = interaction.channel
            fake.author = interaction.user
            fake.content = message
            fake.guild = interaction.guild  # needed by generate_chat_reply's server-awareness lookup
            reply, new_facts, mod_action = await generate_chat_reply(fake, chat_ai_settings)
            # Strip [MEMORY:] from test reply
            if "[MEMORY:" in reply:
                reply = reply.rsplit("[MEMORY:", 1)[0].strip()
            if "[MOD:" in reply:
                reply = reply.rsplit("[MOD:", 1)[0].strip()
            result = f"✅ AI 回覆：\n{reply}"
            if new_facts:
                result += f"\n\n🧠 記憶更新：{', '.join(new_facts)}"
            await interaction.followup.send(result, ephemeral=True)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"❌ /chat test 失敗，完整 traceback:\n{tb}")
            short_tb = tb.strip().split(chr(10))[-1][:200]
            await interaction.followup.send(f"❌ AI 聊天測試失敗：{type(e).__name__}: {e}\n```{short_tb}```", ephemeral=True)

    @app_commands.command(name="memory", description="查看 AI 對你的記憶")
    async def chat_memory(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        mem = user_memories.get(user_id, {})
        facts = mem.get("facts", [])
        count = mem.get("interaction_count", 0)
        if not facts:
            await interaction.response.send_message("🧠 AI 目前對你沒有任何記憶。多聊天就會開始記住你了！", ephemeral=True)
            return
        lines = [f"🧠 AI 對 **{interaction.user.display_name}** 的記憶（{len(facts)} 條 / {count} 次互動）："]
        for i, f in enumerate(facts, 1):
            lines.append(f"{i}. {f}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="memory_clear", description="清除 AI 對你的記憶（擁有者可清除指定用戶）")
    @app_commands.describe(user="要清除記憶的用戶（不填則清除自己的）")
    async def chat_memory_clear(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        if user and not is_owner(interaction):
            await interaction.response.send_message("❌ 只有管理員能清除他人的記憶。", ephemeral=True)
            return
        target_id = str(target.id)
        if target_id in user_memories:
            old_count = len(user_memories[target_id].get("facts", []))
            del user_memories[target_id]
            save_user_memories()
            await interaction.response.send_message(
                f"✅ 已清除 AI 對 {target.mention} 的記憶（原本有 {old_count} 條）",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(f"ℹ️ {target.mention} 沒有任何記憶。", ephemeral=True)

    @app_commands.command(name="debug", description="診斷 AI 聊天問題（機器人擁有者限定）")
    async def chat_debug(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        lines = []
        lines.append(f"**AI 聊天診斷**")
        lines.append(f"")
        lines.append(f"**1. 功能狀態**")
        lines.append(f"  enabled: {'✅ 開啟' if chat_ai_settings.get('enabled') else '❌ 關閉'}")
        lines.append(f"  → 如果關閉，請執行 `/chat toggle`")
        lines.append(f"")
        lines.append(f"**2. API 設定**")
        lines.append(f"  API Key: {'✅ 已設定' if chat_ai_settings.get('api_key') else '❌ 未設定'}")
        lines.append(f"  API URL: `{chat_ai_settings.get('api_url', '未設定')}`")
        lines.append(f"  Model: `{chat_ai_settings.get('model', '未設定')}`")
        lines.append(f"")
        lines.append(f"**3. Message Content Intent**")
        has_intent = bot.intents.message_content
        lines.append(f"  程式碼: {'✅ 已啟用' if has_intent else '❌ 未啟用'}")
        lines.append(f"  → 如果上面是 ✅ 但 bot 仍不回覆，請到 Discord Developer Portal")
        lines.append(f"  → Bot → Privileged Gateway Intents → 開啟 MESSAGE CONTENT INTENT")
        lines.append(f"")
        lines.append(f"**4. 頻道白名單**")
        wl = chat_ai_settings.get("channels_whitelist", [])
        if wl:
            lines.append(f"  {'、'.join(f'<#{cid}>' for cid in wl)}")
            lines.append(f"  → 只有以上頻道會回覆，其他頻道被忽略")
        else:
            lines.append(f"  所有頻道（無限制）")
        lines.append(f"")
        lines.append(f"**5. 冷卻時間**")
        lines.append(f"  {chat_ai_settings.get('cooldown_seconds', 60)} 秒（@提及不受限）")
        lines.append(f"")
        filter_str = chat_ai_settings.get("filter_strength", "mention")
        filter_descs = {
            "off": "關閉：回覆所有非空訊息",
            "low": "低：只擋打招呼/連結/emoji/極短",
            "medium": "中：需問題/關鍵字/15字以上",
            "high": "高：只回問題和關鍵字",
        }
        lines.append(f"**6. 過濾強度**")
        lines.append(f"  目前：{filter_str} — {filter_descs.get(filter_str, '')}")
        lines.append(f"  → 用 `/chat filter` 調整")
        lines.append(f"")
        lines.append(f"**7. 伺服器結構感知**")
        if _server_context_cache:
            for gid, cache in _server_context_cache.items():
                d = cache.get("data", {})
                age = int(_time.time() - cache.get("updated", 0))
                lines.append(f"  Guild {gid}: {d.get('guild_name', '?')} — {len(d.get('channels', []))} 頻道, {len(d.get('members', []))} 成員快取 ({age}s ago)")
        else:
            lines.append(f"  ❌ 尚未建立快取")
        lines.append(f"  → 用 `/chat server_info` 手動刷新")
        lines.append(f"")
        lines.append(f"**8. 濫用偵測**")
        abuse_on = chat_ai_settings.get("abuse_detection_enabled", False)
        abuse_strict = chat_ai_settings.get("abuse_detection_strictness", "medium")
        abuse_admins = chat_ai_settings.get("abuse_mute_admins", False)
        lines.append(f"  狀態：{'✅ 開啟' if abuse_on else '❌ 關閉'}")
        lines.append(f"  嚴格度：{abuse_strict}")
        lines.append(f"  禁言管理員：{'是' if abuse_admins else '否'}")
        if mod_action_log:
            lines.append(f"  累計禁言次數：{len(mod_action_log)}")
        lines.append(f"  → `/chat abuse_toggle` 開關 | `/chat abuse_level` 調整 | `/chat abuse_log` 查看記錄")
        lines.append(f"")
        lines.append(f"**9. AI 紀錄頻道**")
        log_ch_id = chat_ai_settings.get("log_channel_id")
        if log_ch_id:
            lines.append(f"  ✅ 已設定：<#{log_ch_id}>")
            lines.append(f"  對話紀錄 + 禁言紀錄都會發送到此頻道")
        else:
            lines.append(f"  ❌ 未設定")
            lines.append(f"  → 用 `/chat log_channel` 設定")
        lines.append(f"")
        lines.append(f"**7. 測試**")
        lines.append(f"  請在這個頻道發一則 >15 字的訊息，然後查看 Render logs")
        lines.append(f"  應該能看到 `📩 on_message: ...` 的日誌")
        lines.append(f"")
        embed = discord.Embed(title="🔍 AI 聊天診斷", description="\n".join(lines), color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status", description="查看 AI 聊天設定")
    async def chat_status(self, interaction: discord.Interaction):
        enabled = "✅ 開啟" if chat_ai_settings["enabled"] else "❌ 關閉"
        model = chat_ai_settings.get("model", "gpt-4o-mini")
        cooldown = chat_ai_settings.get("cooldown_seconds", 60)
        wl = chat_ai_settings.get("channels_whitelist", [])
        if wl:
            channels = ", ".join(f"<#{cid}>" for cid in wl)
        else:
            channels = "所有頻道"
        key_set = "✅ 已設定" if chat_ai_settings.get("api_key") else "❌ 未設定"

        embed = discord.Embed(title="🤖 AI 聊天設定", color=discord.Color.green())
        embed.add_field(name="狀態", value=enabled, inline=True)
        embed.add_field(name="API Key", value=key_set, inline=True)
        embed.add_field(name="模型", value=f"`{model}`", inline=True)
        embed.add_field(name="冷卻時間", value=f"{cooldown} 秒", inline=True)
        filter_str = chat_ai_settings.get("filter_strength", "mention")
        filter_names = {"off": "關閉", "low": "低", "medium": "中", "high": "高"}
        embed.add_field(name="過濾強度", value=filter_names.get(filter_str, filter_str), inline=True)
        embed.add_field(name="頻道白名單", value=channels, inline=False)
        mem_count = len(user_memories)
        embed.add_field(name="用戶記憶", value=f"已記住 {mem_count} 位使用者", inline=True)
        abuse_on = chat_ai_settings.get("abuse_detection_enabled", False)
        abuse_strict = chat_ai_settings.get("abuse_detection_strictness", "medium")
        embed.add_field(name="濫用偵測", value=f"{'✅' if abuse_on else '❌'} {abuse_strict}", inline=True)
        log_ch_id = chat_ai_settings.get("log_channel_id")
        log_ch_val = f"<#{log_ch_id}>" if log_ch_id else "未設定"
        embed.add_field(name="紀錄頻道", value=log_ch_val, inline=True)
        # Micropedia status
        micro_on = chat_ai_settings.get("micropedia_enabled", True)
        micro_max = chat_ai_settings.get("micropedia_max_results", 5)
        embed.add_field(name="微國家百科", value=f"{'✅' if micro_on else '❌'} (最多{micro_max}篇)", inline=True)
        embed.set_footer(text="/chat toggle | /chat filter | /chat abuse_toggle | /chat log_channel | /chat memory | /chat micropedia | /chat debug")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="micropedia", description="開關微國家百科查詢功能（機器人擁有者限定）")
    @app_commands.describe(action="開啟或關閉", max_results="每次查詢最多抓取幾篇文章（1-10）")
    @app_commands.choices(action=[
        app_commands.Choice(name="開啟", value="on"),
        app_commands.Choice(name="關閉", value="off"),
    ])
    async def chat_micropedia(self, interaction: discord.Interaction, action: app_commands.Choice[str] = None, max_results: int = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if action:
            chat_ai_settings["micropedia_enabled"] = (action.value == "on")
            save_chat_ai_settings()
        if max_results is not None:
            chat_ai_settings["micropedia_max_results"] = max(1, min(10, max_results))
            save_chat_ai_settings()
        status = "✅ 開啟" if chat_ai_settings.get("micropedia_enabled", True) else "❌ 關閉"
        max_r = chat_ai_settings.get("micropedia_max_results", 5)
        await interaction.response.send_message(
            f"📚 微國家百科查詢：{status}\n每次查詢最多抓取：{max_r} 篇文章\n"
            f"來源：https://www.micropedia.site/",
            ephemeral=True
        )

    @app_commands.command(name="micropedia_test", description="測試微國家百科查詢（機器人擁有者限定）")
    @app_commands.describe(query="要搜尋的關鍵字")
    async def chat_micropedia_test(self, interaction: discord.Interaction, query: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        max_r = chat_ai_settings.get("micropedia_max_results", 5)
        result = await _fetch_micropedia(query, max_r)
        if not result:
            await interaction.followup.send(f"📚 搜尋「{query}」沒有找到結果。", ephemeral=True)
        else:
            # Truncate for Discord (2000 char limit)
            display = result[:1900]
            if len(result) > 1900:
                display += "..."
            await interaction.followup.send(f"📚 搜尋「{query}」的結果：\n\n{display}", ephemeral=True)


    @app_commands.command(name="emoji_alias", description="設定表情符號的別名，讓 AI 看懂含義（機器人擁有者限定）")
    @app_commands.describe(
        emoji="要設定別名的表情符號（直接貼上表情或輸入名稱）",
        alias="人類可讀的別名（例如：偉廷微笑）。留空則清除該表情的別名",
    )
    async def system_emoji_alias(self, interaction: discord.Interaction, emoji: str, alias: str = ""):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        # Parse emoji: user may type the raw <:name:id> or just the name
        m = re.match(r"<a?:(\w+):(\d+)>", emoji)
        if m:
            emoji_name = m.group(1)
            emoji_id = m.group(2)
        else:
            emoji_name = emoji.strip().strip(":")
            emoji_id = None
            for e in interaction.guild.emojis:
                if e.name == emoji_name:
                    emoji_id = str(e.id)
                    break
            if not emoji_id:
                await interaction.response.send_message(
                    f"❌ 找不到名為「{emoji}」的表情符號。\n請直接從 Discord 表情選擇器貼上完整的表情，或輸入表情名稱。",
                    ephemeral=True
                )
                return

        emoji_obj = None
        for e in interaction.guild.emojis:
            if str(e.id) == emoji_id:
                emoji_obj = e
                break

        if not alias:
            if emoji_name in emoji_aliases:
                del emoji_aliases[emoji_name]
                save_emoji_aliases()
                await interaction.response.send_message(f"✅ 已清除表情 `{emoji_name}` 的別名。", ephemeral=True)
            else:
                await interaction.response.send_message(f"ℹ️ 表情 `{emoji_name}` 本來就沒有設定別名。", ephemeral=True)
        else:
            emoji_aliases[emoji_name] = {
                "alias": alias,
                "emoji_id": emoji_id,
                "animated": bool(emoji_obj and emoji_obj.animated),
            }
            save_emoji_aliases()
            prefix = "a" if emoji_obj and emoji_obj.animated else ""
            await interaction.response.send_message(
                f"✅ 表情別名已設定：\n"
                f"  表情：<{prefix}:{emoji_name}:{emoji_id}>\n"
                f"  別名：{alias}\n"
                f"  AI 現在會知道這個表情代表「{alias}」，並在合適的時機使用。\n"
                f"  （AI 也可以用 `:{alias}:` 來表示，系統會自動轉換）",
                ephemeral=True
            )

    @app_commands.command(name="emoji_list", description="查看所有已設定別名的表情符號")
    async def system_emoji_list(self, interaction: discord.Interaction):
        if not emoji_aliases:
            await interaction.response.send_message(
                "ℹ️ 目前沒有設定任何表情別名。\n"
                "用 `/system emoji_alias` 來設定，讓 AI 看懂自訂表情的含義。",
                ephemeral=True
            )
            return

        lines = ["🧩 已設定的表情別名："]
        for name, data in emoji_aliases.items():
            prefix = "a" if data.get("animated") else ""
            eid = data.get("emoji_id", "")
            alias_label = data.get("alias", "")
            lines.append(f"<{prefix}:{name}:{eid}> = {alias_label}")

        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n..."
        await interaction.response.send_message(text, ephemeral=True)


    @app_commands.command(name="forum_index", description="查看/刷新論壇貼文搜尋索引（機器人擁有者限定）")
    async def system_forum_index(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        posts = await _refresh_forum_index(interaction.guild)
        forum_count = len(list(interaction.guild.forums))

        embed = discord.Embed(
            title="🗂️ 論壇貼文搜尋索引",
            description=(
                f"論壇頻道數：{forum_count}\n"
                f"已索引貼文數：{len(posts)}\n"
                f"快取有效期：每 15 分鐘自動刷新（此指令可手動立即刷新）"
            ),
            color=discord.Color.blue(),
        )
        if posts:
            sample = "\n".join(f"• 【{p['channel_name']}】{p['title']}" for p in posts[:15])
            if len(posts) > 15:
                sample += f"\n...還有 {len(posts) - 15} 篇"
            embed.add_field(name="已索引的貼文（部分）", value=sample[:1024] or "無", inline=False)
        embed.set_footer(text="這個索引讓 AI 的 search_discord 工具能找到論壇貼文內容（含 Embed），不只是純文字訊息")
        await interaction.followup.send(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────
# Token Log 定期公告（每 30 分鐘）
# ──────────────────────────────────────────────

async def token_log_loop():
    """Background task: post token usage to the AI log channel every 30 minutes."""
    await asyncio.sleep(120)
    while True:
        try:
            log_channel_id = chat_ai_settings.get("log_channel_id")
            if not log_channel_id:
                await asyncio.sleep(1800)
                continue
            log_ch = None
            for guild in bot.guilds:
                ch = guild.get_channel(int(log_channel_id))
                if ch:
                    log_ch = ch
                    break
            if not log_ch:
                print("⚠️ Token Log: Cannot find log channel")
                await asyncio.sleep(1800)
                continue
            today = _time.strftime("%Y-%m-%d")
            started_at = token_usage.get("started_at", _time.time())
            uptime_seconds = int(_time.time() - started_at)
            uptime_days = uptime_seconds // 86400
            uptime_hours = (uptime_seconds % 86400) // 3600
            uptime_str = f"{uptime_days}天 {uptime_hours}小時"
            embed = discord.Embed(
                title="📊 Token 使用量報告",
                color=discord.Color.dark_green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="📈 累計總量（自啟用以來）",
                value=(
                    f"```\n"
                    f"Total Tokens:   {token_usage['total_tokens']:>12,}\n"
                    f"Prompt Tokens:  {token_usage['prompt_tokens']:>12,}\n"
                    f"Completion:     {token_usage['completion_tokens']:>12,}\n"
                    f"API 呼叫次數:    {token_usage['api_calls']:>12,}\n"
                    f"運行時間:        {uptime_str}\n"
                    f"```"
                ),
                inline=False
            )
            embed.add_field(
                name=f"📅 今日用量（{today}）",
                value=(
                    f"```\n"
                    f"Total Tokens:   {token_usage['today_tokens']:>12,}\n"
                    f"Prompt Tokens:  {token_usage['today_prompt']:>12,}\n"
                    f"Completion:     {token_usage['today_completion']:>12,}\n"
                    f"API 呼叫次數:    {token_usage['today_calls']:>12,}\n"
                    f"```"
                ),
                inline=False
            )
            if token_usage["api_calls"] > 0:
                avg = token_usage["total_tokens"] / token_usage["api_calls"]
                embed.set_footer(text=f"平均每次呼叫 {avg:.0f} tokens | 每 30 分鐘自動更新")
            else:
                embed.set_footer(text="每 30 分鐘自動更新")
            await log_ch.send(embed=embed)
            save_token_usage()
            print(f"📊 Token Log posted: cumulative={token_usage['total_tokens']:,}, today={token_usage['today_tokens']:,}")
        except Exception as e:
            print(f"⚠️ Token Log loop error: {e}")
        await asyncio.sleep(1800)


# ════════════════════════════════════════════════════════════
# AI 問答系統（AI Quiz System）
# 每半小時從微國家百科隨機出題，搶答得分，每晚 22:00 結算冠軍
# ════════════════════════════════════════════════════════════

import random as _quiz_random

# ── 資料檔案路徑 ──
QUIZ_SETTINGS_FILE = os.path.join(DATA_DIR, "quiz_settings.json")
QUIZ_SCORES_FILE = os.path.join(DATA_DIR, "quiz_scores.json")
QUIZ_CHAMPIONS_FILE = os.path.join(DATA_DIR, "quiz_champions.json")
QUIZ_STATE_FILE = os.path.join(DATA_DIR, "quiz_state.json")

# ── Emoji aliases: map cryptic emoji names to human-readable descriptions ──
EMOJI_ALIASES_FILE = os.path.join(DATA_DIR, "emoji_aliases.json")
emoji_aliases = {}  # {original_name: {"alias": "人類可讀名", "emoji_id": "...", "animated": false}}

def save_emoji_aliases():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(EMOJI_ALIASES_FILE, "w", encoding="utf-8") as f:
            json_module.dump(emoji_aliases, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Emoji aliases save failed: {e}")

def load_emoji_aliases():
    global emoji_aliases
    try:
        if os.path.exists(EMOJI_ALIASES_FILE):
            with open(EMOJI_ALIASES_FILE, "r", encoding="utf-8") as f:
                emoji_aliases = json_module.load(f)
            print(f"✅ 表情別名載入：{len(emoji_aliases)} 個別名")
    except Exception as e:
        print(f"⚠️ Emoji aliases load failed: {e}")

# ── 記憶體狀態 ──
quiz_settings = {
    "channel_id": None,       # Discord channel ID for quiz
    "guild_id": None,         # Discord guild ID
    "enabled": False,         # on/off
    "interval_minutes": 30,   # question frequency
}
quiz_scores = {}      # {user_id_str: {username, daily_score, total_score, date}}
quiz_champions = []   # [{date, champion_id, champion_name, champion_score, runner_up_name, runner_up_score}]
# Active questions: {message_id_str: {question, options, correct_index, source_title, source_url, answered_by, created_at}}
quiz_active_questions = {}


def save_quiz_data():
    """Save quiz settings, scores, champions, and active state to disk."""
    global _quiz_last_question_time
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(QUIZ_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json_module.dump(quiz_settings, f, ensure_ascii=False, indent=2)
        with open(QUIZ_SCORES_FILE, "w", encoding="utf-8") as f:
            json_module.dump(quiz_scores, f, ensure_ascii=False, indent=2)
        with open(QUIZ_CHAMPIONS_FILE, "w", encoding="utf-8") as f:
            json_module.dump(quiz_champions, f, ensure_ascii=False, indent=2)
        quiz_state = {
            "active_questions": quiz_active_questions,
            "last_question_time": _quiz_last_question_time,
        }
        with open(QUIZ_STATE_FILE, "w", encoding="utf-8") as f:
            json_module.dump(quiz_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Quiz data save failed: {e}")


def load_quiz_data():
    """Load quiz data from disk."""
    global quiz_settings, quiz_scores, quiz_champions, quiz_active_questions, _quiz_last_question_time
    try:
        if os.path.exists(QUIZ_SETTINGS_FILE):
            with open(QUIZ_SETTINGS_FILE, "r", encoding="utf-8") as f:
                quiz_settings.update(json_module.load(f))
        if os.path.exists(QUIZ_SCORES_FILE):
            with open(QUIZ_SCORES_FILE, "r", encoding="utf-8") as f:
                quiz_scores = json_module.load(f)
        if os.path.exists(QUIZ_CHAMPIONS_FILE):
            with open(QUIZ_CHAMPIONS_FILE, "r", encoding="utf-8") as f:
                quiz_champions = json_module.load(f)
        if os.path.exists(QUIZ_STATE_FILE):
            with open(QUIZ_STATE_FILE, "r", encoding="utf-8") as f:
                state = json_module.load(f)
                quiz_active_questions = state.get("active_questions", {})
                _quiz_last_question_time = state.get("last_question_time", 0)
        print(f"✅ 問答資料載入：{'啟用' if quiz_settings.get('enabled') else '停用'}, "
              f"{len(quiz_scores)} 位玩家, {len(quiz_champions)} 位冠軍, "
              f"{len(quiz_active_questions)} 個活躍題目")
    except Exception as e:
        print(f"⚠️ Quiz data load failed: {e}")


async def _generate_quiz_question() -> dict | None:
    """Fetch a random micropedia article and generate a quiz question via AI.
    Returns {question, options: [4], correct_index: 0-3, source_title, source_url} or None."""
    if not chat_ai_settings.get("api_key"):
        print("⚠️ Quiz: No AI API key configured")
        return None

    # Pick a random broad search term to get varied articles
    search_terms = [
        "共和國", "聯邦", "王國", "帝國", "公國", "共和",
        "自由邦", "城邦", "聯盟", "組織", "條約", "宣言",
        "憲法", "政府", "選舉", "文化", "歷史", "經濟",
        "外交", "國旗", "國歌", "節日", "軍事", "教育",
    ]
    term = _quiz_random.choice(search_terms)
    try:
        article_text = await asyncio.wait_for(
            _fetch_micropedia(term, max_results=3),
            timeout=10
        )
    except asyncio.TimeoutError:
        print("⚠️ Quiz: Micropedia fetch timed out")
        return None
    except Exception as e:
        print(f"⚠️ Quiz: Micropedia fetch error: {e}")
        return None

    if not article_text or len(article_text.strip()) < 50:
        # Retry with a different term
        term2 = _quiz_random.choice(search_terms)
        try:
            article_text = await asyncio.wait_for(
                _fetch_micropedia(term2, max_results=3),
                timeout=10
            )
        except Exception:
            print("⚠️ Quiz: Micropedia retry also failed")
            return None
        if not article_text or len(article_text.strip()) < 50:
            print("⚠️ Quiz: Not enough content from micropedia")
            return None

    # Truncate to keep token usage reasonable
    article_text = article_text[:3000]

    # Extract source title/URL from the article text if present
    source_title = ""
    source_url = ""
    for line in article_text.split("\n"):
        if line.startswith("📖 ") or line.startswith("標題:") or line.startswith("【"):
            source_title = line.strip("📖標題：【】 ")
            break
    for line in article_text.split("\n"):
        if "micropedia.site" in line:
            source_url = line.strip()
            break

    # Generate question via AI
    system_prompt = (
        "你是微國家百科問答出題機。根據提供的百科資料，出一道單選題。\n"
        "題目要求：\n"
        "- 只出單選題，4個選項\n"
        "- 題目要清楚明確，答案必須能從資料中找到\n"
        "- 選項要合理，有迷惑性但不能有爭議\n"
        "- 正確答案的位置要隨機（不要總是放在同一個位置）\n\n"
        "請嚴格回覆以下 JSON 格式（不要加 markdown code block，不要加其他文字）：\n"
        '{"question": "題目", "options": ["選項A", "選項B", "選項C", "選項D"], "correct_index": 0}'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"百科資料：\n{article_text}\n\n請根據以上資料出一道單選題。"}
    ]

    try:
        result = await asyncio.wait_for(
            call_chat_api(messages, chat_ai_settings),
            timeout=30
        )
    except asyncio.TimeoutError:
        print("⚠️ Quiz: AI question generation timed out")
        return None
    except Exception as e:
        print(f"⚠️ Quiz: AI API error: {e}")
        return None

    # Parse the AI response
    raw_reply = result.get("content", "")
    if not raw_reply:
        # Try tool_calls if content is empty
        tool_calls = result.get("tool_calls", [])
        if tool_calls:
            raw_reply = tool_calls[0].get("function", {}).get("arguments", "")

    # Strip markdown code blocks if present
    raw_reply = raw_reply.strip()
    if raw_reply.startswith("```"):
        raw_reply = raw_reply.split("\n", 1)[-1] if "\n" in raw_reply else raw_reply[3:]
    if raw_reply.endswith("```"):
        raw_reply = raw_reply[:-3]
    raw_reply = raw_reply.strip()

    try:
        quiz_data = json_module.loads(raw_reply)
    except Exception:
        # Try to extract JSON from the text
        import re
        match = re.search(r'\{[^{}]*"question"[^{}]*\}', raw_reply, re.DOTALL)
        if match:
            try:
                quiz_data = json_module.loads(match.group())
            except Exception:
                print(f"⚠️ Quiz: Cannot parse AI response: {raw_reply[:200]}")
                return None
        else:
            print(f"⚠️ Quiz: Cannot parse AI response: {raw_reply[:200]}")
            return None

    # Validate schema
    if not isinstance(quiz_data, dict):
        print("⚠️ Quiz: AI response is not a dict")
        return None
    if "question" not in quiz_data or "options" not in quiz_data or "correct_index" not in quiz_data:
        print("⚠️ Quiz: AI response missing required fields")
        return None
    options = quiz_data["options"]
    if not isinstance(options, list) or len(options) != 4:
        print(f"⚠️ Quiz: options must have 4 items, got {len(options)}")
        return None
    correct_index = quiz_data["correct_index"]
    if not isinstance(correct_index, int) or correct_index < 0 or correct_index > 3:
        print(f"⚠️ Quiz: correct_index must be 0-3, got {correct_index}")
        return None
    question = quiz_data["question"]
    if not isinstance(question, str) or len(question.strip()) < 5:
        print(f"⚠️ Quiz: question too short")
        return None

    return {
        "question": question.strip(),
        "options": [str(o).strip() for o in options],
        "correct_index": correct_index,
        "source_title": source_title,
        "source_url": source_url,
    }


class QuizAnswerView(discord.ui.View):
    """Interactive buttons for quiz answers. Times out after 10 minutes."""

    def __init__(self, question_data: dict, message_id: int):
        super().__init__(timeout=600)  # 10 minutes
        self.question_data = question_data
        self.message_id = message_id
        self.answered = False
        self.correct_user_id = None

        for i, option_text in enumerate(question_data["options"]):
            label = f"{'🇦🇧🇨🇩'[i]} {option_text}" if i < 4 else option_text
            # Use simple letter labels to keep buttons short
            letter = "ABCD"[i]
            btn = discord.ui.Button(
                label=f"{letter}. {option_text[:70]}",
                style=discord.ButtonStyle.primary,
                custom_id=f"quiz_{message_id}_{i}"
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            await self._handle_answer(interaction, index)
        return callback

    async def _handle_answer(self, interaction: discord.Interaction, selected_index: int):
        user_id_str = str(interaction.user.id)

        # Each user gets exactly ONE attempt per question
        if not hasattr(self, '_user_attempted'):
            self._user_attempted = set()
        if user_id_str in self._user_attempted:
            if user_id_str == self.correct_user_id:
                await interaction.response.send_message("你已經答對了！🎉", ephemeral=True)
            else:
                await interaction.response.send_message("你已經答過了，這題沒有機會了！", ephemeral=True)
            return

        # Already answered correctly by someone
        if self.answered:
            await interaction.response.send_message("已經有人搶答成功了！⚡", ephemeral=True)
            return

        # Mark this user as having used their one attempt
        self._user_attempted.add(user_id_str)

        correct_index = self.question_data["correct_index"]

        if selected_index == correct_index:
            # First correct answer!
            self.answered = True
            self.correct_user_id = user_id_str

            # Award 5 points
            today = _time.strftime("%Y-%m-%d")
            user_entry = quiz_scores.get(user_id_str, {
                "username": interaction.user.display_name,
                "daily_score": 0,
                "total_score": 0,
                "date": today,
            })
            # Reset daily score if date changed
            if user_entry.get("date") != today:
                user_entry["daily_score"] = 0
                user_entry["date"] = today
            user_entry["username"] = interaction.user.display_name
            user_entry["daily_score"] = user_entry.get("daily_score", 0) + 5
            user_entry["total_score"] = user_entry.get("total_score", 0) + 5
            quiz_scores[user_id_str] = user_entry
            save_quiz_data()

            # Update the active question
            quiz_active_questions[str(self.message_id)]["answered_by"] = user_id_str

            # Edit the embed to show the answer
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.color = discord.Color.green()
                embed.add_field(
                    name="🎉 搶答成功！",
                    value=f"**{interaction.user.display_name}** 最先答對，獲得 **5 分**！\n"
                          f"正確答案：**{'ABCD'[correct_index]}. {self.question_data['options'][correct_index]}**",
                    inline=False
                )
                if self.question_data.get("source_url"):
                    embed.add_field(
                        name="📚 來源",
                        value=f"[{self.question_data.get('source_title', '查看原文')}]({self.question_data['source_url']})",
                        inline=False
                    )
                # Disable all buttons
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.send_message(
                    f"🎉 答對了！+5 分！你今天累積 {user_entry['daily_score']} 分。",
                    ephemeral=True
                )

            # Also send a public celebration message
            try:
                await interaction.followup.send(
                    f"🎉 **{interaction.user.display_name}** 搶答成功！+5 分！"
                    f"（今日累積 {user_entry['daily_score']} 分）",
                    ephemeral=False
                )
            except Exception:
                pass
            print(f"🎉 Quiz: {interaction.user.display_name} answered correctly (+5 pts, daily={user_entry['daily_score']})")
        else:
            # Wrong answer — one strike and you're out
            await interaction.response.send_message(
                "❌ 答錯了！這題你已經沒有機會了，等下一題吧！",
                ephemeral=True
            )

    async def on_timeout(self):
        """Reveal the answer when no one answers in time."""
        if self.answered:
            return  # Already answered, nothing to do

        # Mark as timed out
        quiz_active_questions.pop(str(self.message_id), None)
        save_quiz_data()

        # Try to edit the message with the answer
        try:
            channel_id = quiz_settings.get("channel_id")
            if channel_id:
                channel = bot.get_channel(int(channel_id))
                if channel:
                    correct_idx = self.question_data["correct_index"]
                    embed = discord.Embed(
                        title="⏰ 時間到！無人答對",
                        color=discord.Color.orange(),
                    )
                    embed.add_field(
                        name="正確答案",
                        value=f"**{'ABCD'[correct_idx]}. {self.question_data['options'][correct_idx]}**",
                        inline=False
                    )
                    if self.question_data.get("source_url"):
                        embed.add_field(
                            name="📚 來源",
                            value=f"[{self.question_data.get('source_title', '查看原文')}]({self.question_data['source_url']})",
                            inline=False
                        )
                    # Disable all buttons
                    for child in self.children:
                        child.disabled = True
                    # Get the original message
                    msg = await channel.fetch_message(self.message_id)
                    if msg:
                        await msg.edit(embed=embed, view=self)
                    print("⏰ Quiz: Question timed out, answer revealed")
        except Exception as e:
            print(f"⚠️ Quiz timeout reveal failed: {e}")


_quiz_last_question_time = 0  # timestamp of last posted question

async def quiz_question_loop():
    """Background task: post a new quiz question at the configured interval.
    Uses short 15-second poll cycles so interval changes take effect immediately."""
    global _quiz_last_question_time
    await asyncio.sleep(60)  # Wait for bot to be ready
    while True:
        try:
            interval_secs = quiz_settings.get("interval_minutes", 30) * 60

            # Not enabled? Short sleep and re-check
            if not quiz_settings.get("enabled"):
                await asyncio.sleep(15)
                continue

            channel_id = quiz_settings.get("channel_id")
            if not channel_id:
                await asyncio.sleep(15)
                continue

            # Has enough time passed since the last question?
            now = _time.time()
            if _quiz_last_question_time and (now - _quiz_last_question_time) < interval_secs:
                await asyncio.sleep(15)
                continue

            # Clean up stale active questions (older than 10 minutes)
            stale_keys = [
                k for k, v in quiz_active_questions.items()
                if (now - v.get("created_at", 0)) > 600
            ]
            for k in stale_keys:
                quiz_active_questions.pop(k, None)
                print(f"🧹 Quiz: Cleaned up stale question {k}")
            if stale_keys:
                save_quiz_data()

            # Check if there's an unanswered active question — don't pile up
            if quiz_active_questions:
                print(f"ℹ️ Quiz: {len(quiz_active_questions)} question(s) still active, skipping this round")
                await asyncio.sleep(15)
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                print(f"⚠️ Quiz: Cannot find channel {channel_id}")
                await asyncio.sleep(15)
                continue

            # Generate the question
            print("📝 Quiz: Generating new question...")
            quiz_data = await _generate_quiz_question()
            if not quiz_data:
                print("⚠️ Quiz: Failed to generate question, will retry next cycle")
                _quiz_last_question_time = now  # Reset timer to avoid immediate retry spam
                await asyncio.sleep(15)
                continue

            # Create embed
            embed = discord.Embed(
                title="🧠 微國家百科問答",
                description=f"**{quiz_data['question']}\n\n快選出正確答案！最先答對得 5 分！",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="⏱️ 10 分鐘內搶答 | 最先答對得 5 分")

            # Send the question
            msg = await channel.send(embed=embed)
            view = QuizAnswerView(quiz_data, msg.id)
            await msg.edit(view=view)

            # Store the active question
            quiz_active_questions[str(msg.id)] = {
                "question": quiz_data["question"],
                "options": quiz_data["options"],
                "correct_index": quiz_data["correct_index"],
                "source_title": quiz_data.get("source_title", ""),
                "source_url": quiz_data.get("source_url", ""),
                "answered_by": None,
                "created_at": _time.time(),
            }

            _quiz_last_question_time = _time.time()
            save_quiz_data()
            print(f"✅ Quiz: Question posted in #{channel.name} (msg_id={msg.id})")
        except Exception as e:
            print(f"⚠️ Quiz loop error: {e}")

        await asyncio.sleep(15)  # Short poll cycle — interval changes take effect immediately


async def quiz_settlement_loop():
    """Background task: settle daily champion at 22:00 every day."""
    await asyncio.sleep(60)  # Wait for bot to be ready
    while True:
        try:
            # Check if it's 22:00 (check every 30 seconds for precision)
            now = _time.localtime()
            if now.tm_hour == 22 and now.tm_min == 0 and now.tm_sec < 30:
                today = _time.strftime("%Y-%m-%d")

                # Find today's top scorer(s)
                today_scores = []
                for uid, entry in quiz_scores.items():
                    if entry.get("date") == today and entry.get("daily_score", 0) > 0:
                        today_scores.append((uid, entry["username"], entry["daily_score"]))

                channel_id = quiz_settings.get("channel_id")
                channel = bot.get_channel(int(channel_id)) if channel_id else None

                if not today_scores:
                    if channel:
                        embed = discord.Embed(
                            title="📊 今日問答結算",
                            description="今天沒有人得分，再接再厲！明天 22:00 再結算～",
                            color=discord.Color.orange(),
                        )
                        await channel.send(embed=embed)
                    # Still reset daily scores
                    for uid, entry in quiz_scores.items():
                        if entry.get("date") == today:
                            entry["daily_score"] = 0
                    save_quiz_data()
                    print("📊 Quiz: Daily settlement — no scores today")
                else:
                    # Sort by score descending
                    today_scores.sort(key=lambda x: -x[2])
                    champion_uid, champion_name, champion_score = today_scores[0]
                    runner_up_name = today_scores[1][1] if len(today_scores) > 1 else "—"
                    runner_up_score = today_scores[1][2] if len(today_scores) > 1 else 0

                    # Check for ties
                    tied = [(uid, name, score) for uid, name, score in today_scores if score == champion_score]

                    # Record champion(s)
                    for uid, name, score in tied:
                        quiz_champions.append({
                            "date": today,
                            "champion_id": uid,
                            "champion_name": name,
                            "champion_score": score,
                            "runner_up_name": runner_up_name,
                            "runner_up_score": runner_up_score,
                        })

                    if channel:
                        if len(tied) > 1:
                            embed = discord.Embed(
                                title="🏆 今日問答結算 — 共同冠軍！",
                                color=discord.Color.gold(),
                                timestamp=discord.utils.utcnow(),
                            )
                            champ_text = "\n".join(f"👑 **{name}** — {score} 分" for _, name, score in tied)
                            embed.add_field(name="共同冠軍", value=champ_text, inline=False)
                        else:
                            embed = discord.Embed(
                                title="🏆 今日問答結算",
                                color=discord.Color.gold(),
                                timestamp=discord.utils.utcnow(),
                            )
                            embed.add_field(
                                name="🥇 冠軍",
                                value=f"**{champion_name}** — {champion_score} 分",
                                inline=False
                            )
                            if len(today_scores) > 1:
                                embed.add_field(
                                    name="🥈 亞軍",
                                    value=f"**{runner_up_name}** — {runner_up_score} 分",
                                    inline=False
                                )
                            embed.add_field(
                                name="📊 完整排名",
                                value="\n".join(
                                    f"{i+1}. {name} — {score} 分"
                                    for i, (_, name, score) in enumerate(today_scores[:10])
                                ),
                                inline=False
                            )
                        embed.set_footer(text="每日 22:00 自動結算 | 明日重新計分")
                        await channel.send(embed=embed)

                    # Reset daily scores for the new day
                    for uid, entry in quiz_scores.items():
                        if entry.get("date") == today:
                            entry["daily_score"] = 0
                    save_quiz_data()
                    print(f"🏆 Quiz: Champion settled — {champion_name} ({champion_score} pts)")

                # Sleep past this minute to avoid double-settling
                await asyncio.sleep(60)
        except Exception as e:
            print(f"⚠️ Quiz settlement error: {e}")

        await asyncio.sleep(30)


# ── Slash Command Group ──

class QuizGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="quiz", description="AI 問答系統")

    @app_commands.command(name="toggle", description="開啟/關閉 AI 問答功能（機器人擁有者限定）")
    async def quiz_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        quiz_settings["enabled"] = not quiz_settings.get("enabled", False)
        save_quiz_data()
        status = "開啟" if quiz_settings["enabled"] else "關閉"
        await interaction.response.send_message(f"✅ AI 問答已{status}。", ephemeral=True)

    @app_commands.command(name="channel", description="設定 AI 問答頻道（機器人擁有者限定）")
    @app_commands.describe(channel="要設為問答頻道的頻道")
    async def quiz_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        quiz_settings["channel_id"] = str(channel.id)
        quiz_settings["guild_id"] = str(interaction.guild.id) if interaction.guild else None
        save_quiz_data()
        await interaction.response.send_message(
            f"✅ AI 問答頻道已設為 {channel.mention}。\n"
            f"每 30 分鐘會自動出題，最先答對得 5 分，每晚 22:00 結算冠軍。",
            ephemeral=True
        )

    @app_commands.command(name="interval", description="設定出題間隔分鐘數（機器人擁有者限定）")
    @app_commands.describe(minutes="間隔分鐘數（預設 30）")
    async def quiz_interval(self, interaction: discord.Interaction, minutes: int):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if minutes < 5:
            await interaction.response.send_message("❌ 間隔至少 5 分鐘。", ephemeral=True)
            return
        quiz_settings["interval_minutes"] = minutes
        save_quiz_data()
        await interaction.response.send_message(f"✅ 出題間隔已設為 {minutes} 分鐘。", ephemeral=True)

    @app_commands.command(name="scoreboard", description="查看問答積分榜")
    async def quiz_scoreboard(self, interaction: discord.Interaction):
        today = _time.strftime("%Y-%m-%d")
        today_scores = []
        all_time_scores = []
        for uid, entry in quiz_scores.items():
            if entry.get("date") == today and entry.get("daily_score", 0) > 0:
                today_scores.append((entry["username"], entry["daily_score"]))
            total = entry.get("total_score", 0)
            if total > 0:
                all_time_scores.append((entry["username"], total))

        today_scores.sort(key=lambda x: -x[1])
        all_time_scores.sort(key=lambda x: -x[1])

        embed = discord.Embed(
            title="📊 AI 問答積分榜",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )

        if today_scores:
            embed.add_field(
                name=f"📅 今日排名 ({today})",
                value="\n".join(
                    f"{i+1}. {name} — {score} 分"
                    for i, (name, score) in enumerate(today_scores[:10])
                ),
                inline=False
            )
        else:
            embed.add_field(name="📅 今日排名", value="尚無得分紀錄", inline=False)

        if all_time_scores:
            embed.add_field(
                name="🏆 總排行",
                value="\n".join(
                    f"{i+1}. {name} — {score} 分"
                    for i, (name, score) in enumerate(all_time_scores[:10])
                ),
                inline=False
            )
        else:
            embed.add_field(name="🏆 總排行", value="尚無得分紀錄", inline=False)

        embed.set_footer(text="每日 22:00 結算 | 最先答對得 5 分")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="champion", description="查看歷屆問答冠軍")
    async def quiz_champion(self, interaction: discord.Interaction):
        if not quiz_champions:
            await interaction.response.send_message("尚無冠軍紀錄。每晚 22:00 自動結算。", ephemeral=True)
            return

        recent = quiz_champions[-7:]  # last 7 days
        embed = discord.Embed(
            title="🏆 歷屆問答冠軍",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        for champ in reversed(recent):
            embed.add_field(
                name=f"📅 {champ['date']}",
                value=f"👑 **{champ['champion_name']}** — {champ['champion_score']} 分\n"
                      f"🥈 {champ.get('runner_up_name', '—')} — {champ.get('runner_up_score', 0)} 分",
                inline=False
            )
        embed.set_footer(text="顯示最近 7 天 | 每晚 22:00 自動結算")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="now", description="立即出題（機器人擁有者限定）")
    async def quiz_now(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        channel_id = quiz_settings.get("channel_id")
        if not channel_id:
            await interaction.response.send_message("❌ 尚未設定問答頻道。請先用 `/quiz channel` 設定。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        quiz_data = await _generate_quiz_question()
        if not quiz_data:
            await interaction.followup.send("❌ 出題失敗，請稍後再試。", ephemeral=True)
            return
        channel = bot.get_channel(int(channel_id))
        if not channel:
            await interaction.followup.send("❌ 找不到問答頻道。", ephemeral=True)
            return
        embed = discord.Embed(
            title="🧠 微國家百科問答",
            description=f"**{quiz_data['question']}**\n\n快選出正確答案！最先答對得 5 分！",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="⏱️ 10 分鐘內搶答 | 最先答對得 5 分")
        msg = await channel.send(embed=embed)
        view = QuizAnswerView(quiz_data, msg.id)
        await msg.edit(view=view)
        quiz_active_questions[str(msg.id)] = {
            "question": quiz_data["question"],
            "options": quiz_data["options"],
            "correct_index": quiz_data["correct_index"],
            "source_title": quiz_data.get("source_title", ""),
            "source_url": quiz_data.get("source_url", ""),
            "answered_by": None,
            "created_at": _time.time(),
        }
        await interaction.followup.send(f"✅ 已在 {channel.mention} 出題。", ephemeral=True)

    @app_commands.command(name="status", description="查看問答系統狀態")
    async def quiz_status(self, interaction: discord.Interaction):
        today = _time.strftime("%Y-%m-%d")
        today_players = sum(1 for e in quiz_scores.values() if e.get("date") == today and e.get("daily_score", 0) > 0)
        total_questions_answered = sum(1 for q in quiz_active_questions.values() if q.get("answered_by"))
        embed = discord.Embed(
            title="📋 AI 問答系統狀態",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="狀態", value="✅ 啟用" if quiz_settings.get("enabled") else "❌ 停用", inline=True)
        embed.add_field(name="出題間隔", value=f"{quiz_settings.get('interval_minutes', 30)} 分鐘", inline=True)
        ch = quiz_settings.get("channel_id")
        embed.add_field(name="頻道", value=f"<#{ch}>" if ch else "未設定", inline=True)
        embed.add_field(name="今日玩家", value=str(today_players), inline=True)
        embed.add_field(name="總玩家", value=str(len(quiz_scores)), inline=True)
        embed.add_field(name="冠軍紀錄", value=str(len(quiz_champions)), inline=True)
        embed.add_field(name="活躍題目", value=str(len(quiz_active_questions)), inline=True)
        embed.set_footer(text="每日 22:00 自動結算 | /quiz toggle 開關 | /quiz channel 設定頻道")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────
# 快報與公報指令
# ──────────────────────────────────────────────

class BriefingGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="briefing", description="每日快報與每週公報")

    @app_commands.command(name="daily_set", description="設定每日自動快報時間（機器人擁有者限定）")
    @app_commands.describe(time="執行時間 HH:MM（例如：23:00）", channel="發佈快報的頻道")
    async def daily_set(self, interaction: discord.Interaction, time: str, channel: discord.TextChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
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

    @app_commands.command(name="daily_off", description="關閉每日自動快報（機器人擁有者限定）")
    async def daily_off(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        briefing_settings["daily_enabled"] = False
        save_briefing_settings()
        await interaction.response.send_message("✅ 每日自動快報已關閉。可用 `/briefing daily_now` 手動執行。", ephemeral=True)

    @app_commands.command(name="daily_now", description="立即生成每日快報（機器人擁有者限定）")
    @app_commands.describe(channel="發佈快報的頻道（預設：當前頻道）")
    async def daily_now(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if not ai_settings["api_key"]:
            await interaction.response.send_message("❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定。", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.send_message(f"📝 每日快報開始生成，請到 {target.mention} 查看。", ephemeral=True)
        await run_briefing(target, hours=24, mode="daily")

    @app_commands.command(name="weekly_set", description="設定每週自動公報時間（機器人擁有者限定）")
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
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
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

    @app_commands.command(name="weekly_off", description="關閉每週自動公報（機器人擁有者限定）")
    async def weekly_off(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        briefing_settings["weekly_enabled"] = False
        save_briefing_settings()
        await interaction.response.send_message("✅ 每週自動公報已關閉。可用 `/briefing weekly_now` 手動執行。", ephemeral=True)

    @app_commands.command(name="weekly_now", description="立即生成每週公報（機器人擁有者限定）")
    @app_commands.describe(channel="發佈公報的頻道（預設：當前頻道）")
    async def weekly_now(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
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

async def _graceful_shutdown_save():
    """Save every local state file and force one last upload to Drive.
    Called on SIGTERM (Render redeploy/restart) so we never lose the last
    few seconds of activity between periodic sync cycles."""
    print("🛑 收到終止訊號，儲存所有暫存資料到雲端硬碟...")
    try:
        save_polls_to_disk()
        save_quiz_data()
        save_token_usage()
        save_briefing_settings()
        save_chat_ai_settings()
        save_user_memories()
        await sync_to_drive()
        print("✅ 關機前資料已全部同步到 Google Drive")
    except Exception as e:
        print(f"⚠️ 關機前儲存失敗：{e}")


def _install_shutdown_handler(loop):
    """Register SIGTERM/SIGINT handlers that flush state to Drive before exit."""
    import signal as _signal

    def _handler(sig_name):
        print(f"🛑 收到 {sig_name}，準備優雅關機...")
        asyncio.ensure_future(_shutdown_and_close())

    async def _shutdown_and_close():
        await _graceful_shutdown_save()
        await bot.close()

    try:
        for sig in (_signal.SIGTERM, _signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: _handler(s.name))
    except NotImplementedError:
        # add_signal_handler isn't available on some platforms (e.g. Windows) — skip gracefully
        print("ℹ️ 此平台不支援 signal handler，略過優雅關機設定（Linux/Render 上不受影響）")


def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("⚠️  請設定環境變數 DISCORD_BOT_TOKEN")
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
        pass


if __name__ == "__main__":
    main()
