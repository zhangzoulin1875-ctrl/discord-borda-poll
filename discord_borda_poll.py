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
  COOKIE_SECRET   - Session 簽名密鑰（選填，建議固定設定；不設則自動生成並透過 Drive 持久化保存，重啟不失效）

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
  /chat min_interval <seconds> - 設定全域最短回應間隔（防炸）
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

AI 自動計票（論壇貼文投票，用於秘書處自行在論壇貼文發起的投票，非 /poll 系統）：
  /tally count [thread] [legend] [mode]  自動判讀貼文投票格式與 Emoji 對照並計票（管理員限定）
                                          thread 留空則使用目前所在的貼文
                                          legend 可手動指定 Emoji=候選人 對照（逗號分隔）
                                          mode 可強制指定 single（單選）或 ranked（波達計數法）
                                          計票完成後可點擊按鈕將結果公佈於原貼文
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
except Exception as e:
    print("⚠️ 靜默例外:", e)
print = functools.partial(print, flush=True)

import discord
from discord import app_commands
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union
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
import io
import glob

try:
    import jwt as pyjwt  # PyJWT for Google Drive service account auth
except ImportError:
    pyjwt = None

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    print("⚠️ Pillow 未安裝，會議排程通知圖功能將無法使用（pip install Pillow）")
import hmac
import hashlib
import json as json_module
from datetime import datetime, timedelta, timezone
from aiohttp import web

# ── GMT+8 (Taiwan) timezone for all user-facing timestamps and scheduling ──
# Render runs in UTC; we convert everything to Asia/Taipei for display.
GMT8 = timezone(timedelta(hours=8))


def _now_iso() -> str:
    """目前時間（GMT+8台灣時區）的 ISO 格式字串，用於資料儲存的時間戳記。"""
    return datetime.now(GMT8).isoformat()


def _now_dt():
    """目前時間（UTC，timezone-aware），用於 discord.Embed 的 timestamp 參數——
    Discord 客戶端會自動依使用者當地時區顯示，因此 embed timestamp 統一用 UTC，
    跟其他文字顯示用的 GMT+8 時間字串是分開的兩套（沿用專案既有慣例）。"""
    return discord.utils.utcnow()

# ── This bot is dedicated to a single Discord server: ICEA (國際總會 |
# International Cultural Exchange Alliance). The dashboard used to make
# users pick from a list of every server they happen to manage on Discord
# (confusing — most of those servers have nothing to do with this bot), so
# it's now hardcoded to skip straight to this one guild.
ICEA_GUILD_ID = "1425065927027720286"
GUILD_ID = int(ICEA_GUILD_ID)  # 整數形式，供模組使用


# ──────────────────────────────────────────────
# Keep-Alive HTTP Server
# ──────────────────────────────────────────────

async def self_ping_loop():
    """每 5 分鐘 self-ping 一次，防止 Render 休眠。
    Uses aiohttp (async) instead of urllib (blocking) to avoid freezing the
    event loop for up to 10 seconds on network timeouts."""
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
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    health_url,
                    headers={"User-Agent": "SelfPing/1.0"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    print(f"🏓 Self-ping OK ({resp.status})")
            except Exception as e:
                print(f"⚠️  Self-ping 失敗：{e}")
            await asyncio.sleep(270)  # 每 4.5 分鐘 ping 一次


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
    app.router.add_get("/api/schedule-settings", api_get_schedule_settings)
    app.router.add_put("/api/schedule-settings", api_set_schedule_settings)
    app.router.add_post("/api/test-ai-connection", api_test_ai_connection)
    app.router.add_post("/api/test-admin-functions", api_test_admin_functions)
    app.router.add_post("/api/test-all-functions", api_test_all_functions)
    # Dashboard routes
    app.router.add_get("/dashboard", dashboard_index)
    app.router.add_get("/hoi4", hoi4_page)
    app.router.add_get("/login", dashboard_login)
    app.router.add_get("/callback", dashboard_callback)
    app.router.add_post("/logout", dashboard_logout)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/guilds", api_guilds)
    app.router.add_get("/api/default-guild", api_default_guild)
    app.router.add_get("/api/guilds/{gid}/polls", api_polls)
    app.router.add_get("/api/guilds/{gid}/polls/{pid}", api_poll_detail)
    app.router.add_post("/api/guilds/{gid}/polls", api_create_poll)
    app.router.add_post("/api/guilds/{gid}/polls/{pid}/start", api_start_poll)
    app.router.add_post("/api/guilds/{gid}/polls/{pid}/end", api_end_poll)
    app.router.add_delete("/api/guilds/{gid}/polls/{pid}", api_delete_poll)
    app.router.add_post("/api/guilds/{gid}/polls/{pid}/options", api_add_option)
    app.router.add_put("/api/guilds/{gid}/polls/{pid}/roles", api_set_roles)
    app.router.add_get("/oauth/drive/callback", oauth_drive_callback)
    # Debug endpoint (no auth) — returns count + first entry keys
    app.router.add_get("/api/debug/nations", api_debug_nations)
    # Member nations API
    app.router.add_get("/api/guilds/{gid}/nations", api_list_nations)
    app.router.add_post("/api/guilds/{gid}/nations", api_create_nation)
    app.router.add_put("/api/guilds/{gid}/nations/{nid}", api_update_nation)
    app.router.add_delete("/api/guilds/{gid}/nations/{nid}", api_delete_nation)
    app.router.add_post("/api/guilds/{gid}/global-scan/start", api_global_scan_start)
    app.router.add_get("/api/guilds/{gid}/global-scan/status", api_global_scan_status)
    app.router.add_get("/api/guilds/{gid}/global-scan/result", api_global_scan_result)
    app.router.add_post("/api/guilds/{gid}/global-scan/batch", api_global_scan_batch)
    app.router.add_post("/api/guilds/{gid}/global-scan/init", api_global_scan_init)
    app.router.add_post("/api/guilds/{gid}/global-scan/finish", api_global_scan_finish)
    app.router.add_get("/api/siege-settings", api_get_siege_settings)
    app.router.add_put("/api/siege-settings", api_set_siege_settings)
    app.router.add_get("/api/server-registry", api_get_server_registry)
    app.router.add_put("/api/server-registry", api_set_server_registry)
    app.router.add_get("/api/sub-bot-commands", api_get_sub_bot_commands)
    app.router.add_put("/api/sub-bot-commands", api_set_sub_bot_commands)
    app.router.add_get("/api/ww1-settings", api_get_ww1_settings)
    app.router.add_put("/api/ww1-settings", api_set_ww1_settings)
    # Galgame API routes (registered by module 190)
    try:
        for _path, _method, _handler in _galgame_api_routes:
            if _method == "GET":
                app.router.add_get(_path, _handler)
            elif _method == "PUT":
                app.router.add_put(_path, _handler)
            elif _method == "POST":
                app.router.add_post(_path, _handler)
            elif _method == "DELETE":
                app.router.add_delete(_path, _handler)
        print("💬 Galgame API routes registered")
    except Exception as e:
        print(f"⚠️ Galgame API route registration failed: {e}")
    # HOI4 game API routes (registered by module 170, skip when HOI4_ENABLED=false)
    if os.getenv("HOI4_ENABLED", "true").lower() in ("false", "0", "no", "off"):
        print("🚫 HOI4 已停用，API 路由不註冊")
    else:
        try:
            for _path, _method, _handler in HOI4_API_ROUTES:
                if _method == "GET":
                    app.router.add_get(_path, _handler)
                elif _method == "PUT":
                    app.router.add_put(_path, _handler)
                elif _method == "POST":
                    app.router.add_post(_path, _handler)
            print("🎮 HOI4 API routes registered")
        except Exception as e:
            print(f"⚠️ HOI4 API route registration failed: {e}")
    print(f"📊 Dashboard routes registered")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Keep-alive HTTP server started on port {port}")


# ── Dashboard API: 會員國管理 ──

async def api_debug_nations(request):
    """Debug endpoint — no auth required. Returns basic diagnostics."""
    try:
        mn = _member_nations
        entry_count = len(mn.get("entries", [])) if isinstance(mn, dict) else f"NOT_DICT:{type(mn)}"
        sample = []
        if isinstance(mn, dict) and mn.get("entries"):
            for e in mn["entries"][:3]:
                sample.append({
                    "id": e.get("id", "MISSING"),
                    "guild_id": e.get("guild_id", "MISSING"),
                    "name_zh": e.get("name_zh", "MISSING"),
                    "keys": list(e.keys()),
                })
        return web.json_response({
            "type": str(type(mn)),
            "is_dict": isinstance(mn, dict),
            "has_entries": isinstance(mn, dict) and "entries" in mn,
            "entry_count": entry_count,
            "sample": sample,
        })
    except Exception as ex:
        import traceback
        return web.json_response({"error": str(ex), "trace": traceback.format_exc()[:500]}, status=500)


async def api_list_nations(request):
    try:
        user = await _get_session_user(request)
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401)
        gid_raw = request.match_info["gid"]
        if not gid_raw.isdigit():
            return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
        gid = int(gid_raw)
        guilds = await _fetch_guilds(user["access_token"])
        ge = next((g for g in guilds if int(g["id"]) == gid), None)
        if not _is_nation_admin(user.get("user_id", ""), ge):
            return web.json_response({"error": "forbidden：您沒有管理會員國的權限（需 Discord 管理員、白名單或機器人擁有者）"}, status=403)
        entries = [e for e in _member_nations["entries"] if int(e.get("guild_id", 0)) == gid]
        # Strip internal fields, return safe dict. Use .get() everywhere —
        # some entries may be partial/legacy (e.g. created before the
        # category system existed, or from an interrupted write) and must
        # never crash the whole listing.
        out = []
        for e in entries:
            out.append({
                "id": e.get("id", ""),
                "name_zh": e.get("name_zh", ""),
                "name_en": e.get("name_en", ""),
                "iso_code": e.get("iso_code", ""),
                "representatives": e.get("representatives", []) or [],
                "representative_names": e.get("representative_names", []) or [],
                "registered_by_name": e.get("registered_by_name", ""),
                "registered_date": e.get("registered_date", ""),
                "category": e.get("category") or e.get("status") or "member",
                "notes": e.get("notes", ""),
            })
        return web.json_response(out)
    except Exception as ex:
        import traceback
        print(f"❌ api_list_nations 例外：{ex}")
        traceback.print_exc()
        return web.json_response({"error": f"伺服器錯誤：{ex}"}, status=500)


async def api_create_nation(request):
  try:
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not _is_nation_admin(user.get("user_id", ""), ge):
        return web.json_response({"error": "forbidden：您沒有註冊會員國的權限（需 Discord 管理員、白名單或機器人擁有者）"}, status=403)
    data = await request.json()

    name_zh = (data.get("name_zh") or "").strip()
    name_en = (data.get("name_en") or "").strip()
    iso_code = (data.get("iso_code") or "").strip().upper()
    reps = data.get("representatives", [])
    notes = (data.get("notes") or "").strip()

    if not name_zh or not name_en or not iso_code:
        return web.json_response({"error": "國名（中英）和 ISO 代碼皆為必填"}, status=400)
    if len(iso_code) < 2 or len(iso_code) > 3:
        return web.json_response({"error": "ISO 代碼應為 2-3 碼"}, status=400)

    cat = data.get("category", "member").strip().lower()
    if cat not in ("member", "council", "observer", "removed"):
        cat = "member"

    # Check duplicate (excluding 已除籍) — use int() for type safety
    existing = [
        e for e in _member_nations["entries"]
        if int(e.get("guild_id", 0)) == gid
        and e.get("iso_code", "").upper() == iso_code
        and e.get("category") != "removed"
    ]
    if existing:
        return web.json_response({"error": f"ISO 代碼 {iso_code} 已被註冊"}, status=409)

    import uuid as _uuid
    # Parse representatives safely — frontend may send strings, ints, or nulls
    rep_ids = []
    for r in (reps or [])[:3]:
        try:
            if r is not None and str(r).strip():
                rep_ids.append(int(str(r).strip()))
        except (ValueError, TypeError):
            pass

    entry = {
        "id": str(_uuid.uuid4()),
        "guild_id": gid,
        "name_zh": name_zh,
        "name_en": name_en,
        "iso_code": iso_code,
        "category": cat,
        "representatives": rep_ids,
        "representative_names": [],
        "registered_by": user.get("user_id", 0),
        "registered_by_name": user.get("username", ""),
        "registered_date": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M"),
        "notes": notes,
    }
    _member_nations["entries"].append(entry)
    save_member_nations()
    return web.json_response({"ok": True, "id": entry["id"]})
  except Exception as ex:
    import traceback
    print(f"❌ api_create_nation 例外：{ex}")
    traceback.print_exc()
    return web.json_response({"error": f"伺服器錯誤：{ex}"}, status=500)


async def api_update_nation(request):
  try:
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not _is_nation_admin(user.get("user_id", ""), ge):
        return web.json_response({"error": "forbidden：您沒有編輯會員國的權限（需 Discord 管理員、白名單或機器人擁有者）"}, status=403)
    nid = request.match_info["nid"]
    data = await request.json()

    entry = next(
        (e for e in _member_nations["entries"]
         if e.get("guild_id") == gid and e["id"] == nid),
        None,
    )
    if not entry:
        return web.json_response({"error": "找不到該會員國"}, status=404)

    if "name_zh" in data:
        entry["name_zh"] = data["name_zh"].strip()
    if "name_en" in data:
        entry["name_en"] = data["name_en"].strip()
    if "iso_code" in data:
        entry["iso_code"] = data["iso_code"].strip().upper()
    if "representatives" in data:
        entry["representatives"] = [int(r) for r in data["representatives"][:3]]
    if "category" in data:
        cat = data["category"].strip().lower()
        if cat in ("member", "council", "observer", "removed"):
            entry["category"] = cat
    if "notes" in data:
        entry["notes"] = data["notes"].strip()

    save_member_nations()
    return web.json_response({"ok": True})
  except Exception as ex:
    import traceback
    print(f"❌ api_update_nation 例外：{ex}")
    traceback.print_exc()
    return web.json_response({"error": f"伺服器錯誤：{ex}"}, status=500)


async def api_delete_nation(request):
  try:
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
    guilds = await _fetch_guilds(user["access_token"])
    ge = next((g for g in guilds if int(g["id"]) == gid), None)
    if not _is_nation_admin(user.get("user_id", ""), ge):
        return web.json_response({"error": "forbidden：您沒有刪除會員國的權限（需 Discord 管理員、白名單或機器人擁有者）"}, status=403)
    nid = request.match_info["nid"]

    before = len(_member_nations["entries"])
    _member_nations["entries"] = [
        e for e in _member_nations["entries"]
        if not (e.get("guild_id") == gid and e["id"] == nid)
    ]
    if len(_member_nations["entries"]) == before:
        return web.json_response({"error": "找不到該會員國"}, status=404)

    save_member_nations()
    return web.json_response({"ok": True})
  except Exception as ex:
    import traceback
    print(f"❌ api_delete_nation 例外：{ex}")
    traceback.print_exc()
    return web.json_response({"error": f"伺服器錯誤：{ex}"}, status=500)


# ── Global Micropedia Scan API ──

async def api_global_scan_start(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    if _global_scan_state.get("status") == "running":
        return web.json_response({"error": "Scan is already running"}, status=409)
    global _global_scan_task
    _global_scan_task = asyncio.ensure_future(_run_global_micropedia_scan())
    return web.json_response({"status": "started"})


async def api_global_scan_status(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_global_scan_state)


async def api_global_scan_result(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_global_scan_result)


def _check_scan_api_key(request):
    """Validate the X-Scan-Key header for local script access."""
    key = os.getenv("SCAN_API_KEY", "")
    if not key:
        return True  # No key set = open access (user can set one later)
    provided = request.headers.get("X-Scan-Key", "")
    return provided == key


async def api_global_scan_init(request):
    """Initialize a new scan session from the local runner script."""
    if not _check_scan_api_key(request):
        return web.json_response({"error": "invalid api key"}, status=403)
    global _global_scan_state
    try:
        data = await request.json()
        total = int(data.get("total", 0))
        _global_scan_state = {
            "status": "running",
            "progress": 0,
            "total": total,
            "current_batch": "等待本地腳本傳送...",
            "started_at": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S"),
            "completed_at": "",
            "error": "",
            "batch_count": 0,
            "mode": "local-runner",
        }
        _global_scan_result.setdefault("total_articles", total)
        _save_global_scan_result()
        return web.json_response({"status": "initialized", "total": total})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_global_scan_batch(request):
    """Receive a batch of article texts from the local runner, run AI extraction."""
    if not _check_scan_api_key(request):
        return web.json_response({"error": "invalid api key"}, status=403)
    global _global_scan_state
    try:
        data = await request.json()
        articles = data.get("articles", [])
        batch_idx = int(data.get("batch_idx", 0))
        batch_count = int(data.get("batch_count", 0))

        if not articles:
            return web.json_response({"status": "empty", "extracted": None})

        batch_text = "\n\n".join(
            f"【{a.get('title', '?')}】\n{a.get('content', '')}" for a in articles
        )

        titles_preview = ", ".join(a.get("title", "?") for a in articles[:3])
        _global_scan_state["current_batch"] = f"批次 {batch_idx + 1}/{batch_count}: {titles_preview}..."

        system_prompt = (
            '你是一位嚴謹的歷史學家與微國家學學者，正在為一份完整的微國家百科關係圖譜做資料收錄工作。\n\n'
            '【鐵律 — 絕對不可違反】\n'
            '1. 條目中提到的每一個人物、國家/組織、事件都必須收錄，不論看起來多不重要、只被提及一次、'
            '或只是配角。絕對不准因為「精簡」「統整」「篇幅」等理由省略任何一個人或國家或事件。\n'
            '   【特別注意】有些人或國家在微國家百科中沒有自己的獨立條目，他們的資訊散落在'
            '其他人的條目裡（例如張三的資料出現在李四的條目描述中）。即使如此，你仍然必須'
            '為這些「只在別人條目中被提到」的人/國家/組織建立獨立的 countries 或 key_figures 條目，'
            '把你在本批次條目中能找到的相關資訊都填進去。不准因為「他沒有自己的條目」就只在'
            '別人的 description 裡順帶提及而不建獨立條目。\n'
            '2. 每個事件的描述要完整還原條目中的細節與背景，不要壓縮成一句話簡述。\n'
            '3. 對於人物，除了基本身份資訊外，務必仔細挖掘條目中提到的：'
            '恩怨、對立、私人衝突、爭議（disputes）——是跟誰、為什麼；'
            '以及軼事、趣聞、非正式的小故事（anecdotes）——這些往內容細節找，'
            '通常藏在條目的敘述細節裡，不是每個人都會直接寫「恩怨」兩個字。\n'
            '4. 對於事件，如果條目文字中有明確指出「這個事件是被什麼事件引發/導致的」（caused_by），'
            '或「這個事件後來導致/促成了什麼事件」（leads_to），請把那些後續/前置事件的名稱列出來，'
            '讓事件之間可以串成因果鏈。如果條目沒有明確講到前後因果的其他事件名稱，這兩個欄位留空陣列即可，'
            '不要瞎猜或編造不存在的事件名稱。\n\n'
            '請以繁體中文輸出嚴格 JSON 格式（不可使用 markdown 程式碼區塊），包含以下 4 個 key：\n'
            '1. countries: [{"name": "...", "aliases": ["..."], "type": "micronation/organization/individual", '
            '"description": "...", "status": "active/dissolved/unknown"}]\n'
            '2. relationships: [{"from": "...", "to": "...", "type": "alliance/conflict/treaty/trade/diplomatic/cultural/personal", '
            '"description": "...", "context": "...", "status": "active/historical/ended"}]\n'
            '3. key_figures: [{"name": "...", "affiliation": "...", "role": "...", "description": "...", '
            '"disputes": ["與某某因為某事產生衝突/對立...", "..."], '
            '"anecdotes": ["軼事描述...", "..."]}]\n'
            '   （disputes 與 anecdotes 若條目中完全沒提到，輸出空陣列 []，不要編造）\n'
            '4. major_events: [{"event": "...", "participants": ["..."], "date": "...", "description": "...", '
            '"consequences": "...", "leads_to": ["後續事件名稱（僅限條目明確提到的）", "..."], '
            '"caused_by": ["前置事件名稱（僅限條目明確提到的）", "..."]}]\n'
            '僅輸出 JSON 物件，請勿附加任何額外文字。'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": '條目內容:\n' + batch_text}
        ]

        extracted = None
        try:
            resp = await call_chat_api(messages, chat_ai_settings, max_tokens=8000, category="admin")
            ai_text = resp.get("content") or ""
            ai_text_clean = ai_text.strip()
            if ai_text_clean.startswith("```"):
                ai_text_clean = re.sub(r"^```(?:json)?\s*", "", ai_text_clean, flags=re.IGNORECASE)
                ai_text_clean = re.sub(r"\s*```$", "", ai_text_clean)
                ai_text_clean = ai_text_clean.strip()

            try:
                extracted = json_module.loads(ai_text_clean)
            except Exception:
                m = re.search(r"\{.*\}", ai_text, re.DOTALL)
                if m:
                    try:
                        extracted = json_module.loads(m.group(0))
                    except Exception:
                        extracted = None
                if extracted is None:
                    # Full parse failed (likely truncated by max_tokens). Salvage
                    # whichever of the 4 fields DID finish generating cleanly,
                    # instead of throwing the entire batch's extraction away.
                    salvaged = _salvage_scan_extraction(ai_text_clean or ai_text)
                    if salvaged:
                        extracted = salvaged
                        print(f"♻️ 批次 {batch_idx + 1} JSON 被截斷，搶救回 "
                              f"{sum(len(v) for v in salvaged.values())} 項資料（而非整批捨棄）")

            if isinstance(extracted, dict):
                _merge_scan_batch(extracted)
        except Exception as aie:
            print(f"⚠️ 本地掃描 AI 解析失敗 (批次 {batch_idx + 1}): {aie}")

        _global_scan_state["progress"] += len(articles)
        _global_scan_state["batch_count"] = batch_idx + 1
        _global_scan_result["last_updated"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")

        # NOTE: intra-scan consolidation removed — with high concurrency (many
        # batches in flight at once) an every-N-batches full-graph consolidation
        # would (a) resend an ever-growing accumulated dataset as context on
        # every trigger, getting slower as the scan progresses, and (b) stall
        # whichever concurrent request happens to hit the trigger index for
        # however long that consolidation takes. A single consolidation pass
        # at /finish (after all batches land) achieves the same end result
        # without this per-batch tax. See api_global_scan_finish.
        _save_global_scan_result()
        return web.json_response({
            "status": "ok",
            "extracted_count": sum(len(extracted.get(k, [])) for k in ["countries", "relationships", "key_figures", "major_events"]) if isinstance(extracted, dict) else 0,
            "progress": _global_scan_state["progress"],
            "total": _global_scan_state["total"],
        })

    except Exception as e:
        import traceback
        print(f"❌ 本地掃描批次處理失敗: {e}\n{traceback.format_exc()}")
        return web.json_response({"error": str(e)}, status=500)


async def api_global_scan_finish(request):
    """Run the additive causal-chain linking pass and mark scan as completed.
    NOTE: this never rewrites or drops any already-recorded entry — see
    _link_event_causal_chains for why the old destructive full-graph
    regeneration was replaced."""
    if not _check_scan_api_key(request):
        return web.json_response({"error": "invalid api key"}, status=403)
    global _global_scan_state
    try:
        # 1. Rescue orphan entities (those referenced but without standalone entries)
        await _rescue_orphan_entities()
        # 2. Build causal chains between events (additive only)
        await _link_event_causal_chains()
        _global_scan_state["status"] = "completed"
        _global_scan_state["completed_at"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
        _global_scan_result["last_updated"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
        _save_global_scan_result()
        return web.json_response({
            "status": "completed",
            "countries": len(_global_scan_result.get("countries", [])),
            "relationships": len(_global_scan_result.get("relationships", [])),
            "key_figures": len(_global_scan_result.get("key_figures", [])),
            "major_events": len(_global_scan_result.get("major_events", [])),
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ──────────────────────────────────────────────
# Dashboard: OAuth2 & Session
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# Dashboard: OAuth2 & Session (signed cookies - survives restarts/redeploys)
# ──────────────────────────────────────────────

# COOKIE_SECRET 持久化說明：
# 若沒設定 COOKIE_SECRET 環境變數，舊寫法是每次進程啟動都用 token_urlsafe(32)
# 隨機生成一組新密鑰——這代表容器只要重啟一次（Render 免費方案閒置15分鐘會
# 休眠、部署會重啟、OOM也會重啟），所有已簽發但還沒驗證的 session cookie
# 會瞬間全部失效，使用者會遇到「Discord授權完成後又被踢回登入頁」的詭異
# 循環（因為簽發cookie跟驗證cookie剛好夾在一次重啟前後，用了不同密鑰）。
# 修法：改成 lazy + 持久化——優先用環境變數；沒有的話嘗試讀取本機
# data/cookie_secret.json（這個檔案會被現有的 Drive 週期同步機制自動抓到，
# 隨機生成的資料夾*.json不需要額外註冊，sync_to_drive()/load_from_drive()都是
# 動態掃描資料夾裡所有.json檔）；都沒有才生成新的並立即寫入本機+觸發即時
# Drive上傳，讓下次重啟時 load_from_drive() 能搶在真正的OAuth流程完成前
# 把同一把密鑰復原回來（Discord授權來回至少要好幾秒，通常比Drive下載快得多）。
_cookie_secret_cache = None

def _get_cookie_secret() -> str:
    global _cookie_secret_cache
    if _cookie_secret_cache:
        return _cookie_secret_cache
    env_val = os.getenv("COOKIE_SECRET", "")
    if env_val:
        _cookie_secret_cache = env_val
        return _cookie_secret_cache
    secret_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cookie_secret.json")
    try:
        if os.path.exists(secret_path):
            with open(secret_path, "r", encoding="utf-8") as f:
                saved = json_module.load(f)
            if saved.get("secret"):
                _cookie_secret_cache = saved["secret"]
                return _cookie_secret_cache
    except Exception as e:
        print(f"⚠️ 讀取 cookie_secret.json 失敗（將生成新密鑰）: {e}")
    new_secret = py_secrets.token_urlsafe(32)
    _cookie_secret_cache = new_secret
    try:
        os.makedirs(os.path.dirname(secret_path), exist_ok=True)
        with open(secret_path, "w", encoding="utf-8") as f:
            json_module.dump({"secret": new_secret}, f)
        print("🔑 已產生新的 COOKIE_SECRET 並存檔（將透過 Drive 同步持久化，避免下次重啟登入全失效）")
        try:
            asyncio.ensure_future(_immediate_drive_upload("cookie_secret.json"))
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ 儲存 cookie_secret.json 失敗: {e}")
    return new_secret


OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")


def _create_signed_cookie(data: dict) -> str:
    """Create an HMAC-signed cookie containing user data."""
    payload = __import__("base64").b64encode(json_module.dumps(data).encode()).decode()
    sig = hmac.new(_get_cookie_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_signed_cookie(cookie: str) -> dict:
    """Verify and decode a signed cookie. Returns None if invalid."""
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = hmac.new(_get_cookie_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
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


def _read_hoi4_html():
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hoi4.html")
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>hoi4.html not found</h1>"


async def _get_session_user(request):
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    return _verify_signed_cookie(cookie)


def _is_guild_admin(guild_entry):
    perms = int(guild_entry.get("permissions", 0))
    return bool(perms & 0x8) or bool(perms & 0x20)


def _is_nation_admin(user_id_str: str, guild_entry: dict = None) -> bool:
    """Check if a user is allowed to manage nations (register/edit/delete).
    Allowed if: (a) Discord guild admin/manage_server, OR (b) on the
    nation_admin_whitelist, OR (c) is the bot owner."""
    # Bot owner always has access
    if str(user_id_str) == str(BOT_OWNER_ID):
        return True
    # Whitelist check
    whitelist = application_settings.get("nation_admin_whitelist", [])
    if str(user_id_str) in [str(w) for w in whitelist]:
        return True
    # Discord guild admin check
    if guild_entry and _is_guild_admin(guild_entry):
        return True
    return False


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


async def api_get_siege_settings(request):
    """取得攻城戰設定。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({
        "enabled": _siege_settings.get("enabled", True),
        "channel_id": _siege_settings.get("channel_id"),
        "reward_pool": _siege_settings.get("reward_pool", 5000),
        "attack_cooldown": _siege_settings.get("attack_cooldown", 1200),
        "min_hp": _siege_settings.get("min_hp", 80000),
        "max_hp": _siege_settings.get("max_hp", 120000),
        "min_defense": _siege_settings.get("min_defense", 10),
        "max_defense": _siege_settings.get("max_defense", 35),
        "min_damage": _siege_settings.get("min_damage", 100),
        "max_damage": _siege_settings.get("max_damage", 2000),
        "active": _siege_state.get("active", False),
        "nation_name": _siege_state.get("nation_name", ""),
        "current_hp": _siege_state.get("current_hp", 0),
        "max_hp_current": _siege_state.get("max_hp", 0),
        "defense_pct": _siege_state.get("defense_pct", 0),
        "total_damage_dealt": _siege_state.get("total_damage_dealt", 0),
        "player_count": len(_siege_state.get("player_damage", {})),
    })


async def api_set_siege_settings(request):
    """更新攻城戰設定。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    if "enabled" in body:
        _siege_settings["enabled"] = body["enabled"]
    if "channel_id" in body:
        _siege_settings["channel_id"] = body["channel_id"] if body["channel_id"] else None
    if "reward_pool" in body:
        _siege_settings["reward_pool"] = int(body["reward_pool"])
    if "attack_cooldown" in body:
        _siege_settings["attack_cooldown"] = int(body["attack_cooldown"])
    if "min_hp" in body:
        _siege_settings["min_hp"] = int(body["min_hp"])
    if "max_hp" in body:
        _siege_settings["max_hp"] = int(body["max_hp"])
    if "min_defense" in body:
        _siege_settings["min_defense"] = int(body["min_defense"])
    if "max_defense" in body:
        _siege_settings["max_defense"] = int(body["max_defense"])
    if "min_damage" in body:
        _siege_settings["min_damage"] = int(body["min_damage"])
    if "max_damage" in body:
        _siege_settings["max_damage"] = int(body["max_damage"])
    save_siege_data()
    return web.json_response({"ok": True})


async def api_get_server_registry(request):
    """取得所有已註冊伺服器清單（含分級 & WW1 設定）。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")
    if str(uid_str) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — bot owner only"}, status=403)
    servers = []
    for gid, info in _server_registry.items():
        servers.append({
            "guild_id": gid,
            "name": info.get("name", "Unknown"),
            "tier": info.get("tier", "guest"),
            "member_count": info.get("member_count", 0),
            "ww1_channel_id": info.get("ww1_channel_id"),
            "ww1_panel_message_id": info.get("ww1_panel_message_id"),
            "joined_at": info.get("joined_at", ""),
        })
    # Sort: owner first, then by member_count desc
    servers.sort(key=lambda s: (s["tier"] != "owner", -(s["member_count"] or 0)))
    summary = get_registry_summary()
    return web.json_response({"servers": servers, "summary": summary})


async def api_set_server_registry(request):
    """更新指定伺服器的分級 / WW1 頻道。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")
    if str(uid_str) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — bot owner only"}, status=403)
    body = await request.json()
    gid = str(body.get("guild_id", ""))
    if not gid or gid not in _server_registry:
        return web.json_response({"error": "伺服器未註冊"}, status=400)
    # Tier update
    if "tier" in body:
        new_tier = body["tier"]
        if new_tier in ("owner", "guest"):
            _server_registry[gid]["tier"] = new_tier
    # WW1 channel update
    if "ww1_channel_id" in body:
        ch_id = body["ww1_channel_id"]
        if ch_id and str(ch_id).strip():
            _server_registry[gid]["ww1_channel_id"] = int(str(ch_id).strip())
        else:
            _server_registry[gid]["ww1_channel_id"] = None
            _server_registry[gid]["ww1_panel_message_id"] = None
    save_server_registry()
    print(f"📋 Dashboard 更新伺服器 {gid}: tier={_server_registry[gid].get('tier')}, ww1_ch={_server_registry[gid].get('ww1_channel_id')}")
    return web.json_response({"ok": True})


async def api_get_sub_bot_commands(request):
    """取得子機器人（娛樂機器人）所有候選指令的目錄與開關狀態，供 Dashboard 逐指令開關 UI 使用。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")
    if str(uid_str) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — bot owner only"}, status=403)
    try:
        catalog = _get_sub_bot_command_catalog()
    except Exception as e:
        print(f"⚠️ 取得子機器人指令目錄失敗：{e}")
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({
        "commands": catalog,
        "sub_bot_online": sub_bot is not None and sub_bot.user is not None,
    })


async def api_set_sub_bot_commands(request):
    """更新子機器人指令開關設定並即時同步到 Discord（若子機器人已連線）。
    body: {"commands": {"quiz.toggle": false, "draw": true, ...}}"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")
    if str(uid_str) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — bot owner only"}, status=403)
    try:
        body = await request.json()
        updates = body.get("commands", {})
        if not isinstance(updates, dict):
            return web.json_response({"error": "commands 必須是物件"}, status=400)
        for key, enabled in updates.items():
            _sub_bot_cmd_config[str(key)] = bool(enabled)
        _save_sub_bot_cmd_config()
        print(f"📋 Dashboard 更新子機器人指令開關：{updates}")
        if sub_bot is not None:
            try:
                await _sync_sub_bot_tree()
            except Exception as e:
                print(f"⚠️ 子機器人指令即時同步失敗（設定已儲存，重啟後仍會生效）：{e}")
        return web.json_response({"ok": True, "catalog": _get_sub_bot_command_catalog()})
    except Exception as e:
        print(f"⚠️ 更新子機器人指令開關失敗：{e}")
        return web.json_response({"error": str(e)}, status=500)


async def api_get_ww1_settings(request):
    """取得 WW1 賽博一戰設定。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")
    if str(uid_str) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — bot owner only"}, status=403)
    s = _cyber_war_settings
    st = _cyber_war_state
    deposits = st.get("deposits", {})
    total_pool = sum(d.get("amount", 0) for d in deposits.values())
    return web.json_response({
        "channel_id": s.get("channel_id"),
        "turn_interval_hours": s.get("turn_interval_hours", 1),
        "deposit": s.get("deposit", 100),
        # Game state (read-only info)
        "active": st.get("active", False),
        "game_id": st.get("game_id", 0),
        "turn": st.get("turn", 0),
        "battlefield": st.get("battlefield", ""),
        "winner": st.get("winner"),
        "deposits_locked": st.get("deposits_locked", False),
        "total_pool": total_pool,
        "prize_multiplier": st.get("prize_multiplier", 0),
        "bettor_count": sum(1 for d in deposits.values() if d.get("amount", 0) > 0),
        "fac_a_name": st.get("factions", {}).get("A", {}).get("name", ""),
        "fac_a_flag": st.get("factions", {}).get("A", {}).get("flag", ""),
        "fac_a_progress": st.get("factions", {}).get("A", {}).get("progress", 0),
        "fac_b_name": st.get("factions", {}).get("B", {}).get("name", ""),
        "fac_b_flag": st.get("factions", {}).get("B", {}).get("flag", ""),
        "fac_b_progress": st.get("factions", {}).get("B", {}).get("progress", 0),
    })


async def api_set_ww1_settings(request):
    """更新 WW1 賽博一戰設定。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")
    if str(uid_str) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — bot owner only"}, status=403)
    body = await request.json()
    if "channel_id" in body:
        ch_id = body["channel_id"]
        _cyber_war_settings["channel_id"] = int(ch_id) if ch_id and str(ch_id).strip() else None
    if "turn_interval_hours" in body:
        hours = int(body["turn_interval_hours"])
        if hours >= 1:
            _cyber_war_settings["turn_interval_hours"] = hours
    if "deposit" in body:
        dep = int(body["deposit"])
        if dep >= 0:
            _cyber_war_settings["deposit"] = dep
    save_cyber_war()
    print(f"📋 Dashboard 更新 WW1 設定: channel={_cyber_war_settings.get('channel_id')}, interval={_cyber_war_settings.get('turn_interval_hours')}h, deposit={_cyber_war_settings.get('deposit')}")
    return web.json_response({"ok": True})


async def dashboard_index(request):
    return web.Response(text=_read_dashboard_html(), content_type="text/html")


async def hoi4_page(request):
    if os.getenv("HOI4_ENABLED", "true").lower() in ("false", "0", "no", "off"):
        return web.Response(text="<h1>鋼鐵風暴已關閉</h1><p>管理員已停用此功能。</p>", content_type="text/html")
    return web.Response(text=_read_hoi4_html(), content_type="text/html")


_ALLOWED_LOGIN_REDIRECTS = {"/dashboard", "/hoi4"}

async def dashboard_login(request):
    if not OAUTH_CLIENT_ID or not OAUTH_REDIRECT_URI:
        return web.Response(text="OAuth not configured. Set OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI", status=500)
    next_path = request.query.get("next", "/dashboard")
    if next_path not in _ALLOWED_LOGIN_REDIRECTS:
        next_path = "/dashboard"
    url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={OAUTH_CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(OAUTH_REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20guilds"
        f"&state={urllib.parse.quote(next_path)}"
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
    next_path = request.query.get("state", "/dashboard")
    if next_path not in _ALLOWED_LOGIN_REDIRECTS:
        next_path = "/dashboard"
    r = web.HTTPFound(next_path)
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
    sig = hmac.new(_get_cookie_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_drive_oauth_state(state: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        admin_id, ts, sig = decoded.rsplit(":", 2)
        payload = f"{admin_id}:{ts}"
        expected = hmac.new(_get_cookie_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
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
    return web.json_response({"user_id": user["user_id"], "username": user["username"], "avatar_url": av, "admin_guild_count": len(ag)})


async def api_guilds(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")
    guilds = await _fetch_guilds(user["access_token"])
    out = []
    seen_ids = set()
    for g in guilds:
        if _is_guild_admin(g):
            ic = f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g.get("icon") else None
            out.append({"id": g["id"], "name": g["name"], "icon_url": ic, "nation_only": False})
            seen_ids.add(str(g["id"]))
    # Also surface guilds for nation-whitelisted users / bot owner, even if
    # Discord's OAuth guild list doesn't carry admin/manage_server permission
    # bits for them (or the guild lookup fails for any other reason) — they
    # still need to be able to click into the server to reach 會員國 management.
    is_owner_or_whitelisted = _is_nation_admin(uid_str, None)
    if is_owner_or_whitelisted:
        for g in guilds:
            if str(g["id"]) not in seen_ids:
                ic = f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g.get("icon") else None
                out.append({"id": g["id"], "name": g["name"], "icon_url": ic, "nation_only": True})
                seen_ids.add(str(g["id"]))
    return web.json_response(out)


async def api_default_guild(request):
    """This bot only serves ICEA — return that single guild directly instead
    of making the user pick from a list of every Discord server they happen
    to manage. Still verifies the logged-in user actually has admin rights
    (or is nation-whitelisted/the bot owner) on ICEA before handing back
    dashboard access.

    IMPORTANT: membership/permissions are checked via the bot's OWN live
    guild connection (bot.get_guild + fetch_member), NOT via the user's
    Discord OAuth access token hitting /users/@me/guilds. That endpoint is
    aggressively rate-limited by Discord and _fetch_guilds silently returns
    [] on any failure (429, network hiccup, etc.) — which used to surface as
    a false "你不是 ICEA 伺服器的成員" error even for actual members. The
    bot token's own guild/member cache has no such rate-limit trap and is
    always accurate for the one guild this bot lives in."""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    uid_str = user.get("user_id", "")

    guild = bot.get_guild(int(ICEA_GUILD_ID))
    if not guild:
        # Bot itself isn't connected/ready yet — genuine transient state,
        # not a permissions issue. Tell the user to just retry.
        return web.json_response({"error": "機器人尚未連上 ICEA 伺服器（可能剛重啟），請稍後重試"}, status=503)

    member = guild.get_member(int(uid_str))
    if member is None:
        try:
            member = await guild.fetch_member(int(uid_str))
        except discord.NotFound:
            member = None
        except Exception as e:
            print(f"⚠️ api_default_guild：fetch_member 失敗：{e}")
            member = None

    if member is None:
        return web.json_response({"error": "你不是 ICEA 伺服器的成員，無法使用此後台"}, status=403)

    is_guild_admin = member.guild_permissions.administrator or member.guild_permissions.manage_guild
    is_owner_or_whitelisted = _is_nation_admin(uid_str, None)
    if not is_guild_admin and not is_owner_or_whitelisted:
        return web.json_response({"error": "forbidden：你在 ICEA 伺服器沒有管理權限"}, status=403)

    ic = str(guild.icon.url) if guild.icon else None
    return web.json_response({
        "id": str(guild.id),
        "name": guild.name,
        "icon_url": ic,
        "nation_only": (not is_guild_admin) and is_owner_or_whitelisted,
    })


async def api_polls(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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
    gid_raw = request.match_info["gid"]
    if not gid_raw.isdigit():
        return web.json_response({"error": "無效的伺服器 ID（請重新整理頁面並重新選擇伺服器）"}, status=400)
    gid = int(gid_raw)
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


def _load_json_file(filepath: str, default=None):
    """Generic JSON file loader with UTF-8 encoding and error fallback."""
    try:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json_module.load(f)
    except (json_module.JSONDecodeError, Exception) as e:
        print(f"⚠️ 載入 {os.path.basename(filepath)} 失敗（使用預設值）: {e}")
    return default if default is not None else {}


def _salvage_json_array_field(raw_text: str, key: str) -> list:
    """Best-effort recovery of one top-level JSON array field from a
    possibly truncated/malformed AI response. If the model's output got
    cut off mid-generation (hit max_tokens), a plain json.loads() on the
    whole text fails and — without this — we'd throw away EVERY field,
    including ones that finished fine before the cutoff. This walks the
    text looking for `"key": [ ... ]`, tracks bracket/string depth by hand
    (ignoring brackets inside string literals), and:
      - if the array closes cleanly, parses and returns it whole;
      - if it's truncated mid-array, trims back to the last complete
        top-level object in that array and closes it there, so we keep
        every item that DID finish generating instead of losing the lot.
    Returns [] if the key isn't found or nothing salvageable exists."""
    marker = f'"{key}"'
    m_idx = raw_text.find(marker)
    if m_idx == -1:
        return []
    start = raw_text.find("[", m_idx)
    if start == -1:
        return []

    depth = 0
    in_string = False
    escape = False
    last_complete_item_end = None  # index just after a top-level "},"
    i = start
    n = len(raw_text)
    while i < n:
        ch = raw_text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "[" or ch == "{":
                depth += 1
            elif ch == "]" or ch == "}":
                depth -= 1
                if ch == "}" and depth == 1:
                    last_complete_item_end = i + 1
                if depth == 0:
                    # Clean close — try the whole thing first.
                    candidate = raw_text[start:i + 1]
                    try:
                        parsed = json_module.loads(candidate)
                        return parsed if isinstance(parsed, list) else []
                    except Exception:
                        break
        i += 1

    # Truncated (or the clean parse above failed) — salvage up to the last
    # fully-formed object we saw, if any.
    if last_complete_item_end is not None:
        candidate = raw_text[start:last_complete_item_end].rstrip().rstrip(",") + "]"
        try:
            parsed = json_module.loads(candidate)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _salvage_scan_extraction(raw_text: str) -> dict:
    """Recover as much as possible from a batch-extraction AI response that
    failed a straight json.loads() — per-field, so a cutoff in one field
    (usually the last one, major_events) doesn't also sacrifice fields that
    finished generating cleanly before it."""
    result = {}
    for key in ("countries", "relationships", "key_figures", "major_events"):
        arr = _salvage_json_array_field(raw_text, key)
        if arr:
            result[key] = arr
    return result


def _save_json_file(filepath: str, data, indent=2) -> bool:
    """Generic atomic JSON file saver — writes to .tmp then os.replace.
    Prevents corruption if the process crashes mid-write."""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json_module.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp_path, filepath)  # atomic on POSIX
        return True
    except Exception as e:
        print(f"⚠️ 儲存 {os.path.basename(filepath)} 失敗: {e}")
        return False
BRIEFING_DATA_FILE = os.path.join(DATA_DIR, "briefing_settings.json")


def save_briefing_settings():
    try:
        os.makedirs(os.path.dirname(BRIEFING_DATA_FILE), exist_ok=True)
        _save_json_file(BRIEFING_DATA_FILE, briefing_settings, indent=None)
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
    cutoff = datetime.now(GMT8) - timedelta(hours=hours)
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
                    time_str = (msg.created_at + timedelta(hours=8)).strftime("%m/%d %H:%M")
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
                except Exception as e:
                    print("⚠️ 靜默例外:", e)

        # Final output
        full_text = f"{title}\n" + accumulated
        if len(full_text) <= 2000:
            await live_msg.edit(content=full_text)
        else:
            import io
            await live_msg.edit(content=f"{title}\n✅ 已生成（完整內容見下方附件）")
            file_content = f"# {title}\n# 生成時間：{datetime.now(GMT8).strftime('%Y-%m-%d %H:%M')}\n# 涵蓋範圍：過去 {hours} 小時\n\n---\n\n{accumulated}"
            file = discord.File(
                io.BytesIO(file_content.encode("utf-8")),
                filename=f"{'daily_briefing' if is_daily else 'weekly_bulletin'}_{datetime.now(GMT8).strftime('%Y%m%d_%H%M')}.md"
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
        now = datetime.now(GMT8)
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
        now = datetime.now(GMT8)
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
- 語氣自然輕鬆，像群組裡的一個朋友
- 不要使用 markdown 標題（## ###）
- 不要每次都長篇大論，有時候一句話就夠了
- 可以開玩笑，但不要冒犯別人
- 如果對話涉及微國家相關知識，你會收到微國家百科的查詢資料，請優先參考這些資料回答

─── 資料使用原則 ───
- 上方注入的百科/Discord/知識庫資料都是可信的事實來源。如果資料裡有答案，就直接、自信地回答，不要猶豫。
- 只有當你「完全沒有相關資料」時，才說「這個我不確定」或「目前沒有查到相關資料」。
- 不要因為覺得資料「可能不夠完整」就退縮——有資料就回答，沒資料才說不知道。
- 不要自行把兩個不同名稱/條目推論為同一個東西（例如「A 國家就是 B 國家的別稱」），
  除非資料裡明確這樣寫。但這不影響你回答其他有資料的問題。
- 不要編造沒有出現在資料中的細節來讓回答看起來更完整。

─── 「不確定」的使用邊界（重要）───
「這個我不確定」/「資料沒有寫到這點」只能用在一種情況：使用者問的是一件
微國家內部具體發生過的事實（人事、事件、數字、條文…），你查了上面注入的
資料卻真的完全找不到答案。除此之外，下面這些狀況都「不准」回「不確定」，
要用你自己的判斷正常回應：
1. 使用者問題明顯是玩笑、惡搞、荒謬假設（例如比較「牛大便」和「豬大便」
   誰比較貴這種根本不存在的東西）——這種問題本來就不會有「資料」，正確
   反應是順著玩笑吐槽、講幹話、或用常識回一句，而不是正經地說「資料沒
   有寫到這點」。把明顯的整人/搞笑問題當成資料查詢在跑，本身就是答錯。
2. 使用者問的是跟微國家完全無關的一般知識、時事、生活問題——直接用你
   自己的知識或上面注入的網路搜尋結果回答，不要因為百科查不到就卡住。
3. 使用者問的是意見、看法、感受、閒聊——這些本來就沒有「資料」可查，
   直接自然對話即可。
先判斷「這是不是一個真的可能有官方記錄的具體事實問題」，只有這種情況
才走「查資料→沒查到→誠實說不確定」的流程；其他情況一律當一般對話處理。
─── 回覆格式鐵律 ───
- 直接給出答案，不要在回覆中展示你的思考過程、推理步驟、或分析邏輯
- 不要寫「讓我想想」「我來分析一下」「首先」「好的，我來看看」等思考性開場白
- 不要使用 <think> 或 <thinking> 標籤，不要輸出任何形式的 reasoning
- 你的回覆必須是一個自然的對話回應，不是思考筆記
- 違反以上規則的回覆會被直接刪除思考部分，只保留最終答案"""

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
    "min_response_interval": 0,  # 全域最短回應間隔（秒），0=不限。防止機器人被防炸系統踢
    "vision_model": "",  # 視覺模型名稱（用於識圖，留空=停用識圖功能。使用同一個 API URL/Key，只是模型名不同）
    "vision_fallback_chain": "",  # 視覺模型降級鏈（逗號分隔，主視覺模型失敗時依序嘗試）
    "ai_hard_ceiling": 20,           # AI pipeline 硬上限（秒）
    "ai_soft_target": 16,            # AI 軟目標（秒）
    "turtle_soup_enabled": False,    # AI 海龜湯遊戲是否啟用
    "turtle_soup_channel_id": None,  # 海龜湯頻道 ID
    "turtle_soup_guild_channels": {}, # {guild_id_str: channel_id_str} 外伺服器子頻道
    "turtle_soup_difficulty": "medium",  # 預設難度：easy / medium / hard
    "vision_extra_budget": 20,       # 訊息含圖片時，額外加給文字 AI 的預算（秒）——
                                      # 圖片描述會塞進 system prompt，讓文字模型要處理的
                                      # 內容變大變慢，固定 20s 硬上限對純文字聊天夠用，
                                      # 但對含圖片的訊息常常不夠，導致「文字/視覺模型都已
                                      # 成功回應，卻還是被判定逾時」。
    "ai_max_tokens": 2000,           # AI 回覆最大 token 數
    "preprocess_timeout": 6,         # 預處理（百科/Discord/網路）各路逾時（秒）
    "tool_skip_threshold": 12,       # 時間預算低於此值時關閉工具（秒）
    "reasoning_effort": "low",      # reasoning 模型思考強度: "none"(關閉) / "low" / "medium" / "high" / "auto"
                                    # GLM-5.2, DeepSeek-R1 等 reasoning 模型適用。none=跳過思考直接回答
    "reasoning_admin_effort": "medium",  # 行政功能(提案/計票/入盟)的思考強度，品質需求較高
    "reasoning_chat_effort": "low",  # 聊天功能的思考強度，速度優先
    "reasoning_entertainment_effort": "low",  # 娛樂功能(海龜湯/狼人殺/占卜)的思考強度
    "reasoning_enabled_timeout": 90,  # 開啟 reasoning 時的 timeout（秒），預設 90 秒
    "reasoning_disabled_timeout": 25, # 關閉 reasoning 時的 timeout（秒），預設 25 秒
    "circuit_breaker_cooldown": 120, # 熔斷器冷卻時間（秒）
    "forum_index_interval": 900,    # 論壇索引更新間隔（秒）
    "channel_index_interval": 1800,  # 頻道索引更新間隔（秒）
    "drive_sync_interval": 60,       # Drive 同步間隔（秒）
    "fallback_enabled": False,      # 是否啟用備援 API
    "fallback_api_url": "",         # 備援 API 端點 URL
    "fallback_api_key": "",         # 備援 API Key
    "fallback_model": "",          # 備援模型名稱
    "model_fallback_chain": "",   # 模型降級鏈（逗號分隔，主模型 401/503/502/504 時自動依序嘗試）
    "fallback_daily_limit": 10,    # 備援模式下每位用戶每日對話上限
    "fallback_rate_per_min": 6,    # 備援 API 每分鐘聊天請求上限
    "fallback_owner_exempt": True,  # 機器人擁有者豁免備援限速與每日配額
    "owner_skip_model_chain": True,  # 擁有者跳過模型降級鏈：主模型不可用時直接調用備援 API（不逐一嘗試降級鏈中的其他模型），因為備援模型通常比降級鏈裡的免費模型更強
    "fallback_daily_limit_msg": "⚠️ 你的今日備援 API 用量已達上限，為了節省備援資源給重要的行政功能，聊天備援暫時關閉。主要 API 恢復後即可正常使用～",
    "fallback_rate_limit_msg": "⚠️ 備援 API 請求過於頻繁，請稍等一下再試～",
    "entertainment_unavailable_msg": "🔧 AI 系統暫時維護中，娛樂功能暫時關閉，請稍後再試～",
    "circuit_cooldown_msg": "🔌 AI API 目前被供應商暫時封鎖（anomalous behavior），已自動暫停請求，將在約 2 分鐘後重試。",
    # ── AI 網警（自動訊息審查）──
    "ai_mod_enabled": False,            # 是否啟用 AI 網警
    "ai_mod_model": "",                 # 審查用的模型名稱（留空=使用主模型）
    "ai_mod_api_url": "",              # 審查用的 API URL（留空=使用主 API URL）
    "ai_mod_api_key": "",              # 審查用的 API Key（留空=使用主 API Key）
    "ai_mod_report_channel": None,     # 通報頻道 ID
    "ai_mod_custom_rules": "",          # 額外伺服器規則（自由文字，注入到審查 prompt）
    "ai_mod_confidence": "medium",     # 靈敏度：low / medium / high（影響通報門檻）
    "ai_mod_cooldown": 30,             # 同一使用者兩次通報之間的最短間隔（秒）
    "ai_mod_max_tokens": 150,          # 審查回覆最大 token 數（輕量級）
    "ai_mod_timeout": 10,              # 審查 API 逾時（秒）
    "ai_mod_exempt_roles": [],          # 豁免角色 ID 列表（管理員等）
    # ── AI 網警：嚴重違規自動處置 ──
    "ai_mod_severe_enabled": False,       # 啟用嚴重違規自動刪除+警告
    "ai_mod_severe_rules": "",           # 嚴重違規判定規則（留空=用預設）
    # ── 娛樂功能 AI 模型選擇（留空 = 用主模型）──
    "quiz_model": "",                   # AI 搶答遊戲模型
    "turtle_soup_model": "",             # AI 海龜湯模型
    "werewolf_model": "",                # AI 狼人殺模型
    "fortune_model": "",                 # AI 占卜模型
    "chat_model": "",                     # 聊天功能專用模型（留空=用主模型）
    "admin_model": "",                    # 行政功能專用模型（留空=用主模型）
    "entertainment_model": "",            # 娛樂功能專用模型（留空=用主模型）
    # ── 總 AI 池系統（統一管理所有 API 端點+模型）──
    "ai_pool": [],                        # [{"id":"p1","name":"OpenAI","api_url":"...","api_key":"...","models":"gpt-4o-mini,gpt-4o"}]
    "model_roles": {},                    # 角色→池綁定：{"main":{"pool_id":"p1","model":"gpt-4o-mini"}, "backup":{...}, "chat":{...}, "admin":{...}, "entertainment":{...}, "quiz":{...}, "turtle_soup":{...}, "werewolf":{...}, "fortune":{...}, "vision":{...}, "ai_mod":{...}}
    "model_chains": {"main": [], "vision": []},  # 降級鏈：{"main":[{"pool_id":"p1","model":"m2"}, ...], "vision":[...]}
    # ── 文生圖（Text-to-Image）──
    "t2i_enabled": False,              # 是否啟用文生圖功能
    "t2i_api_url": "",                 # 文生圖 API URL（例如 https://api.openai.com/v1/images/generations）
    "t2i_api_key": "",                 # 文生圖 API Key（可與聊天 API 不同）
    "t2i_model": "",                   # 文生圖模型名稱（例如 dall-e-3, flux-1, stable-diffusion-xl）
    "t2i_size": "1024x1024",           # 圖片尺寸（1024x1024 / 1792x1024 / 1024x1792）
    "t2i_quality": "standard",         # 品質（standard / hd，DALL-E 適用）
    "t2i_cooldown": 60,                # 每位使用者兩次生圖之間的最短間隔（秒）
    "t2i_daily_limit": 10,             # 每位使用者每日生圖上限
    "t2i_owner_exempt": True,          # 擁有者豁免限速與每日配額
    "t2i_auto_detect": True,           # 是否在聊天中自動偵測生圖請求
    # 高級生圖通道（優先使用，失敗或額度用完時降級回 t2i_* 預設通道）
    "t2i_premium_enabled": False,      # 是否啟用高級生圖通道
    "t2i_premium_api_url": "",         # 高級生圖 API URL（例如 https://api.openai.com/v1/images/generations）
    "t2i_premium_api_key": "",          # 高級生圖 API Key
    "t2i_premium_model": "",            # 高級生圖模型（例如 dall-e-3, gpt-image-1, flux-pro）
    "t2i_premium_size": "",             # 高級生圖尺寸（留空=沿用 t2i_size）
    "t2i_premium_quality": "",          # 高級生圖品質（留空=沿用 t2i_quality）
    "t2i_premium_daily_limit": 30,      # 高級通道每日總額度（全伺服器共用）
    "t2i_premium_daily_count": 0,      # 高級通道今日已用次數
    "t2i_premium_daily_date": "",       # 高級通道額度計算日期（自動重置）
    "t2i_filter_enabled": False,       # 是否啟用生圖提示詞過濾
    "t2i_filter_pool_id": "",           # 過濾模型使用的 API 池 ID（從 Dashboard 下拉選）
    "t2i_filter_model": "",             # 過濾模型名稱（留空=用該池的預設模型）
    "t2i_filter_timeout": 15,           # 過濾 API 逾時（秒）
    "t2i_filter_max_tokens": 100,       # 過濾回覆最大 token 數
    "t2i_filter_strictness": "medium",  # 審查嚴格度：loose/medium/strict
    "t2i_filter_vision_model": "",     # 嚴格模式用於圖片複審的視覺模型（留空=用主視覺模型）
}

CHAT_AI_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chat_ai_settings.json")

# Per-channel cooldown tracking
chat_cooldowns: dict = {}  # channel_id -> timestamp
_last_global_reply: float = 0  # 全域上次回應時間（所有頻道共用）
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
        _save_json_file(TOKEN_USAGE_FILE, token_usage)
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
        today = datetime.now(GMT8).strftime("%Y-%m-%d")
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

# ── Reasoning 模型控制 ──
# GLM-5.2, DeepSeek-R1 等 reasoning 模型在回答前會先「思考」(reasoning)，
# 思考過程可能耗時 30-120 秒。這裡提供統一的控制機制：
# 1. 透過 payload 參數控制思考強度（reasoning_effort / thinking）
# 2. 根據是否開啟 reasoning 動態調整 timeout

def _get_reasoning_effort(fallback_mode: str = "full", category: str = "") -> str:
    """根據呼叫類型取得 reasoning_effort 設定。

    全域 reasoning_effort 是最高優先級的 kill switch：
    - 設為 "none" 時，所有類別都回 "none"（完全停用 reasoning），
      確保使用者從 dashboard 設定「關閉」能真正全域生效，
      不會被 per-category 預設值覆蓋。
    - 設為其他值或未設定時，使用 per-category 設定。

    category 優先於 fallback_mode——明確指定時使用 category。
    fallback_mode 僅在 category 未指定時作為向後兼容的推斷依據。
    """
    # 全域 kill switch：reasoning_effort="none" → 所有類別都停用
    _global_effort = chat_ai_settings.get("reasoning_effort", "")
    if _global_effort == "none":
        return "none"
    if category:
        if category == "chat":
            return chat_ai_settings.get("reasoning_chat_effort", "low")
        elif category == "entertainment":
            return chat_ai_settings.get("reasoning_entertainment_effort", "low")
        elif category == "admin":
            return chat_ai_settings.get("reasoning_admin_effort", "medium")
    # 向後兼容：從 fallback_mode 推斷
    if fallback_mode == "rate_limited":
        return chat_ai_settings.get("reasoning_chat_effort", "low")
    elif fallback_mode == "disabled":
        return chat_ai_settings.get("reasoning_entertainment_effort", "low")
    else:  # "full" or default
        return chat_ai_settings.get("reasoning_admin_effort", "medium")

def _build_reasoning_params(effort: str) -> dict:
    """根據 reasoning_effort 值構建 API payload 中的 reasoning 控制參數。
    同時發送多種格式以兼容不同 API 供應商：
    - reasoning_effort: OpenAI o1/o3 格式（也適用於 Nvidia NIM）
    - thinking: ZhipuAI/GLM 格式
    - enable_thinking: 部分 API 使用的布林值格式
    """
    params = {}
    if effort == "none":
        # 關閉 reasoning — 不送任何參數，交給 API 用自己的預設行為。
        # ⚠️ 這對「預設不思考」的模型沒問題，但對 gpt-oss 系列、部分
        # glm/deepseek build 這種「預設就會思考」的 reasoning 模型完全
        # 無效——不送參數 = 用它自己的預設值，很多時候預設值仍然是
        # 開著思考的，短預算的聊天請求會被隱藏思考活活拖到逾時，或
        # max_tokens 被思考吃光只回傳空白內容。呼叫端若需要「真正確定
        # 關閉」，應改用 _build_reasoning_disable_params()（會明確送出
        # 關閉信號，並仰賴 _reasoning_unsupported_apis 白名單機制處理
        # 會拒絕未知欄位的端點）。這個函式維持回傳空字典是為了不影響
        # 既有沒有該白名單防護機制的呼叫點（call_ai_api 等背景摘要功能）。
        return {}
    elif effort in ("low", "medium", "high", "auto"):
        params["reasoning_effort"] = effort
        params["thinking"] = {"type": "enabled", "effort": effort}
        params["enable_thinking"] = True
    # else: effort is empty/unknown → don't send any params (use API default)
    return params


def _build_reasoning_disable_params() -> dict:
    """明確要求 API／模型關閉或最小化 reasoning（思考）行為，而不是單純
    不送參數賭它預設關閉。同時送多種格式盡量兼容不同供應商：
    - reasoning_effort: "none"（OpenAI o1/o3 及相容代理格式）
    - thinking: {"type": "disabled"}（ZhipuAI/GLM 格式）
    - enable_thinking: False（部分代理使用的布林值格式）

    這是為了修正一個「只針對特定模型優化」的通用性 bug：許多開源
    reasoning 模型（openai/gpt-oss-120b、gpt-oss-20b 等）是「預設思考」
    架構，若呼叫端因為預算不足而選擇「不送任何 reasoning 參數」，這些
    模型仍會用自己的預設值思考，短預算聊天請求下會出現兩種症狀：
    (1) 思考期間完全不吐字，觸發 socket 逾時（連線層面完全連不上感）；
    (2) max_tokens 被思考吃光，finish_reason=length 但 content 空白。
    明確送出關閉信號才能真正改變這些模型的行為，而不是被動猜測。

    ⚠️ 呼叫端必須先確認 api_url 不在 _reasoning_unsupported_apis 白名單
    裡才呼叫這個函式——某些端點對未知欄位做嚴格驗證會直接 400。
    一旦真的被拒絕，既有的 400 自動偵測邏輯會把該端點加入白名單並
    立即用清乾淨的 payload 重試，之後就不會再對它送這些參數。"""
    return {
        "reasoning_effort": "none",
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }

def _get_reasoning_timeout(effort: str, fallback_mode: str = "full", category: str = "") -> int:
    """根據是否開啟 reasoning 取得適當的 timeout。"""
    if effort == "none" or not effort:
        return chat_ai_settings.get("reasoning_disabled_timeout", 25)
    else:
        return chat_ai_settings.get("reasoning_enabled_timeout", 90)


def _track_token_usage(data: dict):
    usage = data.get("usage")
    if not usage or not isinstance(usage, dict):
        return
    total = usage.get("total_tokens", 0)
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    if not total and not prompt and not completion:
        return
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
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
    # Rolling 60s window for API call rate detection (used by AI refine loop)
    _api_call_timestamps.append(_time.time())


# ── Rolling API call rate tracker (for dynamic refine interval) ──
_api_call_timestamps: list = []  # timestamps of recent API calls (pruned to last 120s)

def _get_api_calls_per_minute() -> int:
    """Return the number of API calls in the last 60 seconds."""
    now = _time.time()
    cutoff = now - 60
    # Prune old entries
    while _api_call_timestamps and _api_call_timestamps[0] < cutoff:
        _api_call_timestamps.pop(0)
    return len(_api_call_timestamps)

def _compute_dynamic_refine_interval() -> int:
    """Compute the effective refine interval in seconds based on:
    - Current API call rate (calls/min)
    - Knowledge base fullness
    - Recent yield (consecutive cycles producing no new knowledge)

    Rules:
    - High traffic (>20 calls/min): slow down to 15 min
    - Medium traffic (10-20 calls/min): 5 min
    - Low traffic (<10 calls/min): may speed up, but the user's base
      interval is a CEILING for the "normal" case — we only shorten it
      moderately (down to half, floor 2 min), never collapse to a fixed
      60s regardless of what the user configured.
    - Knowledge base full (>90%): slow down to 60 min regardless of traffic
    - If recent cycles kept coming back empty/duplicate (no real knowledge
      produced), back off further — no point burning API calls every
      minute when the well is dry.
    """
    base_minutes = ai_refine_settings.get("interval_minutes", 5)
    base_secs = base_minutes * 60

    # Knowledge base fullness check
    max_entries = ai_refine_settings.get("max_knowledge_entries", 500)
    current_entries = len(ai_refined_knowledge)
    if max_entries > 0 and current_entries >= max_entries * 0.9:
        return 3600  # 1 hour when knowledge base is 90%+ full

    calls_per_min = _get_api_calls_per_minute()

    if calls_per_min > 20:
        # High traffic — back off significantly
        dynamic = max(base_secs, 900)  # at least 15 min
    elif calls_per_min > 10:
        # Medium traffic — moderate pace
        dynamic = max(base_secs, 300)  # at least 5 min
    else:
        # Low traffic — a new cycle may be DISPATCHED at most every 60s.
        # (This is a dispatch interval, not a wait-for-completion interval —
        # concurrency lets multiple cycles run in parallel, see ai_refine_loop.)
        dynamic = 60

    # Back off if recent cycles kept yielding nothing (duplicate/empty).
    # Prevents wasting API calls every cycle when there's simply nothing
    # new left to extract right now.
    if _refine_empty_streak >= 5:
        dynamic = max(dynamic, 1200)  # at least 20 min
    elif _refine_empty_streak >= 3:
        dynamic = max(dynamic, 600)  # at least 10 min

    return dynamic

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
# Guards the AI-chat-room stale cleanup + panel auto-repost so they only run
# ONCE per process lifetime, even if on_ready fires again after a gateway
# reconnect (not just a full restart) — avoids re-deleting/re-posting the
# panel on every reconnect blip.
_chat_room_startup_done: bool = False

# ──────────────────────────────────────────────
# 每用戶記憶系統（per-user memory）
# ──────────────────────────────────────────────

USER_MEMORIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "user_memories.json")
user_memories: dict = {}  # {user_id_str: {facts: [...], name: str, interaction_count: int, last_seen: float}}

# ── 短期對話歷史（per-user，僅限本人與 AI 的對話，不含其他人的訊息）──
# In-memory only (deliberately NOT persisted to disk/Drive) — this is meant
# as short-lived rolling context for the CURRENT conversation, not permanent
# memory (that's what user_memories/[MEMORY:] is for). Resetting on restart
# is fine and even desirable to avoid dragging in stale, long-dead threads.
# Strictly scoped per user_id — never mixes in what other people said, which
# was the earlier bug (injecting the whole channel's recent chatter caused
# the AI to answer whatever topic other people happened to be discussing).
_user_chat_history: dict = {}  # user_id_str -> [{"role": "user"/"assistant", "content": str}, ...]
_USER_HISTORY_MAX_TURNS = 4  # keep last 4 user+assistant exchanges (8 messages)
_USER_HISTORY_MAX_AGE = 1800  # 30 分鐘——太久之前的對話不再視為「近期」


def _get_user_history(user_id: str) -> list:
    """Return this user's recent conversation turns with the bot, dropping
    any that have aged out. Returns a plain list of {"role","content"} dicts
    ready to splice into the messages array sent to the AI."""
    entry = _user_chat_history.get(user_id)
    if not entry:
        return []
    now = _time.time()
    turns = [t for t in entry.get("turns", []) if now - t.get("_ts", 0) <= _USER_HISTORY_MAX_AGE]
    entry["turns"] = turns
    return [{"role": t["role"], "content": t["content"]} for t in turns]


def _append_user_history(user_id: str, user_text: str, assistant_text: str):
    """Record this exchange for the user's short-term history, trimmed to
    the last _USER_HISTORY_MAX_TURNS pairs."""
    if not user_text or not assistant_text:
        return
    entry = _user_chat_history.setdefault(user_id, {"turns": []})
    now = _time.time()
    entry["turns"].append({"role": "user", "content": user_text[:500], "_ts": now})
    entry["turns"].append({"role": "assistant", "content": assistant_text[:500], "_ts": now})
    # Keep only the most recent N pairs (2N messages)
    max_msgs = _USER_HISTORY_MAX_TURNS * 2
    if len(entry["turns"]) > max_msgs:
        entry["turns"] = entry["turns"][-max_msgs:]


# ──────────────────────────────────────────────
# 專屬 AI 聊天室系統（AI Chat Room — like ChatGPT/Gemini app experience）
# ──────────────────────────────────────────────
# Each user can create their own private text channel via a button panel.
# In these channels, the AI:
#   - Responds to EVERY message (no mention needed, no cooldown, no filter)
#   - Has FULL channel conversation history (fetches last N messages as
#     actual message objects passed to the API, not system-prompt injection)
#   - Behaves like a dedicated 1-on-1 AI assistant (like ChatGPT/Gemini app)
#
# Data is persisted to ai_chat_rooms.json (auto-synced to Drive like all
# other data/*.json files).

AI_CHAT_ROOMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ai_chat_rooms.json")
ai_chat_rooms: dict = {
    "rooms": {},        # channel_id_str -> {"user_id": str, "user_name": str, "created_at": float, "guild_id": int, "message_count": int}
    "panel_channel_id": None,   # where the button panel is
    "panel_message_id": None,   # the panel message itself (for redeploy cleanup/repost)
    "category_id": None,        # category to create rooms under
    "max_rooms": 50,             # global cap
    "max_history_messages": 50, # how many messages to fetch for AI context
    "enabled": True,
}


def save_ai_chat_rooms():
    try:
        os.makedirs(os.path.dirname(AI_CHAT_ROOMS_FILE), exist_ok=True)
        _save_json_file(AI_CHAT_ROOMS_FILE, ai_chat_rooms, indent=None)
    except Exception as e:
        print(f"⚠️ Failed to save AI chat rooms: {e}")


def load_ai_chat_rooms():
    global ai_chat_rooms
    try:
        if os.path.exists(AI_CHAT_ROOMS_FILE):
            with open(AI_CHAT_ROOMS_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
            # Merge with defaults so new keys appear on old saves
            for k, v in loaded.items():
                ai_chat_rooms[k] = v
            print(f"✅ 載入 AI 聊天室設定：{len(ai_chat_rooms.get('rooms', {}))} 個聊天室")
    except Exception as e:
        print(f"⚠️ Failed to load AI chat rooms: {e}")


def is_ai_chat_room(channel_id: int) -> bool:
    """Check if this channel is an AI chat room."""
    return str(channel_id) in ai_chat_rooms.get("rooms", {})


def get_ai_chat_room_owner(channel_id: int) -> str:
    """Get the owner user_id of this AI chat room, or empty string."""
    room = ai_chat_rooms.get("rooms", {}).get(str(channel_id))
    return room.get("user_id", "") if room else ""


def user_has_chat_room(user_id: int, guild_id: int = None) -> bool:
    """Check if this user already has an active AI chat room."""
    rooms = ai_chat_rooms.get("rooms", {})
    for ch_id, room in rooms.items():
        if room.get("user_id") == str(user_id):
            if guild_id is None or room.get("guild_id") == guild_id:
                return True
    return False


def get_user_chat_room_channel(user_id: int) -> int:
    """Get the channel_id of the user's existing chat room, or 0."""
    rooms = ai_chat_rooms.get("rooms", {})
    for ch_id, room in rooms.items():
        if room.get("user_id") == str(user_id):
            return int(ch_id)
    return 0


# ── Button View for the chat room panel ──
class AIChatRoomPanelView(discord.ui.View):
    """Persistent view for the AI Chat Room creation panel.
    This needs to be added to the bot's persistent views so it survives
    bot restarts."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="開啟專屬 AI 聊天室",
        style=discord.ButtonStyle.primary,
        emoji="🤖",
        custom_id="ai_chat_room:create"
    )
    async def create_room_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not ai_chat_rooms.get("enabled", True):
            await interaction.response.send_message("❌ AI 聊天室功能目前未開放。", ephemeral=True)
            return

        user = interaction.user
        uid_str = str(user.id)

        # Check if user already has a room
        existing_ch = get_user_chat_room_channel(user.id)
        if existing_ch:
            # Try to find the channel and point them to it
            ch = interaction.guild.get_channel(existing_ch) if interaction.guild else None
            if ch:
                await interaction.response.send_message(
                    f"你已經有一個專屬聊天室了：{ch.mention}\n直接在那裡跟 AI 對話即可～",
                    ephemeral=True
                )
                return
            else:
                # Channel was deleted but record remains — clean up
                ai_chat_rooms["rooms"].pop(str(existing_ch), None)
                save_ai_chat_rooms()

        # Check max rooms
        rooms = ai_chat_rooms.get("rooms", {})
        if len(rooms) >= ai_chat_rooms.get("max_rooms", 50):
            await interaction.response.send_message(
                "❌ 已達聊天室數量上限，請聯繫管理員。", ephemeral=True
            )
            return

        # Check category is set
        category_id = ai_chat_rooms.get("category_id")
        if not category_id:
            await interaction.response.send_message(
                "❌ 管理員尚未設定聊天室分類頻道。請聯繫管理員使用 `/chat room_category` 設定。",
                ephemeral=True
            )
            return

        category = interaction.guild.get_channel(int(category_id))
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "❌ 聊天室分類頻道不存在或類型錯誤。請聯繫管理員重新設定。",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Create the private channel
        try:
            # Channel name: ai-username or ai-userid
            safe_name = "".join(c for c in user.display_name if c.isalnum() or c in "-_") or str(user.id)
            ch_name = f"ai-{safe_name}"[:100]

            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, manage_channels=True,
                    embed_links=True, attach_files=True
                ),
                user: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, attach_files=True,
                    embed_links=True
                ),
            }

            new_ch = await interaction.guild.create_text_channel(
                name=ch_name,
                category=category,
                overwrites=overwrites,
                topic=f"專屬 AI 聊天室 — {user.display_name} (ID: {user.id})"
            )

            # Register in data
            ai_chat_rooms["rooms"][str(new_ch.id)] = {
                "user_id": uid_str,
                "user_name": user.display_name,
                "created_at": _time.time(),
                "guild_id": interaction.guild.id,
                "message_count": 0,
            }
            save_ai_chat_rooms()

            # Send welcome message with close button
            welcome_embed = discord.Embed(
                title="🤖 歡迎來到你的專屬 AI 聊天室！",
                description=(
                    f"嗨 {user.mention}！這是你和 AI 的私人聊天空間。\n\n"
                    f"✅ 在這裡**直接打字**，AI 就會回覆（不需要 @）\n"
                    f"✅ AI 會記住這個頻道裡的**所有對話**，就像 ChatGPT/Gemini 一樣\n"
                    f"✅ 你可以傳送圖片讓 AI 分析（如果視覺模型已設定）\n\n"
                    f"準備好就開始聊吧～"
                ),
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )
            welcome_embed.set_footer(text="點擊下方按鈕可以關閉聊天室（頻道會被刪除）")

            close_view = AIChatRoomCloseView()
            await new_ch.send(embed=welcome_embed, view=close_view)

            await interaction.followup.send(
                f"✅ 已為你建立專屬聊天室：{new_ch.mention}\n點擊前往開始對話～",
                ephemeral=True
            )
            print(f"🤖 AI 聊天室已建立：#{new_ch.name} (for {user.display_name}, ID:{user.id})")

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Bot 沒有建立頻道的權限。請聯繫管理員確認 Bot 在目標分類有「管理頻道」權限。",
                ephemeral=True
            )
        except Exception as e:
            print(f"❌ AI 聊天室建立失敗：{e}")
            await interaction.followup.send(f"❌ 建立失敗：{e}", ephemeral=True)


class AIChatRoomCloseView(discord.ui.View):
    """View with a close button for AI chat rooms."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="關閉聊天室",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="ai_chat_room:close"
    )
    async def close_room_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch_id = str(interaction.channel.id)
        room = ai_chat_rooms.get("rooms", {}).get(ch_id)

        if not room:
            await interaction.response.send_message("❌ 這不是 AI 聊天室。", ephemeral=True)
            return

        # Only room owner or bot owner can close
        uid_str = str(interaction.user.id)
        is_owner_close = uid_str == room.get("user_id")
        is_bot_owner = str(interaction.user.id) == str(BOT_OWNER_ID)

        if not (is_owner_close or is_bot_owner):
            await interaction.response.send_message("❌ 只有聊天室主人或管理員可以關閉。", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Clean up record
            ai_chat_rooms["rooms"].pop(ch_id, None)
            save_ai_chat_rooms()

            # Delete the channel
            await interaction.channel.delete(reason=f"AI 聊天室由 {interaction.user.display_name} 關閉")
            print(f"🤖 AI 聊天室已關閉：#{interaction.channel.name} (by {interaction.user.display_name})")
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"❌ AI 聊天室關閉失敗：{e}")


def _make_chat_room_panel_embed() -> discord.Embed:
    """Build the panel embed shown with the 'open chat room' button.
    Shared by /room setup and the auto-repost-on-startup logic so both
    always produce byte-identical panels."""
    panel_embed = discord.Embed(
        title="🤖 專屬 AI 聊天室",
        description=(
            "點擊下方按鈕，開啟你自己的專屬 AI 聊天室！\n\n"
            "✅ 1 對 1 私人對話空間\n"
            "✅ AI 記住整個頻道的對話歷史（像 ChatGPT/Gemini）\n"
            "✅ 不需要 @，直接打字 AI 就會回\n"
            "✅ 可以傳圖片讓 AI 分析\n\n"
            "每人限開 1 間，不用時可以關閉。"
        ),
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )
    panel_embed.set_footer(text="ICEA 專屬 AI 聊天室系統")
    return panel_embed


async def _repost_chat_room_panel(channel, delete_old: bool = True) -> discord.Message | None:
    """(Re)post the chat room panel in `channel`, deleting any previous panel
    message first. Used both by /room setup (manual) and automatically on
    every bot startup (in on_ready) — this guarantees the button always
    works after a redeploy, regardless of whether the old message's
    persistent view survived the restart cleanly or not.

    Returns the newly sent message, or None if it failed."""
    if delete_old:
        # 1) Try the message ID we saved from last time — fastest path.
        old_msg_id = ai_chat_rooms.get("panel_message_id")
        if old_msg_id:
            try:
                old_msg = await channel.fetch_message(int(old_msg_id))
                await old_msg.delete()
                print(f"🧹 已刪除舊的聊天室面板訊息（ID: {old_msg_id}）")
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"⚠️ 刪除舊面板訊息失敗（by ID）：{e}")

        # 2) Safety net: also scan recent history for any leftover bot
        # messages that look like the panel embed (covers cases where the
        # stored message_id is missing/stale, e.g. very first upgrade, or
        # duplicates left over from manual re-runs of /room setup).
        try:
            async for msg in channel.history(limit=20):
                if msg.author.id == bot.user.id and msg.embeds:
                    if msg.embeds[0].title == "🤖 專屬 AI 聊天室":
                        try:
                            await msg.delete()
                            print(f"🧹 已清除殘留的聊天室面板訊息（ID: {msg.id}）")
                        except Exception:
                            pass
        except Exception as e:
            print(f"⚠️ 掃描舊面板訊息失敗：{e}")

    # 3) Send the fresh panel with an identical embed + a freshly-bound
    # persistent view (the view class is already registered globally via
    # bot.add_view(), so this new message's components work immediately —
    # no need to wait for anything else).
    try:
        new_msg = await channel.send(embed=_make_chat_room_panel_embed(), view=AIChatRoomPanelView())
        ai_chat_rooms["panel_message_id"] = new_msg.id
        save_ai_chat_rooms()
        print(f"✅ 聊天室面板已（重新）發送至 #{channel.name}（訊息 ID: {new_msg.id}）")
        return new_msg
    except Exception as e:
        print(f"❌ 發送聊天室面板失敗：{e}")
        return None


# ── Generate AI reply for chat room (with full channel history) ──
async def generate_chat_room_reply(message, settings: dict) -> tuple:
    """Generate a reply for an AI chat room message, using full channel
    conversation history (like ChatGPT/Gemini app). Returns (reply, model_info).

    Unlike generate_chat_reply which only sends the current message + 4
    recent turns, this fetches the last N messages from the channel and
    sends them as actual conversation messages to the AI API — giving the
    AI a continuous, app-like conversation context."""

    user_id = str(message.author.id)
    user_name = message.author.display_name
    max_history = ai_chat_rooms.get("max_history_messages", 50)

    # Build system prompt — similar to generate_chat_reply but tailored
    # for 1-on-1 chat room experience
    system_prompt = settings["system_prompt"]

    # Inject current time
    _now = datetime.now(GMT8)
    _weekday_cn = ["一", "二", "三", "四", "五", "六", "日"][_now.weekday()]
    system_prompt += (
        f"\n\n─── 即時時間 ───\n"
        f"現在時間：{_now.strftime('%Y年%m月%d日')}（星期{_weekday_cn}）"
        f" {_now.strftime('%H:%M')}，GMT+8 台灣時間。"
        f"你的訓練資料有截止日期，可能不知道最近發生的事。"
        f"請使用 web_search 工具上網查證，不要憑訓練資料直接否定。"
    )

    # Chat room specific instruction
    system_prompt += (
        f"\n\n─── 專屬聊天室模式 ───\n"
        f"你現在在「{user_name}」的專屬聊天室裡，這是 1 對 1 的對話空間。\n"
        f"上方已經注入了你和 {user_name} 之前的完整對話歷史，請參考上下文回覆，\n"
        f"就像 ChatGPT 或 Gemini 的 App 版一樣——記住之前聊過什麼，保持對話連貫性。\n"
        f"這裡的對話比群組更自由，可以聊任何話題，不用局限在微國家事務。"
    )

    # Inject user memory (same as generate_chat_reply)
    mem = user_memories.get(user_id, {})
    facts = mem.get("facts", [])
    if facts:
        memory_lines = []
        for f in facts[-10:]:
            memory_lines.append(f"- {f.get('content', '')}")
        system_prompt += (
            f"\n\n─── 你對「{user_name}」的記憶 ───\n"
            f"以下是之前互動中記住的關於這位使用者的事：\n"
            + "\n".join(memory_lines)
        )
    else:
        system_prompt += f"\n\n你目前對「{user_name}」沒有特別的記憶。"

    # Inject per-user memory tag (same mechanism as regular chat)
    if facts:
        system_prompt += (
            f"\n\n─── 記憶寫入規則 ───\n"
            f"如果這次對話中有值得記住的事實（使用者告訴你關於他自己的事、偏好、"
            f"重要日期、計畫等），請在回覆最後加上 [MEMORY: 簡短記憶內容] 標記。"
            f"系統會自動將此存入你對這位使用者的長期記憶。只記真正值得記住的事。"
        )

    # ── 自動背景資料注入（跟一般聊天室 generate_chat_reply 同一套邏輯）──
    # 專屬聊天室之前只靠「AI 自己決定要不要呼叫工具」來查百科/搜尋，但較弱的
    # 免費模型常常不會主動呼叫，導致明明資料庫/百科裡有的資訊也回答「沒有資料」。
    # 這裡改成跟一般聊天室一樣：不管 AI 想不想查，都先自動比對百科、Discord 歷史、
    # 會員國登記資料、網路搜尋，直接把結果餵給 AI 當作事實依據。
    clean_content = (message.content or "").strip()
    # Image-only messages get a placeholder so downstream auto-context
    # checks don't all short-circuit on len < 4.  We don't actually
    # search micropedia/discord/web for an image (that would be silly),
    # but we need clean_content to be non-empty for the system prompt
    # assembly below (e.g. the chat room mode instruction references it).
    if not clean_content and message.attachments:
        clean_content = "(使用者傳了一張圖片)"

    _nation_registry_auto = ""
    if message.guild and any(m in clean_content for m in _NATION_REGISTRY_MARKERS):
        _nation_registry_auto = _format_nation_registry_context(message.guild.id, clean_content)

    micropedia_enabled = settings.get("micropedia_enabled", True)
    max_results = settings.get("micropedia_max_results", 5)
    _need_micropedia = bool(micropedia_enabled and len(clean_content) >= 4)
    _need_discord = bool(message.guild and len(clean_content) >= 4)
    _need_web = len(clean_content) >= 6

    async def _do_micropedia():
        if not _need_micropedia:
            return ""
        try:
            return await asyncio.wait_for(
                _micropedia_auto_context(clean_content, max_results), timeout=settings.get("preprocess_timeout", 6)
            )
        except Exception:
            return ""

    async def _do_discord():
        if not _need_discord:
            return ""
        try:
            return await asyncio.wait_for(
                _search_discord_history(message.guild, clean_content, limit=15), timeout=settings.get("preprocess_timeout", 6)
            )
        except Exception:
            return ""

    async def _do_web():
        if not _need_web:
            return ""
        try:
            return await asyncio.wait_for(_web_search(clean_content[:200]), timeout=settings.get("preprocess_timeout", 6))
        except Exception:
            return ""

    _t_room_pre = _time.time()
    auto_context, _discord_auto, _web_auto = await asyncio.gather(
        _do_micropedia(), _do_discord(), _do_web()
    )
    print(f"⏱️ AI 聊天室預處理（百科+Discord+網路 平行）耗時 {_time.time()-_t_room_pre:.1f}s")

    if _nation_registry_auto:
        system_prompt += (
            f"\n\n─── 本伺服器會員國登記資料（機器人自己的資料庫，非百科）───\n"
            f"以下是機器人資料庫裡登記的國家名單，依照使用者問題自動篩選類別。"
            f"這是官方登記資料，請直接引用回答，不要說「查不到」或「沒有明確列出」。\n{_nation_registry_auto}"
        )

    if auto_context:
        system_prompt += (
            f"\n\n─── 微國家百科資料（已自動比對到相關文章）───\n"
            f"以下是根據使用者訊息，自動從微國家百科 (micropedia.site) 比對到的相關文章。"
            f"請優先參考這些資料來回答問題，如果文章內容已經涵蓋使用者問的事，就直接回答，"
            f"不要猶豫或說「沒有資料」。只有文章確實沒涵蓋細節時才誠實說沒查到。\n{auto_context}"
        )

    if _discord_auto and "沒有找到" not in _discord_auto:
        system_prompt += (
            f"\n\n─── Discord 伺服器歷史資料（已自動搜尋到相關內容）───\n"
            f"以下是根據使用者的問題，自動從整個伺服器搜尋到的相關內容，這些是真實記錄，"
            f"如果下面的資料已經有答案就直接引用回答，不要說「不確定」。\n{_discord_auto}"
        )

    if _web_auto:
        system_prompt += (
            f"\n\n─── 網際網路搜尋結果（已自動查詢）───\n"
            f"以下是自動從網際網路搜尋到的結果，如果使用者問的是真實世界的事物就參考這些資料，"
            f"跟問題無關就忽略。\n{_web_auto[:1500]}"
        )

    # ── 資料檔案庫自動注入 ──
    if len(clean_content) >= 4:
        try:
            _data_lib_ctx = _build_data_library_context(clean_content)
        except Exception:
            _data_lib_ctx = ""
        if _data_lib_ctx:
            system_prompt += _data_lib_ctx
            print(f"📊 資料檔案庫(聊天室): 已注入到 AI 上下文")

    if ai_refined_knowledge:
        recent_knowledge = ai_refined_knowledge[-12:]
        if recent_knowledge:
            knowledge_lines = []
            for k in recent_knowledge:
                conf_tag = "✅" if k.get("confidence", "high") == "high" else "⚠️"
                knowledge_lines.append(f"- {conf_tag} [{k.get('date', '?')}] {k.get('topic', '')}：{k.get('summary', '')}")
            system_prompt += (
                f"\n\n─── 微國家精煉知識庫 ───\n"
                f"以下是從社群討論中萃取、經百科驗證修正的知識摘要。"
                f"✅ = 已經百科驗證（可信），⚠️ = 社群討論但百科未覆蓋（僅供參考）。\n"
                + "\n".join(knowledge_lines)
            )

    _room_vision_diag = []  # diagnostics from vision API calls (for AI log embed)

    # ── Fetch channel history ──
    history_messages = []
    try:
        async for msg in message.channel.history(limit=max_history, before=message):
            if msg.author.bot and msg.author.id != bot.user.id:
                continue
            if not msg.content and not msg.attachments:
                continue
            # Skip bot's embed-only messages (welcome, etc.)
            if msg.author.bot and msg.embeds and not msg.content:
                continue

            role = "assistant" if msg.author.id == bot.user.id else "user"
            content_text = msg.content or ""
            # Handle attachments (images) — describe them if vision model exists
            if msg.attachments:
                for att in msg.attachments:
                    if att.content_type and "image" in att.content_type:
                        vision_model = settings.get("vision_model", "")
                        if vision_model:
                            # Try to describe the image
                            try:
                                desc = await _describe_image(att.url, settings, _vision_diag=_room_vision_diag)
                                if desc:
                                    content_text += f"\n[圖片：{desc}]"
                            except Exception:
                                content_text += "\n[圖片]"
                        else:
                            content_text += "\n[圖片（未設定視覺模型，無法分析）]"
            if content_text.strip():
                history_messages.append({"role": role, "content": content_text[:2000]})

        # Reverse to chronological order (oldest first)
        history_messages.reverse()
    except Exception as e:
        print(f"⚠️ AI 聊天室歷史取得失敗：{e}")

    # Build the messages array for the API
    api_messages = [{"role": "system", "content": system_prompt}]
    api_messages.extend(history_messages)
    # The current message is already in history_messages since we fetched
    # up to `before=message`... actually no, `before=message` EXCLUDES the
    # current message. So we need to add the current message separately.
    current_content = message.content or ""
    if message.attachments:
        for att in message.attachments:
            if att.content_type and "image" in att.content_type:
                vision_model = settings.get("vision_model", "")
                if vision_model:
                    try:
                        desc = await _describe_image(att.url, settings, _vision_diag=_room_vision_diag)
                        if desc:
                            current_content += f"\n[圖片：{desc}]"
                    except Exception:
                        current_content += "\n[圖片]"
                else:
                    current_content += "\n[圖片（未設定視覺模型）]"
    # Safety net: if the current message ended up with only whitespace
    # (e.g. image-only with no vision model and the [圖片] tag got eaten
    # by some edge case), give the AI a minimal placeholder so the API
    # doesn't reject it as an empty user message.
    if not current_content.strip():
        current_content = "(使用者傳了一張圖片，請看看圖片內容並回應)"
    api_messages.append({"role": "user", "content": current_content[:2000]})

    # Determine fallback mode — chat room uses rate_limited (same as regular chat)
    _fb_mode = "rate_limited"
    _fb_user = user_id

    # Call the AI API — only offer tools as a FALLBACK when auto-context
    # above came up thin (mirrors generate_chat_reply's _context_rich logic).
    # If we already auto-injected micropedia/discord/web results, skip tools
    # entirely — cheaper, faster, and avoids the weak model just not calling
    # them and answering "沒有資料" anyway.
    _room_context_rich = bool(
        (auto_context and len(auto_context) > 50)
        or (_discord_auto and "沒有找到" not in _discord_auto and len(_discord_auto) > 50)
        or _nation_registry_auto
    )
    _norm = settings.get("api_url", "").rstrip("/")
    if not _norm.endswith("/chat/completions"):
        if _norm.endswith("/v1") or _norm.endswith("/v2"):
            _norm += "/chat/completions"
        else:
            _norm += "/v1/chat/completions"
    _tools_supported = _norm in _tools_supported_apis
    _tools_unsup = _norm in _tools_unsupported_apis
    tools_ok = _tools_supported and not _tools_unsup

    tools = []
    if not _room_context_rich:
        if tools_ok:
            if settings.get("micropedia_enabled", True):
                tools.append(_MICROPEDIA_TOOL_SCHEMA)
            tools.append(_DISCORD_SEARCH_TOOL_SCHEMA)
            tools.append(_WEB_SEARCH_TOOL_SCHEMA)
        elif not _tools_unsup and not _tools_supported:
            if settings.get("micropedia_enabled", True):
                tools.append(_MICROPEDIA_TOOL_SCHEMA)
            tools.append(_DISCORD_SEARCH_TOOL_SCHEMA)
            tools.append(_WEB_SEARCH_TOOL_SCHEMA)
    tools = tools if tools else None

    _reply_model = None
    _reply_fallback = False
    _reply_diag = []

    try:
        result = await call_chat_api(
            api_messages, settings,
            tools=tools,
            max_tokens=settings.get("ai_max_tokens", 800),
            timeout_total=_get_reasoning_timeout(_get_reasoning_effort(_fb_mode), _fb_mode),
            timeout_read=_get_reasoning_timeout(_get_reasoning_effort(_fb_mode), _fb_mode) - 5,
            is_background=False,
            fallback_mode=_fb_mode,
            fallback_user_id=_fb_user,
            category="admin",
        )
        if result.get("error") and not result.get("content"):
            return None, {"model": "?", "fallback": False, "diag": result.get("_diag", [])}

        raw_reply = result.get("content") or ""
        _reply_model = result.get("_used_model", settings.get("model", "?"))
        _reply_fallback = result.get("_used_fallback", False)
        _reply_diag = result.get("_diag", [])

        # Handle tool calls if present
        if result.get("tool_calls") and tools:
            # Process tool calls (simplified — single round)
            assistant_msg = result
            tool_results = await _process_tool_results_simple(
                assistant_msg, message, settings, tools
            )
            if tool_results:
                api_messages.append(assistant_msg)
                api_messages.extend(tool_results)
                # Force a final answer
                api_messages.append({
                    "role": "user",
                    "content": "請根據以上搜尋結果回答我的問題。"
                })
                result2 = await call_chat_api(
                    api_messages, settings, tools=None,
                    max_tokens=settings.get("ai_max_tokens", 800),
                    timeout_total=_get_reasoning_timeout(_get_reasoning_effort(_fb_mode), _fb_mode),
                    timeout_read=_get_reasoning_timeout(_get_reasoning_effort(_fb_mode), _fb_mode) - 5,
                    is_background=False,
                    fallback_mode=_fb_mode,
                    fallback_user_id=_fb_user,
                    category="admin",
                )
                if result2.get("content"):
                    raw_reply = result2.get("content") or ""
                    _reply_model = result2.get("_used_model", _reply_model)
                    _reply_fallback = result2.get("_used_fallback", _reply_fallback)
                    _reply_diag.extend(result2.get("_diag", []))

    except Exception as e:
        print(f"❌ AI 聊天室 API 呼叫失敗：{e}")
        return None, {"model": "?", "fallback": False, "diag": [f"❌ 例外：{str(e)[:100]}"]}

    # Clean up the reply — strip thinking tags, memory tags
    actual_reply = _strip_thinking(raw_reply)

    # Extract memory if present
    new_facts = None
    if "[MEMORY:" in actual_reply:
        try:
            mem_match = re.search(r"\[MEMORY:\s*(.+?)\]", actual_reply)
            if mem_match:
                mem_content = mem_match.group(1).strip()
                new_facts = [{"content": mem_content, "date": _now.strftime("%Y-%m-%d")}]
                actual_reply = (actual_reply[:mem_match.start()] + actual_reply[mem_match.end():]).strip()
        except Exception:
            pass

    # Extract moderation action if present
    mod_action = None
    if "[MOD:" in actual_reply:
        try:
            mod_match = re.search(r"\[MOD:\s*(.+?)\]", actual_reply)
            if mod_match:
                mod_action = mod_match.group(1).strip()
                actual_reply = (actual_reply[:mod_match.start()] + actual_reply[mod_match.end():]).strip()
        except Exception:
            pass

    return actual_reply, {"model": _reply_model, "fallback": _reply_fallback, "diag": _reply_diag, "vision_diag": _room_vision_diag if _room_vision_diag else []}, new_facts, mod_action


async def _process_tool_results_simple(assistant_msg, message, settings, tools):
    """Process tool calls from the AI in a simplified single-round way.
    Returns a list of tool result messages to append to the conversation."""
    tool_calls = assistant_msg.get("tool_calls", [])
    if not tool_calls:
        return []

    results = []
    for tc in tool_calls[:3]:  # max 3 tool calls
        func_name = tc.get("function", {}).get("name", "")
        func_args_str = tc.get("function", {}).get("arguments", "{}")
        try:
            func_args = json_module.loads(func_args_str)
        except Exception:
            func_args = {}

        tool_content = ""
        try:
            if func_name == "search_micropedia":
                search_q = func_args.get("query", message.content[:50])
                tool_content = await _micropedia_auto_context(search_q, settings.get("micropedia_max_results", 5))
                if not tool_content:
                    tool_content = "未找到相關百科文章。"
            elif func_name == "web_search":
                search_q = func_args.get("query", message.content[:100])
                tool_content = await _web_search(search_q)
                if not tool_content:
                    tool_content = "未找到相關網路搜尋結果。"
            elif func_name == "search_discord":
                search_q = func_args.get("query", message.content[:100])
                if message.guild:
                    tool_content = await _search_discord_history(message.guild, search_q, limit=15)
                if not tool_content:
                    tool_content = "未找到相關伺服器內容。"
        except Exception as e:
            tool_content = f"搜尋失敗：{e}"

        results.append({
            "role": "tool",
            "tool_call_id": tc.get("id", "unknown"),
            "content": tool_content[:2000],
        })

    return results


def save_user_memories():
    try:
        os.makedirs(os.path.dirname(USER_MEMORIES_FILE), exist_ok=True)
        _save_json_file(USER_MEMORIES_FILE, user_memories, indent=None)
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
        _save_json_file(CHAT_AI_DATA_FILE, chat_ai_settings, indent=None)
        # 立即同步到 Drive（不等 60 秒週期迴圈），避免重啟/重新部署時競態遺失資料
        try:
            asyncio.ensure_future(_immediate_drive_upload("chat_ai_settings.json"))
        except RuntimeError:
            pass  # 沒有 running event loop（例如同步呼叫路徑），週期迴圈仍會補上
    except Exception as e:
        print(f"⚠️ Failed to save chat AI settings: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 總 AI 池：統一角色解析
# ─────────────────────────────────────────────────────────────────────────────
# 所有 AI 角色都透過 model_roles 字典映射到 AI 池中的特定端點+模型。
# 角色清單：main, backup, chat, admin, entertainment, quiz, turtle_soup,
#           werewolf, fortune, vision, ai_mod
# 降級鏈透過 model_chains 字典：{"main": [{pool_id, model}, ...], "vision": [...]}
# ─────────────────────────────────────────────────────────────────────────────

# 角色繼承鏈：如果該角色未綁定，回退到哪個角色
_ROLE_FALLBACK = {
    "backup": None,          # backup 沒有回退（直接用 legacy fallback_api_*）
    "chat": "main",
    "admin": "main",
    "entertainment": "main",
    "quiz": "entertainment",
    "turtle_soup": "entertainment",
    "werewolf": "entertainment",
    "fortune": "entertainment",
    "vision": "main",
    "ai_mod": "main",
}


def _resolve_role_endpoint(role: str, settings: dict = None) -> tuple:
    """解析角色 → (api_url, api_key, model)。
    優先順序：
    1. model_roles[role] → 從 AI 池中找對應的 pool entry
    2. 沿 _ROLE_FALLBACK 鏈往上找（chat→main, quiz→entertainment→main, ...）
    3. 最終回退到 legacy 欄位（api_url/api_key/model 或 fallback_api_*）
    回傳 (api_url, api_key, model)；找不到 model 時 model="" 表示無法使用。
    """
    s = settings if settings is not None else chat_ai_settings
    pool = s.get("ai_pool", [])
    roles = s.get("model_roles", {})

    def _try_role(r):
        binding = roles.get(r)
        if not binding or not isinstance(binding, dict):
            return None
        pool_id = binding.get("pool_id", "")
        model = binding.get("model", "")
        if not pool_id or not model:
            return None
        entry = next((e for e in pool if e.get("id") == pool_id), None)
        if not entry:
            return None
        return (entry.get("api_url", ""), entry.get("api_key", ""), model)

    # 1. Try the exact role
    result = _try_role(role)
    if result and result[0] and result[2]:
        return result

    # 2. Walk the fallback chain
    r = role
    for _ in range(5):  # max depth 5
        parent = _ROLE_FALLBACK.get(r)
        if parent is None:
            break
        result = _try_role(parent)
        if result and result[0] and result[2]:
            return result
        r = parent

    # 3. Final fallback: legacy fields
    if role == "backup":
        fb_url = s.get("fallback_api_url", "")
        fb_key = s.get("fallback_api_key", "")
        fb_model = s.get("fallback_model", "")
        return (fb_url, fb_key, fb_model)

    # For everything else, fall back to main model legacy fields
    main_url = s.get("api_url", "")
    main_key = s.get("api_key", "")
    main_model = s.get("model", "gpt-4o-mini")
    # But respect legacy per-category model overrides if they exist
    legacy_map = {
        "chat": "chat_model",
        "admin": "admin_model",
        "entertainment": "entertainment_model",
        "quiz": "quiz_model",
        "turtle_soup": "turtle_soup_model",
        "werewolf": "werewolf_model",
        "fortune": "fortune_model",
        "vision": "vision_model",
        "ai_mod": "ai_mod_model",
    }
    legacy_model_key = legacy_map.get(role)
    if legacy_model_key:
        override = s.get(legacy_model_key, "").strip()
        if override:
            # For ai_mod, also check if it has its own API URL/Key
            if role == "ai_mod":
                ai_mod_url = s.get("ai_mod_api_url", "").strip()
                ai_mod_key = s.get("ai_mod_api_key", "").strip()
                return (ai_mod_url or main_url, ai_mod_key or main_key, override)
            return (main_url, main_key, override)
    return (main_url, main_key, main_model)


def _resolve_chain(chain_name: str, settings: dict = None) -> list:
    """解析降級鏈 → [(api_url, api_key, model), ...]。
    chain_name: "main" 或 "vision"。
    優先使用 model_chains[chain_name]（池式降級鏈），若為空則回退到
    legacy model_fallback_chain / vision_fallback_chain（逗號分隔，同一 API）。
    """
    s = settings if settings is not None else chat_ai_settings
    pool = s.get("ai_pool", [])
    chains = s.get("model_chains", {})

    chain_entries = chains.get(chain_name, [])
    if chain_entries:
        result = []
        for entry in chain_entries:
            pool_id = entry.get("pool_id", "")
            model = entry.get("model", "")
            if not pool_id or not model:
                continue
            pool_entry = next((e for e in pool if e.get("id") == pool_id), None)
            if pool_entry:
                result.append((pool_entry.get("api_url", ""), pool_entry.get("api_key", ""), model))
        if result:
            return result

    # Legacy fallback: comma-separated model names, same API as main/vision
    if chain_name == "vision":
        legacy_chain = s.get("vision_fallback_chain", "").strip()
        main_url, main_key, _ = _resolve_role_endpoint("vision", s)
    else:
        legacy_chain = s.get("model_fallback_chain", "").strip()
        main_url, main_key, _ = _resolve_role_endpoint("main", s)

    if legacy_chain:
        result = []
        for m in legacy_chain.split(","):
            m = m.strip()
            if m:
                result.append((main_url, main_key, m))
        return result
    return []


def _auto_migrate_to_pool():
    """自動遷移：如果 AI 池為空但有 legacy api_url/api_key/model，
    建立一個預設池項目，並把 main 角色綁定到它。
    確保從舊版升級時設定不會遺失。"""
    global chat_ai_settings
    pool = chat_ai_settings.get("ai_pool", [])
    if pool:
        return  # 已有池資料，不需遷移

    main_url = chat_ai_settings.get("api_url", "").strip()
    main_key = chat_ai_settings.get("api_key", "").strip()
    main_model = chat_ai_settings.get("model", "").strip()

    if not main_url or not main_model:
        return  # 沒有 legacy 設定可遷移

    pool_id = "p1"
    new_pool = [{
        "id": pool_id,
        "name": "主要 API",
        "api_url": main_url,
        "api_key": main_key,
        "models": main_model,
    }]

    # If fallback API exists, add it as a second pool entry
    fb_url = chat_ai_settings.get("fallback_api_url", "").strip()
    fb_key = chat_ai_settings.get("fallback_api_key", "").strip()
    fb_model = chat_ai_settings.get("fallback_model", "").strip()
    if fb_url and fb_model:
        new_pool.append({
            "id": "p2",
            "name": "備援 API",
            "api_url": fb_url,
            "api_key": fb_key,
            "models": fb_model,
        })
        chat_ai_settings["model_roles"]["backup"] = {"pool_id": "p2", "model": fb_model}

    # If vision model exists, add it to p1's models
    vision_model = chat_ai_settings.get("vision_model", "").strip()
    if vision_model and vision_model != main_model:
        new_pool[0]["models"] = f"{main_model},{vision_model}"

    chat_ai_settings["ai_pool"] = new_pool
    chat_ai_settings["model_roles"]["main"] = {"pool_id": pool_id, "model": main_model}

    # Migrate model_fallback_chain to model_chains
    chain_raw = chat_ai_settings.get("model_fallback_chain", "").strip()
    if chain_raw:
        chain_list = []
        for m in chain_raw.split(","):
            m = m.strip()
            if m and m != main_model:
                chain_list.append({"pool_id": pool_id, "model": m})
        if chain_list:
            chat_ai_settings.setdefault("model_chains", {})["main"] = chain_list

    # Migrate vision_fallback_chain
    vis_chain_raw = chat_ai_settings.get("vision_fallback_chain", "").strip()
    if vis_chain_raw and vision_model:
        vis_chain_list = []
        for m in vis_chain_raw.split(","):
            m = m.strip()
            if m and m != vision_model:
                vis_chain_list.append({"pool_id": pool_id, "model": m})
        if vis_chain_list:
            chat_ai_settings.setdefault("model_chains", {})["vision"] = vis_chain_list

    print(f"✅ 自動遷移到 AI 池：{len(new_pool)} 個端點，main 綁定到 {pool_id}/{main_model}")


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
            # Migration: if the persisted system_prompt still matches the OLD
            # default (i.e. the user never customized it), auto-upgrade it to
            # the NEW default so anti-hallucination rule improvements actually
            # take effect on already-deployed instances instead of being
            # silently shadowed by a stale saved value.
            _OLD_DEFAULT_CHAT_PROMPT = (
                "你是一個微國家組織的 Discord 成員，也是一個 AI 助手。你會參與頻道中的討論，提供有建設性的意見。\n\n"
                "規則：\n"
                "- 用繁體中文回覆\n"
                "- 保持簡潔，通常 1-3 句話，最多不超過 5 句\n"
                "- 有自己的觀點，不要只是附和或重複別人說的話\n"
                "- 可以討論政策、法案、投票、組織運作等話題\n"
                "- 如果不確定的事實，直接說不確定，不要編造\n"
                "- 語氣自然輕鬆，像群組裡的一個朋友\n"
                "- 不要使用 markdown 標題（## ###）\n"
                "- 不要每次都長篇大論，有時候一句話就夠了\n"
                "- 可以開玩笑，但不要冒犯別人\n"
                "- 如果對話涉及微國家相關知識，你會收到微國家百科的查詢資料，請優先參考這些資料回答"
            )
            # V2 default — the version right before this migration was added
            # to (i.e. has "資料使用原則" but NOT the "不確定的使用邊界" rule
            # that stops the AI from saying "不確定" on obvious jokes/off-topic
            # questions). Instances that already auto-upgraded once need a
            # second upgrade to pick up this improvement too.
            _OLD_DEFAULT_CHAT_PROMPT_V2 = (
                "你是一個微國家組織的 Discord 成員，也是一個 AI 助手。你會參與頻道中的討論，提供有建設性的意見。\n\n"
                "規則：\n"
                "- 用繁體中文回覆\n"
                "- 保持簡潔，通常 1-3 句話，最多不超過 5 句\n"
                "- 有自己的觀點，不要只是附和或重複別人說的話\n"
                "- 可以討論政策、法案、投票、組織運作等話題\n"
                "- 語氣自然輕鬆，像群組裡的一個朋友\n"
                "- 不要使用 markdown 標題（## ###）\n"
                "- 不要每次都長篇大論，有時候一句話就夠了\n"
                "- 可以開玩笑，但不要冒犯別人\n"
                "- 如果對話涉及微國家相關知識，你會收到微國家百科的查詢資料，請優先參考這些資料回答\n\n"
                "─── 資料使用原則 ───\n"
                "- 上方注入的百科/Discord/知識庫資料都是可信的事實來源。如果資料裡有答案，就直接、自信地回答，不要猶豫。\n"
                "- 只有當你「完全沒有相關資料」時，才說「這個我不確定」或「目前沒有查到相關資料」。\n"
                "- 不要因為覺得資料「可能不夠完整」就退縮——有資料就回答，沒資料才說不知道。\n"
                "- 不要自行把兩個不同名稱/條目推論為同一個東西（例如「A 國家就是 B 國家的別稱」），\n"
                "  除非資料裡明確這樣寫。但這不影響你回答其他有資料的問題。\n"
                "- 不要編造沒有出現在資料中的細節來讓回答看起來更完整。\n"
                "─── 回覆格式鐵律 ───\n"
                "- 直接給出答案，不要在回覆中展示你的思考過程、推理步驟、或分析邏輯\n"
                "- 不要寫「讓我想想」「我來分析一下」「首先」「好的，我來看看」等思考性開場白\n"
                "- 不要使用 <think> 或 <thinking> 標籤，不要輸出任何形式的 reasoning\n"
                "- 你的回覆必須是一個自然的對話回應，不是思考筆記\n"
                "- 違反以上規則的回覆會被直接刪除思考部分，只保留最終答案"
            )
            if loaded.get("system_prompt", "").strip() in (
                _OLD_DEFAULT_CHAT_PROMPT.strip(), _OLD_DEFAULT_CHAT_PROMPT_V2.strip()
            ):
                loaded["system_prompt"] = DEFAULT_CHAT_AI_PROMPT
                print("🔄 偵測到 system_prompt 仍是舊版預設值，已自動升級為新版（含「不確定」使用邊界規則）")
            chat_ai_settings.update(loaded)
            print("✅ 載入 AI 聊天設定")
            _auto_migrate_to_pool()
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

    # ── MENTION: ONLY reply when explicitly @mentioned or replying to the bot ──
    if strength == "mention":
        if is_mentioned or is_reply_to_bot:
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


# ── Fallback API rate limiter (chat: 6 req/min) ──
_fallback_chat_timestamps: list = []
_FALLBACK_CHAT_RATE_PER_MIN = 6

# ── Fallback API per-user daily quota ──
# {user_id: {"date": "YYYY-MM-DD", "count": N}}
_fallback_daily_usage: dict = {}

def _check_fallback_daily_limit(user_id: str, limit: int) -> bool:
    """Check and increment per-user daily fallback API usage.
    Returns True if allowed, False if daily limit exceeded."""
    if not user_id or limit <= 0:
        return True  # no limit if no user or limit is 0
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    entry = _fallback_daily_usage.get(user_id)
    if not entry or entry.get("date") != today:
        # New day or first use — reset
        _fallback_daily_usage[user_id] = {"date": today, "count": 0}
        entry = _fallback_daily_usage[user_id]
    if entry["count"] >= limit:
        return False
    entry["count"] += 1
    return True

def _get_fallback_daily_remaining(user_id: str, limit: int) -> int:
    """Return remaining daily fallback quota for a user."""
    if not user_id or limit <= 0:
        return -1  # unlimited
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    entry = _fallback_daily_usage.get(user_id)
    if not entry or entry.get("date") != today:
        return limit
    return max(0, limit - entry["count"])

FALLBACK_DAILY_LIMIT_MSG = "⚠️ 你的今日備援 API 用量已達上限，為了節省備援資源給重要的行政功能，聊天備援暫時關閉。主要 API 恢復後即可正常使用～"

def _is_api_unavailable(error_str: str) -> bool:
    """Check if the error indicates the primary API is down (503/502/500 etc)."""
    if not error_str:
        return False
    return any(code in str(error_str) for code in ["503", "502", "500", "504", "Service Unavailable", "service_unavailable"])

_ENTERTAINMENT_UNAVAILABLE_MSG_DEFAULT = "🔧 AI 系統暫時維護中，娛樂功能暫時關閉，請稍後再試～"

def _get_entertainment_unavailable_msg():
    return chat_ai_settings.get("entertainment_unavailable_msg", _ENTERTAINMENT_UNAVAILABLE_MSG_DEFAULT)

def _check_fallback_chat_rate(limit: int = 6):
    """Sliding-window rate limiter for chat fallback API usage.
    Returns True if a new request is allowed, False if rate limit exceeded."""
    now = _time.time()
    _fallback_chat_timestamps[:] = [t for t in _fallback_chat_timestamps if now - t < 60]
    if len(_fallback_chat_timestamps) >= limit:
        return False
    _fallback_chat_timestamps.append(now)
    return True


async def call_chat_api(messages: list, settings: dict, tools: list = None, max_tokens: int = 300, timeout_total: int = 300, timeout_read: int = 120, is_background: bool = True, fallback_mode: str = "full", fallback_user_id: str = "", category: str = "") -> dict:
    """fallback_mode:
    - "full":          Always use fallback on provider errors (administrative)
    - "rate_limited":  Use fallback but limited to 6 req/min (chat)
    - "disabled":      Never use fallback — return error directly (entertainment/background)
    """
    """Call the chat AI API (non-streaming, short replies).
    Returns the raw assistant message dict (content + possible tool_calls),
    so the caller can drive a tool-calling loop when `tools` is provided.
    Automatically degrades to a plain (no-tools) call if this endpoint has
    already been observed to reject the `tools` field, or if this specific
    request with tools fails for ANY reason — different OpenAI-compatible
    proxies report an unsupported `tools` param with wildly different status
    codes (400, 422, 500, or even a 200 with an error payload instead of
    `choices`), so we don't try to guess which one and instead just retry
    plain whenever a tools-enabled call doesn't come back clean.

    Circuit breaker: if the API returns 403 "anomalous behavior" twice in
    a row, ALL subsequent calls are short-circuited for a cooldown period
    (default 120s) instead of hammering a blocked endpoint on every Discord
    message — which only makes the provider's abuse detector more certain
    the traffic is bot spam."""
    # ── 分類模型覆寫 ──
    # 根據 fallback_mode 判斷呼叫類別，如果該類別有專用模型且 caller 沒有
    # 自行指定 per-feature 模型（即 settings model == 主模型），則覆寫為專用模型。
    # ── 統一角色解析：從 AI 池解析當前 category 的 (api_url, api_key, model) ──
    _cat = category or ""
    if not _cat and fallback_mode == "rate_limited":
        _cat = "chat"
    elif not _cat and fallback_mode == "full":
        _cat = "admin"
    elif not _cat and fallback_mode == "disabled":
        _cat = "entertainment"
    _pool_url, _pool_key, _pool_model = _resolve_role_endpoint(_cat or "chat", chat_ai_settings)
    _settings_model = settings.get("model", "")
    _main_model = chat_ai_settings.get("model", "")
    _settings_url = settings.get("api_url", "")
    _main_url = chat_ai_settings.get("api_url", "")
    if _pool_url and _pool_model:
        # 只有當 caller 沿用主 API（沒自帶不同 API 端點）時才覆寫
        if not _settings_url or _settings_url == _main_url:
            settings["api_url"] = _pool_url
            settings["api_key"] = _pool_key
            if _settings_model == _main_model or not _settings_model:
                settings["model"] = _pool_model
                _diag_cat = f"🎯 池解析：{_cat} → {_pool_model}"
                print(_diag_cat)

    # Circuit breaker check — if tripped, fail fast without hitting the network
    if not _ai_circuit_check():
        remaining = _ai_circuit_breaker["cooldown_seconds"] - (_time.time() - _ai_circuit_breaker["trip_time"])
        print(f"🚫 AI 熔斷器開啟中，跳過請求（剩餘冷卻 {remaining:.0f}s）")
        return {"content": "", "error": _get_circuit_cooldown_msg(), "circuit_open": True, "_diag": ["🔌 熔斷器開啟：API 被供應商暫時封鎖，略過請求"]}

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

    # ── Reasoning 模型 timeout 自動調整 ──
    # 如果 caller 沒有明確指定 timeout（用預設值 300/120），根據這次呼叫
    # 會用的 reasoning_effort 自動決定合理的 timeout。必須在這裡（函式頂層，
    # _deadline 計算之前）做，不能在下面的巢狀 _attempt() 閉包裡做——
    # 那裡對外層變數賦值會被 Python 當成局部變數，讀取會拋 UnboundLocalError。
    if timeout_total == 300:  # 只在用預設值時自動調整；caller 明確傳大值（如 WW1 的 600s）則尊重
        _top_reasoning_effort = _get_reasoning_effort(fallback_mode, category)
        _top_auto_timeout = _get_reasoning_timeout(_top_reasoning_effort, fallback_mode)
        timeout_total = _top_auto_timeout
        timeout_read = max(3, _top_auto_timeout - 5)

    # ── HARD ABSOLUTE DEADLINE ──
    # `timeout_total` is the caller's TOTAL wall-clock budget for this ENTIRE
    # call_chat_api invocation — including internal retries and fallback
    # paths (tools-unsupported retry, non-streaming fallback, empty-content
    # retry). Previously each of those re-used the FULL timeout_total as its
    # own fresh per-request timeout, so e.g. a caller passing timeout_total=12
    # with one internal retry could genuinely take 24s+ before giving up —
    # blowing straight through the outer asyncio.wait_for() budget the caller
    # set up, and getting cancelled mid-flight with nothing to show for it.
    # Fix: track one absolute deadline: every internal network call computes
    # its OWN per-request timeout from however much time is actually left
    # until that deadline, so the whole function can never exceed
    # `timeout_total` seconds wall-clock no matter how many fallback paths fire.
    _t_start = _time.time()
    _deadline = _t_start + timeout_total

    def _remaining(floor=0.5):
        return max(floor, _deadline - _time.time())

    # ── Fix: reserve real time for the backup API, don't just hope leftovers
    # exist ──
    # Under concurrent load (many users chatting at once), the PRIMARY API is
    # exactly what gets slow — its shared free-tier capacity is being split
    # across everyone's simultaneous requests. Before this fix, every
    # internal call (first attempt, retry, model-downgrade chain) defaulted
    # to `_remaining()` — the ENTIRE budget still left — so a slow primary
    # could burn the whole clock before ever reaching the backup API branch,
    # which then either got started with near-zero time left (guaranteed to
    # be cancelled by the caller's own outer deadline) or got skipped
    # outright. That's the exact "偶爾還是逾時" pattern under load.
    # Fix: carve off a fixed RESERVE at the tail of the deadline that only
    # the backup-API branch is allowed to spend — the primary phase (first
    # attempt + retry + model chain) is confined to everything BEFORE that
    # reserve, and within that phase, no single attempt may claim more than
    # half of what's left there either, so a retry/second model always gets
    # a real shot too.
    # ── FIX：reasoning 關閉的短 timeout 場景要重新分配預算 ──
    # 實測發現：即使 reasoning_effort=none，主 API（z-ai/glm-5.2 經 ltzy.top
    # 代理）連「ping」這種最簡單的訊息也會逾時，代表這條代理本身的延遲/佇列
    # 問題不是我們能用 payload 參數關掉的。在 timeout_total 被壓到 25s 這種
    # 短預算下，如果還沿用「主模型可獨佔 85%」的邏輯，主模型會吃掉 ~15s 卻
    # 幾乎必然失敗，只留 ~2-3s 給降級鏈/備援 API——備援根本來不及跑完，
    # 導致「兩邊都沒時間完成」而 100% 逾時。
    # 修正：timeout_total 越短（= reasoning 關閉的快速通道），越要把預算
    # 大幅向備援 API 傾斜——主模型只給一次「快速嘗試」機會，剩下大部分
    # 時間留給已知較快、較穩定的備援 API，讓至少一邊有機會在期限內回應，
    # 而不是浪費時間在一個已知會逾時的主模型上。
    if timeout_total <= 30:
        _fallback_reserve = max(10, timeout_total * 0.55)
    else:
        _fallback_reserve = min(8, max(3, timeout_total * 0.3))
    _primary_deadline = _deadline - _fallback_reserve

    def _remaining_primary(floor=0.5):
        return max(floor, _primary_deadline - _time.time())

    # FIX：原本 0.6（60%）太保守——主模型明明正在正常串流生成 token，
    # 卻因為要「替降級鏈預留時間」而在只生成十幾個 token 後就被 total
    # timeout 硬切斷，然後觸發降級鏈/備援 API，等於「為了降級而降級」。
    # 主模型成功時根本不需要降級，所以把上限提高到 85%，讓正在工作的
    # 主模型有足夠時間完成回應。
    # ── 但短 timeout（reasoning 關閉）場景例外 ──：已知主模型連最簡單
    # 訊息都會逾時，給它 85% 的短預算只是保證性失敗，改用較保守的 60%，
    # 把省下來的時間讓給上面新增的備援保留額度。
    if timeout_total <= 30:
        _max_single_attempt = max(4, (timeout_total - _fallback_reserve) * 0.6)
    else:
        _max_single_attempt = max(5, (timeout_total - _fallback_reserve) * 0.85)
    _used_model = None        # which model actually answered (for logging)
    _used_fallback = False    # whether the backup API was used
    _diag = []                # diagnostic events for AI log embed

    async def _do_non_stream_post(url, pl, timeout):
        """Non-streaming POST: sends stream=False, reads the full JSON
        response in one shot. Returns (status, json_body_string) just like
        _read_stream does — a (200, json_string) on success, (status,
        error_text) on failure."""
        if is_background:
            async with _AI_BG_SEMAPHORE:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=pl, headers=headers, timeout=timeout) as resp:
                        if resp.status != 200:
                            err = await resp.text()
                            print(f"⚠️ 非串流 POST status={resp.status}: {err[:200]}")
                            if resp.status == 403 and "anomalous" in err.lower():
                                _ai_circuit_trip()
                            return resp.status, err
                        body = await resp.text()
                        # Verify it's valid JSON with choices
                        try:
                            data = json_module.loads(body)
                            if "choices" in data:
                                msg = data["choices"][0].get("message", {})
                                # Strip reasoning_content if present
                                if "reasoning_content" in msg:
                                    msg.pop("reasoning_content", None)
                                return 200, json_module.dumps(data)
                        except Exception:
                            pass
                        return 200, body  # might still be parseable downstream
        else:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=pl, headers=headers, timeout=timeout) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        print(f"⚠️ 非串流 POST status={resp.status}: {err[:200]}")
                        if resp.status == 403 and "anomalous" in err.lower():
                            _ai_circuit_trip()
                        return resp.status, err
                    body = await resp.text()
                    try:
                        data = json_module.loads(body)
                        if "choices" in data:
                            msg = data["choices"][0].get("message", {})
                            if "reasoning_content" in msg:
                                msg.pop("reasoning_content", None)
                            return 200, json_module.dumps(data)
                    except Exception:
                        pass
                    return 200, body

    async def _post(payload, _tt=None, _tr=None):
        """Non-streaming-first POST.

        ── FIX：非串流優先 ──
        用戶反映「測試時明明 deepseek 正常，但聊天時一直降級」。根因：
        dashboard 的「完整行政測試」用的是非串流 (stream=False) + 30 秒
        timeout——DeepSeek 處理完提示後直接回傳完整 JSON，穩穩成功。
        但聊天的 call_chat_api 走的是串流模式 (stream=True)，串流需要
        逐 chunk 讀取 SSE，大模型（如 deepseek-v4-pro）在生成前的
        「思考」期間不吐 chunk，如果這段靜默期超過 sock_read 或 _deadline
        限制，連線就被判定為失敗 → 觸發降級鏈 → 換到備援 API，明明
        模型完全正常、只是需要多幾秒思考。

        修正：改為非串流優先（跟測試一樣），串流作為備援。非串流模式
        不依賴 SSE 逐 chunk 時序——API 處理完就一次回傳完整 JSON，只要
        total timeout 足夠就好，沒有「chunk 間靜默被誤殺」的問題。

        串流備援仍然保留：如果非串流 timeout（某些 API 供應商在長回應
        時非串流會被 CDN/代理層先斷線），才改用串流模式，靠 SSE keep-alive
        維持連線。"""
        _budget = _tt if _tt is not None else min(_remaining_primary(), _max_single_attempt)
        _read_budget = _tr if _tr is not None else min(timeout_read, _budget)

        # ── 第一步：非串流 ──
        payload_ns = {**payload, "stream": False}
        payload_ns.pop("stream_options", None)  # non-streaming doesn't need this
        _ns_budget = min(_budget, _remaining_primary(floor=2))
        if _ns_budget >= 2:
            t_ns = aiohttp.ClientTimeout(total=_ns_budget, connect=min(10, _ns_budget), sock_read=max(8, _ns_budget))
            try:
                status, body = await _do_non_stream_post(api_url, payload_ns, t_ns)
                if status == 200:
                    # Quick check: does it have actual content?
                    try:
                        data = json_module.loads(body)
                        msg = data.get("choices", [{}])[0].get("message", {})
                        if msg.get("content") or msg.get("tool_calls"):
                            if use_tools:
                                _tools_supported_apis.add(api_url)
                                save_tools_supported()
                            return status, body
                        # 200 but empty content — fall through to streaming
                        print(f"⚠️ 非串流回應為空，嘗試串流模式...")
                    except Exception:
                        return status, body  # can't parse, let upstream handle
                elif (status == 400 or (status == 503 and "upstream status 400" in body.lower())) and any(
                    k in body and "unsupported parameter" in body.lower()
                    for k in ("reasoning_effort", "thinking", "enable_thinking")
                ):
                    # ── 這個 API 對未知欄位做嚴格驗證，reasoning 控制參數
                    # 被直接拒絕。記住這個 endpoint 以後永遠不送這些參數，
                    # 並立刻用清乾淨的 payload 重試一次（不要落到下面的串流
                    # 備援——串流備援目前沿用同一份 payload，一樣會帶著壞
                    # 參數再失敗一次，白白浪費時間）。
                    print(f"⚠️ 端點不支援 reasoning 參數（{body[:150]}），記住並移除後立即重試...")
                    _reasoning_unsupported_apis.add(api_url)
                    save_reasoning_unsupported()
                    payload_clean = {k: v for k, v in payload.items()
                                      if k not in ("reasoning_effort", "thinking", "enable_thinking")}
                    payload_ns_clean = {**payload_clean, "stream": False}
                    payload_ns_clean.pop("stream_options", None)
                    try:
                        status, body = await _do_non_stream_post(api_url, payload_ns_clean, t_ns)
                        if status == 200:
                            try:
                                data = json_module.loads(body)
                                msg = data.get("choices", [{}])[0].get("message", {})
                                if msg.get("content") or msg.get("tool_calls"):
                                    if use_tools:
                                        _tools_supported_apis.add(api_url)
                                        save_tools_supported()
                                    return status, body
                            except Exception:
                                return status, body
                    except (asyncio.TimeoutError, Exception) as e_clean:
                        print(f"⚠️ 移除 reasoning 參數後仍失敗（{type(e_clean).__name__}: {e_clean}），嘗試串流模式...")
                    payload.clear()
                    payload.update(payload_clean)
                else:
                    print(f"⚠️ 非串流 POST 失敗 (status={status})，嘗試串流模式...")
            except (asyncio.TimeoutError, Exception) as e:
                print(f"⚠️ 非串流 POST 逾時/錯誤（{type(e).__name__}: {e}），嘗試串流模式...")
        else:
            print(f"⏱️ 非串流預算不足（{_ns_budget:.1f}s），直接走串流...")

        # ── 第二步：串流備援 ──
        payload_s = {**payload, "stream": True}
        _s_budget = _remaining_primary(floor=1)
        if _s_budget < 1:
            # Primary phase is exhausted — let the outer deadline/degradation handle it
            raise asyncio.TimeoutError("串流備援：剩餘時間不足")
        t_s = aiohttp.ClientTimeout(total=None, connect=min(10, _s_budget), sock_read=max(8, _read_budget))
        if is_background:
            async with _AI_BG_SEMAPHORE:
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, json=payload_s, headers=headers, timeout=t_s) as resp:
                        return await _read_stream(resp)
        else:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=payload_s, headers=headers, timeout=t_s) as resp:
                    return await _read_stream(resp)

    async def _read_stream(resp):
        """Read an SSE stream and return (status, json_body) where json_body
        is a synthetic non-stream response containing the accumulated message."""
        if resp.status != 200:
            error_text = await resp.text()
            print(f"⚠️ API returned status {resp.status}: {error_text[:200]}")
            # Trip the circuit breaker on 403 anomalous-behavior blocks
            if resp.status == 403 and "anomalous" in error_text.lower():
                _ai_circuit_trip()
            return resp.status, error_text

        content_parts = []
        reasoning_parts = []  # diagnostic only — see note below
        tool_calls_acc = {}  # index -> {id, name, arguments}
        finish_reason = None
        _first_chunk_time = None
        _chunk_count = 0
        _deadline_hit = False

        stream_usage = None  # some APIs send usage in the final chunk
        async for raw_line in resp.content:
            # FIX：取代原本由 aiohttp total timeout 控制整體時限的做法。
            # 如果已經超過整體截止時間（_deadline），就中斷串流——但如果
            # 串流正在產出內容（content_parts 已有東西），不直接丟棄，
            # 而是帶著已收到的部分內容返回（部分回應遠比「全部丟掉再花
            # 時間走降級鏈/備援 API 重來」更有效率且使用者體驗更好）。
            if _time.time() > _deadline:
                _content_so_far = sum(len(p) for p in content_parts)
                if _content_so_far > 0:
                    print(f"⏱️ 串流已超過整體截止時間（已收到 {_content_so_far} chars），帶部分內容返回")
                    _deadline_hit = True
                    break
                else:
                    print(f"⏱️ 串流已超過整體截止時間且尚無內容，中斷以走降級/備援")
                    break
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
                # Capture usage if present (sent by some APIs in the final
                # chunk when stream_options.include_usage is true)
                if chunk.get("usage"):
                    stream_usage = chunk["usage"]
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
                # Some "reasoning" models (e.g. nvidia/nemotron, deepseek-r1
                # style APIs) stream chain-of-thought under a separate
                # `reasoning_content` field instead of `content`. We deliberately
                # do NOT treat this as the actual answer (too unstructured to
                # safely surface to users) — just capture it so we can tell,
                # when `content` ends up empty, whether the model "thought but
                # never answered" vs. produced literally nothing at all.
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    reasoning_parts.append(delta["reasoning_content"])
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
        body_dict = {"choices": [{"message": message, "finish_reason": finish_reason or "stop"}]}
        # ── Fallback: estimate token usage if API didn't return it ──
        # Most streaming endpoints don't include `usage` by default. If
        # stream_options.include_usage didn't work, estimate from content
        # length (~4 chars/token, same heuristic OpenAI uses for tiktoken
        # rough estimates).
        if stream_usage and isinstance(stream_usage, dict):
            body_dict["usage"] = stream_usage
        else:
            content_str = "".join(content_parts)
            est_completion = max(1, len(content_str) // 4)
            # Prompt tokens: estimate from total message content sent.
            # The caller's messages are not accessible here, so we use a
            # rough estimate based on content length + overhead.
            est_prompt = max(1, est_completion * 3)  # prompt usually > completion
            body_dict["usage"] = {
                "total_tokens": est_prompt + est_completion,
                "prompt_tokens": est_prompt,
                "completion_tokens": est_completion,
            }
            body_dict["usage_estimated"] = True  # flag for logging
        body = json_module.dumps(body_dict)
        _elapsed = _first_chunk_time - _time.time() if _first_chunk_time else 0
        _total_chars = sum(len(p) for p in content_parts)
        _reasoning_chars = sum(len(p) for p in reasoning_parts)
        if _deadline_hit:
            print(f"📦 串流因截止時間中斷（部分內容）：{_chunk_count} chunks, content={_total_chars} chars")
        else:
            print(f"📦 串流完成：{_chunk_count} chunks, content={_total_chars} chars, reasoning={_reasoning_chars} chars, tool_calls={len(tool_calls_acc)}")
        if _total_chars == 0 and _reasoning_chars > 0:
            print(f"⚠️ 模型只輸出了 reasoning_content（思考過程）但最終 content 完全空白——"
                  f"這是模型/API 本身「只想不答」，不是我們的串流解析漏抓")
        return 200, body

    # ── 池式降級鏈：每項可有不同 API 端點 ──
    # 定義在 _attempt 外層，讓 fallback 邏輯也能存取
    _pool_chain = _resolve_chain("main", chat_ai_settings)

    async def _attempt():
        """One full attempt: streaming call, with fallbacks for endpoints
        that don't support streaming or don't support `tools`. Raises on
        unrecoverable failure; otherwise returns the assistant message dict
        (which may legitimately have empty content — the outer retry loop
        decides whether that's worth retrying)."""
        # ── Model fallback chain ──
        # Parse the comma-separated model list from settings. The first
        # entry is the primary model (same as settings["model"]); subsequent
        # entries are fallbacks tried only when the primary returns 401
        # (key not authorized for that model) or 503/502/504 (model down
        # or overloaded). This stays on the SAME API endpoint + key —
        # it's a cheap retry, much faster than the full backup API switchover.
        _chain_raw = settings.get("model_fallback_chain", "").strip()
        _primary_model = settings.get("model", "gpt-4o-mini")
        if _chain_raw:
            _model_chain = [m.strip() for m in _chain_raw.split(",") if m.strip()]
            if _primary_model not in _model_chain:
                _model_chain.insert(0, _primary_model)
        else:
            _model_chain = [_primary_model]

        payload = {
            "model": _model_chain[0],
            "messages": messages,
            "temperature": 0.7,
            # Default kept low (300) for normal quick chat replies. Callers that
            # need longer structured output (name rating, daily/weekly briefings,
            # etc.) should pass a higher max_tokens explicitly — otherwise
            # reasoning-style models (e.g. nvidia/nemotron) can burn the entire
            # budget on internal "The user wants me to..." preamble before ever
            # reaching the actual requested format, causing silent truncation.
            "max_tokens": max_tokens,
            "stream_options": {"include_usage": True},
        }
        # ── Reasoning 模型控制 ──
        # GLM-5.2 等 reasoning 模型：根據呼叫類型動態調整思考強度。
        # 注意：timeout 的調整已經在函式最上方（_deadline 計算之前）完成，
        # 這裡只負責把 reasoning 參數塞進 payload，不能在這個巢狀函式
        # (_attempt) 裡對外層的 timeout_total/timeout_read 賦值 ——
        # 那樣做會讓 Python 把它們當成 _attempt 的本地變數，導致還沒賦值
        # 就被讀取而拋出 UnboundLocalError（這正是先前部署崩潰的根因）。
        _reasoning_effort = _get_reasoning_effort(fallback_mode, category)
        _reasoning_params = _build_reasoning_params(_reasoning_effort)
        # ── 短預算跳過 reasoning ──
        # reasoning（思考）模式讓模型在生成前先「想」，輕鬆吃掉 10-15 秒。
        # 聊天路徑只有 ~15-20s 預算，開 reasoning 等於保證逾時——模型還在想
        # 就被 timeout 切斷了。只有預算 >30s 的場景（海龜湯50s、占卜40s
        # 等背景任務）才值得開 reasoning。
        _budget_for_reasoning = _remaining_primary(floor=0)
        _reasoning_endpoint_ok = api_url not in _reasoning_unsupported_apis
        if _reasoning_params and _reasoning_endpoint_ok and _budget_for_reasoning > 30:
            payload.update(_reasoning_params)
            _diag.append(f"🧠 reasoning_effort={_reasoning_effort} (budget {_budget_for_reasoning:.0f}s)")
        elif _reasoning_endpoint_ok:
            # ── FIX：關閉 reasoning 時要「明確要求關閉」，不能只是不送參數 ──
            # 舊邏輯在這裡什麼都不送，賭 API/模型預設就是不思考的。這個假設
            # 對很多模型成立，但對 gpt-oss-120b/20b、部分 glm/deepseek build
            # 這種「預設就會思考」的 reasoning 模型完全錯誤——不送參數只是
            # 沿用它自己的預設值（往往仍是開著思考），短預算聊天請求下
            # 輕則 max_tokens 被思考吃光回應空白，重則思考期間完全不吐字
            # 觸發 socket 逾時（表現得像連線失敗，實際上模型只是還在想）。
            # 明確送出關閉信號才能真正改變行為；已知會拒絕未知欄位的端點
            # 已被 _reasoning_unsupported_apis 排除在外，不會受影響。
            payload.update(_build_reasoning_disable_params())
            if _reasoning_params:
                _diag.append(f"⏭️ reasoning 明確關閉（預算 {_budget_for_reasoning:.0f}s < 30s）")
        else:
            _diag.append("⏭️ reasoning 略過參數（端點不支援 reasoning 控制欄位）")
        if use_tools:
            payload["tools"] = use_tools
            payload["tool_choice"] = "auto"

        ok = False
        data = None
        status = None
        body_text = ""
        _t0 = _time.time()
        _tools_already_stripped = False
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
                _tools_already_stripped = True
                try:
                    status, body_text = await _post(payload)
                except (asyncio.TimeoutError, Exception) as e2:
                    # 拿掉 tools 後還是逾時/失敗 —— 不要往外拋，讓下面的「模型降級鏈」
                    # 邏輯有機會先在同一組 API 上試其他模型，而不是直接跳去備援 API。
                    print(f"⚠️ 移除 tools 後仍逾時/錯誤（{type(e2).__name__}: {e2}），視為失敗狀態，改走模型降級鏈")
                    status = -1  # sentinel：代表是連線例外，不是真的 HTTP 狀態碼
                    body_text = f"{type(e2).__name__}: {e2}"
            else:
                # ── FIX：主要請求逾時（asyncio.TimeoutError／連線例外）時，
                # 之前這裡會直接 raise，導致整個 model_fallback_chain（同一組
                # API 上的其他模型）完全被跳過，逾時等同直接跳去備援 API。
                # 現在改成把它當成一次「失敗」狀態，繼續往下走，讓下面的
                # 「if not ok:」降級鏈邏輯有機會先在同一組 API 上試過整條
                # model_fallback_chain，只有整條鏈都失敗才會真的往外拋、
                # 交給更外層的備援 API 處理。
                print(f"⚠️ 主要請求逾時/錯誤（{type(e).__name__}: {e}），視為失敗狀態，改走模型降級鏈")
                status = -1  # sentinel：代表是連線例外，不是真的 HTTP 狀態碼
                body_text = f"{type(e).__name__}: {e}"

        if status == 200:
            try:
                data = json_module.loads(body_text)
                if "choices" in data:
                    # Check if streaming returned empty content — either the
                    # API doesn't support streaming (returned a regular JSON
                    # response _read_stream couldn't parse as SSE), OR the
                    # model itself genuinely produced no answer this time
                    # (finish_reason=stop with hollow content — a known
                    # intermittent quirk on some free/weak reasoning-model
                    # endpoints, not something a non-streaming retry fixes,
                    # but worth trying since some endpoints DO behave
                    # differently between the two modes).
                    _msg = data["choices"][0].get("message", {})
                    _content = _msg.get("content", "")
                    _tc = _msg.get("tool_calls")
                    if not _content and not _tc:
                        _fb_budget = _remaining()
                        if _fb_budget < 2:
                            print(f"⚠️ 串流回應為空，但剩餘時間不足（{_fb_budget:.1f}s），放棄非串流回退")
                        else:
                            print(f"⚠️ 串流回應為空（可能是 API 不支援 streaming，或本次回應本身就是空的），回退為非串流模式重試（剩餘預算 {_fb_budget:.1f}s）...")
                            payload_ns = dict(payload)
                            payload_ns.pop("stream", None)
                            t2 = aiohttp.ClientTimeout(total=_fb_budget, connect=min(10, _fb_budget), sock_read=_fb_budget)

                            async def _do_fallback_post():
                                async with aiohttp.ClientSession() as sess:
                                    async with sess.post(api_url, json=payload_ns, headers=headers, timeout=t2) as resp2:
                                        if resp2.status == 200:
                                            body_text_ = await resp2.text()
                                            data_ = json_module.loads(body_text_)
                                            return resp2.status, body_text_, data_
                                        return resp2.status, "", None

                            if is_background:
                                async with _AI_BG_SEMAPHORE:
                                    _status2, _body2, _data2 = await _do_fallback_post()
                            else:
                                _status2, _body2, _data2 = await _do_fallback_post()

                            if _status2 == 200 and _data2 and "choices" in _data2:
                                # Strip reasoning_content from non-streaming response
                                # (reasoning models put thinking here, not in content)
                                _ns_msg = _data2["choices"][0].get("message", {})
                                if "reasoning_content" in _ns_msg:
                                    _ns_msg.pop("reasoning_content", None)
                                data = _data2
                                ok = True
                                if use_tools:
                                    _tools_supported_apis.add(api_url)
                                    save_tools_supported()
                                _fb_content = data["choices"][0].get("message", {}).get("content", "")
                                if _fb_content:
                                    print(f"✅ 非串流回退成功，取得 {len(_fb_content)} chars")
                                else:
                                    print(f"⚠️ 非串流回退回應仍是空的（確認是 API/模型本身沒答案，非串流解析問題）")
                            else:
                                print(f"⚠️ 非串流回退也失敗：status={_status2}")
                    else:
                        ok = True
                        if use_tools:
                            _tools_supported_apis.add(api_url)
                            save_tools_supported()
            except Exception as e:
                print(f"⚠️ 解析回應失敗：{e}")

        if not ok and use_tools and not _tools_already_stripped:
            # Endpoint returned a non-200 or malformed response WITH tools —
            # assume it doesn't support function calling and never try again.
            # (若上面的例外處理已經先剝過 tools 重試過一次，這裡就不用重複做。)
            print(f"⚠️ Chat AI 端點帶 tools 參數呼叫失敗（status={status}），之後略過 tools：{body_text[:200]}")
            _tools_unsupported_apis.add(api_url)
            save_tools_unsupported()
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
            try:
                status, body_text = await _post(payload)
                if status == 200:
                    try:
                        data = json_module.loads(body_text)
                        ok = "choices" in data
                    except Exception:
                        ok = False
            except (asyncio.TimeoutError, Exception) as e3:
                print(f"⚠️ 移除 tools 後仍逾時/錯誤（{type(e3).__name__}: {e3}），視為失敗狀態，改走模型降級鏈")
                status = -1
                body_text = f"{type(e3).__name__}: {e3}"

        if not ok:
            _status_label = "連線逾時/例外" if status == -1 else f"HTTP {status}"
            # ── Model fallback chain: try next model on ANY failure status ──
            # Started out only retrying on 401/403/503/502/504 (auth/overload),
            # but real-world proxies also throw model-specific 400s like
            # "模型 X 不支援參數: stream" — a per-model quirk, not a real bad
            # request. Since we've already committed to hunting for a working
            # model once the primary one failed, there's no upside to being
            # picky about the status code: any non-200 just means "this
            # particular model didn't work", so always try the rest of the
            # chain (bounded by the remaining time-budget check below anyway).
            #
            # ── Skip the chain entirely in two cases ──
            # 1. Administrative calls (fallback_mode == "full", e.g. proposal
            #    review, membership application checks) — these can't afford
            #    to burn time cycling through weak free models one by one;
            #    go straight to the (stronger) backup API on the first failure.
            # 2. Owner calls, when owner_skip_model_chain is enabled — the
            #    owner's own backup API (e.g. their personal Gemini) is
            #    typically much stronger than anything in the free-model
            #    degradation chain, so there's no point trying those first.
            _is_owner_call = bool(fallback_user_id) and str(fallback_user_id) == str(BOT_OWNER_ID)
            _skip_chain_for_admin = (fallback_mode == "full")
            _skip_chain_for_owner = _is_owner_call and settings.get("owner_skip_model_chain", True)
            # ── FIX：逾時不跳降級鏈 ──
            # 之前 owner 和行政功能遇到任何失敗（包括逾時）都直接跳到備援 API，
            # 但逾時跟 401/503 不同——一個模型慢不代表降級鏈裡其他模型也慢，
            # 它們可能在完全不同的後端上。備援 API 有配額/成本限制，
            # 免費降級鏈模型應該先有機會試。只有明確的 auth/server 錯誤
            # （401/403/502/503/504）才跳過降級鏈直接備援。
            _is_timeout_failure = (status == -1 or
                                   "timeout" in body_text.lower() or "Timeout" in body_text or
                                   "逾時" in body_text or "Connection" in body_text)
            _skip_model_chain = (_skip_chain_for_admin or _skip_chain_for_owner) and not _is_timeout_failure
            if _skip_model_chain and len(_model_chain) > 1:
                _why = "行政功能" if _skip_chain_for_admin else "擁有者跳過降級"
                _diag.append(f"⏭️ 跳過降級鏈（{_why}），直接備援（{_status_label}）")
                print(f"⏭️ 跳過模型降級鏈（{_why}），直接交由備援 API 處理（{_status_label}）")
            if _is_timeout_failure and not _skip_model_chain and len(_model_chain) > 1:
                _diag.append(f"🔄 逾時不走捷徑，先試降級鏈（{_status_label}）")
                print(f"🔄 主模型逾時，不跳過降級鏈，先試免費模型再考慮備援（{_status_label}）")
            _model_retryable = (not _skip_model_chain) and len(_model_chain) > 1
            if _model_retryable:
                for _mi in range(1, len(_model_chain)):
                    if _remaining_primary() < 2:
                        print(f"⏱️ 模型降級鏈：剩餘時間不足（已進入備援 API 保留時段），跳過 {_model_chain[_mi]}")
                        break
                    _alt_model = _model_chain[_mi]
                    _diag.append(f"🔄 降級：{payload['model']} → {_alt_model}（{_status_label}）")
                    print(f"🔄 模型降級：{payload['model']} → {_alt_model}（{_status_label}）")
                    payload["model"] = _alt_model
                    try:
                        status, body_text = await _post(payload)
                        print(f"⏱️ 模型降級 {_alt_model}：status={status}, 耗時 {_time.time()-_t0:.1f}s")
                        if status == 200:
                            try:
                                data = json_module.loads(body_text)
                                if "choices" in data:
                                    _msg = data["choices"][0].get("message", {})
                                    if _msg.get("content") or _msg.get("tool_calls"):
                                        ok = True
                                        _used_model = _alt_model
                                        _diag.append(f"✅ 降級成功：{_alt_model}")
                                        print(f"✅ 模型降級成功：{_alt_model}")
                                        break
                                    # Empty content but 200 — try non-streaming
                                    # (primary phase — must not dip into the
                                    # reserved backup-API slice)
                                    _ns_budget = min(_remaining_primary(), _max_single_attempt)
                                    if _ns_budget >= 2:
                                        payload_ns = dict(payload)
                                        payload_ns.pop("stream", None)
                                        t2 = aiohttp.ClientTimeout(total=_ns_budget, connect=min(10, _ns_budget), sock_read=_ns_budget)
                                        if is_background:
                                            async with _AI_BG_SEMAPHORE:
                                                async with aiohttp.ClientSession() as sess:
                                                    async with sess.post(api_url, json=payload_ns, headers=headers, timeout=t2) as resp2:
                                                        if resp2.status == 200:
                                                            data = json_module.loads(await resp2.text())
                                                            if "choices" in data and data["choices"][0].get("message", {}).get("content"):
                                                                ok = True
                                                                _used_model = _alt_model
                                                                _diag.append(f"✅ 降級成功（非串流）：{_alt_model}")
                                                                print(f"✅ 模型降級 {_alt_model} 非串流成功")
                                                                break
                            except Exception:
                                pass
                    except Exception as _me:
                        _diag.append(f"⚠️ 降級 {_alt_model} 失敗：{str(_me)[:80]}")
                        print(f"⚠️ 模型降級 {_alt_model} 也失敗：{_me}")
                        continue
            if not ok:
                raise Exception(f"Chat AI API returned {status}: {body_text[:300]}")

        _track_token_usage(data)
        _ai_circuit_success()  # successful call — reset the 403 counter
        _used_model = payload.get("model", settings.get("model", "?"))
        return {**data["choices"][0]["message"], "_used_model": _used_model, "_used_fallback": False, "_diag": _diag}

    # ── Retry once on a hollow result ──
    # Two real failure modes show up in production with free/weak API
    # endpoints (e.g. nvidia/nemotron): (1) a transient connection hiccup
    # that raises an exception, and (2) a 200 response with
    # finish_reason=stop but a completely empty message — no content, no
    # tool_calls. The latter is sampling noise from the model, not a
    # permanent error (a fresh request with the same prompt often just
    # works), so it's worth one automatic retry before giving up.
    last_exc = None
    msg = None
    for _attempt_i in range(2):
        # If the circuit breaker tripped during a previous attempt's 403,
        # stop retrying — further attempts will just hit the same block.
        if not _ai_circuit_check():
            return {"content": "", "error": _get_circuit_cooldown_msg(), "circuit_open": True}
        # ── Deadline guard: don't even START a retry if there isn't
        # meaningfully enough time left for a network round-trip. This is
        # what actually caps total wall-clock time at ~timeout_total — without
        # it, a retry always fired regardless of how much budget the FIRST
        # attempt already burned, routinely doubling total latency.
        if _attempt_i > 0 and _remaining_primary() < 1.5:
            print(f"⏱️ 剩餘時間不足（已進入備援 API 保留時段），放棄重試，直接回傳目前結果")
            break
        try:
            msg = await _attempt()
        except Exception as e:
            last_exc = e
            # If the circuit breaker just tripped (403 anomalous), don't retry
            if _ai_circuit_breaker["tripped"]:
                print(f"🚫 AI 熔斷器因 403 觸發，停止重試")
                return {"content": "", "error": _get_circuit_cooldown_msg(), "circuit_open": True}
            if _attempt_i == 0 and _remaining_primary() >= 1.5:
                print(f"⚠️ Chat AI 呼叫失敗（{e}），重試一次（primary 剩餘 {_remaining_primary():.1f}s）...")
                continue
            print(f"⚠️ Chat AI 呼叫失敗且無剩餘時間重試（{e}）")
            msg = {"content": "", "error": f"AI 回應逾時或失敗：{e}"}
            break
        if not msg.get("content") and not msg.get("tool_calls") and _attempt_i == 0 and _remaining_primary() >= 1.5:
            print(f"⚠️ AI 回應為空（finish_reason=stop 但沒有實際內容），重試一次（primary 剩餘 {_remaining_primary():.1f}s）...")
            continue
        if msg.get("content") or msg.get("tool_calls"):
            if "_used_model" not in msg:
                msg["_used_model"] = _used_model or settings.get("model", "?")
                msg["_used_fallback"] = _used_fallback
            if "_diag" not in msg:
                msg["_diag"] = _diag
            try: _record_api_outcome(True)
            except: pass
            return msg
        # Empty result without exception — let fallback handle it
        break
    # ── Fallback API ──
    # ── 池式降級鏈：每項可有不同 API 端點 ──
    if msg is not None and not msg.get("content") and not msg.get("tool_calls") and _pool_chain and not settings.get("_skip_pool_chain", False):
        _pool_err = msg.get("error", "") if msg else ""
        _is_pool_err = any(c in (_pool_err or "") for c in ["503", "502", "500", "504", "401", "403", "timeout", "Timeout", "逾時", "Connection"])
        if _is_pool_err:
            for _pc_url, _pc_key, _pc_model in _pool_chain:
                if _remaining() < 3:
                    break
                _diag.append(f"🔗 池降級鏈：嘗試 {_pc_model}（{_pc_url[:40]}...）")
                print(f"🔗 池降級鏈：嘗試 {_pc_model} @ {_pc_url[:60]}")
                _pc_settings = {
                    **settings,
                    "api_url": _pc_url,
                    "api_key": _pc_key,
                    "model": _pc_model,
                    "fallback_enabled": False,  # 池降級鏈內不再觸發備援，避免無限遞迴
                    "_skip_pool_chain": True,   # 遞迴呼叫不再進入池降級鏈，避免無限遞迴
                }
                _pc_budget = int(_remaining())
                if _pc_budget < 3:
                    _diag.append(f"⏱️ 時間不足放棄池降級（剩 {_remaining():.1f}s）")
                    break
                try:
                    _pc_msg = await call_chat_api(
                        messages, _pc_settings, tools=tools,
                        max_tokens=max_tokens,
                        timeout_total=_pc_budget,
                        timeout_read=max(2, _pc_budget - 1),
                        is_background=is_background,
                        fallback_mode="disabled",
                    )
                    if _pc_msg and (_pc_msg.get("content") or _pc_msg.get("tool_calls")):
                        _pc_msg["_used_fallback"] = True
                        _pc_msg["_used_model"] = _pc_model
                        _pc_msg["_diag"] = _diag + [f"✅ 池降級鏈成功：{_pc_model}"]
                        print(f"✅ 池降級鏈成功！({_pc_model})")
                        return _pc_msg
                except Exception as _pc_e:
                    print(f"⚠️ 池降級鏈 {_pc_model} 失敗：{_pc_e}")
                    _diag.append(f"⚠️ 池降級鏈 {_pc_model} 失敗：{str(_pc_e)[:80]}")

    # If the primary API returned a provider-side error (503, 502, 500,
    # 504, timeout) and a fallback API is configured, retry the entire
    # call against the fallback endpoint instead of giving up.
    _primary_error = None
    if msg is not None and not msg.get("content") and not msg.get("tool_calls") and msg.get("error"):
        _primary_error = msg.get("error", "")
    elif last_exc is not None:
        _primary_error = str(last_exc)
    if _primary_error and settings.get("fallback_enabled") and fallback_mode != "disabled":
        _is_provider_error = any(
            code in _primary_error
            for code in ["503", "502", "500", "504", "401", "403", "400",
                        "Service Unavailable",
                        "Bad Gateway", "Internal Server Error",
                        "Gateway Timeout", "timeout", "Timeout",
                        "逾時", "Connection", "connection",
                        "Bad Request"]
        )
        if _is_provider_error:
            # Owner exemption — skip rate limit AND daily quota for the bot
            # owner so they're never throttled by their own bot's fallback limits
            _owner_exempt = settings.get("fallback_owner_exempt", True) and str(BOT_OWNER_ID) == fallback_user_id
            if _owner_exempt:
                print(f"👑 擁有者豁免備援限速與配額（用戶 {fallback_user_id}）")

            # Rate limiter for chat fallback (gate 1)
            _fb_gate_ok = True
            _fb_rate_limit = settings.get("fallback_rate_per_min", 6)
            if not _owner_exempt and fallback_mode == "rate_limited" and not _check_fallback_chat_rate(_fb_rate_limit):
                _fb_rate_msg = settings.get("fallback_rate_limit_msg", "⚠️ 備援 API 請求過於頻繁，請稍等一下再試～")
                _diag.append(f"⚠️ 備援速率限制（{_fb_rate_limit}/min）被拒")
                print(f"⚠️ 備援 API 速率限制（{_fb_rate_limit}/min），聊天備援請求被拒絕")
                return {"content": _fb_rate_msg, "error": "rate_limit_exceeded"}

            # Daily per-user quota (gate 2) — only applies to rate_limited mode
            if not _owner_exempt and _fb_gate_ok and fallback_mode == "rate_limited" and fallback_user_id:
                _daily_limit = settings.get("fallback_daily_limit", 10)
                if not _check_fallback_daily_limit(fallback_user_id, _daily_limit):
                    _diag.append(f"⚠️ 備援每日上限已達（{_daily_limit}/天）")
                    print(f"⚠️ 備援 API 每日上限已達（用戶 {fallback_user_id}，上限 {_daily_limit}/天）")
                    _fb_daily_msg = settings.get("fallback_daily_limit_msg", FALLBACK_DAILY_LIMIT_MSG)
                    return {"content": _fb_daily_msg, "error": "daily_limit_exceeded"}
                else:
                    _daily_remaining = _get_fallback_daily_remaining(fallback_user_id, _daily_limit)
                    print(f"✅ 備援 API 每日配額通過（用戶 {fallback_user_id}，今日剩餘 {_daily_remaining}/{_daily_limit}）")

            # Both gates passed — actually call the fallback API
            if _fb_gate_ok:
                # 優先用池解析的 "backup" 角色，回退到 legacy fallback_api_*
                _fb_url, _fb_key, _fb_model = _resolve_role_endpoint("backup", chat_ai_settings)
                if not _fb_url:
                    _fb_url = settings.get("fallback_api_url", "").strip()
                    _fb_key = settings.get("fallback_api_key", "").strip()
                    _fb_model = settings.get("fallback_model", "").strip()
                if _fb_url and _fb_key:
                    _diag.append(f"🔄 主 API 錯誤：{_primary_error[:100]}")
                    _diag.append(f"🔄 切換備援 API：{_fb_model or _fb_url}")
                    print(f"🔄 主要 API 錯誤（{_primary_error[:120]}），切換至備援 API（{_fb_model or _fb_url}）...")
                    _fb_settings = {
                        **settings,
                        "api_url": _fb_url,
                        "api_key": _fb_key,
                        "model": _fb_model or settings.get("model", "gpt-4o-mini"),
                        "fallback_enabled": False,  # prevent infinite recursion
                        "_skip_pool_chain": True,   # 備援API遞迴也不進入池降級鏈
                    }
                    # Bug fix: this used to be `max(5, int(_remaining()))` —
                    # since `_remaining()` never drops below its 0.5 floor,
                    # that ALWAYS forced at least a 5s fallback attempt no
                    # matter how little time was truly left, guaranteeing it
                    # blew past the outer generate_chat_reply deadline and got
                    # hard-cancelled mid-request — burning a real network call
                    # for nothing and still surfacing "⏰ 回覆逾時" to the user.
                    # Now: only attempt the fallback if there's genuinely
                    # enough time left for a real round-trip (>=3s); otherwise
                    # skip cleanly and let the caller's own timeout messaging
                    # handle it, rather than starting a doomed request.
                    _fb_budget = int(_remaining())
                    if _fb_budget < 3:
                        _diag.append(f"⏱️ 時間不足放棄備援（剩 {_remaining():.1f}s）")
                        print(f"⏱️ 剩餘時間不足（{_remaining():.1f}s），放棄備援 API，避免發出注定被取消的請求")
                    else:
                        try:
                            _fb_msg = await call_chat_api(
                                messages, _fb_settings, tools=tools,
                                max_tokens=max_tokens,
                                timeout_total=_fb_budget,
                                timeout_read=max(2, _fb_budget - 1),
                                is_background=is_background,
                                fallback_mode="disabled",  # fallback of fallback = no
                            )
                            if _fb_msg.get("content") or _fb_msg.get("tool_calls"):
                                _fb_msg["_used_model"] = _fb_model or settings.get("model", "?")
                                _fb_msg["_used_fallback"] = True
                                _fb_msg["_diag"] = _diag + [f"✅ 備援 API 成功：{_fb_model or _fb_url}"]
                                print(f"✅ 備援 API 成功！({_fb_msg.get('content', '')[:60]}...)")
                                try: _record_api_outcome(True)
                                except: pass
                                return _fb_msg
                            else:
                                _diag.append(f"⚠️ 備援 API 也失敗：{_fb_msg.get('error', 'unknown')[:80]}")
                                print(f"⚠️ 備援 API 也失敗：{_fb_msg.get('error', 'unknown')}")
                        except Exception as _fb_exc:
                            _diag.append(f"⚠️ 備援 API 例外：{str(_fb_exc)[:80]}")
                            print(f"⚠️ 備援 API 例外：{_fb_exc}")
                else:
                    print(f"⚠️ 備援 API 已啟用但未設定 URL/Key，跳過")

    # Ran out of retry budget without an exception (e.g. broke out of the
    # loop above) — return whatever we have, or a clean timeout error.
    if msg is not None:
        if "_used_model" not in msg:
            msg["_used_model"] = _used_model or settings.get("model", "?")
            msg["_used_fallback"] = _used_fallback
        if "_diag" not in msg:
            msg["_diag"] = _diag
        try: _record_api_outcome(bool(msg.get("content") or msg.get("tool_calls")))
        except: pass
        return msg
    _diag.append(f"❌ 最終失敗：{str(last_exc)[:100] if last_exc else '逾時'}")
    try: _record_api_outcome(False)
    except: pass
    if last_exc:
        return {"content": "", "error": f"AI 回應逾時或失敗：{last_exc}", "_used_model": _used_model or settings.get("model", "?"), "_used_fallback": _used_fallback, "_diag": _diag}
    return {"content": "", "error": "AI 回應逾時", "_used_model": _used_model or settings.get("model", "?"), "_used_fallback": _used_fallback, "_diag": _diag}


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

    # ⚠️ 重要提醒：身分組/成員列表可能滯後（快取每 10 分鐘更新一次）
    lines.append(
        "\n⚠️ 注意：上面的身分組和成員資訊是快取的，可能不是最新狀態。"
        "\n如果使用者問到「誰是某某職位」「誰當選了」「最新的XX是誰」等人事問題，"
        "\n請以 Discord 搜尋結果（如果有被注入到上下文）為準，不要僅憑身分組列表回答。"
        "\n身分組列表只反映快取當下的狀態，人事變動可能還沒同步到。"
    )

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
        await asyncio.sleep(chat_ai_settings.get("forum_index_interval", 900))


async def channel_index_refresh_loop():
    """Background task: refresh the channel-embed index every 30 minutes.
    Scans all text channels for messages containing embeds (announcements,
    official notices, etc.) that Discord's search API can't find because
    it doesn't index embed content. Staggered across channels to avoid
    rate limits."""
    await asyncio.sleep(120)  # Wait a bit longer than forum index
    while True:
        try:
            for guild in bot.guilds:
                await _refresh_channel_index(guild)
                await asyncio.sleep(2)  # stagger between guilds
        except Exception as e:
            print(f"⚠️ 頻道 Embed 索引背景更新失敗：{e}")
        await asyncio.sleep(chat_ai_settings.get("channel_index_interval", 1800))


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


# ── 對話紀錄發送診斷（owner 無法直接看 Render log，靠這個自我診斷）──
# 每次 _send_chat_log 呼叫都會更新這個計數器，透過 /chat log_debug 查看，
# 失敗時也會私訊擁有者（有速率限制避免洗版）。
_log_send_stats: dict = {
    "attempts": 0,
    "successes": 0,
    "failures": 0,
    "skips": 0,
    "last_error": "",
    "last_success_at": "",
    "last_failure_at": "",
    "last_skip_reason": "",
    "last_skip_at": "",
}
_last_log_failure_dm_sent = 0.0  # epoch time — 限速：最多每10分鐘私訊一次擁有者


async def _notify_owner_log_failure(reason: str):
    """對話紀錄發送失敗時私訊擁有者，讓擁有者不需要 Render log 存取權限
    也能即時知道 ai-log 又故障了。速率限制：最多每10分鐘一次。"""
    global _last_log_failure_dm_sent
    now = _time.time()
    if now - _last_log_failure_dm_sent < 600:  # 10分鐘內已經私訊過，跳過
        return
    _last_log_failure_dm_sent = now
    try:
        owner = bot.get_user(BOT_OWNER_ID)
        if not owner:
            owner = await bot.fetch_user(BOT_OWNER_ID)
        if owner:
            await owner.send(
                f"⚠️ **AI 對話紀錄發送失敗**\n"
                f"原因：{reason[:500]}\n\n"
                f"這代表 ai-log 頻道目前收不到對話紀錄。"
                f"用 `/chat log_debug` 查看詳細診斷，或用 `/chat log_test` 手動測試。"
            )
    except Exception as e:
        print(f"⚠️ 私訊擁有者失敗（連通知都發不出去）：{e}")


async def _resolve_log_channel(guild):
    """Resolve the configured log channel, with cache-miss fallback to a live fetch.
    Returns (channel_or_None, error_reason_or_None)."""
    log_ch_id = chat_ai_settings.get("log_channel_id")
    if not log_ch_id:
        return None, "未設定 log_channel_id"
    if not guild:
        return None, "沒有 guild 物件"

    # CRITICAL: log_channel_id may be stored as a string (e.g. from dashboard API).
    # guild.get_channel() and guild.fetch_channel() both expect an int — a string
    # key will silently miss the cache and may fail the API call. Always convert.
    try:
        log_ch_id = int(log_ch_id)
    except (TypeError, ValueError):
        return None, f"log_channel_id 無法轉為整數（值={log_ch_id!r}，型態={type(log_ch_id).__name__}）"

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


async def _send_chat_log(message, user_content: str, ai_reply: str, channel_name: str = "", model_info: dict = None):
    """Send a conversation log to the designated log channel.
    model_info: {"model": str, "fallback": bool, "diag": list} — which model/API answered
    and the full degradation/error diagnostic trail, for API status monitoring.

    每一次呼叫都會更新 _log_send_stats（可用 /chat log_debug 查看），失敗時
    會私訊擁有者（速率限制每10分鐘一次）——因為擁有者沒有 Render log 存取權限，
    "print 到 stderr 但沒人看" 等於沒發生過，這個機制讓失敗變成看得到的事件。"""
    global _log_send_stats
    _log_send_stats["attempts"] += 1

    def _mark_skip(reason: str):
        _log_send_stats["skips"] += 1
        _log_send_stats["last_skip_reason"] = reason
        _log_send_stats["last_skip_at"] = _now_iso()

    def _mark_fail(reason: str):
        _log_send_stats["failures"] += 1
        _log_send_stats["last_error"] = reason[:500]
        _log_send_stats["last_failure_at"] = _now_iso()

    def _mark_success():
        _log_send_stats["successes"] += 1
        _log_send_stats["last_success_at"] = _now_iso()

    if not chat_ai_settings.get("log_channel_id"):
        _mark_skip("log_channel_id 未設定")
        return  # not configured, nothing to do
    if not message.guild:
        print("⚠️ 對話紀錄：訊息沒有 guild（私訊？），略過")
        _mark_skip("訊息沒有 guild（私訊）")
        return

    try:
        log_ch, err = await _resolve_log_channel(message.guild)
    except Exception as e:
        print(f"⚠️ 對話紀錄發送失敗（_resolve_log_channel 例外）：{e}")
        _mark_fail(f"_resolve_log_channel 例外：{e}")
        asyncio.ensure_future(_notify_owner_log_failure(f"_resolve_log_channel 例外：{e}"))
        return
    if not log_ch:
        print(f"⚠️ 對話紀錄發送失敗：{err}")
        _mark_fail(f"頻道解析失敗：{err}")
        asyncio.ensure_future(_notify_owner_log_failure(f"頻道解析失敗：{err}"))
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

        # Build model/API status label for the embed
        _model_name = "?"
        _api_label = "主 API"
        _diag_lines = []
        if model_info:
            _model_name = model_info.get("model") or "?"
            if model_info.get("fallback"):
                _api_label = "🔄 備援 API"
            _diag_lines = model_info.get("diag", [])

        # Determine embed color: blue=normal, orange=backup, red=error
        _embed_color = discord.Color.blue()
        if model_info and model_info.get("fallback"):
            _embed_color = discord.Color.orange()
        if _diag_lines and any("❌" in d or "⚠️" in d for d in _diag_lines):
            _embed_color = discord.Color.orange()

        embed = discord.Embed(
            title="💬 AI 對話紀錄",
            color=_embed_color,
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

        # ── API 診斷日誌 field ──
        # Show the full degradation/error trail in the same embed:
        # model downgrades, fallback switches, timeouts, circuit breaker, etc.
        if _diag_lines:
            # Join all diag lines, truncate to Discord's 1024-char field value limit
            _diag_text = "\n".join(_diag_lines)
            # Reserve 8 chars for the "```\n" / "\n```" wrapper added below,
            # so the final field value never exceeds Discord's 1024 limit.
            if len(_diag_text) > 1010:
                _diag_text = _diag_text[:1010] + "..."
            embed.add_field(
                name="📋 API 診斷",
                value="```\n" + _diag_text + "\n```",
                inline=False
            )

        # ── 視覺模型診斷 field ──
        # Show vision API status: which model was used, latency, degradation, errors
        _vision_lines = model_info.get("vision_diag", []) if model_info else []
        if _vision_lines:
            _vdiag_text = "\n".join(_vision_lines)
            # Same 8-char wrapper reservation as the API diag field above.
            if len(_vdiag_text) > 1010:
                _vdiag_text = _vdiag_text[:1010] + "..."
            embed.add_field(
                name="📷 識圖診斷",
                value="```\n" + _vdiag_text + "\n```",
                inline=False
            )

        ch_name = channel_name or (message.channel.name if hasattr(message.channel, "name") else "?")
        _vision_tag = " | 識圖: 有" if _vision_lines else (" | 識圖: 無圖片" if not message.attachments else " | 識圖: 失敗")
        embed.set_footer(text=f"#{ch_name} | {_api_label} | 模型: {_model_name}{_vision_tag} | User ID: {author.id}")
        try:
            await log_ch.send(embed=embed)
            print(f"📝 對話紀錄已發送到 #{log_ch.name}（模型={_model_name}, {_api_label}, 診斷={len(_diag_lines)}筆）")
            _mark_success()
        except discord.HTTPException as http_err:
            # Defensive fallback: any embed validation error (e.g. a field
            # exceeding Discord's length limits) must NOT lose the log entry
            # entirely. Retry with a minimal embed (just the conversation,
            # no diag fields) so the core record still gets through.
            print(f"⚠️ 完整對話紀錄發送失敗（{http_err}），改用精簡版重試...")
            try:
                minimal_embed = discord.Embed(
                    title="💬 AI 對話紀錄（精簡版 — 完整版超出長度限制）",
                    color=_embed_color,
                    timestamp=discord.utils.utcnow(),
                )
                minimal_embed.add_field(name=f"👤 {author.display_name}", value=f"> {user_text}", inline=False)
                minimal_embed.add_field(name="🤖 AI 回覆", value=f"> {ai_text}", inline=False)
                minimal_embed.set_footer(text=f"#{ch_name} | {_api_label} | 模型: {_model_name} | User ID: {author.id}")
                await log_ch.send(embed=minimal_embed)
                print(f"📝 精簡版對話紀錄已發送到 #{log_ch.name}")
                _mark_success()
            except Exception as retry_err:
                print(f"⚠️ 精簡版對話紀錄也發送失敗，改用純文字最後嘗試：{retry_err}")
                # 最後一道防線：純文字訊息（連embed都可能因為某種原因失敗，
                # 但純文字send幾乎不可能因為內容格式而失敗，只會因為權限/網路失敗）
                try:
                    plain_text = (
                        f"💬 對話紀錄（純文字備援 — embed發送持續失敗）\n"
                        f"👤 {author.display_name}: {user_text}\n"
                        f"🤖 AI: {ai_text}"
                    )[:2000]
                    await log_ch.send(plain_text)
                    print(f"📝 純文字備援對話紀錄已發送到 #{log_ch.name}")
                    _mark_success()
                except Exception as plain_err:
                    print(f"⚠️ 純文字備援也失敗，對話紀錄徹底遺失：{plain_err}")
                    _mark_fail(f"embed+精簡版+純文字全部失敗：{plain_err}")
                    asyncio.ensure_future(_notify_owner_log_failure(f"embed+精簡版+純文字全部失敗：{plain_err}"))
    except discord.Forbidden:
        _reason = f"Bot 沒有在 #{getattr(log_ch, 'name', '?')} 發送訊息/嵌入的權限"
        print(f"⚠️ 對話紀錄發送失敗：{_reason}")
        _mark_fail(_reason)
        asyncio.ensure_future(_notify_owner_log_failure(_reason))
    except Exception as e:
        print(f"⚠️ 對話紀錄發送失敗：{e}")
        _mark_fail(str(e))
        asyncio.ensure_future(_notify_owner_log_failure(str(e)))


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
        except Exception as e:
            print("⚠️ 靜默例外:", e)
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
# ── AI API circuit breaker ──
# When the API returns 403 "anomalous behavior" repeatedly, we stop sending
# requests for a cooldown period instead of hammering a blocked endpoint
# on every single Discord message (which only makes the block worse).
_ai_circuit_breaker: dict = {
    "tripped": False,           # is the breaker currently open (blocking calls)?
    "trip_time": 0,             # when was it tripped? (time.monotonic)
    "consecutive_403": 0,       # how many 403s in a row?
    "cooldown_seconds": 120,   # how long to wait before trying again
}

AI_CIRCUIT_COOLDOWN_MSG = "🔌 AI API 目前被供應商暫時封鎖（anomalous behavior），已自動暫停請求，將在約 2 分鐘後重試。"

def _get_circuit_cooldown_msg():
    return chat_ai_settings.get("circuit_cooldown_msg", AI_CIRCUIT_COOLDOWN_MSG)

def _ai_circuit_check() -> bool:
    """Return True if calls are ALLOWED (breaker closed or cooldown expired).
    If the breaker was tripped but the cooldown has elapsed, reset it."""
    if not _ai_circuit_breaker["tripped"]:
        return True
    elapsed = _time.time() - _ai_circuit_breaker["trip_time"]
    if elapsed >= chat_ai_settings.get("circuit_breaker_cooldown", 120):
        print(f"🔄 AI 熔斷器冷卻結束（已等待 {elapsed:.0f}s），恢復請求")
        _ai_circuit_breaker["tripped"] = False
        _ai_circuit_breaker["consecutive_403"] = 0
        return True
    return False

def _ai_circuit_trip():
    """Trip the breaker — called when we get a 403 anomalous-behavior response."""
    _ai_circuit_breaker["consecutive_403"] += 1
    if _ai_circuit_breaker["consecutive_403"] >= 2 and not _ai_circuit_breaker["tripped"]:
        _ai_circuit_breaker["tripped"] = True
        _ai_circuit_breaker["trip_time"] = _time.time()
        print(f"🚫 AI 熔斷器觸發：連續 {_ai_circuit_breaker['consecutive_403']} 次 403，"
              f"暫停所有 AI 請求 {chat_ai_settings.get('circuit_breaker_cooldown', 120)}s")

def _ai_circuit_success():
    """Reset the consecutive-403 counter on a successful call."""
    _ai_circuit_breaker["consecutive_403"] = 0

_tools_unsupported_apis: set = set()
_tools_supported_apis: set = set()
# ── Reasoning 參數不支援白名單 ──
# 有些 API 供應商對 payload 做嚴格參數驗證，收到不認識的欄位
# （reasoning_effort/thinking/enable_thinking）直接回 400 Bad Request，
# 而不是像大多數供應商一樣忽略未知欄位。一旦偵測到，記住這個 endpoint
# 之後永遠不要再送 reasoning 參數，避免每次呼叫都白白浪費一次 400 重試
# 的時間（這在短 timeout 的快速通道下是致命的）。
_reasoning_unsupported_apis: set = set()

# ── Global background-AI-call throttle ──
# Root cause of "every single chat reply times out": multiple background
# systems (ai_refine_loop, community_awareness_loop, global micropedia scan,
# orphan rescue, quiz generation, daily summaries, /analyze user, name
# rating, ...) all call the SAME (often free/rate-limited) AI API endpoint,
# with NO concurrency cap between them — ai_refine_loop explicitly launches
# a new cycle without waiting for the previous one to finish, so cycles can
# pile up and fire many concurrent requests. When several of these fire at
# once alongside a live user chat message, the provider gets hammered with
# simultaneous connections and starts hanging on ALL of them (including the
# live chat request), well past our internal per-call timeouts — this is
# what "Timeout on reading data from socket" on every single message means.
# Fix: every BACKGROUND AI call must acquire this semaphore before hitting
# the network, capping how many background requests can be in flight at
# once. Live, user-facing chat replies (generate_chat_reply) are exempt —
# they pass is_background=False and always go straight through, so a flood
# of background work never delays an actual Discord reply.
_AI_BG_SEMAPHORE = asyncio.Semaphore(2)
_TOOLS_SUPPORTED_FILE = os.path.join(DATA_DIR, "tools_supported_apis.json")


def save_tools_supported():
    """Persist the set of API URLs known to support tool calling."""
    _save_json_file(_TOOLS_SUPPORTED_FILE, list(_tools_supported_apis))


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
    _save_json_file(TOOLS_UNSUPPORTED_FILE, list(_tools_unsupported_apis))

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

REASONING_UNSUPPORTED_FILE = os.path.join(DATA_DIR, "reasoning_unsupported_apis.json")

def save_reasoning_unsupported():
    _save_json_file(REASONING_UNSUPPORTED_FILE, list(_reasoning_unsupported_apis))

def load_reasoning_unsupported():
    global _reasoning_unsupported_apis
    try:
        if os.path.exists(REASONING_UNSUPPORTED_FILE):
            with open(REASONING_UNSUPPORTED_FILE, "r", encoding="utf-8") as f:
                _reasoning_unsupported_apis = set(json_module.load(f))
            if _reasoning_unsupported_apis:
                print(f"✅ reasoning_unsupported_apis 載入：{_reasoning_unsupported_apis}（略過 reasoning 參數）")
    except Exception as e:
        print(f"⚠️ reasoning_unsupported_apis load failed: {e}")

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


# ── 口語填充詞（用於從完整問句中抽取真正的關鍵詞） ──
# 使用者問問題常常是完整口語句子（例如「查看組織公告頻道，最新當選的秘書長是誰？」），
# 這些填充詞如果混進 bigram 比對，會稀釋掉真正關鍵詞（如「秘書長」「當選」）的
# 重疊比例，導致明明相關的內容因為 containment 算出來太低而被漏掉。
_QUERY_FILLER_WORDS = (
    "請問", "查看", "幫我", "看看", "查詢", "查一下", "一下", "麻煩",
    "是誰", "是什麼", "是不是", "有沒有", "可以嗎", "好嗎",
    "最新", "現在", "目前", "剛剛", "剛才",
    "怎麼樣", "怎麼", "如何", "可以", "想知道", "知道",
    "誰是", "結果如何", "組織", "頻道", "公告",
    "嗎", "呢", "啊", "喔", "呀", "的", "了", "嘛",
)


def _extract_search_keywords(query: str) -> list:
    """Strip common filler/question words from a natural-language query to
    recover the actual content keywords. Returns fragments of 2+ chars that
    can be checked as direct substrings against target text — this catches
    real matches that bigram-containment-of-the-full-query misses when the
    query is a long conversational sentence with lots of filler words."""
    import re as _re
    q = query.strip()
    # Normalize punctuation to spaces (acts as a split point)
    q = _re.sub(r"[？?！!，,。.、；;：:「」『』（）()]", " ", q)
    for fw in _QUERY_FILLER_WORDS:
        q = q.replace(fw, " ")
    chunks = [c.strip() for c in q.split() if len(c.strip()) >= 2]
    return chunks


def _keyword_substring_hit(query: str, text: str) -> bool:
    """True if any meaningful keyword extracted from the (possibly long,
    conversational) query appears verbatim in the target text."""
    keywords = _extract_search_keywords(query)
    if not keywords:
        return False
    return any(kw in text for kw in keywords)


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


def _substring_match_titles(message: str, titles: list, top_n: int = 5) -> list:
    """Catch named-entity mentions that bigram containment misses — e.g. a
    short 2-3 char abbreviation/nickname ('厂万') that's a substring of a
    LONGER title ('厂万自治區'), where the overlap-ratio in
    _fuzzy_match_titles is too small to pass its containment threshold.

    Uses TWO candidate sources:
    1. _extract_search_keywords (filler-word-stripped chunks) — cheap, but
       CJK has no word boundaries, so a message with no filler words in it
       collapses to ONE giant chunk (the whole clause), which almost never
       matches any title as a substring.
    2. A raw sliding-window decomposition (2-4 char windows) of the message
       itself — this is what actually recovers short entity names/nicknames
       buried inside a run-on sentence with no natural split point (e.g.
       "厂万有代表多少個國家" → window "厂万" is generated even though no
       filler word ever separated it from the rest of the clause).

    A hit counts if: the candidate is a substring of the title, OR the
    title itself (if short, <=6 chars) is a substring of the raw message —
    either direction can be the "real" name depending on which one is
    longer (message might use a short nickname, or the full title name)."""
    import re as _re
    keywords = _extract_search_keywords(message)
    _clean_msg = _re.sub(r"[\s？?！!，,。.、；;：:「」『』（）()]", "", message)[:80]
    window_candidates = set()
    for wlen in (2, 3, 4):
        for i in range(len(_clean_msg) - wlen + 1):
            window_candidates.add(_clean_msg[i:i + wlen])
    all_candidates = set(keywords) | window_candidates
    if not all_candidates:
        return []
    scored = []
    for t in titles:
        t_clean = t.strip()
        if not t_clean:
            continue
        hit = False
        best_len = 0
        for kw in all_candidates:
            if len(kw) >= 2 and (kw in t_clean or (len(t_clean) <= 6 and t_clean in message)):
                hit = True
                best_len = max(best_len, len(kw))
        if hit:
            # Prefer titles where the matched keyword covers more of the
            # title (more specific match), then shorter titles (more precise).
            scored.append((t, best_len / max(len(t_clean), 1), len(t_clean)))
    scored.sort(key=lambda x: (-x[1], x[2]))
    return [t for t, _, _ in scored[:top_n]]


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
        if _shared_session and not _shared_session.closed:
            session = _shared_session
            titles = await _get_micropedia_titles(session)
            matched = _fuzzy_match_titles(message_text, titles, top_n=max_results) if titles else []
            # Also try direct substring matching — catches short named-entity
            # mentions (e.g. a 2-3 char abbreviation like "厂万") that ARE a
            # substring of a longer title but whose bigram-overlap ratio is
            # too low to pass _fuzzy_match_titles' containment threshold.
            if titles:
                sub_matched = _substring_match_titles(message_text, titles, top_n=max_results)
                for t in sub_matched:
                    if t not in matched:
                        matched.append(t)
                matched = matched[:max_results]
            if not matched:
                # FORCED real internet search fallback — local bigram
                # matching against the cached title list is a heuristic and
                # can miss (or be stale up to 6h). Never silently give up:
                # force a real web search before concluding there's nothing.
                keywords = _extract_search_keywords(message_text)
                search_q = keywords[0] if keywords else message_text[:30]
                matched = await _micropedia_ddg_site_search(search_q, max_results)
            if not matched:
                return ""
            print(f"📚 Micropedia: 自動比對/強制聯網到 {len(matched)} 篇文章: {matched}")
            return await _micropedia_fetch_content(session, matched, query=message_text)
        else:
            async with aiohttp.ClientSession() as session:
                titles = await _get_micropedia_titles(session)
                matched = _fuzzy_match_titles(message_text, titles, top_n=max_results) if titles else []
                if titles:
                    sub_matched = _substring_match_titles(message_text, titles, top_n=max_results)
                    for t in sub_matched:
                        if t not in matched:
                            matched.append(t)
                    matched = matched[:max_results]
                if not matched:
                    keywords = _extract_search_keywords(message_text)
                    search_q = keywords[0] if keywords else message_text[:30]
                    matched = await _micropedia_ddg_site_search(search_q, max_results)
                if not matched:
                    return ""
                print(f"📚 Micropedia: 自動比對/強制聯網到 {len(matched)} 篇文章: {matched}")
                return await _micropedia_fetch_content(session, matched, query=message_text)
    except Exception as e:
        print(f"📚 Micropedia: 自動比對錯誤：{e}")
        return ""


def _wikitext_template_to_text(inner: str) -> str:
    """Convert the content of a resolved (innermost, no nested braces)
    MediaWiki template {{...}} into readable '【Name】key：value；...' text.
    Infobox-style templates (name=value parameters) carry real facts —
    e.g. an Infobox political party's founding date, leader, seat count —
    that must NOT be silently discarded. Pure citation/reference/formatting
    templates carry no standalone facts and are dropped to avoid noise."""
    parts = inner.split("|")
    name = parts[0].strip()
    name_lower = name.lower()
    if any(k in name_lower for k in ["cite", "citation", "ref", "來源", "reflist",
                                       "notelist", "convert", "lang-", "efn"]):
        return ""
    if len(parts) <= 1:
        return ""  # no parameters at all — nothing to extract
    kv_pairs = []
    for p in parts[1:]:
        p = p.strip()
        if not p:
            continue
        if "=" in p:
            k, v = p.split("=", 1)
            k, v = k.strip(), v.strip()
            if v:
                kv_pairs.append(f"{k}：{v}")
        elif p:
            kv_pairs.append(p)
    if not kv_pairs:
        return ""
    return f"【{name}】" + "；".join(kv_pairs)


def _wikitable_to_text(table_body: str) -> str:
    """Convert the content of a MediaWiki table {| ... |} (exclusive of the
    {| and |} delimiters) into readable text — one line per row, formatted
    as 'header：value；header2：value2' when a header row is detected,
    otherwise pipe-joined cell values. Previously tables were deleted
    outright, so any fact that only existed in tabular form (e.g. a list of
    political parties with their status columns) was invisible to the AI."""
    import re as _re
    lines = table_body.split("\n")
    headers = []
    rows = []
    current_row = []

    def _strip_cell_attrs(cell: str) -> str:
        # Wikitext cells can carry attributes before a final "|", e.g.
        # 'style="background:red" | 是' -> keep only the text after the
        # last "|" when the part before it looks like an attribute list.
        if "|" in cell:
            maybe_attr, maybe_text = cell.rsplit("|", 1)
            if "=" in maybe_attr or "style" in maybe_attr.lower() or "align" in maybe_attr.lower():
                return maybe_text.strip()
        return cell.strip()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("|-"):
            if current_row:
                rows.append(current_row)
                current_row = []
            continue
        if line.startswith("|+"):
            continue  # caption — skip
        if line.startswith("!"):
            for c in _re.split(r"!!", line[1:]):
                headers.append(_strip_cell_attrs(c))
            continue
        if line.startswith("|"):
            for c in _re.split(r"\|\|", line[1:]):
                current_row.append(_strip_cell_attrs(c))
            continue
    if current_row:
        rows.append(current_row)

    out_lines = []
    for row in rows:
        # Compare against the RAW cell count (including now-empty cells,
        # e.g. an icon cell whose [[File:...]] link was already stripped)
        # so header alignment isn't lost just because some cells are blank —
        # only filter empties out when building the final display text.
        if headers and len(row) == len(headers):
            pairs = [f"{h}：{v}" for h, v in zip(headers, row) if v]
            if pairs:
                out_lines.append("；".join(pairs))
        else:
            non_empty = [v for v in row if v]
            if non_empty:
                out_lines.append(" | ".join(non_empty))
    return "\n".join(out_lines)


def _clean_wikitext(text: str) -> str:
    """Remove MediaWiki markup to get clean text — CONVERTS tables and
    infobox/parameter templates into readable text instead of deleting them.
    Previously both were stripped outright, so any fact that only existed
    in a table (e.g. 現有黨派 list) or an Infobox (founding date, leader,
    seat count, etc.) was completely invisible to the AI, even though the
    article visibly contained the data on the website."""
    import re as _re

    # Drop pure icon/image links first — they carry no textual info and
    # otherwise leave junk like "50px|thumb" behind after link cleanup.
    text = _re.sub(r"\[\[(?:File|Image|檔案|文件):[^\]]*\]\]", "", text, flags=_re.IGNORECASE)

    # Templates {{...}} — repeatedly resolve INNERMOST templates first (no
    # nested braces inside), converting each into readable
    # "【Name】key：value；..." text instead of deleting. Looping until
    # stable also fixes the old non-nested-regex limitation where an outer
    # template's true closing "}}" was left dangling after a naive single pass.
    _tpl_re = _re.compile(r"\{\{([^{}]*)\}\}")
    _prev = None
    while _prev != text:
        _prev = text
        text = _tpl_re.sub(lambda m: _wikitext_template_to_text(m.group(1)), text)

    # Tables {| ... |} — convert to readable "header：value；..." lines
    # instead of deleting.
    _tbl_re = _re.compile(r"\{\|(.*?)\|\}", _re.DOTALL)
    text = _tbl_re.sub(lambda m: _wikitable_to_text(m.group(1)), text)

    # Remove wiki links [[link|display]] -> display
    text = _re.sub(r"\[\[[^]]*?\|([^]]*)\]\]", r"\1", text)
    text = _re.sub(r"\[\[([^]]*)\]\]", r"\1", text)
    # Remove external links [url text] -> text
    text = _re.sub(r"\[https?://\S+\s+([^]]*)\]", r"\1", text)
    text = _re.sub(r"\[https?://\S+\]", "", text)
    # Remove HTML tags
    text = _re.sub(r"<[^>]+>", "", text)
    # Remove headings markup =...=
    text = _re.sub(r"^=+\s*(.*?)\s*=+$", r"\1", text, flags=_re.MULTILINE)
    # Remove list markers
    text = _re.sub(r"^[\*#:]+\s*", "", text, flags=_re.MULTILINE)
    # Remove excess whitespace
    text = _re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


async def _micropedia_ddg_site_search(query: str, max_results: int = 5) -> list:
    """FORCED real-internet fallback for finding micropedia.site article
    titles. MediaWiki's own search (list=search) does basic MySQL matching
    with NO Chinese word segmentation — full phrases often return zero hits.
    Instead of relying on a local cached-title bigram heuristic (imperfect,
    can match the wrong title), this does a REAL web search restricted to
    site:micropedia.site via DuckDuckGo, which handles natural-language /
    CJK queries far better, then extracts the real article titles from the
    result URLs (/wiki/<title>). This is the mandatory, no-AI-judgment-needed
    path — it always runs as a fallback whenever the structured search comes
    up empty, so results are never left to depend on the AI choosing to
    search or on a stale local title cache."""
    import urllib.parse as _up
    titles = []
    _timeout = aiohttp.ClientTimeout(total=6, connect=3)
    _headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        search_q = f"{query} site:micropedia.site"
        url = f"https://html.duckduckgo.com/html/?q={_up.quote(search_q)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_headers, timeout=_timeout) as resp:
                if resp.status != 200:
                    print(f"📚 Micropedia: 強制聯網搜尋 DDG 回傳 {resp.status}")
                    return []
                html = await resp.text()
        # DDG HTML result links look like:
        # <a class="result__a" href="https://www.micropedia.site/wiki/XXX">...
        # (sometimes wrapped in a DDG redirect URL with uddg= param)
        raw_urls = re.findall(r'href="([^"]*micropedia\.site[^"]*)"', html)
        for raw_url in raw_urls:
            u = raw_url
            # Unwrap DDG redirect wrapper if present
            if "uddg=" in u:
                m = re.search(r"uddg=([^&]+)", u)
                if m:
                    u = _up.unquote(m.group(1))
            m2 = re.search(r"micropedia\.site/(?:wiki|index\.php\?title=)/?([^&#?]+)", u)
            if m2:
                title = _up.unquote(m2.group(1)).replace("_", " ").strip()
                if title and not any(title.startswith(p) for p in _MICROPEDIA_SKIP_PREFIXES):
                    if title not in titles:
                        titles.append(title)
            if len(titles) >= max_results:
                break
        if titles:
            print(f"🌐 Micropedia 強制聯網：找到 {len(titles)} 篇文章: {titles}")
        else:
            print(f"🌐 Micropedia 強制聯網：沒有找到結果 for '{query}'")
    except asyncio.TimeoutError:
        print(f"🌐 Micropedia 強制聯網逾時 for '{query}'")
    except Exception as e:
        print(f"🌐 Micropedia 強制聯網例外：{e}")
    return titles


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


def _smart_truncate_wikitext(clean: str, query: str, max_len: int = 3000) -> str:
    """Truncate cleaned article text to max_len — but NOT naively from the
    start. Long articles (e.g. a country's main page with 中央官制/旗幟/行政
    區劃/政治/文化歷史/軍事/外交 sections BEFORE a 其他語言國名翻譯 table near
    the bottom) put the section the user actually asked about far past a
    from-the-top cutoff, so the table-to-text conversion succeeded but the
    result never survived truncation — the AI still never saw it. Instead,
    locate where the query is actually relevant in the text and keep a
    window centered there instead of the top:
    1. Literal substring match on keywords extracted from the query
       (reuses _extract_search_keywords — strips filler words/punctuation).
    2. If no keyword appears verbatim (common for Chinese, which has no
       word segmentation, so a filler-free query can still be one long
       unsplit chunk), fall back to a bigram-density scan: slide a window
       across the body and keep whichever one contains the most of the
       query's 2-char bigrams."""
    if len(clean) <= max_len:
        return clean
    intro_budget = min(400, max_len // 4)
    intro = clean[:intro_budget]
    if not query:
        return clean[:max_len] + "..."
    remainder_budget = max_len - intro_budget - 30

    keywords = sorted(_extract_search_keywords(query), key=len, reverse=True)
    match_pos = -1
    for kw in keywords:
        idx = clean.find(kw, intro_budget)
        if idx != -1:
            match_pos = idx
            break

    if match_pos == -1:
        q_bigrams = _bigrams(query)
        if q_bigrams:
            body = clean[intro_budget:]
            step = max(200, remainder_budget // 4)
            best_score, best_start = 0, -1
            for start in range(0, max(1, len(body) - 1), step):
                window = body[start:start + remainder_budget]
                score = sum(1 for bg in q_bigrams if bg in window)
                if score > best_score:
                    best_score, best_start = score, start
            if best_start != -1 and best_score > 0:
                match_pos = intro_budget + best_start

    if match_pos == -1:
        # No relevance signal found at all — fall back to the previous
        # from-the-top behavior.
        return clean[:max_len] + "..."

    window_start = max(intro_budget, match_pos - remainder_budget // 3)
    window_end = min(len(clean), window_start + remainder_budget)
    window = clean[window_start:window_end]
    return f"{intro}\n...[中略]...\n{window}..."


async def _micropedia_fetch_content(session, titles: list, query: str = "") -> str:
    """Fetch article content for the given titles via the MediaWiki content API
    (action=query&prop=revisions&rvprop=content) — JSON API, not scraping.
    `query` (the user's original message/search term) is used to smart-
    truncate long articles around the relevant section instead of always
    keeping only the top of the article."""
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
            # Bumped from 2000 to 3000 chars, and truncation is now
            # query-aware (see _smart_truncate_wikitext) — long articles
            # bury far-down sections/tables past a naive from-the-top cutoff.
            clean = _smart_truncate_wikitext(clean, query, max_len=3000)
            content_parts.append(f"【{title}】\n{clean}")

    return "\n\n".join(content_parts)


async def _fetch_micropedia_inner_body(session, query, max_results, cache_key):
    """Inner body of _fetch_micropedia_inner — extracted for session safety.
    Search strategy: (1) official MediaWiki search API first (fast,
    structured), (2) FORCED real internet search (DuckDuckGo, site-scoped)
    as a mandatory fallback if the structured search finds nothing — this is
    not optional/AI-judgment-gated, it always runs so CJK phrase-matching
    failures never silently return empty."""
    print(f"📚 Micropedia: 搜尋 '{query}'")
    titles = await _micropedia_search_api(session, query, max_results)
    if not titles:
        print(f"📚 Micropedia: 內部搜尋 '{query}' 沒有結果，強制轉為聯網搜尋...")
        titles = await _micropedia_ddg_site_search(query, max_results)
    if not titles:
        print(f"📚 Micropedia: 搜尋 '{query}' 沒有結果（內部+聯網都沒找到）")
        _micropedia_cache[cache_key] = (_time.time(), "")
        return ""
    print(f"📚 Micropedia: 找到 {len(titles)} 篇相關文章: {titles[:5]}")
    result = await _micropedia_fetch_content(session, titles, query=query)
    _micropedia_cache[cache_key] = (_time.time(), result)
    print(f"📚 Micropedia: 取得內容 ({len(result)} chars)")
    return result


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
        if _shared_session and not _shared_session.closed:
            session = _shared_session
        else:
            # Use context manager to ensure session is always closed
            # even on cancellation/timeout
            async with aiohttp.ClientSession() as session:
                return await _fetch_micropedia_inner_body(
                    session, query, max_results, cache_key
                )
        # Reuse shared session
        return await _fetch_micropedia_inner_body(
            session, query, max_results, cache_key
        )
    except asyncio.TimeoutError:
        print(f"📚 Micropedia: 搜尋逾時 for '{query}'")
        return ""
    except Exception as e:
        print(f"📚 Micropedia: 錯誤 for '{query}': {e}")
        return ""


async def _fetch_micropedia(query: str, max_results: int = 5) -> str:
    """Thin wrapper enforcing a hard overall time budget (10s) on a single
    micropedia lookup, regardless of network conditions — guarantees a tool
    call the AI makes never meaningfully stalls the reply pipeline. (Slightly
    higher than before since this may now include a forced web-search
    fallback on top of the structured MediaWiki search.)"""
    try:
        return await asyncio.wait_for(_fetch_micropedia_inner(query, max_results), timeout=5)
    except asyncio.TimeoutError:
        print(f"📚 Micropedia: 整體查詢逾時（>5s），放棄 for '{query}'")
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

# ── 頻道 Embed 索引 ──
# Discord 的訊息搜尋 API 只索引純文字，不索引 embed 內容。很多官方公告
# （人事任命、選舉結果、出入許可等）是純 embed 訊息（content 為空），
# 所以 search_discord 永遠找不到。這個索引掃描所有文字頻道，把有 embed
# 的訊息內容抓出來建索引，讓搜尋能找到這些公告。
_channel_index_cache: dict = {}  # {guild_id: {"entries": [...], "updated": ts}}
_CHANNEL_INDEX_TTL = 1800  # 30 分鐘快取


async def _refresh_channel_index(guild) -> list:
    """Scan all text channels (non-forum) for recent messages and index their
    content — BOTH plain text AND embeds. Discord's search API doesn't index
    embed content, and its full-text search is unreliable for CJK, so we
    maintain our own bigram-searchable index of recent channel messages.
    Forum channels are already covered by _refresh_forum_index so we skip
    them here. Only fetches the last 50 messages per channel to keep API call
    count reasonable (~1 call/channel). Staggered with small delays to avoid
    rate limits."""
    entries = []
    skip_reasons = {}  # channel_name -> reason string, for diagnostics
    excluded_channels = []  # channel_names skipped as test/log
    _t0 = _time.time()
    _ch_count = 0
    _msg_count = 0
    try:
        _log_ch_id = chat_ai_settings.get("log_channel_id")
        _EXCLUDE_NAME_MARKERS = ("測試", "test", "log", "紀錄")

        def _is_excluded_channel(ch) -> bool:
            """Skip internal testing/log channels — they pollute search
            results with dummy test messages and, worse, create a feedback
            loop: the AI-log channel literally quotes back users' search
            queries (e.g. "查詢了「最新公告」"), so searching for that same
            term later matches the LOG ENTRY instead of real content,
            getting noisier every time someone asks a question."""
            if _log_ch_id and ch.id == _log_ch_id:
                return True
            name_lower = ch.name.lower()
            return any(marker.lower() in name_lower for marker in _EXCLUDE_NAME_MARKERS)

        all_candidate_channels = [
            ch for ch in guild.text_channels
            if ch.type in (discord.ChannelType.text, discord.ChannelType.news)
        ]
        text_channels = []
        for ch in all_candidate_channels:
            if _is_excluded_channel(ch):
                excluded_channels.append(ch.name)
            else:
                text_channels.append(ch)
        print(f"📢 開始頻道訊息索引：{len(text_channels)} 個文字頻道（已排除 {len(excluded_channels)} 個測試/紀錄頻道）...")

        for ch in text_channels:
            _ch_count += 1
            _ch_msg_count = 0
            try:
                async for msg in ch.history(limit=50):
                    # Extract text from BOTH plain content AND embeds
                    text_parts = []
                    if msg.content and msg.content.strip():
                        text_parts.append(msg.content.strip())
                    for emb in msg.embeds:
                        if emb.title:
                            text_parts.append(str(emb.title))
                        if emb.description:
                            text_parts.append(str(emb.description))
                        for field in emb.fields:
                            text_parts.append(f"{field.name}: {field.value}")
                        if emb.footer and emb.footer.text:
                            text_parts.append(str(emb.footer.text))
                        if emb.author and emb.author.name:
                            text_parts.append(str(emb.author.name))
                    full_text = "\n".join(p for p in text_parts if p).strip()
                    if not full_text or len(full_text) < 5:
                        continue  # skip empty / tiny messages

                    _msg_count += 1
                    _ch_msg_count += 1
                    author = msg.author.display_name if msg.author else "未知"
                    date_str = (msg.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if msg.created_at else ""

                    entries.append({
                        "channel_name": ch.name,
                        "author": author,
                        "date": date_str,
                        "text": full_text[:1000],
                    })
                if _ch_msg_count == 0:
                    skip_reasons[ch.name] = "讀取成功但沒有符合條件的訊息（全部太短或是空的）"
            except discord.Forbidden:
                skip_reasons[ch.name] = "❌ 沒有權限讀取（缺少「查看頻道」或「讀取訊息歷史」權限）"
                print(f"⚠️ 頻道索引「{ch.name}」權限不足，機器人無法讀取此頻道歷史")
            except Exception as e:
                skip_reasons[ch.name] = f"❌ 讀取失敗：{e}"
                print(f"⚠️ 頻道索引「{ch.name}」失敗：{e}")

            if _ch_count % 5 == 0:
                await asyncio.sleep(0.5)

        print(f"📢 頻道訊息索引完成：{_ch_count} 頻道，{_msg_count} 則訊息，耗時 {_time.time() - _t0:.1f}s")
    except Exception as e:
        print(f"⚠️ 頻道索引刷新失敗：{e}")

    _channel_index_cache[guild.id] = {
        "entries": entries,
        "updated": _time.time(),
        "skip_reasons": skip_reasons,
        "excluded_channels": excluded_channels,
    }
    return entries


def _search_channel_index(query: str, entries: list, top_n: int = 5) -> list:
    """Search the channel message index using bigram matching.

    Falls back to keyword-substring matching (extracted from the query with
    filler words stripped) when full-query bigram containment is too low —
    this handles long conversational questions where filler words dilute the
    containment ratio for otherwise perfectly relevant hits."""
    query = query.strip()
    query_bg = _bigrams(query)
    if not query_bg or not entries:
        return []
    keyword_hit_cache = None  # computed once per query, reused per entry
    scored = []
    for e in entries:
        text = e["text"]
        text_bg = _bigrams(text)
        if not text_bg:
            continue
        overlap = query_bg & text_bg
        substring_hit = bool(query) and query in text
        keyword_hit = _keyword_substring_hit(query, text)
        if not overlap and not substring_hit and not keyword_hit:
            continue
        containment = len(overlap) / len(query_bg) if query_bg else 0
        if not substring_hit and not keyword_hit and containment < 0.3:
            continue
        # Boost score if keyword-hit so real matches surface first
        score = max(containment, 0.5) if keyword_hit else containment
        scored.append((e, score, substring_hit or keyword_hit))
    scored.sort(key=lambda x: (-x[2], -x[1]))
    return [e for e, _, _ in scored[:top_n]]




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
    _t0 = _time.time()
    _processed = 0
    try:
        for forum in guild.forums:
            threads = list(forum.threads)  # active posts (already cached)
            try:
                async for t in forum.archived_threads(limit=100):
                    threads.append(t)
            except Exception as e:
                print(f"⚠️ 無法取得「{forum.name}」的封存貼文：{e}")

            print(f"🗂️ 開始索引「{forum.name}」— {len(threads)} 篇貼文...")
            for thread in threads:
                _processed += 1
                if _processed % 10 == 0:
                    print(f"🗂️ 索引進度：已處理 {_processed} 篇，耗時 {_time.time() - _t0:.1f}s")
                try:
                    starter = thread.starter_message
                    if starter is None:
                        try:
                            starter = await asyncio.wait_for(thread.fetch_message(thread.id), timeout=5)
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
                    # happen AFTER the original post. Wrapped in a hard
                    # per-thread timeout so one slow/rate-limited thread can
                    # never stall the whole index refresh indefinitely.
                    reply_lines = []
                    last_activity = thread.created_at

                    async def _walk_replies():
                        nonlocal last_activity
                        async for msg in thread.history(limit=50, oldest_first=True):
                            if starter and msg.id == starter.id:
                                continue
                            last_activity = msg.created_at  # track newest as we go — no extra API call needed
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
                            date_str = (msg.created_at + timedelta(hours=8)).strftime("%Y-%m-%d") if msg.created_at else "?"
                            author = msg.author.display_name if msg.author else "未知"
                            reply_lines.append(f"[{date_str}] {author}: {body[:200]}")

                    try:
                        await asyncio.wait_for(_walk_replies(), timeout=8)
                    except asyncio.TimeoutError:
                        print(f"⚠️ 討論串「{thread.name}」回覆讀取逾時，改用已讀到的部分")
                    except Exception as e:
                        print("⚠️ 靜默例外:", e)

                    if reply_lines:
                        text_parts.append("─── 討論串回覆（含後續進展/狀態更新）───")
                        text_parts.extend(reply_lines)

                    posts.append({
                        "title": thread.name,
                        "tags": tags,
                        "channel_name": forum.name,
                        "text": "\n".join(str(p) for p in text_parts if p),
                        "reply_lines": reply_lines,  # kept separate so we can always surface the LATEST update
                        "url": thread.jump_url,
                        "author": (starter.author.display_name if starter and starter.author else "未知"),
                        "created_at": (thread.created_at + timedelta(hours=8)).strftime("%Y-%m-%d") if thread.created_at else "",
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
    print(f"🗂️ Forum 索引已更新：{guild.name} — {len(posts)} 篇貼文（{forum_count} 個論壇頻道，共索引 {total_replies} 則回覆），總耗時 {_time.time() - _t0:.1f}s")
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
    short exact term (e.g. a proper noun) should always count as a hit.

    Also falls back to keyword-substring matching (query with filler words
    stripped) for long conversational queries that would otherwise fail the
    containment threshold despite containing the right keywords."""
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
        keyword_hit = _keyword_substring_hit(query, text)
        if not overlap and not substring_hit and not keyword_hit:
            continue
        containment = len(overlap) / len(query_bg) if query_bg else 0
        if not substring_hit and not keyword_hit and containment < 0.5:
            continue
        score = max(containment, 0.5) if keyword_hit else containment
        scored.append((p, score, substring_hit or keyword_hit))
    scored.sort(key=lambda x: (-x[2], -x[1]))
    return [p for p, _, _ in scored[:top_n]]


async def _live_guild_message_search(guild, query: str, limit: int = 25) -> str:
    """Search Discord guild message history (plain-text content only) via
    Discord's guild search API endpoint. No time limit — searches all of
    history. Returns up to 25 results sorted chronologically (oldest first)
    so the AI can see the full timeline of a topic. Does NOT see embed
    content — that's what the forum index above covers separately."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token or not guild or not query.strip():
        return ""

    query = query.strip()
    headers = {"Authorization": f"Bot {token}"}
    timeout = aiohttp.ClientTimeout(total=10, connect=3)

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

            # Filter out internal testing/log channels — they pollute
            # results with dummy test messages and create a feedback loop
            # (the AI-log channel literally quotes back users' past search
            # queries, so a query matches its own previous log entry).
            _log_ch_id = chat_ai_settings.get("log_channel_id")
            _EXCLUDE_NAME_MARKERS = ("測試", "test", "log", "紀錄")

            def _is_excluded_ch(channel_id_str):
                if not channel_id_str or not channel_id_str.isdigit():
                    return False
                cid = int(channel_id_str)
                if _log_ch_id and cid == _log_ch_id:
                    return True
                ch = guild.get_channel(cid)
                if ch and any(m.lower() in ch.name.lower() for m in _EXCLUDE_NAME_MARKERS):
                    return True
                return False

            messages = [m for m in messages if not _is_excluded_ch(m.get("channel_id", ""))]
            if not messages:
                return ""

            # Sort by timestamp (oldest first) so AI sees the timeline
            messages.sort(key=lambda m: m.get("timestamp", ""))
            lines = [f"🔍 全伺服器訊息搜尋「{query}」的結果（{len(messages)} 則，按時間排列）："]
            for i, msg in enumerate(messages[:limit], 1):
                author = msg.get("author", {}).get("username", "未知")
                msg_content = msg.get("content", "").strip()
                timestamp = msg.get("timestamp", "")
                channel_id = msg.get("channel_id", "")
                # Try to get channel name
                ch_name = "?"
                if channel_id:
                    ch = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
                    if ch:
                        ch_name = ch.name
                msg_content = re.sub(r"<@!?\d+>", "@用戶", msg_content)[:400]
                lines.append(f"\n[{i}] #{ch_name} | {author} ({timestamp[:16] if timestamp else '?'}): {msg_content}")
            return "\n".join(lines)[:4000]
    except asyncio.TimeoutError:
        print(f"⚠️ Discord 訊息搜尋逾時 for '{query}'")
        return ""
    except Exception as e:
        print(f"⚠️ Discord search error: {e}")
        return ""


async def _search_discord_history_inner(guild, query: str, limit: int = 10) -> str:
    """Runs the forum-index search, channel-embed search, AND live message
    search CONCURRENTLY and merges all three into one result string for the AI."""
    if not guild or not query.strip():
        return "搜尋條件不足"
    query = query.strip()

    async def _forum_part():
        try:
            posts = await _get_forum_index(guild)
            matched = _search_forum_posts(query, posts, top_n=8)
            if not matched:
                return ""
            parts = [
                f"📋 論壇貼文搜尋「{query}」的結果（{len(matched)} 篇）：\n"
                f"⚠️ 重要：每篇貼文下面若有「最新進展」，那才是目前的真實狀態"
                f"（可能已撤案/已通過/已否決/已有後續結果），絕對不要只看原始貼文內容就下結論，"
                f"原始提案內容可能早就過時了。"
            ]
            for p in matched:
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
                parts.append(block)
            return "\n".join(parts)
        except Exception as e:
            print(f"⚠️ Forum 索引搜尋失敗：{e}")
            return ""

    async def _embed_part():
        """Search the channel message index for recent messages
        (人事任命、選舉結果、出入許可 etc.) that Discord's search API
        can't find because it doesn't index embed content."""
        try:
            cached = _channel_index_cache.get(guild.id)
            entries = cached["entries"] if cached else []
            if not entries:
                return ""
            matched = _search_channel_index(query, entries, top_n=5)
            if not matched:
                return ""
            parts = [f"📢 頻道訊息搜尋「{query}」的結果（{len(matched)} 則）："]
            for e in matched:
                parts.append(
                    f"\n#{e['channel_name']} | {e['author']} ({e['date']})\n{e['text'][:400]}"
                )
            return "\n".join(parts)
        except Exception as e:
            print(f"⚠️ 頻道訊息搜尋失敗：{e}")
            return ""

    async def _kb_part():
        """Search the permanent knowledge base (daily AI summaries stored
        on Google Drive) for historical context that may not be in recent
        channel messages or forum posts."""
        try:
            matched = _search_knowledge_base(query, top_n=3)
            if not matched:
                return ""
            parts = [f"📚 永久知識庫搜尋「{query}」的結果（{len(matched)} 篇每日摘要）："]
            for s in matched:
                parts.append(
                    f"\n📅 {s['date']}\n{s['summary'][:800]}"
                )
            return "\n".join(parts)
        except Exception as e:
            print(f"⚠️ 知識庫搜尋失敗：{e}")
            return ""

    forum_result, embed_result, kb_result, live_result = await asyncio.gather(
        asyncio.wait_for(_forum_part(), timeout=3),
        asyncio.wait_for(_embed_part(), timeout=3),  # in-memory bigram match — instant
        asyncio.wait_for(_kb_part(), timeout=3),  # in-memory bigram match — instant
        asyncio.wait_for(_live_guild_message_search(guild, query, limit), timeout=10),
        return_exceptions=True,
    )
    if isinstance(forum_result, Exception):
        forum_result = ""
    if isinstance(embed_result, Exception):
        embed_result = ""
    if isinstance(kb_result, Exception):
        kb_result = ""
    if isinstance(live_result, Exception):
        live_result = ""

    combined = "\n\n".join(r for r in (forum_result, embed_result, kb_result, live_result) if r)
    if not combined:
        return f"沒有找到包含「{query}」的訊息或論壇貼文"
    return combined[:6000]


async def _search_discord_history(guild, query: str, limit: int = 10) -> str:
    """Thin wrapper enforcing a hard overall time budget (10s) regardless of
    network conditions — guarantees this tool call never meaningfully stalls
    the reply pipeline (same pattern as _fetch_micropedia)."""
    try:
        return await asyncio.wait_for(_search_discord_history_inner(guild, query, limit), timeout=8)
    except asyncio.TimeoutError:
        print(f"⚠️ Discord 搜尋整體逾時（>15s），放棄 for '{query}'")
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


async def _web_search(query: str) -> str:
    """Search the web using Wikipedia (zh+en) + DuckDuckGo — ALL sources run
    CONCURRENTLY with tight per-source timeouts and a hard overall cap, so a
    single web_search tool call can never itself become the pipeline
    bottleneck (previous version ran up to 6 HTTP requests sequentially at
    10s each — worst case ~60s for ONE tool call). No API keys needed, all
    endpoints are free and HTTPS-accessible from Render."""
    _ws_timeout = aiohttp.ClientTimeout(total=4, connect=2)
    _ws_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    # Wikipedia requires a descriptive User-Agent with contact info,
    # otherwise they return 403.
    _wiki_headers = {
        "User-Agent": "ICEA-Bot/1.0 (https://icea.org; contact@icea.org)"
    }

    async def _wiki_lookup(session, lang):
        try:
            search_url = (
                f"https://{lang}.wikipedia.org/w/api.php?action=query"
                f"&list=search&srsearch={urllib.parse.quote(query)}"
                f"&format=json&utf8=1&srlimit=3"
            )
            async with session.get(search_url, headers=_wiki_headers, timeout=_ws_timeout) as resp:
                if resp.status != 200:
                    return None
                data = json_module.loads(await resp.text())
            search_results = data.get("query", {}).get("search", [])
            if not search_results:
                return None
            page_ids = "|".join(str(r["pageid"]) for r in search_results[:3])
            extract_url = (
                f"https://{lang}.wikipedia.org/w/api.php?action=query"
                f"&prop=extracts&exintro=1&explaintext=1&format=json"
                f"&exchars=800&pageids={page_ids}"
            )
            async with session.get(extract_url, headers=_wiki_headers, timeout=_ws_timeout) as resp2:
                if resp2.status != 200:
                    return None
                ext_data = json_module.loads(await resp2.text())
            pages = ext_data.get("query", {}).get("pages", {})
            for pid, page in pages.items():
                title = page.get("title", "")
                extract = page.get("extract", "")
                if extract and len(extract) > 30:
                    return f"📖 維基百科({lang})：{title}\n{extract[:600]}"
            return None
        except Exception as e:
            print(f"⚠️ web_search Wikipedia({lang}) 例外：{e}")
            return None

    async def _ddg_api_lookup(session):
        out = []
        try:
            ddg_url = (
                f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}"
                f"&format=json&no_html=1&skip_disambig=1"
            )
            async with session.get(ddg_url, headers=_ws_headers, timeout=_ws_timeout) as resp:
                if resp.status == 200:
                    # DDG returns Content-Type: application/x-javascript,
                    # which aiohttp's resp.json() rejects — use text() + json.loads()
                    data = json_module.loads(await resp.text())
                    abstract = data.get("Abstract", "")
                    if abstract:
                        out.append(f"🔍 {abstract[:500]}")
                    related = data.get("RelatedTopics", [])
                    for r in related[:3]:
                        if isinstance(r, dict) and r.get("Text"):
                            out.append(f"🔍 {r['Text'][:300]}")
        except Exception as e:
            print(f"⚠️ web_search DDG API 例外：{e}")
        return out

    async def _ddg_html_lookup(session):
        out = []
        try:
            ddg_html_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            async with session.get(ddg_html_url, headers=_ws_headers, timeout=_ws_timeout) as resp:
                if resp.status in (200, 202):
                    html = await resp.text()
                    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
                    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
                    for i in range(min(len(titles), len(snippets), 5)):
                        clean_title = re.sub(r'<[^>]+>', '', titles[i]).strip()
                        clean_snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                        if clean_snippet and len(clean_snippet) > 20:
                            out.append(f"🌐 {clean_title}：{clean_snippet[:300]}")
        except Exception as e:
            print(f"⚠️ web_search DDG HTML 例外：{e}")
        return out

    async def _run_all():
        results = []
        async with aiohttp.ClientSession() as session:
            # Run Wikipedia (zh+en) and DDG API concurrently — this alone
            # cuts the common case from ~4 sequential requests to 1 round-trip.
            wiki_zh, wiki_en, ddg_api_results = await asyncio.gather(
                _wiki_lookup(session, "zh"),
                _wiki_lookup(session, "en"),
                _ddg_api_lookup(session),
                return_exceptions=False,
            )
            if wiki_zh:
                results.append(wiki_zh)
            if wiki_en:
                results.append(wiki_en)
            results.extend(ddg_api_results)
            # HTML fallback only if the fast sources came up short — this is
            # the slowest/least reliable source, so it's last-resort only.
            if len(results) < 2:
                results.extend(await _ddg_html_lookup(session))
        return results

    try:
        results = await asyncio.wait_for(_run_all(), timeout=6)
    except asyncio.TimeoutError:
        print(f"⚠️ web_search 整體逾時（>10s） for '{query}'")
        results = []
    except Exception as e:
        print(f"⚠️ web_search 整體例外：{e}")
        results = []

    return "\n\n".join(results[:6]) if results else ""


_WEB_SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "搜尋網際網路，取得即時、最新的資訊。用於："
            "（1）你不確定的事實，（2）你可能認為「不存在」「沒發生過」的事，"
            "（3）涉及時事、新聞、近期事件、或你的訓練資料可能過時的問題，"
            "（4）任何需要查證而非憑記憶回答的問題。"
            "⚠️ 當你準備說「這不存在」「這沒發生過」「這不是真的」「沒有這個」時，"
            "必須先呼叫這個工具確認，不要憑訓練資料直接否定。"
            "可以用英文或中文搜尋，視問題性質決定。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵字，用你認為最可能找到答案的詞。可以用中文或英文。"
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


_TOOL_DUMP_MARKERS = (
    "🔍 全伺服器訊息搜尋", "📢 頻道訊息搜尋", "📋 論壇貼文搜尋",
    "📚 微國家百科搜尋", "📚 永久知識庫搜尋", "─── Discord 伺服器歷史資料",
)


def _strip_thinking(text: str) -> str:
    """Remove chain-of-thought / reasoning output that reasoning models
    (GPT-oss, DeepSeek-R1, etc.) sometimes embed in the content field.

    Two strategies:
    1. Tag-based: strip ilda.../thinking blocks (handles most reasoning models)
    2. Preamble-based: strip known thinking preambles followed by the actual answer
    """
    if not text:
        return text

    # 1. Strip closed ilda.../thinking>
    text = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', text, flags=re.DOTALL)

    # 2. Handle unclosed ilda tag
    think_open = text.find('<think')
    if think_open != -1 and '</think' not in text[think_open:]:
        after_think = text[think_open:]
        after_think = re.sub(r'^<think(?:ing)?>\s*', '', after_think)
        blocks = re.split(r'\n\s*\n', after_think)
        if len(blocks) > 1:
            last_block = blocks[-1].strip()
            if last_block and len(last_block) > 5:
                text = last_block
            else:
                text = text[:think_open]
        else:
            text = text[:think_open]

    # 3. If there's a closed ilda tag, keep only content after it
    close_idx = text.rfind('</think')
    if close_idx != -1:
        after_close = text[close_idx:]
        after_close = re.sub(r'^</think(?:ing)?>\s*', '', after_close)
        if after_close.strip():
            text = after_close.strip()

    # 4. Strip thinking preambles — if the text starts with a known thinking
    #    phrase, strip everything up to the first double-newline separator.
    #    The actual answer almost always follows a blank line after thinking.
    _THINKING_PREAMBLES = [
        '讓我想想', '讓我思考', '讓我分析', '讓我看看',
        '我來想想', '我來分析', '我來思考', '我來看看',
        '好的，我來', '好的，讓我', '好的我來', '好的讓我',
        '嗯，讓我', '嗯，我來', '嗯讓我', '嗯我來',
        '首先，', '首先我', '首先讓',
        'Let me think', 'Let me analyze', 'Let me consider',
        'Okay, so', 'Okay so', 'Alright, so', 'Alright so',
        'Well, let me', 'Well let me',
    ]
    text_stripped = text.strip()
    for preamble in _THINKING_PREAMBLES:
        if text_stripped.startswith(preamble):
            # Find the first double-newline — that's where thinking ends
            # and the actual answer begins
            sep_idx = text.find('\n\n')
            if sep_idx != -1:
                remainder = text[sep_idx + 2:].strip()
                # Only strip if there's real content after the separator
                if len(remainder) > 5:
                    text = remainder
                else:
                    # Stalling preamble followed by nothing substantial —
                    # the model said e.g. "讓我想想...\n\n" and stopped.
                    # Blank it out entirely rather than sending a
                    # non-committal filler as the "final answer".
                    text = ""
            else:
                # The model's ENTIRE reply is just a stalling phrase like
                # "讓我想想..." with no follow-up at all — this is a lazy
                # non-answer, not a real reply. Treat it as empty so the
                # caller's safety net substitutes an honest fallback
                # message instead of showing this to the user verbatim.
                text = ""
            break

    return text.strip()


def _strip_raw_tool_dump(text: str) -> str:
    """Defensive safety net: weak/free AI models sometimes echo the raw
    tool-call search result verbatim as their 'final answer' instead of
    composing a natural-language summary (observed in production — the
    reply literally contained '🔍 全伺服器訊息搜尋「...」的結果...[1]...[2]...').
    Detect known tool-output header markers and cut everything from that
    point onward, keeping only any natural-language prose that came before
    it. If nothing meaningful is left, the caller falls back to a generic
    message rather than showing raw data dumps to the user."""
    if not text:
        return text
    cut_at = len(text)
    for marker in _TOOL_DUMP_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut_at = min(cut_at, idx)
    cleaned = text[:cut_at].strip()
    return cleaned


async def _describe_image(image_url: str, settings: dict, _vision_diag: list = None) -> str:
    """Call a vision-capable model to describe an image. Uses the same API
    URL/Key as the chat AI, but with a different model name (settings["vision_model"]).
    Returns a text description of the image, or empty string on failure.

    _vision_diag: optional list to append diagnostic events to (for AI log embed).
    Supports a degradation chain: if the primary vision model fails, try
    models in settings["vision_fallback_chain"] in order, then the
    fallback API endpoint (same as the chat degradation chain)."""
    if _vision_diag is None:
        _vision_diag = []
    # ── 池解析：vision 角色 ──
    _vis_url, _vis_key, vision_model = _resolve_role_endpoint("vision", settings)
    if not vision_model:
        vision_model = settings.get("vision_model", "")
        _vis_url = settings.get("api_url", "")
        _vis_key = settings.get("api_key", "")
    if not vision_model:
        _vision_diag.append("📷 視覺模型未設定，跳過識圖")
        return ""

    api_url = (_vis_url or settings.get("api_url", "")).rstrip("/")
    if not api_url.endswith("/chat/completions"):
        if api_url.endswith("/v1") or api_url.endswith("/v2"):
            api_url += "/chat/completions"
        else:
            api_url += "/v1/chat/completions"

    _vision_api_key = _vis_key or settings.get("api_key", "")

    headers = {
        "Authorization": f"Bearer {_vision_api_key}",
        "Content-Type": "application/json",
    }

    _prompt_text = (
        "請詳細描述這張圖片的內容。包括：\n"
        "- 圖片的主題和場景\n"
        "- 可見的文字（完整轉錄）\n"
        "- 人物、物體、顏色、動作等細節\n"
        "- 如果是截圖，說明是什麼應用/網頁的截圖\n"
        "- 如果是迷因或梗圖，解釋其含義\n"
        "用繁體中文回答，簡潔但完整。"
    )

    async def _try_vision(model_name, url, key, label, diag_list):
        """Single attempt to call a vision model. Returns (description, success)."""
        _payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _prompt_text},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "max_tokens": 500,
            "temperature": 0.3,
        }
        _hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            _t0 = _time.time()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=_payload, headers=_hdrs,
                    timeout=aiohttp.ClientTimeout(total=90, connect=10, sock_read=80),
                ) as resp:
                    if resp.status != 200:
                        _err = await resp.text()
                        _short = _err[:150].replace("\n", " ")
                        diag_list.append(f"📷 {label}（{model_name}）HTTP {resp.status}：{_short}")
                        print(f"⚠️ 視覺模型 {label}（{model_name}）API 返回 {resp.status}: {_err[:200]}")
                        return "", False
                    data = json_module.loads(await resp.text())
                    choices = data.get("choices", [])
                    if choices:
                        desc = choices[0].get("message", {}).get("content", "")
                        if desc:
                            _elapsed = _time.time() - _t0
                            diag_list.append(f"📷 {label}（{model_name}）✅ {_elapsed:.1f}s, {len(desc)} chars")
                            print(f"📷 視覺模型 {label}（{model_name}）識圖完成（{_elapsed:.1f}s, {len(desc)} chars）")
                            return desc.strip(), True
            diag_list.append(f"📷 {label}（{model_name}）回應為空")
            print(f"⚠️ 視覺模型 {label}（{model_name}）回應為空")
            return "", False
        except asyncio.TimeoutError:
            diag_list.append(f"📷 {label}（{model_name}）逾時（>90s）")
            print(f"⚠️ 視覺模型 {label}（{model_name}）識圖逾時（>90s）")
            return "", False
        except Exception as e:
            _short = str(e)[:100]
            diag_list.append(f"📷 {label}（{model_name}）例外：{_short}")
            print(f"⚠️ 視覺模型 {label}（{model_name}）識圖失敗：{e}")
            return "", False

    # ── Build the model attempt list (degradation chain) ──
    _attempt_list = [(vision_model, api_url, settings["api_key"], "主視覺模型")]

    # Pool-based vision chain (each entry can have different API)
    _pool_vis_chain = _resolve_chain("vision", settings)
    if _pool_vis_chain:
        for _vc_url, _vc_key, _vc_model in _pool_vis_chain:
            _vc_url_norm = _vc_url.rstrip("/")
            if not _vc_url_norm.endswith("/chat/completions"):
                if _vc_url_norm.endswith("/v1") or _vc_url_norm.endswith("/v2"):
                    _vc_url_norm += "/chat/completions"
                else:
                    _vc_url_norm += "/v1/chat/completions"
            _attempt_list.append((_vc_model, _vc_url_norm, _vc_key, f"降級視覺({_vc_model})"))
    else:
        # Legacy: comma-separated model names, same API endpoint
        _chain_raw = settings.get("vision_fallback_chain", "").strip()
        if _chain_raw:
            for _m in _chain_raw.split(","):
                _m = _m.strip()
                if _m and _m != vision_model:
                    _attempt_list.append((_m, api_url, _vision_api_key, f"降級視覺({_m})"))

    # Try the fallback API endpoint as last resort (same logic as chat: all chain models fail → backup API)
    if settings.get("fallback_enabled", False):
        _fb_url = settings.get("fallback_api_url", "").strip()
        _fb_key = settings.get("fallback_api_key", "").strip()
        _fb_model = settings.get("fallback_model", "").strip()
        if _fb_url and _fb_key and _fb_model:
            _fb_url_norm = _fb_url.rstrip("/")
            if not _fb_url_norm.endswith("/chat/completions"):
                if _fb_url_norm.endswith("/v1") or _fb_url_norm.endswith("/v2"):
                    _fb_url_norm += "/chat/completions"
                else:
                    _fb_url_norm += "/v1/chat/completions"
            _attempt_list.append((_fb_model, _fb_url_norm, _fb_key, "備援API識圖"))

    # ── Try each model in order ──
    for i, (model_name, url, key, label) in enumerate(_attempt_list):
        if i > 0:
            print(f"📷 視覺模型降級：嘗試 {label}（{model_name}）...")
        desc, ok = await _try_vision(model_name, url, key, label, _vision_diag)
        if ok and desc:
            return desc

    _vision_diag.append("📷 所有視覺模型均失敗，識圖跳過")
    return ""


_NATION_CATEGORY_LABELS = {"member": "成員國", "council": "理事國", "observer": "觀察國", "removed": "已除籍"}

# Keywords that signal the user is asking about the bot's OWN nation/member
# registry — this data lives ONLY in _member_nations (registered via
# /nation register + the dashboard), never in the wiki (micropedia) and
# never in Discord message history, so neither of those auto-context
# searches can ever surface it. Without this dedicated injector the AI
# would correctly (but uselessly) say "查不到" forever, no matter how much
# the wiki/message-search pipelines improve — the data source itself
# was simply never wired in.
_NATION_REGISTRY_MARKERS = (
    "理事國", "會員國", "成員國", "觀察國", "除籍", "會員名單", "國家名單",
    "代表國", "ISO", "iso", "哪些國家", "有幾個國家", "全部國家", "國家一覽",
)


def _format_nation_registry_context(guild_id: int, query: str) -> str:
    """Format the bot's own member-nation registry (_member_nations) into
    plain text for AI context — filtered to a specific category (成員國/
    理事國/觀察國/已除籍) if the query names one, otherwise all categories
    grouped. This is authoritative, structured data the bot itself owns
    (via /nation register + dashboard), so it's injected as ground truth,
    not as something the AI needs to search for."""
    try:
        entries = [e for e in _member_nations.get("entries", []) if int(e.get("guild_id", 0)) == int(guild_id)]
    except Exception:
        entries = []
    if not entries:
        return ""

    # Narrow to a specific category if the query clearly names one
    cat_filter = None
    if "理事國" in query:
        cat_filter = "council"
    elif "觀察國" in query:
        cat_filter = "observer"
    elif "除籍" in query:
        cat_filter = "removed"
    elif "成員國" in query or "會員國" in query:
        cat_filter = "member"

    if cat_filter:
        entries = [e for e in entries if e.get("category", "member") == cat_filter]
        if not entries:
            label = _NATION_CATEGORY_LABELS.get(cat_filter, cat_filter)
            return f"目前登記為「{label}」的國家：無（尚未有國家被登記在這個類別）"

    cat_order = {"member": 0, "council": 1, "observer": 2, "removed": 3}
    entries = sorted(entries, key=lambda e: cat_order.get(e.get("category", "member"), 99))

    lines = []
    current_cat = None
    for e in entries:
        cat = e.get("category", "member")
        if cat != current_cat:
            current_cat = cat
            lines.append(f"【{_NATION_CATEGORY_LABELS.get(cat, cat)}】")
        reps = e.get("representative_names") or []
        rep_str = "、".join(reps) if reps else "未指定代表"
        lines.append(
            f"- {e.get('name_zh', '?')}（{e.get('name_en', '?')}，ISO：{e.get('iso_code', '?')}）"
            f"代表：{rep_str}"
        )
    return "\n".join(lines)


# ── 文生圖（Text-to-Image）功能 ──
# 偵測聊天中的生圖請求，調用 T2I API 生成圖片，並在回覆中附上圖片。
# 也可以透過 /chat draw 指令直接要求生圖。

import re as _t2i_re

# 生圖請求偵測關鍵詞（用於聊天自動偵測）
_T2I_TRIGGERS = [
    # 中文：明確的生圖指令（結尾要求出現「圖/圖片」字樣）
    r"(?:幫我|請)?畫一?(?:張|個|幅)?(.+)",
    r"生成一?(?:張|幅)?(.+?)(?:的)?圖(?:片)?",
    r"產生一?(?:張|幅)?(.+?)(?:的)?圖(?:片)?",
    r"製作一?(?:張|幅)?(.+?)(?:的)?圖(?:片)?",
    r"畫圖[：: ]+(.+)",
    # 中文：用「張/幅」這種圖畫專用量詞時，不需要再出現「圖」字
    # （例如「生成一張國旗」「製作一幅風景」——量詞本身已經暗示是圖像）
    r"生成一?(?:張|幅)(.+)",
    r"產生一?(?:張|幅)(.+)",
    r"製作一?(?:張|幅)(.+)",
    r"畫一?(?:張|幅)(.+)",
    # 英文
    r"draw (?:me )?(?:a |an |the )?(.+)",
    r"generate (?:a |an |the )?(.+?)(?:image|picture|pic)",
    r"create (?:a |an |the )?(.+?)(?:image|picture|pic)",
    r"make (?:a |an |the )?(.+?)(?:image|picture|pic)",
]

# 否定關鍵詞：包含這些的不要觸發（避免誤判）
_T2I_NEGATIVE = [
    "畫質", "畫面", "畫家", "畫作", "畫展", "畫廊", "圖片品質",
    "截圖", "修圖", "P圖", "動圖", "表情圖",
    "picture quality", "image quality", "screenshot",
]

# T2I 冷卻追蹤
_t2i_cooldowns: dict = {}  # user_id -> last_generation_timestamp
_t2i_daily_usage: dict = {}  # user_id -> {date: count}

# pollinations.ai 免費額度限制「同一 IP 同時只能有 1 個請求在跑」，多人同時 /draw
# 或聊天觸發生圖時若同時送出會直接收到 HTTP 429 "Queue full (max: 1)"。
# 用一個全域鎖把所有 pollinations 請求序列化（真正排隊送出，而不是同時搶著送），
# 避免使用者看到 429 錯誤——代價是多人同時生圖時後面的人要多等一下（正常且預期）。
_t2i_pollinations_lock = asyncio.Lock()

def _check_t2i_rate_limit(user_id: str, settings: dict) -> tuple:
    """Check if user can generate an image. Returns (allowed, reason)."""
    is_owner_user = user_id == "1482256878334640209"
    if is_owner_user and settings.get("t2i_owner_exempt", True):
        return (True, None)

    cooldown = settings.get("t2i_cooldown", 60)
    daily_limit = settings.get("t2i_daily_limit", 10)

    now = _time.time()
    last_gen = _t2i_cooldowns.get(user_id, 0)
    if cooldown > 0 and (now - last_gen) < cooldown:
        remaining = int(cooldown - (now - last_gen))
        return (False, f"⏱️ 文生圖冷卻中，請等待 {remaining} 秒後再試～")

    # Daily limit
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    user_daily = _t2i_daily_usage.setdefault(user_id, {})
    today_count = user_daily.get(today, 0)
    if daily_limit > 0 and today_count >= daily_limit:
        return (False, f"📋 你今天的文生圖額度已用完（每日上限 {daily_limit} 張），明天再來～")

    return (True, None)

def _record_t2i_usage(user_id: str):
    """Record a T2I generation for rate limiting."""
    _t2i_cooldowns[user_id] = _time.time()
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    _t2i_daily_usage.setdefault(user_id, {})[today] = _t2i_daily_usage.get(user_id, {}).get(today, 0) + 1

def _detect_t2i_keyword(text: str) -> bool:
    """快速關鍵字檢測——判斷訊息是否可能為文生圖請求（不需 AI 呼叫）。"""
    if not text:
        return False
    _t2i_keywords = [
        "畫", "生圖", "生成圖", "文生圖", "draw", "畫一張", "畫個",
        "幫我畫", "畫圖", "生成一張", "畫一下", "create image",
        "generate image", "make image", "繪製",
    ]
    text_lower = text.lower()
    for kw in _t2i_keywords:
        if kw in text_lower:
            return True
    return False


async def _detect_t2i_request_ai(text: str, settings: dict) -> str | None:
    """Use AI to determine if a user's message is requesting image generation.
    Returns the image prompt to use if yes, None if no.

    ── 為什麼拆成兩個獨立呼叫，而不是像第一版一樣一次要求「判斷+翻譯成英文」──
    這個 bot 用的是不穩定的弱/免費模型（整個專案history有大量記錄：海龜湯提示
    外洩、波達計票AI判讀等，都是同一個模式——一次丟給弱模型兩件事，它常常
    不遵守嚴格輸出格式，亂回一通）。「判斷是否要生圖」+「同時翻成英文prompt」
    是兩個任務疊在一起，弱模型很容易生出不符合 "IMAGE: ..."/"NO" 格式的雜訊，
    被嚴格 parser 判定「不明確」而放棄——這就是先前版本「還是不生圖」的真正原因。

    新設計：
    1. 第一次呼叫：只問一個超簡單的是非題（是/否），這是弱模型最不容易答錯的任務。
    2. 只有判定「是」時，才用第二次呼叫做英文 prompt 翻譯——這個呼叫只在真正
       要生圖時才發生（頻率低很多），就算翻譯失敗，也不影響「有沒有生圖」這件事，
       直接 fallback 用原始文字送給圖片 API（大部分圖片 API 對中文也有基本支援）。
    3. fallback_mode 改成 "rate_limited"（跟一般聊天一樣用免費降級鏈），
       而不是 "disabled"——這個 API 本身就常常不穩定，"disabled" 代表主模型
       一有狀況就直接放棄判斷，等於整個生圖偵測隨著主模型穩定度隨機失效。
    4. timeout 從 6s 拉長到 10s——這個 API 端點過去多次被記錄為需要 16-20s
       才能穩定回覆，6s 對它來說太緊，時常還沒回來就先判定逾時放棄。
    """
    if not settings.get("t2i_enabled") or not settings.get("t2i_auto_detect"):
        return None
    if not settings.get("t2i_api_url") or not settings.get("t2i_model"):
        return None

    text = text.strip()
    if len(text) < 4:
        return None

    # ── Step 1：極簡是非題判斷 ──
    yn_messages = [
        {
            "role": "system",
            "content": (
                "你只需要判斷一件事：使用者這句話，是不是在要求你「生成/畫/產生/製作一張圖片」。\n"
                "只回答「是」或「否」這一個字，不要有任何其他文字、標點或解釋。\n"
                "判斷原則：\n"
                "- 使用者明確要求產生新圖片（畫一張/生成一張/幫我弄張圖等）→ 是\n"
                "- 使用者只是聊天、問問題、討論事情，或在談論已存在的圖片/截圖 → 否\n"
                "- 語意模糊但傾向想要你創作一張圖 → 是"
            )
        },
        {"role": "user", "content": text[:500]},
    ]

    try:
        yn_result = await call_chat_api(
            yn_messages, settings,
            tools=None,
            max_tokens=10,
            timeout_total=10,
            timeout_read=9,
            is_background=True,
            fallback_mode="rate_limited",
            fallback_user_id="t2i_intent_check",
            category="chat",
        )
        yn_reply = (yn_result.get("content") or "").strip()
        if not yn_reply:
            print("🎨 T2I 意圖判斷：空回覆，視為否")
            return None

        # 寬鬆比對：只要回覆裡「有」肯定字樣就算是，優先檢查否定避免「不是」誤判成「是」
        _neg_markers = ("否", "不是", "不要", "no", "NO", "No")
        _pos_markers = ("是", "對", "yes", "YES", "Yes", "要")
        _is_negative = any(yn_reply.startswith(m) for m in _neg_markers) or yn_reply.strip() in ("否", "不", "no", "No", "NO")
        _is_positive = (not _is_negative) and any(m in yn_reply for m in _pos_markers)

        print(f"🎨 T2I 意圖判斷回覆: 「{yn_reply[:30]}」→ {'是' if _is_positive else '否'}")
        if not _is_positive:
            return None
    except asyncio.TimeoutError:
        print("🎨 T2I 意圖判斷逾時（>10s），跳過（正常聊天）")
        return None
    except Exception as e:
        print(f"🎨 T2I 意圖判斷例外: {type(e).__name__}: {e}，跳過")
        return None

    # ── Step 2：判定要生圖後，再用一次呼叫把中文需求翻成英文 prompt ──
    # 這一步失敗不影響「要不要生圖」的結論，失敗就直接用原始文字當 prompt。
    prompt_to_use = text[:300]
    try:
        translate_messages = [
            {
                "role": "system",
                "content": (
                    "Translate the user's image request into a concise English image-generation "
                    "prompt (max 200 chars). Reply with ONLY the English prompt text, nothing else "
                    "— no quotes, no explanation, no prefix."
                )
            },
            {"role": "user", "content": text[:500]},
        ]
        tr_result = await call_chat_api(
            translate_messages, settings,
            tools=None,
            max_tokens=80,
            timeout_total=10,
            timeout_read=9,
            is_background=True,
            fallback_mode="rate_limited",
            fallback_user_id="t2i_intent_check",
            category="chat",
        )
        tr_reply = (tr_result.get("content") or "").strip()
        tr_reply = tr_reply.strip(chr(34) + chr(39) + chr(96))
        if tr_reply and len(tr_reply) >= 2:
            prompt_to_use = tr_reply[:300]
        else:
            print("🎨 T2I 英文翻譯失敗/空回覆，改用原始文字當 prompt")
    except Exception as e:
        print(f"🎨 T2I 英文翻譯例外（改用原始文字）: {type(e).__name__}: {e}")

    print(f"🎨 AI 判定生圖意圖，prompt: {prompt_to_use[:80]}...")
    return prompt_to_use

async def _t2i_filter_prompt(prompt: str, settings: dict) -> dict:
    """Send the image prompt to a filtering model for safety review BEFORE
    sending it to the image generator. Returns:
      {"allowed": True} — safe to generate
      {"allowed": False, "reason": "..."} — blocked, tell user why

    Uses the model configured via t2i_filter_pool_id (from the AI pool) +
    t2i_filter_model. If the filter is enabled but the model isn't configured
    or the filter API fails, we fail-OPEN (allow) so the bot doesn't block
    all image generation when the filter model has issues — but we print
    a warning so it's visible.
    """
    if not settings.get("t2i_filter_enabled"):
        return {"allowed": True}

    # ── 硬編碼色情黑名單（AI 審查之前先快速擋掉） ──
    # 這些詞彙幾乎只出現在色情/擦邊語境，不需要 AI 判斷即可直接擋掉。
    # 注意：只列色情/性相關詞彙，不列暴力/戰爭/武器相關詞彙（攻城戰等遊戲場景放行）。
    _prompt_lower = prompt.lower()
    _blocklist_en = [
        "nsfw", "nude", "naked", "nudity", "porn", "porno", "pornographic",
        "hentai", "ecchi", "lewd", "explicit sexual", "sexual explicit",
        "18+", "xxx", "erotica", "erotic", "masturbat", "orgasm",
        "intercourse", "genital", "penis", "vagina", "breast", "boob",
        "topless", "bottomless", "undressed", "lingerie", "thong",
        "bikini", "panties", "bra ", "cleavage", "areola", "nipple",
        "ass ", "butt ", "booty", "thighs", "fetish", "bondage",
        "bdsm", "dominatrix", "stripper", "strip club", "sensual",
        "seductive", "provocative", "tit ", "dick ", "cock ", "cum ",
        "creampie", "milf", "gilf", "dilf", "furry", "anthro sex",
        "anthropomorphic sex", "anime girl nude", "anime nude",
        "waifu nude", "rule 34", "rule34", "r34", "cheesecake",
        "pinup", "pin-up", "glamour shot",
    ]
    _blocklist_zh = [
        "裸體", "裸露", "裸體藝術", "全裸", "半裸", "赤裸",
        "色情", "成人", "18禁", "限制級", "情趣", "情色",
        "性交", "做愛", "性愛", "性行為", "自慰", "高潮",
        "乳溝", "胸部", "奶子", "乳頭", "陰部", "陰道",
        "陰莖", "龜頭", "屁股", "翹臀", "內褲", "胸罩",
        "比基尼", "絲襪", "吊帶襪", "兔女郎", "女働裝性感",
        "性感", "誘惑", "媚惑", "騷", "淫", "蕩",
        "獸交", "獸人色情", "擬人色情", "擬人性愛",
        "蘿莉", "正太", "蘿", "兒童色情", "未成年色情",
        "觸手", "觸手怪", "凌辱", "強暴", "性侵",
        "二次元裸", "動漫裸體", "本子", "同人誌", "裏番",
        "肉番", "肉感", "肉體", "肉欲", "肉慾",
    ]
    for _w in _blocklist_en:
        if _w in _prompt_lower:
            print(f"🎨 T2I 提示詞黑名單命中（EN）：{_w}")
            return {"allowed": False, "reason": f"提示詞包含色情/擦邊詞彙：{_w}"}
    for _w in _blocklist_zh:
        if _w in prompt:
            print(f"🎨 T2I 提示詞黑名單命中（ZH）：{_w}")
            return {"allowed": False, "reason": f"提示詞包含色情/擦邊詞彙：{_w}"}

    pool_id = settings.get("t2i_filter_pool_id", "").strip()
    filter_model = settings.get("t2i_filter_model", "").strip()
    timeout_s = int(settings.get("t2i_filter_timeout", 15))
    max_tokens = int(settings.get("t2i_filter_max_tokens", 100))
    strictness = settings.get("t2i_filter_strictness", "medium").lower()
    # 審查嚴格度影響 system prompt 的措辭：
    #   loose  — 只擋明確違規（露骨色情/暴力/CSAM/非法）
    #   medium — 預設，加上暗示性內容、仇恨符號、深度偽造等
    #   strict — medium 基礎上，生成後再用視覺模型複審圖片本身
    # 政策：只擋色情/性相關內容與 CSAM，不擋暴力/血腥/戰爭場景等——
    # 因為攻城戰、戰爭主題等遊戲功能常需要生成持刀持槍、戰鬥場景的圖片，
    # 這些屬於正常創作內容，不應被當成違規。
    strictness_rules = {
        "loose": "Reject ONLY if the prompt explicitly contains hardcore sexual/pornographic content "
                 "or CSAM. Allow suggestive content, artistic nudity, violence, gore, weapons, war/battle "
                 "scenes, and other non-sexual mature content.",
        "medium": "Reject if the prompt contains: sexual/explicit/pornographic content, suggestive/implicit "
                  "sexual content, sexualized anime/furry characters, or CSAM. Also reject borderline terms "
                  "commonly used to bypass filters: furry, hentai, ecchi, waifu nude, rule34, lewd, "
                  " sensual/seductive anime characters. "
                  "Do NOT reject for violence, gore, weapons, war/battle scenes, "
                  "or other non-sexual content — those are allowed.",
        "strict": "Reject if the prompt contains: sexual/explicit/pornographic/suggestive content, "
                  "sexualized anime/furry characters, or CSAM. Also reject borderline terms commonly used "
                  "to bypass filters: furry, hentai, ecchi, waifu nude, rule34, lewd, sensual/seductive "
                  "anime characters, pinup/glamour style. "
                  "Be conservative on sexual content — when in doubt about sexual content, block. "
                  "Do NOT reject for violence, gore, weapons, war/battle scenes, hate symbols, or political "
                  "imagery — those are allowed and out of scope for this filter.",
    }
    review_rules = strictness_rules.get(strictness, strictness_rules["medium"])

    pool = settings.get("ai_pool", [])
    pool_entry = next((e for e in pool if e.get("id") == pool_id), None)
    if not pool_entry:
        print(f"🎨 T2I 過濾：已啟用但找不到池 ID '{pool_id}'，放行（fail-open）")
        return {"allowed": True}

    api_url = pool_entry.get("api_url", "").strip()
    api_key = pool_entry.get("api_key", "").strip()
    if not api_url:
        print("🎨 T2I 過濾：池端點 URL 為空，放行（fail-open）")
        return {"allowed": True}

    if not filter_model:
        # Use the pool entry's default model if not explicitly set
        filter_model = pool_entry.get("model", "").strip()
    if not filter_model:
        print("🎨 T2I 過濾：模型名稱為空，放行（fail-open）")
        return {"allowed": True}

    # Normalize chat completions URL
    chat_url = api_url.rstrip("/")
    if not chat_url.endswith("/chat/completions"):
        if chat_url.endswith("/v1"):
            chat_url += "/chat/completions"
        elif "/v1/" not in chat_url:
            chat_url += "/v1/chat/completions"

    review_prompt = (
        f"You are an image prompt safety reviewer (strictness level: {strictness}). "
        f"{review_rules}\n"
        "If the prompt is safe, reply exactly: SAFE\n"
        "If the prompt is unsafe, reply: BLOCKED: <brief reason in Traditional Chinese>\n"
        f"\nPrompt to review: {prompt[:500]}"
    )

    payload = {
        "model": filter_model,
        "messages": [
            {"role": "system", "content": "You are a content safety filter. Respond with SAFE or BLOCKED: <reason>."},
            {"role": "user", "content": review_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s, connect=10, sock_read=timeout_s - 2)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(chat_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    print(f"🎨 T2I 過濾 API 失敗 (HTTP {resp.status}): {err_text[:200]}，放行（fail-open）")
                    return {"allowed": True}
                try:
                    data = await resp.json()
                except Exception:
                    return {"allowed": True}
                reply_text = ""
                choices = data.get("choices", [])
                if choices:
                    reply_text = choices[0].get("message", {}).get("content", "").strip()
                print(f"🎨 T2I 過濾結果: {reply_text[:100]}")
                if reply_text.upper().startswith("SAFE"):
                    return {"allowed": True}
                elif reply_text.upper().startswith("BLOCKED"):
                    reason = reply_text[len("BLOCKED"):].lstrip(": ").strip()
                    if not reason:
                        reason = "提示詞內容不符安全規範"
                    return {"allowed": False, "reason": reason}
                else:
                    # Ambiguous response — be conservative and allow (the image API
                    # itself usually has its own safety filter as a backstop)
                    print(f"🎨 T2I 過濾回覆不明確，放行: {reply_text[:100]}")
                    return {"allowed": True}
    except asyncio.TimeoutError:
        print(f"🎨 T2I 過濾逾時（{timeout_s}s），放行（fail-open）")
        return {"allowed": True}
    except Exception as e:
        print(f"🎨 T2I 過濾例外: {type(e).__name__}: {e}，放行（fail-open）")
        return {"allowed": True}


async def _t2i_filter_image(image_path: str, prompt: str, settings: dict) -> dict:
    """Post-generation image review using a vision model. Only called in 'strict' mode.
    Sends the generated image to a vision-capable model and asks it to judge
    whether the image itself contains NSFW/harmful content (regardless of what
    the prompt said — the image API might have ignored the text filter).

    Returns:
      {"allowed": True} — image is safe
      {"allowed": False, "reason": "..."} — image is blocked
    Fail-open ONLY on network/infra errors (timeout, 500, connection issues).
    Fail-CLOSED on refusal responses — if the vision model refuses to analyze
    the image or returns an empty/ambiguous response, it likely triggered its
    own NSFW safety filter, so we BLOCK the image. This is critical: NSFW images
    cause vision models to refuse, which old code treated as "ambiguous→allow".
    """
    strictness = settings.get("t2i_filter_strictness", "medium").lower()
    if strictness != "strict":
        return {"allowed": True}
    if not settings.get("t2i_filter_enabled"):
        return {"allowed": True}
    print(f"🎨 T2I 嚴格模式：開始圖片複審（image_path={image_path[:60]}...）")

    # Resolve which vision model to use for image review:
    # 1. t2i_filter_vision_model (explicit setting) → 2. vision_model (main) → 3. skip
    vision_model = settings.get("t2i_filter_vision_model", "").strip()
    api_url = ""
    api_key = ""

    if vision_model:
        # Use the filter pool's endpoint if available (same pool as text filter)
        pool_id = settings.get("t2i_filter_pool_id", "").strip()
        pool_entry = next((e for e in settings.get("ai_pool", []) if e.get("id") == pool_id), None)
        if pool_entry:
            api_url = pool_entry.get("api_url", "").strip()
            api_key = pool_entry.get("api_key", "").strip()
    else:
        # Fall back to the main vision model configuration
        _vis_url, _vis_key, _vis_model = _resolve_role_endpoint("vision", settings)
        vision_model = _vis_model or settings.get("vision_model", "").strip()
        api_url = _vis_url or settings.get("api_url", "").strip()
        api_key = _vis_key or settings.get("api_key", "").strip()

    if not vision_model:
        print("🎨 T2I 嚴格模式：未設定視覺模型，跳過圖片複審（fail-open）")
        return {"allowed": True}
    if not api_url:
        print("🎨 T2I 嚴格模式：視覺 API URL 為空，跳過圖片複審（fail-open）")
        return {"allowed": True}

    # Read the generated image file and convert to base64 data URL
    try:
        import base64 as _b64
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        b64_str = _b64.b64encode(img_bytes).decode("utf-8")
        # Detect mime type from file header
        mime = "image/png"
        if img_bytes[:4] == b"\x89PNG":
            mime = "image/png"
        elif img_bytes[:2] == b"\xff\xd8":
            mime = "image/jpeg"
        elif img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
            mime = "image/webp"
        data_url = f"data:{mime};base64,{b64_str}"
    except Exception as e:
        print(f"🎨 T2I 嚴格模式：讀取圖片失敗: {e}，跳過複審（fail-open）")
        return {"allowed": True}

    # Normalize chat completions URL
    chat_url = api_url.rstrip("/")
    if not chat_url.endswith("/chat/completions"):
        if chat_url.endswith("/v1"):
            chat_url += "/chat/completions"
        elif "/v1/" not in chat_url:
            chat_url += "/v1/chat/completions"

    # 改用「描述圖片」而非「當審查員」的框架——
    # 舊版叫模型扮演「安全審查員」回覆 SAFE/BLOCKED，但很多視覺模型
    # 不願意扮演這個角色，連無害圖片也拒答，導致 100% 誤擋。
    # 修正版叫模型「描述你看到的圖片內容」，再從描述文字中偵測色情關鍵字。
    # — 無害圖片：模型正常描述 → 描述中無色情關鍵字 → 放行
    # — 色情圖片：模型要嘛描述中帶色情字眼（→擋），要嘛拒絕描述（→擋）
    # — 不再需要模型「扮演審查員」，避免角色扮演導致的整體拒答問題
    vision_prompt = (
        "Briefly describe what you see in this image in 1-2 sentences. "
        "Just describe the visual content factually — objects, people, actions, setting. "
        "Do not judge or evaluate whether the content is appropriate.\n"
        f"The image was generated from this prompt: {prompt[:200]}"
    )

    payload = {
        "model": vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": int(settings.get("t2i_filter_max_tokens", 100)),
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout_s = int(settings.get("t2i_filter_timeout", 15))
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s + 15, connect=10, sock_read=timeout_s + 10)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(chat_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    _err_lower = err_text.lower()
                    # HTTP 400/403 + safety/policy/content 字眼 → API 自己判定圖片有問題
                    # HTTP 400/403 + 任何安全/政策相關字眼 → API 自己判定圖片有問題 → 擋
                    # （在「描述圖片」框架下，API 層面的安全拒絕幾乎一定是圖片內容問題，
                    #  因為我們不是叫它審查，只是叫它描述）
                    if resp.status in (400, 403) and any(_w in _err_lower for _w in
                            ("safety", "policy", "content_filter", "content policy",
                             "inappropriate", "nsfw", "explicit", "sexual",
                             "pornograph", "csam", "sensitive")):
                        _reason = f"視覺 API 拒絕分析圖片（HTTP {resp.status}，疑似不當內容）"
                        print(f"🎨 T2I 圖片複審：API 內容政策拒絕 (HTTP {resp.status}): {err_text[:150]}")
                        return {"allowed": False, "reason": _reason}
                    print(f"🎨 T2I 圖片複審 API 失敗 (HTTP {resp.status}): {err_text[:200]}，放行（fail-open）")
                    return {"allowed": True}
                try:
                    data = await resp.json()
                except Exception:
                    return {"allowed": True}
                reply_text = ""
                choices = data.get("choices", [])
                if choices:
                    reply_text = choices[0].get("message", {}).get("content", "").strip()
                print(f"🎨 T2I 圖片複審結果: {reply_text[:200]}")

                # ── 描述式審查：檢查模型回覆中是否包含色情關鍵字 ──
                _reply_upper = reply_text.upper()
                _sexual_keywords = [
                    "NUDITY", "NUDE", "NAKED", "SEXUAL", "PORNOGRAPH",
                    "NSFW", "EXPLICIT SEXUAL", "GENITAL", "BREAST",
                    "INTERCOURSE", "EROTIC", "LEWD", "OBSCENE",
                    "CSAM", "CHILD PORNOGRAPH", "UNDERAGE", "MINOR IN",
                    "NOT SAFE FOR WORK",
                ]
                # 偵測拒答模式（模型的安全過濾觸發）
                _refusal_markers = [
                    "I CAN'T", "I CANNOT", "I'M SORRY", "I AM SORRY",
                    "I'M UNABLE", "I AM UNABLE", "UNABLE TO",
                    "REFUSE", "I WON'T", "I WILL NOT", "AGAINST MY",
                    "INAPPROPRIATE", "VIOLATES", "CONTENT POLICY",
                    "NOT ABLE TO", "CANNOT ASSIST", "CAN'T ASSIST",
                    "I'M NOT ABLE", "NOT APPROPRIATE",
                ]
                _is_refusal = any(_m in _reply_upper for _m in _refusal_markers)
                _has_sexual_kw = any(_m in _reply_upper for _m in _sexual_keywords)

                # 情況1：描述中包含色情關鍵字 → 圖片有色情內容 → 擋
                if _has_sexual_kw:
                    _reason = f"圖片描述偵測到色情內容：{reply_text[:100]}"
                    print(f"🎨 T2I 圖片複審：描述含色情關鍵字，BLOCKED: {_reason[:120]}")
                    return {"allowed": False, "reason": _reason}

                # 情況2：模型拒絕描述圖片 → 圖片很可能觸發了模型的安全過濾 → 擋
                # （新框架是「描述圖片」而非「審查圖片」，模型不會因為不想扮演
                #  審查員而拒絕——如果連單純的描述都拒絕，通常是圖片本身有問題）
                if _is_refusal or not reply_text:
                    _reason = "視覺模型拒絕描述圖片（疑似不當內容觸發安全過濾）"
                    if _is_refusal and reply_text:
                        _reason = f"視覺模型拒絕描述：{reply_text[:80]}"
                    print(f"🎨 T2I 圖片複審：模型拒答，fail-closed BLOCKED: {_reason[:100]}")
                    return {"allowed": False, "reason": _reason}

                # 情況3：模型正常描述了圖片，描述中無色情關鍵字 → 放行
                print(f"🎨 T2I 圖片複審：描述正常，放行: {reply_text[:150]}")
                return {"allowed": True}
    except asyncio.TimeoutError:
        print(f"🎨 T2I 圖片複審逾時（{timeout_s+15}s），放行（fail-open）")
        return {"allowed": True}
    except Exception as e:
        print(f"🎨 T2I 圖片複審例外: {type(e).__name__}: {e}，放行（fail-open）")
        return {"allowed": True}


async def _generate_image(prompt: str, settings: dict) -> dict:
    """Generate an image. Tries premium channel first (if enabled + quota remaining),
    falls back to default channel on any failure.

    Returns {"success": True, "image_url"/"image_path": ..., "channel": "premium"/"default"}
    or {"success": False, "error": ...}
    """
    # ── 提示詞安全過濾（在生圖之前先審查） ──
    filter_result = await _t2i_filter_prompt(prompt, settings)
    if not filter_result.get("allowed"):
        reason = filter_result.get("reason", "提示詞內容不符安全規範")
        print(f"🎨 T2I 提示詞被過濾攔截: {reason[:100]}")
        return {"success": False, "error": f"🚫 提示詞被安全過濾攔截：{reason}", "filtered": True}

    # ── 高級生圖通道（優先嘗試，失敗自動降級） ──
    premium_error = None
    premium_skip_reason = None
    premium_ok = _t2i_premium_available(settings)
    if settings.get("t2i_premium_enabled") and not premium_ok:
        if not settings.get("t2i_premium_api_url") or not settings.get("t2i_premium_model"):
            premium_skip_reason = "已啟用但 URL 或模型名稱未填"
        else:
            premium_skip_reason = f"今日額度已用完（{settings.get('t2i_premium_daily_count', 0)}/{settings.get('t2i_premium_daily_limit', 30)}）"

    if premium_ok:
        premium_settings = {
            "t2i_api_url": settings.get("t2i_premium_api_url", "").strip(),
            "t2i_api_key": settings.get("t2i_premium_api_key", "").strip(),
            "t2i_model": settings.get("t2i_premium_model", "").strip(),
            "t2i_size": settings.get("t2i_premium_size", "").strip() or settings.get("t2i_size", "1024x1024"),
            "t2i_quality": settings.get("t2i_premium_quality", "").strip() or settings.get("t2i_quality", "standard"),
        }
        print(f"🎨 T2I 嘗試高級通道: {premium_settings['t2i_model']} @ {premium_settings['t2i_api_url'][:40]}")
        premium_result = await _generate_image_core(prompt, premium_settings)
        if premium_result.get("success"):
            _t2i_premium_consume(settings)
            premium_result["channel"] = "premium"
            print(f"🎨 T2I 高級通道成功（今日已用 {settings.get('t2i_premium_daily_count', 0)}/{settings.get('t2i_premium_daily_limit', 30)}）")
            # ── 嚴格模式：生成後用視覺模型複審圖片 ──
            if settings.get("t2i_filter_enabled") and settings.get("t2i_filter_strictness", "medium").lower() == "strict":
                _img_p = premium_result.get("image_path")
                _img_url = premium_result.get("image_url")
                # 如果只有 URL 沒有檔案，先下載成檔案供視覺模型審查
                if not _img_p and _img_url:
                    try:
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as _dl:
                            async with _dl.get(_img_url) as _dl_r:
                                if _dl_r.status == 200:
                                    _ib = await _dl_r.read()
                                    _ct = _dl_r.headers.get("Content-Type", "image/png")
                                    _ex = "jpg" if ("jpeg" in _ct or "jpg" in _ct) else ("webp" if "webp" in _ct else "png")
                                    _img_p = os.path.join(DATA_DIR, f"t2i_review_{int(_time.time()*1000)}.{_ex}")
                                    with open(_img_p, "wb") as _f:
                                        _f.write(_ib)
                                    premium_result["image_path"] = _img_p
                    except Exception as _e:
                        print(f"🎨 T2I 嚴格模式：下載圖片失敗，跳過複審: {_e}")
                if _img_p:
                    _img_review = await _t2i_filter_image(_img_p, prompt, settings)
                    if not _img_review.get("allowed"):
                        _reason = _img_review.get("reason", "圖片內容不符安全規範")
                        print(f"🎨 T2I 嚴格模式圖片複審攔截: {_reason[:100]}")
                        try:
                            os.remove(_img_p)
                        except Exception:
                            pass
                        return {"success": False, "error": f"🚫 生成的圖片未通過安全複審：{_reason}", "filtered": True}
            return premium_result
        else:
            premium_error = premium_result.get("error", "未知錯誤")
            print(f"🎨 T2I 高級通道失敗，降級回預設通道: {premium_error[:150]}")

    # ── 預設生圖通道 ──
    result = await _generate_image_core(prompt, settings)
    if result.get("success"):
        result["channel"] = "default"
        # ── 嚴格模式：生成後用視覺模型複審圖片 ──
        if settings.get("t2i_filter_enabled") and settings.get("t2i_filter_strictness", "medium").lower() == "strict":
            _img_p = result.get("image_path")
            _img_url = result.get("image_url")
            # 如果只有 URL 沒有檔案，先下載成檔案供視覺模型審查
            if not _img_p and _img_url:
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as _dl:
                        async with _dl.get(_img_url) as _dl_r:
                            if _dl_r.status == 200:
                                _ib = await _dl_r.read()
                                _ct = _dl_r.headers.get("Content-Type", "image/png")
                                _ex = "jpg" if ("jpeg" in _ct or "jpg" in _ct) else ("webp" if "webp" in _ct else "png")
                                _img_p = os.path.join(DATA_DIR, f"t2i_review_{int(_time.time()*1000)}.{_ex}")
                                with open(_img_p, "wb") as _f:
                                    _f.write(_ib)
                                result["image_path"] = _img_p
                except Exception as _e:
                    print(f"🎨 T2I 嚴格模式：下載圖片失敗，跳過複審: {_e}")
            if _img_p:
                _img_review = await _t2i_filter_image(_img_p, prompt, settings)
                if not _img_review.get("allowed"):
                    _reason = _img_review.get("reason", "圖片內容不符安全規範")
                    print(f"🎨 T2I 嚴格模式圖片複審攔截: {_reason[:100]}")
                    try:
                        os.remove(_img_p)
                    except Exception:
                        pass
                    return {"success": False, "error": f"🚫 生成的圖片未通過安全複審：{_reason}", "filtered": True}
    # 把高級通道失敗/跳過的原因也帶上，這樣 ai-log 才能顯示「高級通道當時為什麼沒用到」，
    # 不然使用者永遠只看得到最終成功用了預設通道，猜不出高級通道到底發生了什麼事。
    if premium_error:
        result["premium_error"] = premium_error
    elif premium_skip_reason:
        result["premium_skip_reason"] = premium_skip_reason
    return result


def _t2i_premium_available(settings: dict) -> bool:
    """Check if the premium T2I channel is enabled and has remaining daily quota."""
    if not settings.get("t2i_premium_enabled"):
        return False
    if not settings.get("t2i_premium_api_url") or not settings.get("t2i_premium_model"):
        return False
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    if settings.get("t2i_premium_daily_date", "") != today:
        return True  # 新的一天，額度重置
    daily_limit = settings.get("t2i_premium_daily_limit", 30)
    daily_count = settings.get("t2i_premium_daily_count", 0)
    return daily_count < daily_limit


def _t2i_premium_consume(settings: dict):
    """Increment the premium daily counter (and reset if it's a new day)."""
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    if settings.get("t2i_premium_daily_date", "") != today:
        settings["t2i_premium_daily_date"] = today
        settings["t2i_premium_daily_count"] = 0
    settings["t2i_premium_daily_count"] = settings.get("t2i_premium_daily_count", 0) + 1
    # 持久化到磁碟 + Drive，確保重啟後額度計數不丟失
    try:
        save_chat_ai_settings()
    except Exception as e:
        print(f"⚠️ T2I premium quota save failed: {e}")


async def _generate_image_core(prompt: str, settings: dict) -> dict:
    """Call T2I API to generate an image from a text prompt.
    Returns: {"success": True, "image_url": "...", "revised_prompt": "..."} or
             {"success": False, "error": "..."}

    Strategy: if the premium channel is enabled and has remaining daily quota, try it
    first. On any failure (HTTP error, timeout, quota exceeded), automatically fall back
    to the default channel (t2i_*). This means the user always gets an image as long as
    at least one channel is working — the premium quota is just a "best effort" upgrade.

    Supports two very different API shapes:
      1. pollinations.ai's native image API — GET https://image.pollinations.ai/prompt/{prompt}
         with query params (width/height/seed/model/token). The prompt goes in the URL
         PATH, not a JSON body — a POST with a JSON body to a made-up /v1/images/generations
         endpoint gets silently ignored by their server (it returns *a* image, but not one
         based on your prompt, which looked like "always the same image" bug).
      2. OpenAI-compatible POST /v1/images/generations with JSON body — for providers that
         actually implement that spec (DALL-E, and many OpenAI-compatible proxies).
         Response may be JSON {"data": [{"url"/"b64_json": ...}]} OR raw image bytes
         (some proxies return the image directly with Content-Type: image/*).
    """
    import random as _random
    from urllib.parse import quote as _urlquote

    api_url = settings.get("t2i_api_url", "").strip()
    api_key = settings.get("t2i_api_key", "").strip()
    model = settings.get("t2i_model", "").strip()
    size = settings.get("t2i_size", "1024x1024")
    quality = settings.get("t2i_quality", "standard")

    if not api_url:
        return {"success": False, "error": "文生圖 API 未設定完整（需要 URL）"}

    try:
        w, h = size.split("x")
        w, h = int(w), int(h)
    except Exception:
        w, h = 1024, 1024

    # 隨機 seed — 避免同樣的 prompt 每次都生成同一張圖
    seed = _random.randint(1, 2_147_483_647)

    # ══════════════════════════════════════════════════════════════
    # 分支 1：pollinations.ai 原生 GET API（prompt 放在網址路徑裡）
    # ══════════════════════════════════════════════════════════════
    if "pollinations.ai" in api_url.lower() and "gen.pollinations.ai" not in api_url.lower():
        encoded_prompt = _urlquote(prompt[:1000], safe="")
        poll_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        params = {
            "width": w,
            "height": h,
            "seed": seed,
            "nologo": "true",
        }
        if model:
            params["model"] = model
        if api_key:
            params["token"] = api_key

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # pollinations 免費額度同時只允許 1 個請求在跑，用全域鎖排隊送出——
        # 多人同時 /draw 時後面的人會在這裡等，而不是一起送出去互相 429。
        # 另外保留 2 次 429 重試（隨機延遲），防範鎖之外仍偶發撞到限流的邊界情況
        # （例如伺服器端還有其他來源共用同一組 IP 額度）。
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            async with _t2i_pollinations_lock:
                try:
                    timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_read=100)
                    async with aiohttp.ClientSession(timeout=timeout) as sess:
                        async with sess.get(poll_url, params=params, headers=headers) as resp:
                            if resp.status == 429:
                                err_text = await resp.text()
                                print(f"🎨 T2I (pollinations) 429 限流 (第{attempt}次): {err_text[:200]}")
                                should_retry = attempt < max_attempts
                            elif resp.status != 200:
                                err_text = await resp.text()
                                print(f"🎨 T2I (pollinations) 失敗 (HTTP {resp.status}): {err_text[:300]}")
                                return {"success": False, "error": f"API 回應 HTTP {resp.status}: {err_text[:200]}"}
                            else:
                                content_type = (resp.headers.get("Content-Type") or "").lower()
                                image_bytes = await resp.read()

                                if not content_type.startswith("image/") and len(image_bytes) < 2000:
                                    # 太小又不是圖片格式，八成是錯誤訊息本體
                                    print(f"🎨 T2I (pollinations) 回應非圖片: {image_bytes[:300]}")
                                    return {"success": False, "error": f"API 未回傳圖片: {image_bytes[:200]}"}

                                ext = "jpg"
                                if "png" in content_type:
                                    ext = "png"
                                elif "webp" in content_type:
                                    ext = "webp"
                                image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.{ext}")
                                with open(image_path, "wb") as f:
                                    f.write(image_bytes)
                                print(f"🎨 T2I (pollinations) 成功: {prompt[:50]}... seed={seed}")
                                return {"success": True, "image_path": image_path, "model": model or "pollinations-default"}
                except asyncio.TimeoutError:
                    return {"success": False, "error": "文生圖 API 逾時（超過 120 秒）"}
                except Exception as e:
                    print(f"🎨 T2I (pollinations) 異常: {type(e).__name__}: {e}")
                    return {"success": False, "error": f"生圖過程發生錯誤: {str(e)[:200]}"}

            # 429 時鎖外等一下再重試（讓鎖釋放給其他排隊中的請求，且給伺服器端喘息時間）
            if attempt < max_attempts:
                await asyncio.sleep(1.5 * attempt + _random.random())

        return {"success": False, "error": "文生圖 API 目前太忙（多次請求都被限流），請稍後再試"}

    # ══════════════════════════════════════════════════════════════
    # 分支 2：Google Imagen 原生 predict API（generativelanguage.googleapis.com）
    # ══════════════════════════════════════════════════════════════
    # Google 的 Gemini OpenAI 相容層（.../v1beta/openai）目前不支援圖片生成，
    # 只支援 chat/completions 和 embeddings，用 OpenAI 格式打過去只會 404。
    # Imagen 系列模型要用完全不同的原生格式：
    #   POST https://generativelanguage.googleapis.com/v1beta/models/{model}:predict
    #   headers: x-goog-api-key
    #   body: {"instances": [{"prompt": ...}], "parameters": {"sampleCount": 1, "aspectRatio": "16:9"}}
    #   response: {"predictions": [{"bytesBase64Encoded": "...", "mimeType": "image/png"}]}
    # 這裡不用寬高像素（那是 DALL-E 的概念），Imagen 只接受固定的長寬比字串，
    # 所以要把使用者選的 WxH 換算成最接近的合法比例，而不是硬塞像素數字進去
    # （硬塞像素或用 OpenAI 的 size 概念會導致圖片被伺服器內部拉伸變形）。
    if "generativelanguage.googleapis.com" in api_url.lower():
        if not model:
            return {"success": False, "error": "文生圖 API 未設定完整（需要模型名稱，例如 imagen-3.0-fast-generate-001）"}
        if not api_key:
            return {"success": False, "error": "Google Imagen API 需要 API Key"}

        def _size_to_aspect_ratio(width: int, height: int) -> str:
            ratio = width / height if height else 1.0
            candidates = {"1:1": 1.0, "3:4": 0.75, "4:3": 4/3, "9:16": 9/16, "16:9": 16/9}
            return min(candidates.items(), key=lambda kv: abs(kv[1] - ratio))[0]

        aspect_ratio = _size_to_aspect_ratio(w, h)
        model_clean = model.strip().lstrip("/")
        predict_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_clean}:predict"
        payload = {
            "instances": [{"prompt": prompt[:1000]}],
            "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio},
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

        try:
            timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_read=100)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(predict_url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        print(f"🎨 T2I (Google Imagen) 失敗 (HTTP {resp.status}): {err_text[:300]}")
                        return {"success": False, "error": f"Google Imagen API 回應 HTTP {resp.status}: {err_text[:200]}"}
                    try:
                        data = await resp.json()
                    except Exception as je:
                        return {"success": False, "error": f"Google Imagen 回應無法解析: {je}"}

                    predictions = data.get("predictions") or []
                    if not predictions or not predictions[0].get("bytesBase64Encoded"):
                        print(f"🎨 T2I (Google Imagen) 回應格式無法解析: {str(data)[:300]}")
                        return {"success": False, "error": "Google Imagen 回應未包含圖片資料（可能是內容政策擋下或額度用盡）"}

                    import base64 as _b64
                    b64_data = predictions[0]["bytesBase64Encoded"]
                    mime_type = predictions[0].get("mimeType", "image/png")
                    ext = "png"
                    if "jpeg" in mime_type or "jpg" in mime_type:
                        ext = "jpg"
                    elif "webp" in mime_type:
                        ext = "webp"
                    image_bytes = _b64.b64decode(b64_data)
                    image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.{ext}")
                    with open(image_path, "wb") as f:
                        f.write(image_bytes)
                    print(f"🎨 T2I (Google Imagen) 成功: {prompt[:50]}... model={model_clean} aspect={aspect_ratio}")
                    return {"success": True, "image_path": image_path, "model": model_clean}

        except asyncio.TimeoutError:
            return {"success": False, "error": "Google Imagen API 逾時（超過 120 秒）"}
        except Exception as e:
            print(f"🎨 T2I (Google Imagen) 異常: {type(e).__name__}: {e}")
            return {"success": False, "error": f"生圖過程發生錯誤: {str(e)[:200]}"}

    # ══════════════════════════════════════════════════════════════
    # 分支 3：Hugging Face Inference API（router.huggingface.co，新版路由）
    # ══════════════════════════════════════════════════════════════
    # HF 的文生圖 API 跟 OpenAI 完全不同：
    #   - URL: POST https://router.huggingface.co/hf-inference/models/{model}
    #     （2025 年底舊版 api-inference.huggingface.co 已完全停用下線，
    #      DNS 直接連不到，一律要改走新的 router.huggingface.co）
    #   - Body: {"inputs": "prompt text", "parameters": {"width": W, "height": H, "seed": S}}
    #   - Response: 原始圖片二進位（raw bytes），不是 JSON
    #   - Auth: Bearer hf_xxxx
    # 注意：model 是放在 URL path 裡，不是 JSON body 裡。
    if "huggingface.co" in api_url.lower():
        if not model:
            return {"success": False, "error": "文生圖 API 未設定完整（需要模型名稱，例如 black-forest-labs/FLUX.1-dev）"}
        if not api_key:
            return {"success": False, "error": "Hugging Face API 需要 API Token（hf_...）"}

        # 組裝 URL：
        # - 舊版 api-inference.huggingface.co 已下線，強制改寫成新版 router
        # - 使用者若填 router.huggingface.co 且指定了特定 provider（如 /fal-ai、
        #   /together），保留該 provider 路徑；否則預設走官方 hf-inference provider
        hf_url = api_url.rstrip("/")
        if "api-inference.huggingface.co" in hf_url.lower():
            # 舊網域整個丟棄改用新版，provider 預設用 hf-inference
            hf_url = "https://router.huggingface.co/hf-inference"
        elif "router.huggingface.co" in hf_url.lower():
            # 已經是新版 — 去掉可能存在的 /models/... 尾巴，只保留 provider 路徑部分
            if "/models/" in hf_url:
                hf_url = hf_url.split("/models/", 1)[0]
            # 使用者只填了 https://router.huggingface.co（沒指定 provider）→ 補上預設 provider
            if hf_url.rstrip("/") == "https://router.huggingface.co":
                hf_url = "https://router.huggingface.co/hf-inference"
        else:
            # 不認得的網域但含 huggingface.co（理論上不太會發生）— 直接改走新版預設
            hf_url = "https://router.huggingface.co/hf-inference"

        hf_url = hf_url.rstrip("/") + "/models/" + model

        payload = {
            "inputs": prompt[:1000],
            "parameters": {
                "width": w,
                "height": h,
                "seed": seed,
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # HF 模型第一次呼叫可能需要 cold start（模型載入），給 180 秒
        try:
            timeout = aiohttp.ClientTimeout(total=180, connect=15, sock_read=150)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(hf_url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        print(f"🎨 T2I (HuggingFace) 失敗 (HTTP {resp.status}): {err_text[:300]}")
                        if resp.status == 503:
                            return {"success": False, "error": "Hugging Face 模型正在載入中（cold start），請稍後再試"}
                        return {"success": False, "error": f"Hugging Face API 回應 HTTP {resp.status}: {err_text[:200]}"}

                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    image_bytes = await resp.read()

                    # HF 正常回應是 image/png 或 image/jpeg 的二進位
                    if content_type.startswith("image/"):
                        ext = "png"
                        if "jpeg" in content_type or "jpg" in content_type:
                            ext = "jpg"
                        elif "webp" in content_type:
                            ext = "webp"
                        image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.{ext}")
                        with open(image_path, "wb") as f:
                            f.write(image_bytes)
                        print(f"🎨 T2I (HuggingFace) 成功: {prompt[:50]}... model={model} seed={seed}")
                        return {"success": True, "image_path": image_path, "model": model}

                    # HF 有時回 JSON 錯誤而非圖片
                    if content_type.startswith("application/json"):
                        try:
                            err_data = await resp.json()
                            err_msg = err_data.get("error") or str(err_data)[:200]
                        except Exception:
                            err_msg = image_bytes[:200].decode("utf-8", errors="replace")
                        print(f"🎨 T2I (HuggingFace) 回應非圖片: {err_msg}")
                        return {"success": False, "error": f"Hugging Face API 回應錯誤: {err_msg}"}

                    # 未知 Content-Type 但內容看起來像圖片（magic number 偵測）
                    if image_bytes[:4] == b"\x89PNG" or image_bytes[:2] == b"\xff\xd8":
                        ext = "png" if image_bytes[:4] == b"\x89PNG" else "jpg"
                        image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.{ext}")
                        with open(image_path, "wb") as f:
                            f.write(image_bytes)
                        print(f"🎨 T2I (HuggingFace) 成功（magic number 偵測）: {prompt[:50]}... model={model} seed={seed}")
                        return {"success": True, "image_path": image_path, "model": model}

                    # 既不是圖片也不是 JSON
                    err_preview = image_bytes[:300].decode("utf-8", errors="replace")
                    print(f"🎨 T2I (HuggingFace) 未知回應類型 ({content_type}): {err_preview}")
                    return {"success": False, "error": f"Hugging Face API 回應無法識別 (Content-Type={content_type}): {err_preview[:150]}"}

        except asyncio.TimeoutError:
            return {"success": False, "error": "Hugging Face API 逾時（超過 180 秒，可能模型正在 cold start）"}
        except Exception as e:
            print(f"🎨 T2I (HuggingFace) 異常: {type(e).__name__}: {e}")
            return {"success": False, "error": f"生圖過程發生錯誤: {str(e)[:200]}"}

    # ══════════════════════════════════════════════════════════════
    # 分支 4：OpenAI 相容 POST /v1/images/generations
    # ══════════════════════════════════════════════════════════════
    if not model:
        return {"success": False, "error": "文生圖 API 未設定完整（需要模型名稱）"}

    # Normalize URL — accept both /images/generations and base URLs
    url = api_url
    if not url.endswith("/images/generations"):
        if url.endswith("/v1"):
            url += "/images/generations"
        else:
            url = url.rstrip("/") + "/v1/images/generations"

    payload = {
        "model": model,
        "prompt": prompt[:1000],  # Most APIs cap at 1000 chars
        "n": 1,
        "size": size,
        "width": w,
        "height": h,
        "seed": seed,
    }
    # DALL-E specific: quality parameter
    if quality and "dall-e" in model.lower():
        payload["quality"] = quality
        payload["response_format"] = "url"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_read=100)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    print(f"🎨 T2I API 失敗 (HTTP {resp.status}): {err_text[:300]}")
                    return {"success": False, "error": f"API 回應 HTTP {resp.status}: {err_text[:200]}"}

                content_type = (resp.headers.get("Content-Type") or "").lower()

                # ── 情況 1：伺服器直接回傳圖片二進位（非 JSON）──
                # 有些 OpenAI 相容代理成功時 Content-Type 是 image/jpeg、image/png 等，
                # 這種情況絕對不能呼叫 resp.json()，直接把整個 body 當圖片下載存檔。
                if content_type.startswith("image/"):
                    image_bytes = await resp.read()
                    ext = "png"
                    if "jpeg" in content_type or "jpg" in content_type:
                        ext = "jpg"
                    elif "webp" in content_type:
                        ext = "webp"
                    elif "gif" in content_type:
                        ext = "gif"
                    image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.{ext}")
                    with open(image_path, "wb") as f:
                        f.write(image_bytes)
                    print(f"🎨 T2I 成功（原始圖片回應，Content-Type={content_type}）: {prompt[:50]}... seed={seed}")
                    return {"success": True, "image_path": image_path, "model": model}

                # ── 情況 2：JSON 包裝格式（OpenAI 相容）──
                try:
                    data = await resp.json()
                except Exception as je:
                    # 宣稱是 JSON 但解析失敗，或 Content-Type 判斷有誤：
                    # fallback 直接把 body 當圖片存檔，總比丟一個解析錯誤訊息給使用者好。
                    raw_body = await resp.read()
                    if raw_body[:8] not in (b"", b"null"):
                        image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.png")
                        with open(image_path, "wb") as f:
                            f.write(raw_body)
                        print(f"🎨 T2I 成功（JSON 解析失敗但已當圖片存檔，Content-Type={content_type}）: {prompt[:50]}...")
                        return {"success": True, "image_path": image_path, "model": model}
                    print(f"🎨 T2I JSON 解析失敗: {je}")
                    return {"success": False, "error": f"回應無法解析（Content-Type={content_type}）: {je}"}

                # Handle different response formats:
                # OpenAI: {"data": [{"url": "...", "revised_prompt": "..."}]}
                # OpenAI b64: {"data": [{"b64_json": "..."}]}
                # Some providers: {"images": [{"url": "..."}]}
                # Others: {"output": [{"url": "..."}]}
                image_url = None
                b64_data = None
                revised_prompt = None

                items = data.get("data") or data.get("images") or data.get("output") or []
                if items and isinstance(items, list):
                    item = items[0]
                    image_url = item.get("url") or item.get("image_url")
                    b64_data = item.get("b64_json") or item.get("b64")
                    revised_prompt = item.get("revised_prompt")

                if not image_url and not b64_data:
                    print(f"🎨 T2I API 回應格式無法解析: {str(data)[:300]}")
                    return {"success": False, "error": "API 回應格式無法識別圖片 URL"}

                # If b64 data, save to temp file and return as file path
                image_path = None
                if b64_data and not image_url:
                    import base64 as _b64
                    image_bytes = _b64.b64decode(b64_data)
                    image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.png")
                    with open(image_path, "wb") as f:
                        f.write(image_bytes)

                result = {"success": True, "model": model}
                # 如果 API 回傳 URL 而非 b64，先下載成檔案——這樣嚴格模式的
                # 視覺複審才能讀取圖片檔。b64 的路徑已經存檔了，不需要再下載。
                if image_url and not image_path:
                    try:
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as _dl_sess:
                            async with _dl_sess.get(image_url) as _dl_resp:
                                if _dl_resp.status == 200:
                                    _img_bytes = await _dl_resp.read()
                                    _ct = _dl_resp.headers.get("Content-Type", "image/png")
                                    _ext = "png"
                                    if "jpeg" in _ct or "jpg" in _ct:
                                        _ext = "jpg"
                                    elif "webp" in _ct:
                                        _ext = "webp"
                                    image_path = os.path.join(DATA_DIR, f"t2i_{int(_time.time()*1000)}_{seed}.{_ext}")
                                    with open(image_path, "wb") as _f:
                                        _f.write(_img_bytes)
                                    print(f"🎨 T2I 已下載圖片到本機（供嚴格複審用）: {image_path}")
                    except Exception as _dl_err:
                        print(f"🎨 T2I 下載圖片失敗（嚴格複審將無法執行）: {_dl_err}")
                if image_url:
                    result["image_url"] = image_url
                if image_path:
                    result["image_path"] = image_path
                if revised_prompt:
                    result["revised_prompt"] = revised_prompt

                print(f"🎨 T2I 成功: {prompt[:50]}... → {'URL' if image_url else 'b64 file'} seed={seed}")
                return result

    except asyncio.TimeoutError:
        return {"success": False, "error": "文生圖 API 逾時（超過 120 秒）"}
    except Exception as e:
        print(f"🎨 T2I 異常: {type(e).__name__}: {e}")
        return {"success": False, "error": f"生圖過程發生錯誤: {str(e)[:200]}"}

async def _send_t2i_log(guild, user, prompt: str, result: dict, elapsed_ms: int = None):
    """把每一次生圖記錄到 ai-log 頻道——跟文字對話紀錄（_send_chat_log）分開，
    專門顯示這次是走「高級通道」還是「預設通道」、用了哪個模型、耗時多久、
    成功或失敗。這樣擁有者不需要 Render log 存取權限，光看 Discord 頻道
    就能確認高級生圖通道是不是真的有被呼叫到（而不是悄悄降級卻沒發現）。"""
    if not chat_ai_settings.get("log_channel_id"):
        return
    if not guild:
        return
    try:
        log_ch, err = await _resolve_log_channel(guild)
    except Exception as e:
        print(f"⚠️ T2I 紀錄發送失敗（_resolve_log_channel 例外）：{e}")
        return
    if not log_ch:
        print(f"⚠️ T2I 紀錄發送失敗：{err}")
        return

    success = result.get("success", False)
    filtered = result.get("filtered", False)
    channel_used = result.get("channel", "?")
    channel_label = {"premium": "✨ 高級通道", "default": "🎨 預設通道"}.get(channel_used, channel_used or "?")
    model_used = result.get("model") or "?"

    if filtered:
        _embed_color = discord.Color.red()
        _title = "🚫 文生圖提示詞過濾攔截"
    elif success and channel_used == "premium":
        _embed_color = discord.Color.gold()
        _title = "🎨 文生圖紀錄"
    elif success:
        _embed_color = discord.Color.blue()
        _title = "🎨 文生圖紀錄"
    else:
        _embed_color = discord.Color.red()
        _title = "🎨 文生圖紀錄"

    embed = discord.Embed(
        title=_title,
        color=_embed_color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="👤 使用者", value=(user.display_name if user else "?"), inline=True)
    embed.add_field(name="📡 通道", value=channel_label, inline=True)
    embed.add_field(name="🤖 模型", value=model_used, inline=True)
    prompt_text = prompt[:300] + ("..." if len(prompt) > 300 else "")
    embed.add_field(name="📝 提示詞", value=f"> {prompt_text}", inline=False)
    if elapsed_ms is not None:
        embed.add_field(name="⏱️ 耗時", value=f"{elapsed_ms}ms", inline=True)
    if success:
        embed.add_field(name="✅ 狀態", value="成功", inline=True)
    else:
        error_text = str(result.get("error", "未知錯誤"))[:500]
        embed.add_field(name="❌ 狀態", value=f"失敗：{error_text}", inline=False)

    # ── 高級通道診斷 ── 就算最終用預設通道成功了，也要讓使用者看得到
    # 「高級通道當時到底發生了什麼事」，不然設定了高級通道卻一直用不到，
    # 完全沒有線索可以自己排查（這正是加這個 log 的初衷）。
    premium_err = result.get("premium_error")
    premium_skip = result.get("premium_skip_reason")
    if premium_err:
        embed.add_field(name="⚠️ 高級通道失敗原因", value=str(premium_err)[:500], inline=False)
    elif premium_skip:
        embed.add_field(name="ℹ️ 高級通道未使用原因", value=premium_skip, inline=False)

    try:
        await log_ch.send(embed=embed)
        print(f"📝 生圖紀錄已發送到 #{log_ch.name}（通道={channel_used}, 模型={model_used}）")
    except Exception as e:
        print(f"⚠️ T2I 紀錄發送例外：{e}")


async def _send_t2i_result(message, prompt: str, result: dict, settings: dict, is_command: bool = False):
    """Send T2I result to Discord — download image and send as file attachment."""
    # 不管成功或失敗都記錄到 ai-log 頻道（fire-and-forget，不阻塞使用者的回覆）
    try:
        asyncio.ensure_future(_send_t2i_log(message.guild, message.author, prompt, result))
    except Exception as _log_e:
        print(f"⚠️ T2I 紀錄排程失敗: {_log_e}")

    if not result.get("success"):
        error_msg = result.get("error", "未知錯誤")
        if result.get("filtered"):
            # 提示詞被安全過濾攔截——用不同的語氣，讓使用者知道這是內容審查不是技術故障
            if is_command:
                await message.reply(f"🚫 提示詞被安全過濾攔截：{error_msg.replace('🚫 提示詞被安全過濾攔截：', '')}", mention_author=False)
            else:
                await message.reply(f"🚫 這個提示詞沒辦法生圖喔：{error_msg.replace('🚫 提示詞被安全過濾攔截：', '')}", mention_author=False)
        elif is_command:
            await message.reply(f"❌ 文生圖失敗：{error_msg}", mention_author=False)
        else:
            await message.reply(f"🎨 生圖失敗了：{error_msg}", mention_author=False)
        return

    image_url = result.get("image_url")
    image_path = result.get("image_path")
    revised_prompt = result.get("revised_prompt")

    # Build the text part of the reply
    channel = result.get("channel", "")
    channel_tag = " ✨高級通道" if channel == "premium" else ""
    text_parts = [f"🎨 根據你的要求生成了圖片！{channel_tag}"]
    if revised_prompt and revised_prompt != prompt:
        text_parts.append(f"（AI 優化後的提示詞：{revised_prompt[:100]}）")
    text_reply = "\n".join(text_parts)

    try:
        if image_path:
            # b64 data saved to file
            file = discord.File(image_path, filename="generated.png")
            await message.reply(text_reply, file=file, mention_author=False)
            # Clean up temp file
            try:
                os.remove(image_path)
            except Exception:
                pass
        elif image_url:
            # Download the image and send as file (persists in Discord CDN)
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as sess:
                    async with sess.get(image_url) as img_resp:
                        if img_resp.status == 200:
                            img_bytes = await img_resp.read()
                            # Determine file extension from content-type
                            ct = img_resp.headers.get("Content-Type", "image/png")
                            ext = "png"
                            if "jpeg" in ct or "jpg" in ct:
                                ext = "jpg"
                            elif "webp" in ct:
                                ext = "webp"
                            file = discord.File(io.BytesIO(img_bytes), filename=f"generated.{ext}")
                            await message.reply(text_reply, file=file, mention_author=False)
                        else:
                            # Fallback: send URL in embed
                            embed = discord.Embed(title="🎨 生成圖片", color=0x5865f2)
                            embed.set_image(url=image_url)
                            embed.set_footer(text=f"提示詞: {prompt[:200]}")
                            await message.reply(text_reply, embed=embed, mention_author=False)
            except Exception as dl_err:
                print(f"🎨 下載圖片失敗，直接發送 URL: {dl_err}")
                embed = discord.Embed(title="🎨 生成圖片", color=0x5865f2)
                embed.set_image(url=image_url)
                embed.set_footer(text=f"提示詞: {prompt[:200]}")
                await message.reply(text_reply, embed=embed, mention_author=False)

        print(f"✅ T2I 圖片已發送 to #{message.channel}")
    except discord.Forbidden:
        await message.reply("❌ 我沒有在這裡發送檔案的權限。", mention_author=False)
    except Exception as e:
        print(f"❌ T2I 發送失敗: {e}")
        await message.reply(f"❌ 圖片發送失敗: {e}", mention_author=False)


async def generate_chat_reply(message, settings: dict) -> tuple:
    """Generate a reply for a chat message with brief context, server awareness, and per-user memory.
    Returns (reply_text, new_facts_or_None, mod_action_or_None, model_info_or_None)."""
    user_id = str(message.author.id)
    user_name = message.author.display_name
    _reply_model = None      # which model actually answered (for AI log)
    _reply_fallback = False  # whether the backup API was used
    _reply_diag = []         # diagnostic events for AI log embed

    # Load user memory
    mem = user_memories.get(user_id, {})
    facts = mem.get("facts", [])

    # Build system prompt with memory — STRICTLY scoped to current user
    system_prompt = settings["system_prompt"]

    # ── 注入即時日期時間 ──
    # The AI's training data has a cutoff and it has no internal clock — it
    # will hallucinate dates ("今天是2024年...") or claim "還沒發生" for
    # events that happened after its cutoff. Inject the real current time
    # so it never needs to guess.
    _now = datetime.now(GMT8)
    _weekday_cn = ["一", "二", "三", "四", "五", "六", "日"][_now.weekday()]
    system_prompt += (
        f"\n\n─── 即時時間 ───\n"
        f"現在時間：{_now.strftime('%Y年%m月%d日')}（星期{_weekday_cn}）"
        f" {_now.strftime('%H:%M')}，GMT+8 台灣時間。"
        f"你的訓練資料有截止日期，可能不知道最近發生的事。"

        f"請使用 web_search 工具上網查證，不要憑訓練資料直接否定。"
    )

    # ── 資料來源可信度優先順序（最高優先級規則）──
    # Establishes a clear trust hierarchy so the AI never treats AI-generated
    # summaries (chronicle/awareness/refined-knowledge) as equally authoritative
    # as directly-verified sources (forced micropedia web search, raw Discord
    # history). This is what stops "connect-the-dots" hallucinations like
    # inferring two different countries are secretly the same entity based on
    # a vague pattern in the chronicle's social-dynamics summary.
    system_prompt += (
        f"\n\n─── 資料來源可信度優先順序（務必遵守）───\n"
        f"你會收到好幾種不同來源的背景資料，可信度不一樣，優先順序如下：\n"
        f"① 【最高】強制聯網查證到的微國家百科 (micropedia.site) 原文內容、"
        f"以及 search_discord / web_search 工具查到的原始資料——這些是直接查證的"
        f"事實來源，如果跟其他資料衝突，一律以這裡為準。\n"
        f"② 【次高】「Discord 伺服器歷史資料」自動搜尋結果——真實的伺服器記錄。\n"
        f"③ 【僅供參考，次要背景】「社群編年史」「社群感知」「微國家精煉知識庫」——"
        f"這些是 AI 自動分析、歸納出來的背景摘要，本質上是「印象整理」而不是查證事實，"
        f"可能包含歸納錯誤。只能拿來當作聊天時的語氣/氛圍參考"
        f"（例如知道最近大家在聊什麼、感覺上誰跟誰比較熟），"
        f"絕對不能拿來當作斷言具體事實的依據，"
        f"更不能拿來推論「兩個實體其實是同一個」這種等同關係——"
        f"如果只有這類次要資料支持某個結論，代表你還沒有真正查證過，"
        f"應該先用 search_micropedia / web_search 查證，而不是直接採信。"
    )

    # Inject server context (channels, roles, emojis, members, current user identity)
    if message.guild:
        try:
            server_ctx = await _get_server_context(message.guild, message.author)
            if server_ctx:
                system_prompt += f"\n\n{server_ctx}"
        except Exception as e:
            print(f"⚠️ 伺服器結構取得失敗：{e}")

    # Inject community awareness — gives the AI a "real member's
    # understanding" of social dynamics, recent events, current topics,
    # and channel culture. This is what makes it feel like the AI
    # actually lives in the community.
    awareness_ctx = _get_community_awareness_context()
    if awareness_ctx:
        system_prompt += f"\n\n{awareness_ctx}"
    # Inject community chronicle — gives the AI deep historical context:
    # long-standing alliances, conflicts, treaties, key events, and their
    # evolution over months/years. This is what lets the AI understand
    # grudges and context that the 20-min awareness scan can't capture.
    chronicle_ctx = _get_community_chronicle_context()
    if chronicle_ctx:
        system_prompt += f"\n\n{chronicle_ctx}"

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
    clean_content = (message.content or "").replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "").strip()
    # If the message is image-only (no text), give the AI a placeholder
    # so it has something to anchor its reply on, beyond just the image
    # description that gets injected later.
    if not clean_content and message.attachments:
        clean_content = "(使用者傳了一張圖片，請看看圖片內容並回應)"

    # ── 圖片識別（子流程）──
    # 如果訊息有圖片附件，且設定了 vision_model，就先用視覺模型描述圖片，
    # 再把描述注入 system prompt，讓主 AI 可以「看到」圖片內容來回答。
    image_context = ""
    _vision_diag = []  # diagnostics from vision API calls (for AI log embed)
    vision_model = settings.get("vision_model", "")
    image_atts = [
        att for att in message.attachments[:2]  # 最多處理 2 張圖
        if att.content_type and att.content_type.startswith("image/")
    ]

    # Defined unconditionally (not just inside "if vision_model and image_atts")
    # so it can ALSO be reused below for describing images in a message the
    # user is replying to — not just the user's own message.
    async def _describe_with_timeout(att):
        try:
            # Matches _describe_image's own 90s internal budget, plus a
            # small margin — the inner aiohttp timeout should fire first
            # in normal cases, this is just a hard outer safety net.
            return await asyncio.wait_for(_describe_image(att.url, settings, _vision_diag=_vision_diag), timeout=95)
        except asyncio.TimeoutError:
            _vision_diag.append(f"📷 譖覺模型識圖逾時（>95s）")
            print(f"⚠️ 視覺模型識圖逾時（>95s），此圖片將略過")
            return ""
        except Exception as e:
            _vision_diag.append(f"📷 識圖子流程例外：{str(e)[:80]}")
            print(f"⚠️ 識圖子流程錯誤：{e}")
            return ""

    if vision_model and image_atts:
        print(f"📷 偵測到 {len(image_atts)} 張圖片附件，呼叫視覺模型 {vision_model} 識圖中...")

        # Run all images concurrently instead of one-by-one — with 2 images,
        # sequential processing would double the worst-case wait; concurrent
        # calls keep it bounded to a single image's latency.
        descriptions = await asyncio.gather(*[_describe_with_timeout(att) for att in image_atts])
        for desc in descriptions:
            if desc:
                image_context += f"\n[圖片描述]：{desc}\n"
        if image_context:
            print(f"📷 識圖完成，注入 {len(image_context)} chars 到上下文")
        else:
            print(f"⚠️ 識圖流程跑完但沒有拿到任何描述（可能全部逾時或失敗）")
    elif image_atts and not vision_model:
        _vision_diag.append("📷 有圖片但視覺模型未設定，無法識圖")

    # ── 回覆訊息上下文（含圖片）──
    # 使用者常常是「回覆」某人的訊息後再 @ 提及機器人（例如：秘書長貼了一張圖
    # 抱怨審美很雷，張作霖回覆那則訊息說「這圖怎麼樣」並 @ 機器人）。
    # 之前只看得到使用者自己打的字，完全看不到被回覆的原始訊息內容/圖片，
    # 導致 AI 答非所問。現在不管被回覆的人是誰（不只是回覆機器人自己），
    # 都會把原始訊息的文字和圖片（呼叫視覺模型描述）一起讀進來當上下文。
    reply_context = ""
    if message.reference and message.reference.message_id:
        try:
            _ref_msg = message.reference.resolved
            if _ref_msg is None or isinstance(_ref_msg, discord.DeletedReferencedMessage):
                _ref_msg = await message.channel.fetch_message(message.reference.message_id)
            if _ref_msg:
                _ref_author = "機器人自己" if _ref_msg.author.id == bot_id else _ref_msg.author.display_name
                _ref_text = (_ref_msg.content or "").strip()
                _ref_image_atts = [
                    att for att in _ref_msg.attachments[:2]
                    if att.content_type and att.content_type.startswith("image/")
                ]
                if _ref_image_atts:
                    if vision_model:
                        print(f"📷 被回覆的訊息含 {len(_ref_image_atts)} 張圖片，一併呼叫視覺模型識圖...")
                        _ref_descs = await asyncio.gather(*[_describe_with_timeout(att) for att in _ref_image_atts])
                        for _d in _ref_descs:
                            if _d:
                                _ref_text += f"\n[圖片：{_d}]"
                        if not any(_ref_descs):
                            _ref_text += "\n[圖片（識圖失敗或逾時）]"
                    else:
                        _ref_text += "\n[圖片（未設定視覺模型，無法分析）]"
                if _ref_text.strip():
                    reply_context = (
                        f"\n\n─── 使用者正在回覆的訊息（來自 {_ref_author}）───\n"
                        f"使用者這則訊息是在 Discord 上「回覆」下面這則訊息，"
                        f"請理解使用者的話是針對這則被回覆的訊息說的，不要當作獨立的一句話來理解：\n"
                        f"{_ref_text[:1000]}"
                    )
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"⚠️ 讀取被回覆訊息失敗：{e}")

    # ── Discord 連結解析 ──
    # If the user's message contains Discord jump URLs, fetch the actual
    # message content so the AI can see what's at that link instead of
    # saying "I can't access Discord links."
    discord_link_context = ""
    _discord_url_pattern = re.compile(
        r"https?://(?:discord\.com|discordapp\.com)/channels/(\d+)/(\d+)/(\d+)"
    )
    _link_matches = _discord_url_pattern.findall(clean_content)
    if _link_matches and message.guild:
        link_parts = []
        for _guild_id, _ch_id, _msg_id in _link_matches[:3]:  # max 3 links
            try:
                _ch = message.guild.get_channel(int(_ch_id)) or bot.get_channel(int(_ch_id))
                if _ch is None:
                    continue
                _target_msg = await _ch.fetch_message(int(_msg_id))
                if _target_msg is None:
                    continue
                _author = _target_msg.author.display_name if _target_msg.author else "未知"
                _date = (_target_msg.created_at + timedelta(hours=8)).strftime("%Y-%m-%d")
                _body = _target_msg.content.strip()
                for embed in _target_msg.embeds:
                    if embed.title:
                        _body += "\n" + str(embed.title)
                    if embed.description:
                        _body += "\n" + str(embed.description)
                    for field in embed.fields:
                        _body += f"\n{field.name}: {field.value}"
                _body = _body.strip()[:800]
                link_parts.append(f"📎 連結內容（#{_ch.name}, {_author}, {_date}）：\n{_body}")
                # If this is a forum thread, also grab a few replies
                if hasattr(_ch, 'history') and _ch.type == discord.ChannelType.public_thread:
                    _replies = []
                    async for _rm in _ch.history(limit=10, after=_target_msg):
                        _rb = _rm.content.strip()[:200]
                        if _rm.embeds and not _rb:
                            for _emb in _rm.embeds:
                                if _emb.description:
                                    _rb = str(_emb.description)[:200]
                                    break
                        if _rb:
                            _rd = (_rm.created_at + timedelta(hours=8)).strftime("%Y-%m-%d")
                            _ra = _rm.author.display_name if _rm.author else "未知"
                            _replies.append(f"[{_rd}] {_ra}: {_rb}")
                    if _replies:
                        link_parts.append("📌 此貼文的回覆：\n" + "\n".join(_replies[-5:]))
            except Exception as e:
                print(f"⚠️ Discord 連結解析失敗：{e}")
        if link_parts:
            discord_link_context = "\n\n".join(link_parts)

    if discord_link_context:
        full_prompt = f"{user_name}: {clean_content}\n\n{discord_link_context}"
    else:
        full_prompt = f"{user_name}: {clean_content}"

    # ── Micropedia + Discord auto-context — RUN IN PARALLEL ──
    # These two were previously sequential (10s + 10s = 20s before AI even
    # starts), which alone blew the 20s budget. Now they run concurrently
    # with asyncio.gather — total pre-AI time = max(micropedia, discord)
    # instead of micropedia + discord.
    micropedia_enabled = settings.get("micropedia_enabled", True)
    max_results = settings.get("micropedia_max_results", 5)

    _INFO_SEEKING_MARKERS = (
        "誰", "是誰", "什麼", "哪", "何時", "多少", "是不是", "有沒有", "?", "？",
        "當選", "新任", "上任", "現任", "現在", "最近", "最新", "剛", "已經",
        "罷免", "撤案", "撤回", "選舉", "投票結果", "誰是", "結果",
    )
    _need_discord = bool(
        message.guild and len(clean_content) >= 4
        and any(m in clean_content for m in _INFO_SEEKING_MARKERS)
    )
    _need_micropedia = bool(micropedia_enabled and len(clean_content) >= 4)

    # Bot's own nation registry (成員國/理事國/觀察國/已除籍) — in-memory,
    # zero I/O, so no need for the async/timeout machinery used below.
    _nation_registry_auto = ""
    if message.guild and any(m in clean_content for m in _NATION_REGISTRY_MARKERS):
        _nation_registry_auto = _format_nation_registry_context(message.guild.id, clean_content)

    # Run both searches concurrently with a tight 6s budget each
    async def _do_micropedia():
        if not _need_micropedia:
            return ""
        try:
            return await asyncio.wait_for(
                _micropedia_auto_context(clean_content, max_results), timeout=settings.get("preprocess_timeout", 6)
            )
        except asyncio.TimeoutError:
            print(f"📚 Micropedia: 自動比對逾時（>6s），跳過")
            return ""
        except Exception as e:
            print(f"📚 Micropedia: 自動比對錯誤：{e}")
            return ""

    async def _do_discord():
        if not _need_discord:
            return ""
        try:
            return await asyncio.wait_for(
                _search_discord_history(message.guild, clean_content, limit=15), timeout=settings.get("preprocess_timeout", 6)
            )
        except asyncio.TimeoutError:
            print("🔍 search_discord 自動比對逾時（>6s），跳過")
            return ""
        except Exception as e:
            print(f"🔍 search_discord 自動比對錯誤：{e}")
            return ""

    # Also run web_search in parallel for real-world questions — this means
    # the AI gets internet search results WITHOUT needing to call the
    # web_search tool, saving a full AI round-trip.
    _need_web = len(clean_content) >= 6
    async def _do_web():
        if not _need_web:
            return ""
        try:
            return await asyncio.wait_for(_web_search(clean_content[:200]), timeout=settings.get("preprocess_timeout", 6))
        except asyncio.TimeoutError:
            print("🌐 web_search 自動搜尋逾時（>6s），跳過")
            return ""
        except Exception as e:
            print(f"🌐 web_search 自動搜尋錯誤：{e}")
            return ""

    _t_pre = _time.time()
    auto_context, _discord_auto, _web_auto = await asyncio.gather(
        _do_micropedia(), _do_discord(), _do_web()
    )
    _t_ctx_done = _time.time()
    print(f"⏱️ 預處理（百科+Discord+網路 平行）耗時 {_t_ctx_done-_t_pre:.1f}s（百科 {len(auto_context)}, Discord {len(_discord_auto)}, 網路 {len(_web_auto)} chars）")

    # Inject micropedia results
    if auto_context:
        system_prompt += (
            f"\n\n─── 微國家百科資料（已自動比對到相關文章）───\n"
            f"以下是根據使用者訊息，自動從微國家百科 (micropedia.site) 比對到的相關文章。"
            f"請優先參考這些資料來回答問題。\n"
            f"以下是自動比對到的百科文章，請根據內容直接回答使用者的問題。\n" \
            f"⚠️ 注意事項：\n" \
            f"1. 如果文章內容已經涵蓋使用者問的事，就直接回答，不要猶豫。\n" \
            f"2. 不要自行把兩個不同條目推論為同一個東西，除非文章明確這樣寫。\n" \
            f"3. 如果文章確實沒有涵蓋使用者問的細節，才說「目前百科沒有寫到這點」。\n{auto_context}"
        )
        print(f"📚 Micropedia: 已自動注入 {len(auto_context)} chars 到 AI 上下文")

    # Inject bot's own nation registry (成員國/理事國/觀察國/已除籍) — this is
    # data the bot itself owns via /nation register + the dashboard, NOT
    # something that exists in the wiki or in Discord message history, so
    # it needs its own dedicated injector rather than relying on the
    # micropedia/search_discord auto-context to somehow stumble onto it.
    if _nation_registry_auto:
        system_prompt += (
            f"\n\n─── 本伺服器會員國登記資料（機器人自己的資料庫，非百科）───\n"
            f"以下是機器人資料庫裡登記的國家名單，依照使用者問題自動篩選類別。"
            f"這是官方登記資料，請直接引用回答，不要說「查不到」或「沒有明確列出」。"
            f"如果下面顯示某類別「無」，代表目前確實沒有國家登記在該類別，"
            f"直接誠實告知使用者即可，不要含糊其辭。\n{_nation_registry_auto}"
        )
        print(f"🌍 會員國登記資料：已自動注入 {len(_nation_registry_auto)} chars 到 AI 上下文")

    # Inject AI refined knowledge (in-memory, instant)
    if ai_refined_knowledge:
        high_confidence = [k for k in ai_refined_knowledge if k.get("confidence", "high") == "high"]
        low_confidence = [k for k in ai_refined_knowledge if k.get("confidence", "high") != "high"]
        recent_knowledge = ai_refined_knowledge[-12:]
        if recent_knowledge:
            knowledge_lines = []
            for k in recent_knowledge:
                conf_tag = "✅" if k.get("confidence", "high") == "high" else "⚠️"
                knowledge_lines.append(f"- {conf_tag} [{k.get('date', '?')}] {k.get('topic', '')}：{k.get('summary', '')}")
            system_prompt += (
                f"\n\n─── 微國家精煉知識庫 ───\n"
                f"以下是從社群討論中萃取、經百科驗證修正的知識摘要。\n"
                f"✅ = 已經百科驗證（可信），⚠️ = 社群討論但百科未覆蓋（僅供參考）。\n"
                f"回答相關問題時優先參考 ✅ 條目，⚠️ 條目可作為補充但需自行判斷。\n"
                + "\n".join(knowledge_lines)
            )
            print(f"🔍 AI精煉: 已注入 {len(high_confidence)} 條高可信 + {len(low_confidence)} 條低可信知識")

    # Inject discord search results
    if _discord_auto and "沒有找到" not in _discord_auto:
        system_prompt += (
            f"\n\n─── Discord 伺服器歷史資料（已自動搜尋到相關內容）───\n"
            f"以下是根據使用者的問題，自動從整個伺服器（含論壇貼文與訊息歷史）搜尋到的相關內容。"
            f"這些是真實存在的伺服器記錄，請優先參考並以此為準來回答，"
            f"尤其是涉及人事任命、選舉結果、提案狀態等問題——下面的資料如果有答案，就直接回答，不要說「不確定」或「沒有公布」。"
            f"如果下面的資料已經有答案就直接引用回答。\n{_discord_auto}"
        )
        print(f"🔍 search_discord: 已自動注入 {len(_discord_auto)} chars 到 AI 上下文")

    # Inject web search results (real-world info)
    if _web_auto:
        system_prompt += (
            f"\n\n─── 網際網路搜尋結果（已自動查詢）───\n"
            f"以下是根據使用者訊息，自動從網際網路（維基百科 + DuckDuckGo）搜尋到的結果。"
            f"如果使用者問的是真實世界的事物、新聞、時事，請參考這些資料回答。"
            f"如果搜尋結果跟問題無關（例如使用者問的是微國家內部事務），就忽略這些結果。\n{_web_auto[:1500]}"
        )
        print(f"🌐 web_search: 已自動注入 {len(_web_auto)} chars 到 AI 上下文")

    # ── 黑名單提示（讓 AI 知道哪些用戶被封禁，避免引用其言論） ──
    bl_users = _blacklist.get("users", [])
    if bl_users:
        bl_names = [u.get("user_name", "") for u in bl_users if u.get("user_name")]
        if bl_names:
            system_prompt += (
                "\n\n─── 封禁用戶提醒 ───\n"
                f"以下用戶已被管理員封禁，請忽略其言論，不要引用或回應他們說過的話："
                f"{', '.join(bl_names[:10])}"
            )

    # ── 資料檔案庫自動注入 ──
    # 搜尋管理者上傳的資料檔案，注入到 AI 上下文
    if len(clean_content) >= 4:
        try:
            _data_lib_ctx = _build_data_library_context(clean_content)
        except Exception:
            _data_lib_ctx = ""
        if _data_lib_ctx:
            system_prompt += _data_lib_ctx
            print(f"📊 資料檔案庫: 已注入到 AI 上下文")

    # ── 修正資料自動注入 ──
    # If validated user corrections exist that match the current question,
    # inject them as ground-truth so the AI learns from past mistakes.
    if len(clean_content) >= 4:
        try:
            matched_corrections = _search_corrections(clean_content, top_n=3)
        except Exception:
            matched_corrections = []
        if matched_corrections:
            corr_lines = []
            for c in matched_corrections:
                corr_lines.append(
                    f"• 問題：{c['question'][:80]}\n"
                    f"  修正：{c['correction'][:200]}\n"
                    f"  （由 {c.get('user_name', '匿名')} 於 {c.get('date', '?')} 提交，已驗證）"
                )
            system_prompt += (
                f"\n\n─── 使用者修正資料（已驗證，請以此為準）───\n"
                f"以下是使用者提交並通過驗證的修正資訊，代表 AI 之前的回答有誤，"
                f"請務必參考這些修正來回答問題：\n"
                + "\n".join(corr_lines)
            )
            print(f"📝 修正資料：已注入 {len(matched_corrections)} 筆到 AI 上下文")

    # ── 讚/倒讚評價自動注入 ──
    # Inject recent dislike feedback so the AI knows which answer styles
    # are problematic, and recent like feedback as positive reinforcement.
    # Only inject the most recent N entries (not all-time) to keep the
    # system prompt lean and avoid stale advice.
    try:
        fb_entries = _feedback.get("entries", [])
        if fb_entries:
            recent_fb = sorted(fb_entries, key=lambda e: e.get("_ts", 0), reverse=True)[:20]
            dislikes = [e for e in recent_fb if e.get("rating") == "dislike"]
            likes = [e for e in recent_fb if e.get("rating") == "like"]
            fb_lines = []
            if dislikes:
                fb_lines.append("⚠️ 以下回答曾收到 👎 倒讚，請避免類似問題的回覆方式：")
                for e in dislikes[:5]:
                    reason = e.get("reason", "?")
                    extra = e.get("custom_text", "")
                    q = e.get("question", "")[:60]
                    detail = f"（{reason}）" if not extra else f"（{reason}：{extra[:80]}）"
                    fb_lines.append(f"  • 問題：{q} {detail}")
            if likes:
                fb_lines.append("✅ 以下回答收到 👍 讚，這類回覆方式受使用者肯定：")
                for e in likes[:3]:
                    reason = e.get("reason", "?")
                    q = e.get("question", "")[:60]
                    fb_lines.append(f"  • 問題：{q}（{reason}）")
            if fb_lines:
                system_prompt += (
                    f"\n\n─── 使用者評價回饋 ───\n"
                    + "\n".join(fb_lines)
                )
                print(f"👍 評價回饋：已注入 {len(likes)} 讚 + {len(dislikes)} 倒讚到 AI 上下文")
    except Exception as e:
        print(f"⚠️ 評價回饋注入失敗：{e}")

    # ── 注入圖片描述到 system prompt ──
    if image_context:
        system_prompt += (
            f"\n\n─── 使用者傳送的圖片（由視覺模型描述）───\n"
            f"使用者傳送了圖片，以下是視覺模型對圖片內容的描述。"
            f"請參考這些描述來回覆使用者的問題或回應圖片內容：\n{image_context}"
        )

    # ── 注入「被回覆的訊息」到 system prompt ──
    if reply_context:
        system_prompt += reply_context

    # ── CONDITIONAL TOOLS: if auto-context already found rich data, skip
    # tools entirely → single AI round (saves a full 7-15s round-trip on slow
    # free APIs). Only offer tools when auto-context came up thin, meaning
    # the AI genuinely needs to search to answer well.
    _context_rich = bool(auto_context and len(auto_context) > 400) or bool(_discord_auto and len(_discord_auto) > 400) or bool(_web_auto and len(_web_auto) > 200) or bool(_nation_registry_auto and len(_nation_registry_auto) > 30)
    if _context_rich:
        print(f"⚡ 快速路徑：自動注入已找到豐富資料（百科 {len(auto_context)} + Discord {len(_discord_auto)} chars），跳過工具呼叫，單輪 AI 回答")

    # Build tool list — only when auto-context is thin AND the endpoint supports tools
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
    _web_search_available = False
    if tools_ok and not _context_rich:  # Skip tools when auto-context is rich
        if micropedia_enabled:
            tools.append(_MICROPEDIA_TOOL_SCHEMA)
        tools.append(_DISCORD_SEARCH_TOOL_SCHEMA)
        tools.append(_WEB_SEARCH_TOOL_SCHEMA)
        _search_discord_available = True
        _web_search_available = True
    elif not _tools_unsup and not _tools_supported and not _context_rich:
        # Endpoint not yet tested — send safe read-only tools (only if context is thin)
        if micropedia_enabled:
            tools.append(_MICROPEDIA_TOOL_SCHEMA)
        tools.append(_WEB_SEARCH_TOOL_SCHEMA)
        _web_search_available = True
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
            f"（例如「黃綠燈罷免案」查不到就試「黃綠燈」或「罷免」）。\n"
            f"這個工具會搜尋整個伺服器的所有頻道訊息和論壇貼文，結果按時間排列，"
            f"你可以從多篇訊息拼湊出完整的事件脈絡。"
            f"如果第一次搜尋結果不夠完整，可以用不同的關鍵字再搜一次。"
            f"找到的內容會標示來源、發言者、日期和內容，"
            f"你可以據此回答使用者的問題；如果找不到，才誠實告知使用者。\n"
            f"⚠️ 極重要：論壇貼文的「原始內容」只是提案剛發起時的樣子，事情很可能早就有後續發展"
            f"（撤案、通過、否決、換人等）。一定要看「最新進展」欄位裡最後幾則回覆的日期和內容，"
            f"那才是目前真正的狀態。如果最新進展顯示案子已經結束/撤回/有結果，"
            f"就不要再用原始提案的角度回答，要以最新狀態為準。\n"
            f"⚠️ 工具回傳的文字內容本身就是完整答案來源，不是網址或連結，"
            f"你不需要、也沒有能力瀏覽任何網頁。絕對不要回覆「我無法查看連結」"
            f"「Discord 訊息需要透過客戶端存取」之類的話——你收到的搜尋結果已經是純文字內容，"
            f"直接根據內容回答使用者的問題就好，不要提到「連結」或「網址」這件事。\n"
            f"⚠️⚠️ 極重要（格式規則）：搜尋結果只是給你看的「原始資料」，"
            f"絕對禁止把搜尋結果原封不動貼到回覆裡！"
            f"不要出現「🔍」「📋」「📢」「📚」開頭的搜尋結果標題，"
            f"不要出現「[1]」「[2]」這種編號列表，不要出現頻道名稱、時間戳記（如 2025-10-19T17:01）、"
            f"「按時間排列」這類字樣——這些都是程式格式，不是給人看的。"
            f"你要做的是自己讀懂搜尋結果，然後用自己的話、正常聊天的口語語氣，"
            f"直接講出結論給使用者聽，就像朋友聊天一樣簡短回答，不要像機器人在報告資料。\n"
            f"例如搜尋結果顯示「了千勾當選第三任秘書長，8月4日上任」，"
            f"你應該回答類似：「剛看到公告，新秘書長是了千勾，8/4正式上任喔」"
            f"這種自然口語，而不是把整段搜尋結果貼上去。"
        )

    # ── web_search 工具說明 ──
    if _web_search_available:
        system_prompt += (
            f"\n\n─── web_search 工具（搜尋網際網路）───\n"
            f"你有一個 web_search 工具，可以搜尋網際網路（維基百科 + DuckDuckGo）。"
            f"不過，系統已經根據你的訊息自動做了一次網路搜尋，結果在上面「網際網路搜尋結果」區塊。"
            f"如果上面的結果已經夠你回答，就不需要再呼叫這個工具。\n\n"
            f"只有當你覺得自動搜尋的結果不夠、或你想用不同的關鍵字再查一次時，才呼叫 web_search。\n\n"
            f"⚠️ 仍然適用的規則：\n"
            f"1. 當你準備回答「這不存在」「沒這回事」「沒發生過」「不是真的」之類的否定結論時，"
            f"先確認自動搜尋結果或呼叫 web_search 查證。\n"
            f"2. 如果搜尋也查不到，才誠實說「網路上也找不到相關資訊」。\n"
            f""
        )

    # ── 注入本人近期對話歷史（僅限本人，不含其他人的訊息）──
    history_turns = _get_user_history(user_id)
    if history_turns:
        system_prompt += (
            f"\n\n─── 與「{user_name}」的近期對話 ───\n"
            f"下面是你和「{user_name}」剛才的對話紀錄，可能跟這次問題有關（例如接續之前的話題、"
            f"回答你剛才問的問題等），請參考上下文回覆，不要當作完全獨立的新問題。"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        *history_turns,
        {"role": "user", "content": full_prompt},
    ]

    async def _run_tool_loop():
        nonlocal _reply_model, _reply_fallback, _reply_diag
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
        # Dynamic per-call timeout based on the ACTUAL remaining budget
        # (_ai_budget, computed below from the 20s total minus pre-AI time
        # already spent) — not a hardcoded 8/12s. Hardcoding wasted budget:
        # if pre-AI context gathering was fast (e.g. 1-2s), the AI call was
        # still artificially capped at 12s even though 17-18s were actually
        # available, giving the (often slow/free) API less time than it
        # could have had to actually finish instead of timing out.
        # Leave a ~1.5s safety margin for our own post-processing overhead.
        if tools:
            # Tool-enabled round: reserve roughly 45% for this round, leaving
            # the rest for tool execution + the mandatory final round.
            _call_tt = max(6, _ai_budget * 0.45)
        else:
            # Single-round quick path: give it nearly the ENTIRE remaining budget.
            _call_tt = max(6, _ai_budget - 1.5)
        _call_tr = max(4, _call_tt - 2)
        assistant_msg = await call_chat_api(msgs, settings, tools=tools, max_tokens=settings.get("ai_max_tokens", 2000), timeout_total=_call_tt, timeout_read=_call_tr, is_background=False, fallback_mode="rate_limited", fallback_user_id=user_id, category="chat")
        _reply_model = assistant_msg.get("_used_model")
        _reply_fallback = assistant_msg.get("_used_fallback", False)
        _reply_diag = assistant_msg.get("_diag", [])
        print(f"⏱️ Round 1（{'含 tools' if tools else '無 tools'}，預算 {_call_tt:.1f}s）耗時 {_time.time()-t0:.1f}s，模型={_reply_model}")
        tool_calls = assistant_msg.get("tool_calls")
        if not tool_calls:
            return assistant_msg.get("content") or ""

        # Model wants to search — execute each requested call CONCURRENTLY
        # (not one-by-one) so e.g. search_micropedia + web_search called
        # together take max(their times) instead of the sum — this is what
        # actually keeps multi-tool turns fast.
        t1 = _time.time()
        msgs = msgs + [assistant_msg]

        async def _run_one_tool(tc):
            fn = tc.get("function", {})
            name = fn.get("name")
            try:
                args = json_module.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            query = (args.get("query") or "").strip()
            try:
                if name == "search_micropedia":
                    print(f"🔧 AI 呼叫 search_micropedia('{query}')")
                    result = await _fetch_micropedia(query, max_results) if query else ""
                    return result if result else "沒有找到相關資料，試試看換一個更短或不同的關鍵字。"
                elif name == "search_discord":
                    print(f"🔧 AI 呼叫 search_discord('{query}')")
                    if query and message.guild:
                        result = await _search_discord_history(message.guild, query, limit=10)
                    else:
                        result = "無法搜尋（沒有 guild 或搜尋詞為空）"
                    return result if result else "沒有找到相關訊息，試試看換一個不同的關鍵字。"
                elif name == "web_search":
                    print(f"🔧 AI 呼叫 web_search('{query}')")
                    result = await _web_search(query) if query else ""
                    return result if result else "網路搜尋沒有找到相關結果。可能這個詞太冷門或太新，試試看換一個更通用的關鍵字。"
                else:
                    return f"未知工具：{name}"
            except Exception as e:
                print(f"⚠️ 工具 {name}('{query}') 執行例外：{e}")
                return "查詢時發生錯誤，請直接告知使用者這部分暫時查不到。"

        tool_contents = await asyncio.gather(*[_run_one_tool(tc) for tc in tool_calls])
        for tc, tool_content in zip(tool_calls, tool_contents):
            msgs = msgs + [{
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": tool_content[:3000],
            }]
        print(f"⏱️ 工具執行（{len(tool_calls)} 個，平行）耗時 {_time.time()-t1:.1f}s")

        # Final round — ALWAYS plain text, no tools. Capped at 2 calls total.
        # Budget = whatever remains of _ai_budget after Round 1 + tool execution,
        # minus a small safety margin — not a hardcoded 8s.
        t2 = _time.time()
        _round2_budget = max(4, _ai_budget - (t2 - t0) - 1)
        final_msg = await call_chat_api(msgs, settings, tools=None, max_tokens=settings.get("ai_max_tokens", 2000), timeout_total=_round2_budget, timeout_read=max(3, _round2_budget - 2), is_background=False, fallback_mode="rate_limited", fallback_user_id=user_id, category="chat")
        _reply_model = final_msg.get("_used_model")
        _reply_fallback = final_msg.get("_used_fallback", False)
        _reply_diag = final_msg.get("_diag", [])
        print(f"⏱️ Round 2（最終答案，無 tools，預算 {_round2_budget:.1f}s）耗時 {_time.time()-t2:.1f}s，總計 {_time.time()-t0:.1f}s，模型={_reply_model}")
        return final_msg.get("content") or ""

    # ── 20s is a HARD requirement per user specification ──
    # The entire pipeline (pre-processing + AI calls + tools) must complete
    # within 20 seconds. If the API is genuinely too slow to finish in 20s,
    # the canned fallback message kicks in — but the user requires 20s as
    # a hard ceiling, not a soft target.
    _AI_HARD_CEILING = settings.get("ai_hard_ceiling", 20)
    _AI_SOFT_TARGET = settings.get("ai_soft_target", 16)
    # FIX：含圖片的訊息，視覺模型的圖片描述會塞進 system prompt，文字模型要
    # 處理的內容量變大，生成時間本來就會比純文字聊天長——但硬上限之前是同一個
    # 固定值，不管有沒有圖片。用戶反映「有圖片的訊息都等超久，文字和圖片模型
    # 都已經成功輸出 token，卻還是回傳逾時」，就是這個固定上限對圖片訊息太緊。
    # 現在偵測到本次有成功注入圖片描述（image_context 非空）就加開額外預算。
    _vision_bonus = settings.get("vision_extra_budget", 20) if image_context else 0
    if _vision_bonus:
        print(f"📷 本次訊息含圖片描述，額外加開 {_vision_bonus}s AI 預算")
    _pipeline_elapsed = _time.time() - _t_pre
    _ai_budget = max(10, _AI_HARD_CEILING + _vision_bonus - _pipeline_elapsed)
    print(f"⏱️ 預處理已花 {_pipeline_elapsed:.1f}s，AI 剩餘預算 {_ai_budget:.1f}s"
          f"（硬上限 {_AI_HARD_CEILING}s{f'+{_vision_bonus}s(圖片)' if _vision_bonus else ''}，目標 {_AI_SOFT_TARGET}s 內完成）")

    # If the remaining budget is tight (<20s — not enough for a safe 2-round
    # tool loop), skip tools entirely so at least a single AI round can use
    # most of what's left. With the larger hard ceiling this rarely triggers
    # in practice, only when pre-AI context gathering itself ran unusually long.
    if _ai_budget < settings.get("tool_skip_threshold", 12) and tools:
        print(f"⚡ 時間預算緊迫（{_ai_budget:.1f}s），關閉工具以確保單輪回答能完成")
        tools = None

    try:
        raw_reply = await asyncio.wait_for(_run_tool_loop(), timeout=_ai_budget)
    except asyncio.TimeoutError:
        print(f"⚠️ AI 回覆流程逾時（>{_ai_budget:.1f}s，總計 {_time.time()-_t_pre:.1f}s）")
        raise
    except Exception as e:
        # Something about the tool-calling machinery itself broke (bad response
        # shape, provider quirk we didn't anticipate, etc.) — never let that take
        # the whole chat feature down. Fall back to one plain, tool-free call.
        print(f"⚠️ 工具呼叫流程失敗，改用純文字模式重試：{e}")
        fallback_msg = await asyncio.wait_for(
            call_chat_api(messages, settings, tools=None, max_tokens=settings.get("ai_max_tokens", 2000), timeout_total=10, timeout_read=8, is_background=False, fallback_mode="rate_limited", fallback_user_id=user_id, category="chat"), timeout=12
        )
        _reply_model = fallback_msg.get("_used_model")
        _reply_fallback = fallback_msg.get("_used_fallback", False)
        _reply_diag = fallback_msg.get("_diag", [])
        raw_reply = fallback_msg.get("content") or ""

    # Safety net: strip raw tool-output dumps that a weak model sometimes
    # echoes verbatim instead of composing a natural-language answer.
    # Strip thinking/reasoning output that reasoning models (GPT-oss, etc.)
    # sometimes embed in the content field
    raw_reply = _strip_thinking(raw_reply)
    _sanitized = _strip_raw_tool_dump(raw_reply)
    if _sanitized != raw_reply.strip():
        print(f"⚠️ 偵測到 AI 原封不動貼上搜尋結果，已清除原始格式（原長度 {len(raw_reply)} → {len(_sanitized)}）")
    if not _sanitized:
        # The entire reply was a raw dump with no prose at all — better to
        # say something honest than show nothing or garbage to the user.
        _sanitized = "我剛剛查了一下資料，但整理答案時卡住了，你可以換個更具體的問法再問我一次嗎？"
    raw_reply = _sanitized

    # Parse [MEMORY:] and [MOD:] tags from reply — REGEX-based and
    # position-independent. The old rsplit()-based parsing assumed the tag
    # was always at the very END of the reply (taking everything BEFORE it
    # as the "real answer"). Weaker/fallback models sometimes put the tag
    # at the START instead, e.g. "[MEMORY: none]紅石省有紅石南橋、紅石北縣..."
    # — with the old logic, parts[0] (the "real answer") became "" and the
    # ENTIRE actual content got swallowed into memory_str, so the user saw
    # a blank/fallback reply even though the AI had genuinely answered.
    # Regex-matching "[MEMORY:...]" as a self-contained tag and stripping
    # ONLY that substring (wherever it sits) fixes this regardless of
    # whether the model puts it at the start, middle, or end.
    actual_reply = raw_reply
    new_facts = None
    mod_action = None

    _mod_match = re.search(r"\[MOD:\s*(-?\d+)\s*\]", actual_reply)
    if _mod_match:
        try:
            mod_seconds = int(_mod_match.group(1))
            if mod_seconds > 0:
                mod_action = mod_seconds
        except ValueError:
            pass
        actual_reply = (actual_reply[:_mod_match.start()] + actual_reply[_mod_match.end():]).strip()

    _mem_match = re.search(r"\[MEMORY:\s*(.*?)\]", actual_reply, re.DOTALL)
    if _mem_match:
        memory_str = _mem_match.group(1).strip()
        actual_reply = (actual_reply[:_mem_match.start()] + actual_reply[_mem_match.end():]).strip()
        if memory_str.lower() != "none" and memory_str:
            new_facts = [f.strip() for f in memory_str.split(",") if f.strip()]

    # Fix any bare :name: emoji shortcodes into real Discord render syntax
    if actual_reply and message.guild:
        actual_reply = _fix_emoji_shortcodes(actual_reply, message.guild)

    # Record this exchange in the user's short-term conversation history —
    # scoped STRICTLY to this user_id, so it never leaks other people's
    # messages, but lets follow-up questions ("我是Windows用戶" answering a
    # question the bot itself just asked) resolve correctly.
    if actual_reply and clean_content:
        _append_user_history(user_id, clean_content, actual_reply)

    # Final safety: if reply is empty after all parsing, return None so caller can handle
    if not actual_reply:
        actual_reply = None

    return actual_reply, new_facts, mod_action, {"model": _reply_model, "fallback": _reply_fallback, "diag": _reply_diag, "vision_diag": _vision_diag if _vision_diag else []}


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


async def _drive_list_files(_retry_count: int = 4) -> list:
    """List all files currently in the configured Google Drive folder.
    Returns a list of {"id": ..., "name": ...} dicts, or [] on any failure.

    重要：這是 load_from_drive() 的第一步，一旦失敗整個載入流程會直接 return，
    連 chat_ai_settings.json 的下載重試都不會被觸發（設定全部變成硬編碼預設值，
    也就是「重啟後設定被清空」的真正根因之一）。開機瞬間網路/DNS/Google API
    偶發抖動很常見，所以這裡要有自己的重試+退避，不能靠呼叫端補救。"""
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    for _attempt in range(_retry_count):
        token = await _get_drive_access_token()
        if not token:
            if _attempt < _retry_count - 1:
                _wait = 2 * (2 ** _attempt)  # 2s, 4s, 8s, 16s
                print(f"⚠️ Drive 列出檔案：取得 token 失敗，{_wait}s 後重試（第 {_attempt+1}/{_retry_count} 次）")
                await asyncio.sleep(_wait)
                continue
            print("⚠️ Drive 列出檔案：多次重試後仍無法取得 token，放棄")
            return []
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
                        if _attempt < _retry_count - 1:
                            _wait = 2 * (2 ** _attempt)
                            print(f"⚠️ Drive 列出檔案：{_wait}s 後重試（第 {_attempt+1}/{_retry_count} 次）")
                            await asyncio.sleep(_wait)
                            continue
                        return []
                    data = json_module.loads(text)
                    return data.get("files", [])
        except Exception as e:
            print(f"⚠️ Drive list files failed: {e}")
            if _attempt < _retry_count - 1:
                _wait = 2 * (2 ** _attempt)
                print(f"⚠️ Drive 列出檔案：例外後 {_wait}s 重試（第 {_attempt+1}/{_retry_count} 次）")
                await asyncio.sleep(_wait)
                continue
            return []
    return []


async def _drive_download(filename: str, _retry_count: int = 3) -> str:
    """Download a file from Google Drive. Returns content or None.
    Retries transient failures (network blips, token issues, non-200 status)
    with exponential backoff — every JSON file matters at boot, not just
    chat_ai_settings.json, so this retry lives here instead of only being
    special-cased by the caller."""
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    for _attempt in range(_retry_count):
        token = await _get_drive_access_token()
        if not token:
            if _attempt < _retry_count - 1:
                _wait = 2 * (2 ** _attempt)
                print(f"⚠️ Drive 下載 {filename}：取得 token 失敗，{_wait}s 後重試（第 {_attempt+1}/{_retry_count} 次）")
                await asyncio.sleep(_wait)
                continue
            return None
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
                        if _attempt < _retry_count - 1:
                            _wait = 2 * (2 ** _attempt)
                            await asyncio.sleep(_wait)
                            continue
                        return None
                    data = json_module.loads(search_text)
                files = data.get("files", [])
                if not files:
                    # 檔案在 Drive 上真的不存在（不是暫時性錯誤）——不用重試
                    return None

                file_id = files[0]["id"]
                download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
                async with session.get(download_url, headers=headers) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        print(f"⚠️ Drive 下載 {filename} 失敗（{resp.status}）：{err[:400]}")
                        if _attempt < _retry_count - 1:
                            _wait = 2 * (2 ** _attempt)
                            print(f"⚠️ Drive 下載 {filename}：{_wait}s 後重試（第 {_attempt+1}/{_retry_count} 次）")
                            await asyncio.sleep(_wait)
                            continue
                        return None
                    return await resp.text()
        except Exception as e:
            print(f"⚠️ Drive download failed ({filename}): {e}")
            if _attempt < _retry_count - 1:
                _wait = 2 * (2 ** _attempt)
                print(f"⚠️ Drive 下載 {filename}：例外後 {_wait}s 重試（第 {_attempt+1}/{_retry_count} 次）")
                await asyncio.sleep(_wait)
                continue
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Drive 版本歷史復原工具 — 給 /chat drive_revisions 與 /chat drive_restore 用。
# 用途：如果某個資料檔（最典型是 chat_ai_settings.json）不知何故被清空/覆蓋成
# 預設值並已同步回 Drive，靠「重啟重新載入」是救不回來的——因為 Drive 上現在
# 的「最新版本」本身就是壞的。Google Drive 對每個檔案都會保留修訂歷史
# （revisions），所以可以直接把清空前的舊版本內容抓回來、當成新的目前版本
# 重新存回去，不需要使用者手動重新輸入所有設定。
# ─────────────────────────────────────────────────────────────────────────────

async def _drive_get_file_id(filename: str) -> str | None:
    """依檔名在設定的資料夾內找檔案 ID，找不到回傳 None。"""
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
                if resp.status != 200:
                    return None
                data = json_module.loads(await resp.text())
                files = data.get("files", [])
                return files[0]["id"] if files else None
    except Exception as e:
        print(f"⚠️ _drive_get_file_id({filename}) 失敗: {e}")
        return None


async def _drive_list_revisions(filename: str) -> list:
    """列出某檔案的所有修訂版本，新到舊排序。每筆含 id/modifiedTime/size。"""
    file_id = await _drive_get_file_id(filename)
    if not file_id:
        return []
    token = await _get_drive_access_token()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            url = (f"https://www.googleapis.com/drive/v3/files/{file_id}/revisions"
                   f"?fields=revisions(id,modifiedTime,size)&pageSize=1000")
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    print(f"⚠️ 列出 {filename} 版本歷史失敗（{resp.status}）：{err[:300]}")
                    return []
                data = json_module.loads(await resp.text())
                revisions = data.get("revisions", [])
                revisions.sort(key=lambda r: r.get("modifiedTime", ""), reverse=True)
                return revisions
    except Exception as e:
        print(f"⚠️ 列出 {filename} 版本歷史例外: {e}")
        return []


async def _drive_download_revision(filename: str, revision_id: str) -> str | None:
    """下載某檔案指定版本的內容。"""
    file_id = await _drive_get_file_id(filename)
    if not file_id:
        return None
    token = await _get_drive_access_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://www.googleapis.com/drive/v3/files/{file_id}/revisions/{revision_id}?alt=media"
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    print(f"⚠️ 下載 {filename} 版本 {revision_id} 失敗（{resp.status}）：{err[:300]}")
                    return None
                return await resp.text()
    except Exception as e:
        print(f"⚠️ 下載 {filename} 版本 {revision_id} 例外: {e}")
        return None


_drive_file_hashes = {}  # filename -> last-uploaded content hash (for skip-unchanged)

async def sync_to_drive():
    """Sync changed local data files to Google Drive.
    Only uploads files whose content has actually changed since the last sync —
    this avoids ~34 redundant API calls every 20s when most files are unchanged."""
    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_B64") and not os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"):
        return
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if not os.path.isdir(data_dir):
        return
    ok_count = 0
    fail_count = 0
    skip_count = 0
    json_filenames = [f for f in os.listdir(data_dir) if f.endswith(".json")]
    for filename in json_filenames:
        filepath = os.path.join(data_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                file_content = f.read()
            # Skip if content hasn't changed since last upload
            import hashlib as _hashlib
            content_hash = _hashlib.md5(file_content.encode("utf-8")).hexdigest()
            if _drive_file_hashes.get(filename) == content_hash:
                skip_count += 1
                continue
            success = await _drive_upload(filename, file_content)
            if success:
                _drive_file_hashes[filename] = content_hash
                ok_count += 1
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            print(f"⚠️ Sync {filename} failed: {e}")
    if fail_count > 0:
        print(f"⚠️ Drive 同步：{ok_count} 成功，{fail_count} 失敗，{skip_count} 跳過（共 {len(json_filenames)} 個檔案）")
    elif ok_count > 0:
        print(f"✅ Drive 同步完成：{ok_count} 上傳，{skip_count} 跳過（共 {len(json_filenames)} 個檔案）")
    # If everything was skipped, stay quiet — no point spamming logs every 20s


async def _immediate_drive_upload(filename: str):
    """立即上傳單一檔案到 Drive（fire-and-forget，不等待週期性同步迴圈）。
    用於關鍵設定變更後（如 chat_ai_settings.json），避免「剛存檔→伺服器碰巧
    重啟/重新部署→尚未同步到 Drive→重啟時從 Drive 拉回舊資料覆蓋掉」的競態，
    這正是 AI 池資料在使用者換瀏覽器/重啟後消失的根因：本地檔案寫入是同步的，
    但 Drive 上傳要等最多 60 秒的週期迴圈，如果這段時間內容器重啟，
    load_from_drive() 會用 Drive 上的舊版本蓋掉本地剛存的新資料。"""
    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_B64") and not os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"):
        return
    try:
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        filepath = os.path.join(data_dir, filename)
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        success = await _drive_upload(filename, content)
        if success:
            import hashlib as _hashlib
            _drive_file_hashes[filename] = _hashlib.md5(content.encode("utf-8")).hexdigest()
            print(f"✅ 即時同步 {filename} 到 Drive 成功")
        else:
            print(f"⚠️ 即時同步 {filename} 到 Drive 失敗（將由週期迴圈補上）")
    except Exception as e:
        print(f"⚠️ 即時同步 {filename} 例外：{e}（將由週期迴圈補上）")


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
        # _drive_download() 內部本身已有 3 次重試+指數退避（2/4/8s），涵蓋所有
        # 檔案的暫時性網路抖動。chat_ai_settings.json 是全機器人設定的單點故障
        # （API key/model/log_channel_id/所有功能開關都在裡面）——多加幾次
        # 額外重試+更長退避，因為這個檔案值得多等一下也不要冒著清空設定的風險。
        _dl_retries = 5 if filename == "chat_ai_settings.json" else 1
        content = None
        for _extra in range(_dl_retries):
            content = await _drive_download(filename)
            if content:
                if _extra > 0:
                    print(f"✅ {filename} 額外重試成功（第 {_extra+1} 輪）")
                break
            if _extra < _dl_retries - 1:
                _wait = 5 * (_extra + 1)  # 5s, 10s, 15s, 20s
                print(f"⚠️ {filename} 下載仍失敗，{_wait}s 後進行額外重試（第 {_extra+2}/{_dl_retries} 輪）...")
                await asyncio.sleep(_wait)
        if content:
            try:
                filepath = os.path.join(data_dir, filename)
                with open(filepath, "w", encoding="utf-8") as fh:
                    fh.write(content)
                if filename == "chat_ai_settings.json":
                    global _drive_load_succeeded
                    _drive_load_succeeded = True
                print(f"✅ 從 Google Drive 載入 {filename}")
                ok_count += 1
            except Exception as e:
                print(f"⚠️ 寫入 {filename} 失敗：{e}")
                fail_count += 1
        else:
            print(f"⚠️ 下載 {filename} 失敗（Drive 上有列出但下載回傳空內容）")
            fail_count += 1
    print(f"🔄 Drive 載入完成：{ok_count} 個成功，{fail_count} 個失敗（共 {len(json_files)} 個檔案）")


def _cleanup_cooldowns():
    """Trim cooldown dicts and other unbounded structures — keep only recent
    entries. Called every 60s from drive_sync_loop."""
    now = _time.time()
    one_hour_ago = now - 3600
    # Cooldown dicts
    for d in (_correction_cooldowns, _feedback_cooldowns):
        stale = [k for k, v in d.items() if v < one_hour_ago]
        for k in stale:
            d.pop(k, None)
    # quiz_scores: remove users with 0 total score and old date (>7 days)
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    old_date = datetime.fromtimestamp(now - 7 * 86400, GMT8).strftime("%Y-%m-%d")
    stale_scores = [
        uid for uid, e in quiz_scores.items()
        if e.get("total_score", 0) == 0 and e.get("date", "") < old_date
    ]
    for uid in stale_scores:
        quiz_scores.pop(uid, None)
    if stale_scores:
        print(f"🧹 清理 {len(stale_scores)} 個過期且零分的 quiz 玩家記錄")


async def drive_sync_loop():
    """Background task: sync local data to Google Drive every 60 seconds.
    Since we now skip unchanged files, 60s is fine — a crash loses at most
    60s of state, but most cycles are no-ops anyway."""
    while True:
        await asyncio.sleep(chat_ai_settings.get("drive_sync_interval", 60))
        await sync_to_drive()
        # Periodic cleanup of cooldown dicts to prevent unbounded growth
        _cleanup_cooldowns()


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
        "min_response_interval": chat_ai_settings.get("min_response_interval", 0),
        "channels_whitelist": chat_ai_settings["channels_whitelist"],
        "filter_strength": chat_ai_settings.get("filter_strength", "mention"),
        "abuse_detection_enabled": chat_ai_settings.get("abuse_detection_enabled", False),
        "abuse_detection_strictness": chat_ai_settings.get("abuse_detection_strictness", "medium"),
        "abuse_mute_admins": chat_ai_settings.get("abuse_mute_admins", False),
        "log_channel_id": chat_ai_settings.get("log_channel_id"),
        "micropedia_enabled": chat_ai_settings.get("micropedia_enabled", True),
        "micropedia_max_results": chat_ai_settings.get("micropedia_max_results", 5),
        "ai_hard_ceiling": chat_ai_settings.get("ai_hard_ceiling", 20),
        "ai_soft_target": chat_ai_settings.get("ai_soft_target", 16),
        "ai_max_tokens": chat_ai_settings.get("ai_max_tokens", 2000),
        "preprocess_timeout": chat_ai_settings.get("preprocess_timeout", 6),
        "tool_skip_threshold": chat_ai_settings.get("tool_skip_threshold", 12),
        "circuit_breaker_cooldown": chat_ai_settings.get("circuit_breaker_cooldown", 120),
        "forum_index_interval": chat_ai_settings.get("forum_index_interval", 900),
        "channel_index_interval": chat_ai_settings.get("channel_index_interval", 1800),
        "drive_sync_interval": chat_ai_settings.get("drive_sync_interval", 60),
        "fallback_enabled": chat_ai_settings.get("fallback_enabled", False),
        "fallback_api_url": chat_ai_settings.get("fallback_api_url", ""),
        "fallback_api_key_masked": (lambda k: k[:6]+"..."+k[-4:] if len(k)>10 else ("***" if k else ""))(chat_ai_settings.get("fallback_api_key", "")),
        "fallback_model": chat_ai_settings.get("fallback_model", ""),
        "model_fallback_chain": chat_ai_settings.get("model_fallback_chain", ""),
        "fallback_daily_limit": chat_ai_settings.get("fallback_daily_limit", 10),
        "fallback_rate_per_min": chat_ai_settings.get("fallback_rate_per_min", 6),
        "fallback_owner_exempt": chat_ai_settings.get("fallback_owner_exempt", True),
        "owner_skip_model_chain": chat_ai_settings.get("owner_skip_model_chain", True),
        "fallback_daily_limit_msg": chat_ai_settings.get("fallback_daily_limit_msg", ""),
        "fallback_rate_limit_msg": chat_ai_settings.get("fallback_rate_limit_msg", ""),
        "entertainment_unavailable_msg": chat_ai_settings.get("entertainment_unavailable_msg", ""),
        "circuit_cooldown_msg": chat_ai_settings.get("circuit_cooldown_msg", ""),
        "vision_model": chat_ai_settings.get("vision_model", ""),
        "vision_fallback_chain": chat_ai_settings.get("vision_fallback_chain", ""),
        "ai_room_enabled": ai_chat_rooms.get("enabled", True),
        "ai_room_panel_channel_id": ai_chat_rooms.get("panel_channel_id"),
        "ai_room_category_id": ai_chat_rooms.get("category_id"),
        "ai_room_max_rooms": ai_chat_rooms.get("max_rooms", 50),
        "ai_room_max_history": ai_chat_rooms.get("max_history_messages", 50),
        "ai_room_count": len(ai_chat_rooms.get("rooms", {})),
        # AI 網警
        "ai_mod_enabled": chat_ai_settings.get("ai_mod_enabled", False),
        "ai_mod_model": chat_ai_settings.get("ai_mod_model", ""),
        "ai_mod_api_url": chat_ai_settings.get("ai_mod_api_url", ""),
        "ai_mod_api_key_masked": (lambda k: k[:6]+"..."+k[-4:] if len(k)>10 else ("***" if k else ""))(chat_ai_settings.get("ai_mod_api_key", "")),
        "ai_mod_report_channel": chat_ai_settings.get("ai_mod_report_channel"),
        "ai_mod_custom_rules": chat_ai_settings.get("ai_mod_custom_rules", ""),
        "ai_mod_confidence": chat_ai_settings.get("ai_mod_confidence", "medium"),
        "ai_mod_cooldown": chat_ai_settings.get("ai_mod_cooldown", 30),
        "ai_mod_max_tokens": chat_ai_settings.get("ai_mod_max_tokens", 150),
        "ai_mod_timeout": chat_ai_settings.get("ai_mod_timeout", 10),
        "ai_mod_exempt_roles": chat_ai_settings.get("ai_mod_exempt_roles", []),
        "ai_mod_severe_enabled": chat_ai_settings.get("ai_mod_severe_enabled", False),
        "ai_mod_severe_rules": chat_ai_settings.get("ai_mod_severe_rules", ""),
        "quiz_model": chat_ai_settings.get("quiz_model", ""),
        "turtle_soup_model": chat_ai_settings.get("turtle_soup_model", ""),
        "werewolf_model": chat_ai_settings.get("werewolf_model", ""),
        "fortune_model": chat_ai_settings.get("fortune_model", ""),
        "chat_model": chat_ai_settings.get("chat_model", ""),
        "admin_model": chat_ai_settings.get("admin_model", ""),
        "entertainment_model": chat_ai_settings.get("entertainment_model", ""),
        # ── AI 池 ──
        "ai_pool": [
            {**{k: v for k, v in entry.items() if k != "api_key"},
             "api_key_masked": (lambda k: k[:6]+"..."+k[-4:] if len(k)>10 else ("***" if k else ""))(entry.get("api_key", ""))}
            for entry in chat_ai_settings.get("ai_pool", [])
        ],
        "model_roles": chat_ai_settings.get("model_roles", {}),
        "model_chains": chat_ai_settings.get("model_chains", {"main": [], "vision": []}),
        # ── 文生圖 ──
        "t2i_enabled": chat_ai_settings.get("t2i_enabled", False),
        "t2i_api_url": chat_ai_settings.get("t2i_api_url", ""),
        "t2i_api_key_masked": (lambda k: k[:6]+"..."+k[-4:] if len(k)>10 else ("***" if k else ""))(chat_ai_settings.get("t2i_api_key", "")),
        "t2i_model": chat_ai_settings.get("t2i_model", ""),
        "t2i_size": chat_ai_settings.get("t2i_size", "1024x1024"),
        "t2i_quality": chat_ai_settings.get("t2i_quality", "standard"),
        "t2i_cooldown": chat_ai_settings.get("t2i_cooldown", 60),
        "t2i_daily_limit": chat_ai_settings.get("t2i_daily_limit", 10),
        "t2i_owner_exempt": chat_ai_settings.get("t2i_owner_exempt", True),
        "t2i_auto_detect": chat_ai_settings.get("t2i_auto_detect", True),
        "t2i_premium_enabled": chat_ai_settings.get("t2i_premium_enabled", False),
        "t2i_premium_api_url": chat_ai_settings.get("t2i_premium_api_url", ""),
        "t2i_premium_api_key_masked": (lambda k: k[:6]+"..."+k[-4:] if len(k)>10 else ("***" if k else ""))(chat_ai_settings.get("t2i_premium_api_key", "")),
        "t2i_premium_model": chat_ai_settings.get("t2i_premium_model", ""),
        "t2i_premium_size": chat_ai_settings.get("t2i_premium_size", ""),
        "t2i_premium_quality": chat_ai_settings.get("t2i_premium_quality", ""),
        "t2i_premium_daily_limit": chat_ai_settings.get("t2i_premium_daily_limit", 30),
        "t2i_premium_daily_count": chat_ai_settings.get("t2i_premium_daily_count", 0),
        "t2i_premium_daily_date": chat_ai_settings.get("t2i_premium_daily_date", ""),
        # ── 生圖提示詞過濾 ──
        "t2i_filter_enabled": chat_ai_settings.get("t2i_filter_enabled", False),
        "t2i_filter_pool_id": chat_ai_settings.get("t2i_filter_pool_id", ""),
        "t2i_filter_model": chat_ai_settings.get("t2i_filter_model", ""),
        "t2i_filter_timeout": chat_ai_settings.get("t2i_filter_timeout", 15),
        "t2i_filter_max_tokens": chat_ai_settings.get("t2i_filter_max_tokens", 100),
        "t2i_filter_strictness": chat_ai_settings.get("t2i_filter_strictness", "medium"),
        "t2i_filter_vision_model": chat_ai_settings.get("t2i_filter_vision_model", ""),
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
    if "min_response_interval" in body:
        chat_ai_settings["min_response_interval"] = int(body["min_response_interval"])
    if "filter_strength" in body:
        chat_ai_settings["filter_strength"] = body["filter_strength"]
    if "abuse_detection_enabled" in body:
        chat_ai_settings["abuse_detection_enabled"] = body["abuse_detection_enabled"]
    if "abuse_detection_strictness" in body:
        chat_ai_settings["abuse_detection_strictness"] = body["abuse_detection_strictness"]
    if "abuse_mute_admins" in body:
        chat_ai_settings["abuse_mute_admins"] = body["abuse_mute_admins"]
    if "ai_hard_ceiling" in body:
        chat_ai_settings["ai_hard_ceiling"] = int(body["ai_hard_ceiling"])
    if "reasoning_effort" in body:
        chat_ai_settings["reasoning_effort"] = body["reasoning_effort"]
    if "reasoning_admin_effort" in body:
        chat_ai_settings["reasoning_admin_effort"] = body["reasoning_admin_effort"]
    if "reasoning_chat_effort" in body:
        chat_ai_settings["reasoning_chat_effort"] = body["reasoning_chat_effort"]
    if "reasoning_entertainment_effort" in body:
        chat_ai_settings["reasoning_entertainment_effort"] = body["reasoning_entertainment_effort"]
    if "reasoning_enabled_timeout" in body:
        chat_ai_settings["reasoning_enabled_timeout"] = int(body["reasoning_enabled_timeout"])
    if "reasoning_disabled_timeout" in body:
        chat_ai_settings["reasoning_disabled_timeout"] = int(body["reasoning_disabled_timeout"])
    if "ai_soft_target" in body:
        chat_ai_settings["ai_soft_target"] = int(body["ai_soft_target"])
    if "ai_max_tokens" in body:
        chat_ai_settings["ai_max_tokens"] = int(body["ai_max_tokens"])
    if "preprocess_timeout" in body:
        chat_ai_settings["preprocess_timeout"] = int(body["preprocess_timeout"])
    if "tool_skip_threshold" in body:
        chat_ai_settings["tool_skip_threshold"] = int(body["tool_skip_threshold"])
    if "circuit_breaker_cooldown" in body:
        chat_ai_settings["circuit_breaker_cooldown"] = int(body["circuit_breaker_cooldown"])
    if "vision_extra_budget" in body:
        chat_ai_settings["vision_extra_budget"] = int(body["vision_extra_budget"])
    if "turtle_soup_enabled" in body:
        chat_ai_settings["turtle_soup_enabled"] = bool(body["turtle_soup_enabled"])
        _save_turtle_soup()
    if "turtle_soup_channel_id" in body:
        chat_ai_settings["turtle_soup_channel_id"] = body["turtle_soup_channel_id"]
        _save_turtle_soup()
    if "turtle_soup_difficulty" in body:
        chat_ai_settings["turtle_soup_difficulty"] = body["turtle_soup_difficulty"]
        _save_turtle_soup()
    if "forum_index_interval" in body:
        chat_ai_settings["forum_index_interval"] = int(body["forum_index_interval"])
    if "channel_index_interval" in body:
        chat_ai_settings["channel_index_interval"] = int(body["channel_index_interval"])
    if "drive_sync_interval" in body:
        chat_ai_settings["drive_sync_interval"] = int(body["drive_sync_interval"])
    if "fallback_enabled" in body:
        chat_ai_settings["fallback_enabled"] = body["fallback_enabled"]
    # AI chat room settings
    if "ai_room_enabled" in body:
        ai_chat_rooms["enabled"] = body["ai_room_enabled"]
        save_ai_chat_rooms()
    if "ai_room_max_rooms" in body:
        ai_chat_rooms["max_rooms"] = int(body["ai_room_max_rooms"])
        save_ai_chat_rooms()
    if "ai_room_max_history" in body:
        ai_chat_rooms["max_history_messages"] = int(body["ai_room_max_history"])
        save_ai_chat_rooms()
    if "fallback_api_url" in body:
        chat_ai_settings["fallback_api_url"] = body["fallback_api_url"]
    if "fallback_api_key" in body and body["fallback_api_key"]:
        chat_ai_settings["fallback_api_key"] = body["fallback_api_key"]
    if "fallback_model" in body:
        chat_ai_settings["fallback_model"] = body["fallback_model"]
    if "model_fallback_chain" in body:
        chat_ai_settings["model_fallback_chain"] = body["model_fallback_chain"]
    if "fallback_daily_limit" in body:
        chat_ai_settings["fallback_daily_limit"] = int(body["fallback_daily_limit"])
    if "fallback_rate_per_min" in body:
        chat_ai_settings["fallback_rate_per_min"] = int(body["fallback_rate_per_min"])
    if "fallback_owner_exempt" in body:
        chat_ai_settings["fallback_owner_exempt"] = bool(body["fallback_owner_exempt"])
    if "owner_skip_model_chain" in body:
        chat_ai_settings["owner_skip_model_chain"] = bool(body["owner_skip_model_chain"])
    if "fallback_daily_limit_msg" in body:
        chat_ai_settings["fallback_daily_limit_msg"] = body["fallback_daily_limit_msg"]
    if "fallback_rate_limit_msg" in body:
        chat_ai_settings["fallback_rate_limit_msg"] = body["fallback_rate_limit_msg"]
    if "entertainment_unavailable_msg" in body:
        chat_ai_settings["entertainment_unavailable_msg"] = body["entertainment_unavailable_msg"]
    if "circuit_cooldown_msg" in body:
        chat_ai_settings["circuit_cooldown_msg"] = body["circuit_cooldown_msg"]
    if "vision_fallback_chain" in body:
        chat_ai_settings["vision_fallback_chain"] = body["vision_fallback_chain"]
    # AI 網警設定
    if "ai_mod_enabled" in body:
        chat_ai_settings["ai_mod_enabled"] = bool(body["ai_mod_enabled"])
    if "ai_mod_model" in body:
        chat_ai_settings["ai_mod_model"] = body["ai_mod_model"]
    if "ai_mod_api_url" in body:
        chat_ai_settings["ai_mod_api_url"] = body["ai_mod_api_url"]
    if "ai_mod_api_key" in body and body["ai_mod_api_key"]:
        chat_ai_settings["ai_mod_api_key"] = body["ai_mod_api_key"]
    if "ai_mod_report_channel" in body:
        chat_ai_settings["ai_mod_report_channel"] = body["ai_mod_report_channel"]
    if "ai_mod_custom_rules" in body:
        chat_ai_settings["ai_mod_custom_rules"] = body["ai_mod_custom_rules"]
    if "ai_mod_confidence" in body:
        chat_ai_settings["ai_mod_confidence"] = body["ai_mod_confidence"]
    if "ai_mod_cooldown" in body:
        chat_ai_settings["ai_mod_cooldown"] = int(body["ai_mod_cooldown"])
    if "ai_mod_max_tokens" in body:
        chat_ai_settings["ai_mod_max_tokens"] = int(body["ai_mod_max_tokens"])
    if "ai_mod_timeout" in body:
        chat_ai_settings["ai_mod_timeout"] = int(body["ai_mod_timeout"])
    if "ai_mod_exempt_roles" in body:
        chat_ai_settings["ai_mod_exempt_roles"] = body["ai_mod_exempt_roles"]
    if "ai_mod_severe_enabled" in body:
        chat_ai_settings["ai_mod_severe_enabled"] = bool(body["ai_mod_severe_enabled"])
    if "ai_mod_severe_rules" in body:
        chat_ai_settings["ai_mod_severe_rules"] = body["ai_mod_severe_rules"]
    if "quiz_model" in body:
        chat_ai_settings["quiz_model"] = body["quiz_model"]
    if "turtle_soup_model" in body:
        chat_ai_settings["turtle_soup_model"] = body["turtle_soup_model"]
    if "werewolf_model" in body:
        chat_ai_settings["werewolf_model"] = body["werewolf_model"]
    if "fortune_model" in body:
        chat_ai_settings["fortune_model"] = body["fortune_model"]
    if "chat_model" in body:
        chat_ai_settings["chat_model"] = body["chat_model"]
    if "admin_model" in body:
        chat_ai_settings["admin_model"] = body["admin_model"]
    if "entertainment_model" in body:
        chat_ai_settings["entertainment_model"] = body["entertainment_model"]
    # ── AI 池 ──
    if "ai_pool" in body:
        # Preserve existing API keys if the new value is empty (user didn't re-enter)
        new_pool = body["ai_pool"]
        old_pool = chat_ai_settings.get("ai_pool", [])
        old_keys = {e.get("id"): e.get("api_key", "") for e in old_pool}
        for entry in new_pool:
            if not entry.get("api_key"):
                entry["api_key"] = old_keys.get(entry.get("id"), "")
        chat_ai_settings["ai_pool"] = new_pool
    if "model_roles" in body:
        chat_ai_settings["model_roles"] = body["model_roles"]
        # ── 同步 legacy 欄位 ──
        # dashboard 的 saveChatAISettings 只同步 main/backup 的 legacy 欄位，
        # 但 vision/quiz/turtle_soup/werewolf/fortune/chat/admin/entertainment
        # 的模型名稱存在 model_roles 裡，很多舊程式碼仍讀 legacy 欄位
        # (e.g. settings.get("vision_model"))，不同步會導致這些功能找不到模型。
        _pool = chat_ai_settings.get("ai_pool", [])
        _legacy_sync_map = {
            "vision": "vision_model",
            "quiz": "quiz_model",
            "turtle_soup": "turtle_soup_model",
            "werewolf": "werewolf_model",
            "fortune": "fortune_model",
            "chat": "chat_model",
            "admin": "admin_model",
            "entertainment": "entertainment_model",
        }
        for _role, _legacy_key in _legacy_sync_map.items():
            _binding = body["model_roles"].get(_role)
            if _binding and isinstance(_binding, dict):
                chat_ai_settings[_legacy_key] = _binding.get("model", "")
            # If role is unassigned (binding is None/empty), clear legacy too
            elif _binding is None:
                chat_ai_settings[_legacy_key] = ""
    if "model_chains" in body:
        chat_ai_settings["model_chains"] = body["model_chains"]
    if "log_channel_id" in body:
        chat_ai_settings["log_channel_id"] = body["log_channel_id"] or None
    # ── 文生圖 ──
    if "t2i_enabled" in body:
        chat_ai_settings["t2i_enabled"] = bool(body["t2i_enabled"])
    if "t2i_api_url" in body:
        chat_ai_settings["t2i_api_url"] = body["t2i_api_url"]
    if "t2i_api_key" in body and body["t2i_api_key"]:
        chat_ai_settings["t2i_api_key"] = body["t2i_api_key"]
    if "t2i_model" in body:
        chat_ai_settings["t2i_model"] = body["t2i_model"]
    if "t2i_size" in body:
        chat_ai_settings["t2i_size"] = body["t2i_size"]
    if "t2i_quality" in body:
        chat_ai_settings["t2i_quality"] = body["t2i_quality"]
    if "t2i_cooldown" in body:
        chat_ai_settings["t2i_cooldown"] = int(body["t2i_cooldown"])
    if "t2i_daily_limit" in body:
        chat_ai_settings["t2i_daily_limit"] = int(body["t2i_daily_limit"])
    if "t2i_owner_exempt" in body:
        chat_ai_settings["t2i_owner_exempt"] = bool(body["t2i_owner_exempt"])
    if "t2i_auto_detect" in body:
        chat_ai_settings["t2i_auto_detect"] = bool(body["t2i_auto_detect"])
    if "t2i_premium_enabled" in body:
        chat_ai_settings["t2i_premium_enabled"] = bool(body["t2i_premium_enabled"])
    if "t2i_premium_api_url" in body:
        chat_ai_settings["t2i_premium_api_url"] = body["t2i_premium_api_url"]
    if "t2i_premium_api_key" in body and body["t2i_premium_api_key"]:
        chat_ai_settings["t2i_premium_api_key"] = body["t2i_premium_api_key"]
    if "t2i_premium_model" in body:
        chat_ai_settings["t2i_premium_model"] = body["t2i_premium_model"]
    if "t2i_premium_size" in body:
        chat_ai_settings["t2i_premium_size"] = body["t2i_premium_size"]
    if "t2i_premium_quality" in body:
        chat_ai_settings["t2i_premium_quality"] = body["t2i_premium_quality"]
    if "t2i_premium_daily_limit" in body:
        chat_ai_settings["t2i_premium_daily_limit"] = int(body["t2i_premium_daily_limit"])
    # ── 生圖提示詞過濾 ──
    if "t2i_filter_enabled" in body:
        chat_ai_settings["t2i_filter_enabled"] = bool(body["t2i_filter_enabled"])
    if "t2i_filter_pool_id" in body:
        chat_ai_settings["t2i_filter_pool_id"] = body["t2i_filter_pool_id"]
    if "t2i_filter_model" in body:
        chat_ai_settings["t2i_filter_model"] = body["t2i_filter_model"]
    if "t2i_filter_timeout" in body:
        chat_ai_settings["t2i_filter_timeout"] = int(body["t2i_filter_timeout"])
    if "t2i_filter_max_tokens" in body:
        chat_ai_settings["t2i_filter_max_tokens"] = int(body["t2i_filter_max_tokens"])
    if "t2i_filter_strictness" in body:
        _strict = body["t2i_filter_strictness"].lower()
        if _strict in ("loose", "medium", "strict"):
            chat_ai_settings["t2i_filter_strictness"] = _strict
    if "t2i_filter_vision_model" in body:
        chat_ai_settings["t2i_filter_vision_model"] = body["t2i_filter_vision_model"]
    save_chat_ai_settings()
    return web.json_response({"ok": True})


# ── Schedule settings API ──
async def api_get_schedule_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({
        "enabled": schedule_settings.get("enabled", True),
        "review_channel_id": schedule_settings.get("review_channel_id"),
        "target_channel_id": schedule_settings.get("target_channel_id"),
        "mention_role_id": schedule_settings.get("mention_role_id"),
        "checkin_start": schedule_settings.get("checkin_start", "13:00"),
        "checkin_end": schedule_settings.get("checkin_end", "21:00"),
        "review_time": schedule_settings.get("review_time", "15:00"),
        "motion_time": schedule_settings.get("motion_time", "20:00"),
        "vote_time": schedule_settings.get("vote_time", "21:00"),
        "regular_meeting_no": schedule_settings.get("regular_meeting_no", 1),
        "briefing_meeting_no": schedule_settings.get("briefing_meeting_no", 1),
    })


async def api_set_schedule_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    if "target_channel_id" in body:
        schedule_settings["target_channel_id"] = body["target_channel_id"] or None
    if "mention_role_id" in body:
        schedule_settings["mention_role_id"] = body["mention_role_id"] or None
    if "review_channel_id" in body:
        schedule_settings["review_channel_id"] = body["review_channel_id"] or None
    if "checkin_start" in body:
        schedule_settings["checkin_start"] = body["checkin_start"]
    if "checkin_end" in body:
        schedule_settings["checkin_end"] = body["checkin_end"]
    if "review_time" in body:
        schedule_settings["review_time"] = body["review_time"]
    if "motion_time" in body:
        schedule_settings["motion_time"] = body["motion_time"]
    if "vote_time" in body:
        schedule_settings["vote_time"] = body["vote_time"]
    if "regular_meeting_no" in body:
        schedule_settings["regular_meeting_no"] = int(body["regular_meeting_no"])
    if "briefing_meeting_no" in body:
        schedule_settings["briefing_meeting_no"] = int(body["briefing_meeting_no"])
    save_schedule_settings()
    return web.json_response({"ok": True})


async def _post_test_payload(session, url, headers, payload):
    """POST 一份可能帶有 reasoning 控制欄位的測試 payload。
    若端點對這些欄位做嚴格驗證回 400，自動移除後重試一次——
    這樣測試才能公平反映「連線本身」是否正常，不會因為某個模型
    不支援 reasoning_effort/thinking/enable_thinking 就整個判死刑。
    回傳 (status, data_or_None, err_text_or_None)。"""
    async with session.post(url, json=payload, headers=headers) as resp:
        status = resp.status
        if status == 200:
            return status, await resp.json(), None
        err_text = await resp.text()
        if status == 400 and "unsupported" in err_text.lower() and any(
            k in err_text.lower() for k in ("reasoning_effort", "thinking", "enable_thinking")
        ):
            payload_clean = {k: v for k, v in payload.items()
                              if k not in ("reasoning_effort", "thinking", "enable_thinking")}
            async with session.post(url, json=payload_clean, headers=headers) as resp2:
                status2 = resp2.status
                if status2 == 200:
                    return status2, await resp2.json(), None
                return status2, None, (await resp2.text())[:200]
        return status, None, err_text[:200]


def _parse_ai_text_result(label, model, data, elapsed, http_status):
    """解析文字模型測試回應。內容空白一律不算「ok」——即使 HTTP 200，
    finish_reason=length 代表 max_tokens 被（通常是隱藏的思考過程）
    吃光，這種模型換到正式聊天的短預算場景下極可能逾時或回應空白，
    絕對不能標成「正常」讓人誤以為換模型不會出事。"""
    content = ""
    choices = data.get("choices", []) if data else []
    finish_reason = choices[0].get("finish_reason", "?") if choices else "?"
    if choices:
        content = (choices[0].get("message", {}).get("content") or "").strip()
    if not content:
        return {"label": label, "status": "degraded", "http_status": http_status,
                "latency_ms": elapsed, "model": model,
                "error": f"連線成功但內容空白（finish_reason={finish_reason}）——"
                         f"此模型可能忽略關閉思考的請求，實際聊天時容易在短預算下逾時或回應空白"}
    return {"label": label, "status": "ok", "latency_ms": elapsed,
            "model": model, "response_snippet": content[:100]}


async def _test_ai_text_model(api_url, api_key, model, label, default_model="gpt-4o-mini", timeout_total=20):
    """通用文字模型連線測試——刻意比照正式聊天路徑的「短預算、明確關閉
    reasoning」設定，這樣測試結果才能真正反映換模型後聊天功能會不會出包。
    不這樣做的話，測試本身沒設定 reasoning 控制參數，對「預設就會思考」
    的模型（如 gpt-oss 系列）測起來會比實際聊天時表現更好、給假的安心感。

    回傳 status 一律是下列四種之一：
      - "ok"       連線成功且拿到真正的文字內容
      - "degraded" 連線成功但內容空白（即使已明確要求關閉 reasoning）——
                    模型很可能在實際短預算聊天情境下也會空白/逾時
      - "timeout"  請求逾時
      - "error"    HTTP 錯誤或其他例外
    """
    import time as _time
    _model = model or default_model
    if not api_url or not api_key:
        return {"label": label, "status": "error", "model": _model, "error": "API URL 或 Key 未設定"}
    url = api_url.strip()
    if not url.endswith("/chat/completions"):
        if url.endswith("/v1") or url.endswith("/v2"):
            url += "/chat/completions"
        else:
            url += "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": _model,
        "messages": [{"role": "user", "content": "請回覆「連線正常」四個字，不要有其他內容。"}],
        "max_tokens": 50,
        "stream": False,
        # 明確關閉 reasoning——不能只是「不送參數」，因為很多開源
        # reasoning 模型是「預設思考」架構，沒收到關閉指令就會用自己的
        # 預設行為（往往還是開著思考）。已知會拒絕未知欄位的端點會被
        # _post_test_payload 自動偵測並移除重試。
        "reasoning_effort": "none",
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    t0 = _time.monotonic()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_total, sock_read=max(8, timeout_total - 5))) as sess:
            status, data, err_text = await _post_test_payload(sess, url, headers, payload)
            elapsed = int((_time.monotonic() - t0) * 1000)
            if status != 200:
                return {"label": label, "status": "error", "http_status": status,
                        "latency_ms": elapsed, "model": _model,
                        "error": f"HTTP {status}: {err_text or ''}"}
            return _parse_ai_text_result(label, _model, data, elapsed, status)
    except asyncio.TimeoutError:
        elapsed = int((_time.monotonic() - t0) * 1000)
        return {"label": label, "status": "timeout", "latency_ms": elapsed,
                "model": _model, "error": f"請求逾時（{timeout_total} 秒）"}
    except Exception as e:
        elapsed = int((_time.monotonic() - t0) * 1000)
        return {"label": label, "status": "error", "latency_ms": elapsed,
                "model": _model, "error": str(e)[:300]}


async def api_test_ai_connection(request):
    """Test primary and/or fallback API connection with a minimal request.
    POST body: {"target": "primary" | "fallback" | "both" | "chain"}
               {"model": "specific model name to test (optional)"}
    - "chain": test every model in the model_fallback_chain individually
    Returns per-target: status (ok/error/timeout), latency_ms, model, response_snippet, error."""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    target = body.get("target", "both")
    specific_model = body.get("model", "").strip()

    async def _test_one(api_url, api_key, model, label):
        return await _test_ai_text_model(api_url, api_key, model, label,
                                          default_model="gpt-4o-mini", timeout_total=15)

    results = []
    if target == "chain":
        # 測試主模型 + 池式降級鏈中每一項（每項可有不同 API 端點）
        main_url, main_key, main_model = _resolve_role_endpoint("main", chat_ai_settings)
        results.append(await _test_one(main_url, main_key, main_model, f"主模型 · {main_model}"))
        for c_url, c_key, c_model in _resolve_chain("main", chat_ai_settings):
            results.append(await _test_one(c_url, c_key, c_model, f"降級鏈 · {c_model}"))
    elif target == "vision_chain":
        # 測試視覺模型 + 視覺降級鏈
        v_url, v_key, v_model = _resolve_role_endpoint("vision", chat_ai_settings)
        if v_model:
            results.append(await _test_one(v_url, v_key, v_model, f"視覺模型 · {v_model}"))
        for c_url, c_key, c_model in _resolve_chain("vision", chat_ai_settings):
            results.append(await _test_one(c_url, c_key, c_model, f"視覺降級鏈 · {c_model}"))
        if not results:
            results.append({"label": "視覺模型", "status": "error", "error": "未設定視覺模型"})
    elif target == "primary" and specific_model:
        main_url, main_key, _ = _resolve_role_endpoint("main", chat_ai_settings)
        results.append(await _test_one(main_url, main_key, specific_model, f"主模型 · {specific_model}"))
    else:
        if target in ("primary", "both"):
            main_url, main_key, main_model = _resolve_role_endpoint("main", chat_ai_settings)
            results.append(await _test_one(main_url, main_key, main_model, "主模型"))
        if target in ("fallback", "both"):
            bk_url, bk_key, bk_model = _resolve_role_endpoint("backup", chat_ai_settings)
            results.append(await _test_one(bk_url, bk_key, bk_model, "備援模型"))
    return web.json_response({"results": results})


async def api_test_admin_functions(request):
    """Comprehensive test of ALL AI models used by administrative functions:
    1. Primary model (text) — the main model (e.g. deepseek-v4-pro)
    2. Every model in the model_fallback_chain (text)
    3. Backup/fallback model (text) — the owner's Gemini
    4. Primary vision model (image recognition) — if vision_model is set
    5. Backup vision model (image recognition) — fallback_model (assumed multimodal)

    For vision tests, sends a preset test image (a simple colored flag-like
    SVG generated as a data URI — no upload needed) and checks whether the
    model returns a valid JSON response describing the image.

    Returns per-test: label, status (ok/error/timeout), latency_ms, model,
    response_snippet, error, and for vision tests: vision_ok (bool)."""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)

    import time as _time

    # A real raster PNG flag-like test image as a data URI — generated by
    # hand (struct + zlib, no Pillow dependency) so no image upload is
    # needed. IMPORTANT: this used to be an SVG data URI, which several
    # vision backends flat-out reject — Gemini returned
    # "Unsupported MIME type: image/svg+xml" (HTTP 400), and the Llama
    # vision endpoint's upstream also failed trying to decode it (HTTP 502
    # "Upstream request failed"). SVG is a vector format; vision models
    # need actual pixel data, so a real PNG is required for this test to
    # mean anything. Encodes a small 60×36 truecolor image: yellow bars top
    # & bottom, red field, blue circle in the middle — reads as a flag design.
    def _make_test_flag_png_data_uri() -> str:
        import struct, zlib, base64 as _b64
        width, height = 60, 36
        yellow, red, blue = (255, 204, 0), (204, 0, 0), (0, 51, 204)
        cx, cy, r2 = width / 2, height / 2, 9 * 9
        raw = bytearray()
        for y in range(height):
            raw.append(0)  # PNG filter byte: none
            for x in range(width):
                if y < 3 or y >= height - 3:
                    px = yellow
                elif (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                    px = blue
                else:
                    px = red
                raw += bytes(px)
        compressed = zlib.compress(bytes(raw), 9)

        def _chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        png = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
        png += _chunk(b"IHDR", ihdr)
        png += _chunk(b"IDAT", compressed)
        png += _chunk(b"IEND", b"")
        return "data:image/png;base64," + _b64.b64encode(png).decode()

    test_image_url = _make_test_flag_png_data_uri()

    async def _test_text(api_url, api_key, model, label):
        if not api_url or not api_key:
            return {"label": label, "type": "text", "status": "error", "error": "API URL 或 Key 未設定"}
        if not model:
            return {"label": label, "type": "text", "status": "error", "error": "模型名稱未設定"}
        result = await _test_ai_text_model(api_url, api_key, model, label, timeout_total=30)
        result["type"] = "text"
        return result

    async def _test_vision(api_url, api_key, vision_model, label):
        if not api_url or not api_key:
            return {"label": label, "type": "vision", "status": "error", "error": "API URL 或 Key 未設定"}
        if not vision_model:
            return {"label": label, "type": "vision", "status": "skipped",
                    "error": "視覺模型未設定，跳過"}
        url = api_url.strip()
        if not url.endswith("/chat/completions"):
            if url.endswith("/v1") or url.endswith("/v2"):
                url += "/chat/completions"
            else:
                url += "/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "這是一張測試用的圖片。請回答 JSON："
                                '{"has_image": true/false, "description": "簡短描述圖片內容"}'
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": test_image_url},
                        },
                    ],
                }
            ],
            "max_tokens": 200,
            "temperature": 0.1,
            "stream": False,
            # 跟文字模型測試一致：明確關閉 reasoning，避免「預設思考」的
            # 視覺模型把 max_tokens 燒在隱藏思考過程上，回不了 JSON 判讀結果。
            "reasoning_effort": "none",
            "thinking": {"type": "disabled"},
            "enable_thinking": False,
        }
        t0 = _time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60, sock_read=50)) as sess:
                status, data, err_text = await _post_test_payload(sess, url, headers, payload)
                elapsed = int((_time.monotonic() - t0) * 1000)
                if status != 200:
                    return {"label": label, "type": "vision", "status": "error",
                            "http_status": status, "latency_ms": elapsed,
                            "model": vision_model,
                            "error": f"HTTP {status}: {err_text or ''}",
                            "vision_ok": False}
                content_text = ""
                choices = data.get("choices", [])
                if choices:
                    content_text = (choices[0].get("message", {}).get("content") or "").strip()
                vision_ok = False
                desc = ""
                if not content_text:
                    finish_reason = choices[0].get("finish_reason", "?") if choices else "?"
                    return {"label": label, "type": "vision", "status": "degraded",
                            "latency_ms": elapsed, "model": vision_model,
                            "error": f"連線成功但內容空白（finish_reason={finish_reason}），無法判讀圖片——"
                                     f"此模型可能忽略關閉思考的請求",
                            "vision_ok": False}
                try:
                    if content_text.startswith("```"):
                        content_text = content_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                    parsed = json_module.loads(content_text)
                    vision_ok = bool(parsed.get("has_image", False))
                    desc = parsed.get("description", "")
                except Exception:
                    # If JSON parse fails, check if response mentions image
                    if any(kw in content_text.lower() for kw in ["圖片", "image", "紅色", "藍色", "圓形", "flag"]):
                        vision_ok = True
                        desc = content_text[:100]
                return {"label": label, "type": "vision", "status": "ok", "latency_ms": elapsed,
                        "model": vision_model, "response_snippet": (desc or content_text)[:150],
                        "vision_ok": vision_ok}
        except asyncio.TimeoutError:
            elapsed = int((_time.monotonic() - t0) * 1000)
            return {"label": label, "type": "vision", "status": "timeout", "latency_ms": elapsed,
                    "model": vision_model, "error": "請求逾時（60 秒）", "vision_ok": False}
        except Exception as e:
            elapsed = int((_time.monotonic() - t0) * 1000)
            return {"label": label, "type": "vision", "status": "error", "latency_ms": elapsed,
                    "model": vision_model, "error": str(e)[:300], "vision_ok": False}

    results = []

    # ── Text models ──
    # 1. Primary model
    results.append(await _test_text(
        chat_ai_settings.get("api_url", ""),
        chat_ai_settings.get("api_key", ""),
        chat_ai_settings.get("model", ""),
        "主 API · 主模型"
    ))

    # 2. Every model in the fallback chain
    chain_raw = chat_ai_settings.get("model_fallback_chain", "").strip()
    if chain_raw:
        for m in [m.strip() for m in chain_raw.split(",") if m.strip()]:
            results.append(await _test_text(
                chat_ai_settings.get("api_url", ""),
                chat_ai_settings.get("api_key", ""),
                m, f"主 API · 降級鏈 · {m}"
            ))

    # 3. Backup/fallback model
    results.append(await _test_text(
        chat_ai_settings.get("fallback_api_url", ""),
        chat_ai_settings.get("fallback_api_key", ""),
        chat_ai_settings.get("fallback_model", ""),
        "備援 API · 備援模型"
    ))

    # ── Vision models ──
    # 4. Primary vision model (uses primary API URL/Key with vision_model)
    vision_model = chat_ai_settings.get("vision_model", "")
    results.append(await _test_vision(
        chat_ai_settings.get("api_url", ""),
        chat_ai_settings.get("api_key", ""),
        vision_model,
        "主 API · 視覺模型（識圖）"
    ))

    # 5. Backup vision model (uses backup API URL/Key with fallback_model,
    #    assumed to be multimodal like Gemini)
    fallback_vision_model = (
        chat_ai_settings.get("fallback_vision_model", "")
        or chat_ai_settings.get("fallback_model", "")
    )
    results.append(await _test_vision(
        chat_ai_settings.get("fallback_api_url", ""),
        chat_ai_settings.get("fallback_api_key", ""),
        fallback_vision_model,
        "備援 API · 視覺模型（識圖）"
    ))

    # Summary
    text_ok = sum(1 for r in results if r["type"] == "text" and r["status"] == "ok")
    text_total = sum(1 for r in results if r["type"] == "text")
    vision_ok = sum(1 for r in results if r["type"] == "vision" and r.get("vision_ok"))
    vision_total = sum(1 for r in results if r["type"] == "vision")

    return web.json_response({
        "results": results,
        "summary": {
            "text_ok": text_ok,
            "text_total": text_total,
            "vision_ok": vision_ok,
            "vision_total": vision_total,
            "all_ok": text_ok == text_total and vision_ok == vision_total,
        }
    })



async def api_test_all_functions(request):
    """Global comprehensive test of ALL AI models across categories, fallback chain, backup, and features."""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)

    import time as _time

    def _make_test_flag_png_data_uri() -> str:
        import struct, zlib, base64 as _b64
        width, height = 60, 36
        yellow, red, blue = (255, 204, 0), (204, 0, 0), (0, 51, 204)
        cx, cy, r2 = width / 2, height / 2, 9 * 9
        raw = bytearray()
        for y in range(height):
            raw.append(0)  # PNG filter byte: none
            for x in range(width):
                if y < 3 or y >= height - 3:
                    px = yellow
                elif (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                    px = blue
                else:
                    px = red
                raw += bytes(px)
        compressed = zlib.compress(bytes(raw), 9)

        def _chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        png = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
        png += _chunk(b"IHDR", ihdr)
        png += _chunk(b"IDAT", compressed)
        png += _chunk(b"IEND", b"")
        return "data:image/png;base64," + _b64.b64encode(png).decode()

    test_image_url = _make_test_flag_png_data_uri()

    async def _test_text(api_url, api_key, model, label):
        if not model:
            return {"label": label, "type": "text", "status": "skipped", "model": "", "error": "模型未設定，跳過"}
        if not api_url or not api_key:
            return {"label": label, "type": "text", "status": "error", "model": model, "error": "API URL 或 Key 未設定"}
        result = await _test_ai_text_model(api_url, api_key, model, label, timeout_total=30)
        result["type"] = "text"
        return result

    async def _test_vision(api_url, api_key, vision_model, label):
        if not vision_model:
            return {"label": label, "type": "vision", "status": "skipped", "model": "", "error": "視覺模型未設定，跳過"}
        if not api_url or not api_key:
            return {"label": label, "type": "vision", "status": "error", "model": vision_model, "error": "API URL 或 Key 未設定"}
        url = api_url.strip()
        if not url.endswith("/chat/completions"):
            if url.endswith("/v1") or url.endswith("/v2"):
                url += "/chat/completions"
            else:
                url += "/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "這是一張測試用的圖片。請回答 JSON："
                                '{"has_image": true/false, "description": "簡短描述圖片內容"}'
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": test_image_url},
                        },
                    ],
                }
            ],
            "max_tokens": 200,
            "temperature": 0.1,
            "stream": False,
            # 跟文字模型測試一致：明確關閉 reasoning，避免「預設思考」的
            # 視覺模型把 max_tokens 燒在隱藏思考過程上，回不了 JSON 判讀結果。
            "reasoning_effort": "none",
            "thinking": {"type": "disabled"},
            "enable_thinking": False,
        }
        t0 = _time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60, sock_read=50)) as sess:
                status, data, err_text = await _post_test_payload(sess, url, headers, payload)
                elapsed = int((_time.monotonic() - t0) * 1000)
                if status != 200:
                    return {"label": label, "type": "vision", "status": "error",
                            "http_status": status, "latency_ms": elapsed,
                            "model": vision_model, "error": f"HTTP {status}: {err_text or ''}"}
                content_text = ""
                choices = data.get("choices", [])
                if choices:
                    content_text = (choices[0].get("message", {}).get("content") or "").strip()
                vision_ok = False
                desc = ""
                if not content_text:
                    finish_reason = choices[0].get("finish_reason", "?") if choices else "?"
                    return {"label": label, "type": "vision", "status": "degraded",
                            "latency_ms": elapsed, "model": vision_model,
                            "error": f"連線成功但內容空白（finish_reason={finish_reason}），無法判讀圖片——"
                                     f"此模型可能忽略關閉思考的請求"}
                try:
                    if content_text.startswith("```"):
                        content_text = content_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                    parsed = json_module.loads(content_text)
                    vision_ok = bool(parsed.get("has_image", False))
                    desc = parsed.get("description", "")
                except Exception:
                    if any(kw in content_text.lower() for kw in ["圖片", "image", "紅色", "藍色", "圓形", "flag"]):
                        vision_ok = True
                        desc = content_text[:100]
                return {"label": label, "type": "vision", "status": "ok", "latency_ms": elapsed,
                        "model": vision_model, "response_snippet": (desc or content_text)[:150]}
        except asyncio.TimeoutError:
            elapsed = int((_time.monotonic() - t0) * 1000)
            return {"label": label, "type": "vision", "status": "timeout", "latency_ms": elapsed,
                    "model": vision_model, "error": "請求逾時（60 秒）"}
        except Exception as e:
            elapsed = int((_time.monotonic() - t0) * 1000)
            return {"label": label, "type": "vision", "status": "error", "latency_ms": elapsed,
                    "model": vision_model, "error": str(e)[:300]}

    results = []

    primary_url = chat_ai_settings.get("api_url", "")
    primary_key = chat_ai_settings.get("api_key", "")
    main_model = chat_ai_settings.get("model", "").strip()

    fallback_url = chat_ai_settings.get("fallback_api_url", "")
    fallback_key = chat_ai_settings.get("fallback_api_key", "")

    # 1. 聊天模型 — chat_model or main model, label "💬 聊天"
    chat_m = chat_ai_settings.get("chat_model", "").strip() or main_model
    results.append(await _test_text(primary_url, primary_key, chat_m, "💬 聊天"))

    # 2. 行政模型 — admin_model or main model, label "🛡️ 行政"
    admin_m = chat_ai_settings.get("admin_model", "").strip() or main_model
    results.append(await _test_text(primary_url, primary_key, admin_m, "🛡️ 行政"))

    # 3. 娛樂模型 — entertainment_model or main model, label "🎮 娛樂"
    ent_m = chat_ai_settings.get("entertainment_model", "").strip() or main_model
    results.append(await _test_text(primary_url, primary_key, ent_m, "🎮 娛樂"))

    # 4. 降級鏈 — each model in model_fallback_chain, label "🔗 降級鏈 · {model}"
    chain_raw = chat_ai_settings.get("model_fallback_chain", "").strip()
    if chain_raw:
        for m in [m.strip() for m in chain_raw.split(",") if m.strip()]:
            results.append(await _test_text(primary_url, primary_key, m, f"🔗 降級鏈 · {m}"))

    # 5. 備援模型 — fallback_model on fallback API, label "🔄 備援 API"
    fallback_m = chat_ai_settings.get("fallback_model", "").strip()
    results.append(await _test_text(fallback_url, fallback_key, fallback_m, "🔄 備援 API"))

    # 6. AI 搶答模型 — quiz_model if set, label "🧠 AI 搶答"
    quiz_m = chat_ai_settings.get("quiz_model", "").strip()
    results.append(await _test_text(primary_url, primary_key, quiz_m, "🧠 AI 搶答"))

    # 7. 海龜湯模型 — turtle_soup_model if set, label "🍜 海龜湯"
    turtle_m = chat_ai_settings.get("turtle_soup_model", "").strip()
    results.append(await _test_text(primary_url, primary_key, turtle_m, "🍜 海龜湯"))

    # 8. 狼人殺模型 — werewolf_model if set, label "🐺 狼人殺"
    werewolf_m = chat_ai_settings.get("werewolf_model", "").strip()
    results.append(await _test_text(primary_url, primary_key, werewolf_m, "🐺 狼人殺"))

    # 9. 占卜模型 — fortune_model if set, label "🔮 占卜"
    fortune_m = chat_ai_settings.get("fortune_model", "").strip()
    results.append(await _test_text(primary_url, primary_key, fortune_m, "🔮 占卜"))

    # 10. 視覺模型 — vision_model if set, label "👁️ 視覺識圖" (skip if not set)
    vision_m = chat_ai_settings.get("vision_model", "").strip()
    results.append(await _test_vision(primary_url, primary_key, vision_m, "👁️ 視覺識圖"))

    # 11. 備援視覺模型 — fallback_vision_model or fallback_model if set, label "🔄 備援視覺識圖" (skip if not set)
    fallback_vis_m = (
        chat_ai_settings.get("fallback_vision_model", "").strip()
        or chat_ai_settings.get("fallback_model", "").strip()
    )
    results.append(await _test_vision(fallback_url, fallback_key, fallback_vis_m, "🔄 備援視覺識圖"))

    # 12. 文生圖模型 — t2i_model, label "🎨 文生圖" (skip if not enabled/configured)
    # 直接呼叫 _generate_image()，跟正式生圖共用同一段解析邏輯（JSON / 原始圖片二進位皆可），
    # 避免測試端點跟正式端點各寫一份、日後其中一邊修好另一邊沒同步的問題。
    if chat_ai_settings.get("t2i_enabled"):
        t2i_model = chat_ai_settings.get("t2i_model", "").strip()
        if not t2i_model or not chat_ai_settings.get("t2i_api_url", "").strip():
            results.append({"label": "🎨 文生圖", "type": "image", "status": "skipped", "model": t2i_model, "error": "文生圖 API 未設定完整，跳過"})
        else:
            t2_t0 = _time.monotonic()
            t2_result = await _generate_image("a simple red circle on white background", chat_ai_settings)
            t2_elapsed = int((_time.monotonic() - t2_t0) * 1000)
            if t2_result.get("success"):
                channel = t2_result.get("channel", "?")
                results.append({"label": "🎨 文生圖", "type": "image", "status": "ok",
                                "latency_ms": t2_elapsed, "model": t2i_model,
                                "response_snippet": f"圖片生成成功（通道: {channel}）"})
                # 清掉測試產生的暫存檔
                _t2i_test_path = t2_result.get("image_path")
                if _t2i_test_path:
                    try:
                        os.remove(_t2i_test_path)
                    except Exception:
                        pass
            else:
                results.append({"label": "🎨 文生圖", "type": "image", "status": "error",
                                "latency_ms": t2_elapsed, "model": t2i_model,
                                "error": t2_result.get("error", "未知錯誤")})
    else:
        results.append({"label": "🎨 文生圖", "type": "image", "status": "skipped", "model": "", "error": "文生圖功能未啟用，跳過"})

    # 13. 生圖提示詞過濾 — label "🛡️ 提示詞過濾"
    if chat_ai_settings.get("t2i_filter_enabled"):
        filter_pool_id = chat_ai_settings.get("t2i_filter_pool_id", "").strip()
        filter_model = chat_ai_settings.get("t2i_filter_model", "").strip()
        pool = chat_ai_settings.get("ai_pool", [])
        pool_entry = next((e for e in pool if e.get("id") == filter_pool_id), None)
        if not pool_entry:
            results.append({"label": "🛡️ 提示詞過濾", "type": "filter", "status": "error", "model": filter_model,
                            "error": "已啟用但找不到對應的 API 池，請至 Dashboard 設定"})
        else:
            pool_model = filter_model or pool_entry.get("model", "")
            # 用一個明確安全的 prompt 測試過濾是否正常運作
            f_t0 = _time.monotonic()
            f_result = await _t2i_filter_prompt("a beautiful landscape painting of mountains", chat_ai_settings)
            f_elapsed = int((_time.monotonic() - f_t0) * 1000)
            if f_result.get("allowed"):
                results.append({"label": "🛡️ 提示詞過濾", "type": "filter", "status": "ok",
                                "latency_ms": f_elapsed, "model": pool_model,
                                "response_snippet": "安全提示詞通過審查"})
            else:
                results.append({"label": "🛡️ 提示詞過濾", "type": "filter", "status": "error",
                                "latency_ms": f_elapsed, "model": pool_model,
                                "error": f"安全提示詞竟被攔截：{f_result.get('reason', '?')}"})
    else:
        results.append({"label": "🛡️ 提示詞過濾", "type": "filter", "status": "skipped", "model": "", "error": "提示詞過濾未啟用，跳過"})

    # Summary
    total = len(results)
    ok_cnt = sum(1 for r in results if r.get("status") == "ok")
    err_cnt = sum(1 for r in results if r.get("status") == "error")
    timeout_cnt = sum(1 for r in results if r.get("status") == "timeout")
    skipped_cnt = sum(1 for r in results if r.get("status") == "skipped")

    return web.json_response({
        "results": results,
        "summary": {
            "total": total,
            "ok": ok_cnt,
            "error": err_cnt,
            "timeout": timeout_cnt,
            "skipped": skipped_cnt,
            "all_ok": (err_cnt == 0 and timeout_cnt == 0 and total > 0)
        }
    })

ai_settings = {
    "api_url": os.getenv("AI_API_URL", "https://api.openai.com/v1/chat/completions"),
    "api_key": os.getenv("AI_API_KEY", ""),
    "model": os.getenv("AI_MODEL", "gpt-4o-mini"),
    "system_prompt": os.getenv("AI_SYSTEM_PROMPT", DEFAULT_AI_SYSTEM_PROMPT),
}


def parse_since(since_str: str):
    """Parse a time string and return UTC datetime."""
    since_str = since_str.strip().lower()
    now_utc = datetime.now(GMT8)

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
    # ── Reasoning 模型控制 ──
    _reasoning_effort = settings.get("reasoning_effort") or chat_ai_settings.get("reasoning_admin_effort", "medium")
    _reasoning_params = _build_reasoning_params(_reasoning_effort)
    if _reasoning_params:
        payload.update(_reasoning_params)
    _reasoning_timeout = _get_reasoning_timeout(_reasoning_effort)
    # Auto-append /chat/completions if only base URL is provided
    api_url = settings["api_url"].rstrip("/")
    if not api_url.endswith("/chat/completions"):
        if api_url.endswith("/v1") or api_url.endswith("/v2"):
            api_url += "/chat/completions"
        else:
            api_url += "/v1/chat/completions"
    # Use streaming to avoid long silent waits — collect chunks as they arrive
    timeout = aiohttp.ClientTimeout(total=_reasoning_timeout, connect=15, sock_read=max(10, _reasoning_timeout // 2))
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
    # Estimate and track token usage (streaming APIs rarely return usage)
    content_str = "".join(result_chunks)
    est_completion = max(1, len(content_str) // 4)
    est_prompt = max(1, len(conversation) // 4)
    _track_token_usage({
        "usage": {
            "total_tokens": est_prompt + est_completion,
            "prompt_tokens": est_prompt,
            "completion_tokens": est_completion,
        }
    })
    return content_str


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
    # ── Reasoning 模型控制 ──
    _reasoning_effort = settings.get("reasoning_effort") or chat_ai_settings.get("reasoning_chat_effort", "low")
    _reasoning_params = _build_reasoning_params(_reasoning_effort)
    if _reasoning_params:
        payload.update(_reasoning_params)
    _reasoning_timeout = _get_reasoning_timeout(_reasoning_effort)
    # Auto-append /chat/completions if only base URL is provided
    api_url = settings["api_url"].rstrip("/")
    if not api_url.endswith("/chat/completions"):
        if api_url.endswith("/v1"):
            api_url += "/chat/completions"
        elif api_url.endswith("/v2"):
            api_url += "/chat/completions"
        else:
            api_url += "/v1/chat/completions"
    timeout = aiohttp.ClientTimeout(total=_reasoning_timeout, connect=15, sock_read=max(15, _reasoning_timeout // 2))
    async with aiohttp.ClientSession() as session:
        async with session.post(api_url, json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(f"AI API returned {resp.status}: {error_text[:500]}")
            _stream_chars = 0
            _stream_usage = None
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json_module.loads(data_str)
                    if chunk.get("usage"):
                        _stream_usage = chunk["usage"]
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        _stream_chars += len(delta["content"])
                        yield delta["content"]
                except Exception:
                    continue
            # Track token usage after stream ends
            if _stream_usage:
                _track_token_usage({"usage": _stream_usage})
            elif _stream_chars > 0:
                est_completion = max(1, _stream_chars // 4)
                est_prompt = max(1, len(conversation) // 4)
                _track_token_usage({
                    "usage": {
                        "total_tokens": est_prompt + est_completion,
                        "prompt_tokens": est_prompt,
                        "completion_tokens": est_completion,
                    }
                })


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
        for gid, polls in list(guild_polls.items()):
            serializable[str(gid)] = {}
            for pid, poll in list(polls.items()):
                serializable[str(gid)][pid] = {
                    "poll_id": poll.poll_id,
                    "title": poll.title,
                    "mode": poll.mode,
                    "status": poll.status,
                    "options": [{"text": o.text} for o in poll.options],
                    "votes": {str(k): v for k, v in list(poll.votes.items())},
                    "message_id": poll.message_id,
                    "created_by": poll.created_by,
                    "allowed_roles": poll.allowed_roles,
                    "description": getattr(poll, "description", ""),
                }
        _save_json_file(DATA_FILE, serializable, indent=None)
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


@bot.event
async def on_ready():
    global _chat_semaphore, _shared_session, _refine_write_lock
    if _chat_semaphore is None:
        _chat_semaphore = asyncio.Semaphore(5)
    if _refine_write_lock is None:
        _refine_write_lock = asyncio.Lock()
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

    # 恢復重啟前的遊戲狀態（海龜湯/狼人殺）
    try:
        await asyncio.sleep(3)  # 等待 guild 資料完全載入
        await restore_active_game_states()
    except Exception as e:
        print(f"⚠️ 遊戲狀態恢復失敗：{e}")

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
        # Always probe — the model may have changed since last startup.
        # (Previously skipped if endpoint was in _tools_unsupported_apis, but that meant switching models never re-tested tool support.)
        asyncio.ensure_future(_probe_tools_support(chat_ai_settings, _norm))

    # ── 自動註冊所有伺服器 ──
    for guild in bot.guilds:
        _is_owner = (str(guild.id) == ICEA_GUILD_ID)
        register_server(guild.id, guild.name, is_owner_server=_is_owner)
        _server_registry[str(guild.id)]["member_count"] = guild.member_count or 0
    save_server_registry()
    print(f"📋 伺服器註冊：{len(_server_registry)} 個伺服器（{sum(1 for s in _server_registry.values() if s.get('tier')=='owner')} owner, {sum(1 for s in _server_registry.values() if s.get('tier')=='guest')} guest）")

    # ── 啟動時檢查所有伺服器的暱稱 ──
    for guild in bot.guilds:
        await _check_and_fix_nickname(guild)

    # ── AI 聊天室：失效頻道清理 + 面板自動重發（僅執行一次）──
    # bot.guilds is finally populated here (unlike setup_hook, which runs
    # BEFORE the gateway connects) — this is the correct place for any
    # "does this channel still exist" check.
    global _chat_room_startup_done
    if not _chat_room_startup_done:
        _chat_room_startup_done = True

        # 1) Clean up rooms whose Discord channel was actually deleted.
        _stale_rooms = []
        for ch_id_str, room in list(ai_chat_rooms.get("rooms", {}).items()):
            ch_id = int(ch_id_str)
            found = any(guild.get_channel(ch_id) for guild in bot.guilds)
            if not found:
                _stale_rooms.append(ch_id_str)
                ai_chat_rooms["rooms"].pop(ch_id_str, None)
        if _stale_rooms:
            save_ai_chat_rooms()
            print(f"🧹 AI 聊天室：清理了 {len(_stale_rooms)} 個已失效的頻道")

        # 2) Auto-repost the chat room panel button so it always works after
        # a redeploy, regardless of whether the old message's persistent
        # view state survived the restart cleanly.
        panel_ch_id = ai_chat_rooms.get("panel_channel_id")
        if panel_ch_id:
            panel_channel = None
            for guild in bot.guilds:
                ch = guild.get_channel(int(panel_ch_id))
                if ch:
                    panel_channel = ch
                    break
            if panel_channel:
                try:
                    await _repost_chat_room_panel(panel_channel)
                except Exception as e:
                    print(f"⚠️ 啟動時重發聊天室面板失敗：{e}")
            else:
                print(f"⚠️ 聊天室面板頻道（ID: {panel_ch_id}）已不存在，略過重發")


# ── 暱稱保護系統 ──
# 機器人偵測到自己的暱稱被改成非預期名稱時，自動改回來。
EXPECTED_NICKNAME = "ICEA official"

async def _check_and_fix_nickname(guild):
    """Check bot's nickname in a guild; if it's not the expected name,
    automatically change it back."""
    try:
        me = guild.get_member(bot.user.id)
        if me is None:
            me = await guild.fetch_member(bot.user.id)
        current_nick = me.nick
        # If nick is None, the bot is using its global username.
        # Only fix if a nick is set AND it's wrong.
        if current_nick is not None and current_nick != EXPECTED_NICKNAME:
            print(f"🔧 暱稱被改成了「{current_nick}」，自動改回「{EXPECTED_NICKNAME}」")
            try:
                await me.edit(nick=EXPECTED_NICKNAME)
                print(f"✅ 暱稱已恢復為「{EXPECTED_NICKNAME}」")
            except discord.Forbidden:
                print(f"❌ 無法修改暱稱：缺少權限（需要在 #{guild.name} 有「變更暱稱」權限）")
            except Exception as e:
                print(f"❌ 修改暱稱失敗：{e}")
    except Exception as e:
        print(f"⚠️ 檢查暱稱失敗（{guild.name}）：{e}")


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    """Triggered when a guild is updated. Not directly useful for nickname
    changes, but kept for completeness."""
    pass


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Triggered when a member is updated. If the bot's own nickname changed,
    automatically revert it."""
    if after.id == bot.user.id:
        # Check if nickname changed
        if before.nick != after.nick:
            if after.nick is not None and after.nick != EXPECTED_NICKNAME:
                print(f"🔧 偵測到暱稱被改為「{after.nick}」，正在恢復...")
                try:
                    await after.edit(nick=EXPECTED_NICKNAME)
                    print(f"✅ 暱稱已自動恢復為「{EXPECTED_NICKNAME}」")
                except discord.Forbidden:
                    print(f"❌ 無法恢復暱稱：缺少權限")
                except Exception as e:
                    print(f"❌ 恢復暱稱失敗：{e}")



# >>> 10_werewolf extracted to modules/10_werewolf.py <<<
async def setup_hook():
    # 🔑 CRITICAL: bind the HTTP port FIRST, before any Drive downloads or
    # data loading. Render's port-scanner only waits a limited window after
    # container start to see an open port — if that's delayed by slow Drive
    # I/O or forum indexing, the scanner times out ("Port scan timeout
    # reached, no open ports detected") and the deploy gets stuck/marked
    # unhealthy even though the bot is actually running fine. Opening the
    # port here, before anything else, means Render sees it within
    # milliseconds of boot — everything else below still runs exactly the
    # same, just after the port is already live.
    await keep_alive_server()

    # Register slash command groups (runs once, before bot connects)
    _hoi4_on = os.getenv("HOI4_ENABLED", "true").lower() not in ("false", "0", "no", "off")
    _all_groups = [PollGroup(), MeetingGroup(), BriefingGroup(), ChatGroup(), ChatRoomGroup(), SystemGroup(), QuizGroup(), NationGroup(), AnalyzeGroup(), MemberNationGroup(), AwarenessGroup(), ScheduleGroup(), TallyGroup(), TurtleSoupGroup(), WerewolfGroup(), EconomyGroup(), StockGroup(), HorseRacingGroup(), SiegeGroup(), ProposalGroup(), CyberWarGroup(), GalgameGroup(), MinerGroup()]
    if _hoi4_on:
        _all_groups.append(StormGroup())
    for grp in _all_groups:
        try:
            bot.tree.add_command(grp)
        except Exception as e:
            print(f"⚠️ 無法註冊指令群組 {type(grp).__name__}: {e}")

    # Register message context-menu commands (right-click a message → Apps)
    for ctx_cmd in [ai_organize_agenda]:
        try:
            bot.tree.add_command(ctx_cmd)
        except Exception as e:
            print(f"⚠️ 無法註冊右鍵選單指令 {getattr(ctx_cmd, 'name', '?')}: {e}")

    # Global interaction check: block blacklisted users from ALL commands
    async def _tree_interaction_check(interaction: discord.Interaction) -> bool:
        # Block blacklisted users
        if interaction.user and is_blacklisted(interaction.user.id):
            try:
                await interaction.response.send_message(
                    "🚫 你已被列入黑名單，無法使用此機器人的任何功能。",
                    ephemeral=True,
                )
            except Exception as e:
                print(f"⚠️ 黑名單通知發送失敗: {e}")
            print(f"🚫 黑名單用戶 {interaction.user.display_name} ({interaction.user.id}) 嘗試使用指令已攔截")
            return False
        # Guild-only: most commands require a server context
        # Allow DM usage only for the bot owner
        if not interaction.guild and interaction.user.id != BOT_OWNER_ID:
            try:
                await interaction.response.send_message(
                    "❌ 此指令只能在伺服器中使用，無法在私訊中使用。",
                    ephemeral=True,
                )
            except Exception as e:
                print("⚠️ 靜默例外:", e)
            return False

        # ── 伺服器分級功能檢查 ──
        # Owner 的 DM / 所有指令一律放行
        if interaction.user.id == BOT_OWNER_ID:
            return True
        # 非主伺服器：檢查指令群組是否允許
        if interaction.guild and str(interaction.guild.id) != ICEA_GUILD_ID:
            _cmd = interaction.command
            _group_name = ""
            if _cmd and hasattr(_cmd, 'parent') and _cmd.parent:
                _group_name = _cmd.parent.name or ""
            elif _cmd and hasattr(_cmd, 'name'):
                _group_name = _cmd.name or ""
            if _group_name:
                _allowed, _reason = check_command_access(interaction.guild.id, _group_name)
                if not _allowed:
                    try:
                        await interaction.response.send_message(
                            f"🔒 {_reason}",
                            ephemeral=True,
                        )
                    except Exception:
                        pass
                    return False
        return True

    bot.tree.interaction_check = _tree_interaction_check
    # Load from Google Drive first (if configured), then from local
    await load_from_drive()
    load_server_registry()
    load_knowledge_base()
    load_corrections()
    load_blacklist()
    load_feedback()
    load_proposal_settings()
    load_application_settings()
    save_application_settings()  # Create file if not exists (ensures Drive sync)
    load_applications()
    save_applications()  # Create file if not exists (ensures Drive sync)
    load_proposals()
    load_schedule_settings()
    save_schedule_settings()  # Create file if not exists (ensures Drive sync)
    # Load local files (will use Drive-downloaded data if available)
    load_polls_from_disk()
    save_polls_to_disk()  # Create file if not exists
    load_briefing_settings()
    load_member_nations()
    save_briefing_settings()  # Create file if not exists
    load_chat_ai_settings()
    # Only save (and potentially overwrite Drive) if we actually loaded real data.
    # If Drive download failed AND no local file exists, saving would write
    # defaults (log_channel_id=None, api_key="", etc.) which then get uploaded
    # to Drive on next sync — permanently destroying the real settings.
    if _drive_load_succeeded or os.path.exists(CHAT_AI_DATA_FILE):
        save_chat_ai_settings()
    else:
        print("⚠️ Drive 載入失敗且無本地檔，跳過 save_chat_ai_settings 以防覆蓋 Drive 上的正確設定")
    load_quiz_data()
    load_economy()
    load_stock_market()
    load_horse_racing()
    load_graceful_restart()
    load_siege_data()
    load_cyber_war()
    load_galgame()  # 必須在 load_from_drive() 之後重新載入（模組 exec 時的 load_galgame 用的是舊本地檔）
    load_miner()  # 礦工遊戲：同樣必須在 load_from_drive() 之後重新載入
    load_refine_settings()
    load_refine_knowledge()
    save_quiz_data()  # Create files if not exists
    load_token_usage()
    save_token_usage()  # Create file if not exists
    load_user_memories()
    load_ai_chat_rooms()
    load_emoji_aliases()
    # NOTE: stale-AI-chat-room cleanup moved to on_ready() — bot.guilds is
    # still EMPTY here in setup_hook (runs before the gateway connects), so
    # running the "does this channel still exist" check here would wrongly
    # flag every single room as stale on every restart. See on_ready().
    load_tools_unsupported()
    load_tools_supported()
    load_reasoning_unsupported()
    asyncio.ensure_future(self_ping_loop())
    asyncio.ensure_future(auto_save_loop())
    asyncio.ensure_future(daily_briefing_scheduler())
    asyncio.ensure_future(weekly_briefing_scheduler())
    asyncio.ensure_future(drive_sync_loop())
    asyncio.ensure_future(werewolf_loop())  # 狼人殺面板管理
    asyncio.ensure_future(server_context_refresh_loop())
    asyncio.ensure_future(forum_index_refresh_loop())
    asyncio.ensure_future(channel_index_refresh_loop())
    asyncio.ensure_future(daily_summary_loop())
    asyncio.ensure_future(quiz_question_loop())
    asyncio.ensure_future(siege_loop())
    asyncio.ensure_future(cyber_war_loop())
    asyncio.ensure_future(turtle_soup_loop())
    asyncio.ensure_future(quiz_settlement_loop())
    asyncio.ensure_future(ai_refine_loop())
    _load_turtle_soup()
    asyncio.ensure_future(community_awareness_loop())
    asyncio.ensure_future(community_chronicle_loop())
    asyncio.ensure_future(token_log_loop())
    asyncio.ensure_future(economy_panel_loop())  # 經濟系統看板
    asyncio.ensure_future(galgame_panel_loop())  # 互動小說看板
    asyncio.ensure_future(miner_loop())  # 琉璃幣礦工看板（僅主機器人，暫不推子機器人）
    if os.getenv("HOI4_ENABLED", "true").lower() not in ("false", "0", "no", "off"):
        asyncio.ensure_future(hoi4_panel_loop())  # 鋼鐵風暴 戰略遊戲面板（可由 HOI4_ENABLED=false 關閉）
    else:
        print("🚫 HOI4 已停用，面板迴圈不啟動")
    asyncio.ensure_future(stock_market_loop())  # AI 股票市場：每2小時一回合
    asyncio.ensure_future(horse_racing_loop())  # 賽馬賭博系統：每30分鐘一局
    # Load community awareness + chronicle data
    _load_community_awareness()
    _load_awareness_settings()
    _load_community_chronicle()
    _load_global_scan_result()
    # Auto-detect guild for awareness if not set
    if not _community_awareness_settings.get("guild_id") and bot.guilds:
        _community_awareness_settings["guild_id"] = str(bot.guilds[0].id)
        _save_awareness_settings()


@bot.event
async def on_thread_create(thread):
    """Detect new forum threads in proposal/application channels and auto-process."""
    parent_id = thread.parent_id if hasattr(thread, 'parent_id') else None

    # ── 提案區偵測 ──
    if proposal_settings.get("enabled"):
        proposal_channels = proposal_settings.get("proposal_channels", [])
        if parent_id and parent_id in proposal_channels:
            try:
                await asyncio.sleep(2)
                starter = await thread.fetch_message(thread.id) if hasattr(thread, 'id') else None
                if starter:
                    if starter.author.bot:
                        return
                    await _process_new_proposal(starter, thread.parent)
                    print(f"📋 論壇貼文提案已處理：#{thread.name}")
            except Exception as e:
                print(f"⚠️ 論壇貼文提案處理失敗：{e}")

    # ── 入盟申請區偵測（秘書處 + 理事國）──
    if application_settings.get("enabled"):
        sec_channels = application_settings.get("application_channels", [])
        council_channels = application_settings.get("council_channels", [])
        if parent_id and parent_id in sec_channels:
            try:
                await asyncio.sleep(2)
                starter = await thread.fetch_message(thread.id) if hasattr(thread, 'id') else None
                if starter:
                    if starter.author.bot:
                        return
                    await _process_new_application(starter, thread.parent, system_type="secretariat")
                    print(f"📝 論壇貼文入盟申請已處理（秘書處）：#{thread.name}")
            except Exception as e:
                print(f"⚠️ 論壇貼文入盟申請處理失敗：{e}")
        elif parent_id and parent_id in council_channels:
            try:
                await asyncio.sleep(2)
                starter = await thread.fetch_message(thread.id) if hasattr(thread, 'id') else None
                if starter:
                    if starter.author.bot:
                        return
                    await _process_new_application(starter, thread.parent, system_type="council")
                    print(f"📝 論壇貼文入盟申請已處理（理事國）：#{thread.name}")
            except Exception as e:
                print(f"⚠️ 論壇貼文入盟申請處理失敗（理事國）：{e}")


@bot.event
@bot.event
async def on_guild_join(guild):
    """機器人被加入新伺服器時自動註冊。"""
    _is_owner = (str(guild.id) == ICEA_GUILD_ID)
    register_server(guild.id, guild.name, is_owner_server=_is_owner)
    if str(guild.id) in _server_registry:
        _server_registry[str(guild.id)]["member_count"] = guild.member_count or 0
    save_server_registry()
    print(f"📋 機器人已加入伺服器：{guild.name} ({guild.id}) — tier={'owner' if _is_owner else 'guest'}")

    # 在系統頻道發歡迎訊息
    try:
        sys_ch = guild.system_channel
        if sys_ch:
            embed = discord.Embed(
                title="🌍 歡迎安裝 ICEA 機器人！",
                description=(
                    "我是 ICEA 的多功能機器人，提供豐富的娛樂與實用功能。\n\n"
                    "**🎮 娛樂功能（可用！）：**\n"
                    "• `/quiz` — AI 搶答 • `/soup` — AI 海龜湯\n"
                    "• `/ww` — AI 狼人殺 • `/vn` — AI 互動小說\n"
                    "• `/draw` — AI 文生圖 • `/siege` — 攻城戰\n"
                    "• `/stock` — AI 股市 • `/horse` — 賽馬\n"
                    "• `/cyber_war` — ⚔️ 跨伺服器 WW1 千人大戰場！\n\n"
                    "**📋 基本功能（可用！）：**\n"
                    "• `/poll` — 波達計數法投票\n"
                    "• `/meeting` — 會議管理\n"
                    "• `/schedule` — 排程提醒\n"
                    "• `/economy` — 經濟系統\n\n"
                    "**🔒 僅限 ICEA 主伺服器：**\n"
                    "AI 聊天、提案/入盟分析、晨報、計票等功能。"
                ),
                color=discord.Color.blue(),
            )
            await sys_ch.send(embed=embed)
    except Exception as e:
        print(f"⚠️ 歡迎訊息發送失敗：{e}")


@bot.event
async def on_guild_remove(guild):
    """機器人被移除伺服器時標記離開。"""
    unregister_server(guild.id)


async def on_message_edit(before: discord.Message, after: discord.Message):
    """Detect edits to application posts and re-check them."""
    # Only care about non-bot messages in application channels
    if after.author.bot:
        return
    if not application_settings.get("enabled") or not after.guild:
        return
    sec_channels = application_settings.get("application_channels", [])
    council_channels = application_settings.get("council_channels", [])
    ch_id = after.channel.id
    parent_id = getattr(after.channel, 'parent_id', None)
    # Determine which system
    system_type = None
    if ch_id in sec_channels or (parent_id and parent_id in sec_channels):
        system_type = "secretariat"
    elif ch_id in council_channels or (parent_id and parent_id in council_channels):
        system_type = "council"
    if not system_type:
        return

    # Check if we already have an entry for this message
    msg_id = str(after.id)
    existing = [a for a in _applications.get("entries", []) if a.get("message_id") == msg_id]
    if existing:
        entry = existing[0]
        # Only re-check if not yet sent to reviewer, or if it was sent but
        # we want to re-validate (status is still pending)
        if entry.get("status") in ("accepted", "rejected"):
            return  # Already reviewed, don't re-process

    print(f"📝 偵測到入盟申請編輯：msg {msg_id} by {after.author.display_name}")

    try:
        # Determine the channel object (parent for forum threads)
        ch = after.channel
        if isinstance(ch, discord.Thread) and parent_id and (parent_id in sec_channels or parent_id in council_channels):
            ch = ch.parent
        await _process_new_application(after, ch, is_edit=True, system_type=system_type)
    except Exception as e:
        print(f"⚠️ 入盟申請編輯處理錯誤：{e}")


# ── AI 網警：狀態追蹤 ──
_ai_mod_last_report: dict = {}  # user_id -> timestamp of last report
_ai_mod_semaphore = asyncio.Semaphore(3)  # max 3 concurrent moderation checks


async def _ai_moderate_message(message):
    """AI 網警：用輕量級 AI 檢查每一則訊息是否違規。
    非阻塞式 — fire-and-forget，不影響正常訊息流程。
    偵測到疑似違規就通報到指定頻道。"""
    settings = chat_ai_settings

    # 基本條件檢查
    if not settings.get("ai_mod_enabled"):
        return
    if not settings.get("ai_mod_report_channel"):
        return
    if not message.guild:
        return
    if message.author.bot:
        return

    # 取得 API 設定（優先用池解析的 ai_mod 角色）
    api_url, api_key, model = _resolve_role_endpoint("ai_mod", settings)
    if not api_url:
        api_url = settings.get("ai_mod_api_url") or settings.get("api_url", "")
        api_key = settings.get("ai_mod_api_key") or settings.get("api_key", "")
        model = settings.get("ai_mod_model") or settings.get("model", "gpt-4o-mini")
    if not api_url or not api_key:
        return

    # 豁免角色檢查
    exempt_roles = settings.get("ai_mod_exempt_roles", [])
    if exempt_roles and message.author.roles:
        for role in message.author.roles:
            if str(role.id) in [str(r) for r in exempt_roles]:
                return
        # 也豁免伺服器管理員
        if message.author.guild_permissions.manage_guild or message.author.guild_permissions.administrator:
            return

    # 冷卻檢查（同一使用者）
    uid = str(message.author.id)
    now = _time.time()
    cooldown = settings.get("ai_mod_cooldown", 30)
    last = _ai_mod_last_report.get(uid, 0)
    if now - last < cooldown:
        return

    # 跳過太短的訊息（省 API 用量）
    content = (message.content or "").strip()
    if len(content) < 3:
        return

    # 靈敏度設定
    confidence = settings.get("ai_mod_confidence", "medium")
    threshold_map = {"low": "high", "medium": "medium", "high": "low"}
    threshold_desc = threshold_map.get(confidence, "medium")

    # 建構審查 system prompt
    mod_prompt = (
        "你是一個 Discord 伺服器的 AI 網警系統，負責自動審查每則訊息是否違規。"
        "你的判斷依據是以下社群準則和伺服器自訂規則。\n\n"
        "【社群準則】\n"
        "1. 禁止人身攻擊、侮辱、歧視性言論（針對種族、性別、宗教、性傾向等）\n"
        "2. 禁止騷擾、恐嚇、跟蹤行為\n"
        "3. 禁止散布仇恨言論或煽動對立\n"
        "4. 禁止垃圾訊息、廣告宣傳（除非在指定頻道）\n"
        "5. 禁止 NSFW 內容（色情、暴力、血腥）\n"
        "6. 禁止冒充他人身份\n"
        "7. 禁止惡意刷屏或干擾正常討論\n"
    )

    custom_rules = settings.get("ai_mod_custom_rules", "").strip()
    if custom_rules:
        mod_prompt += "\n【伺服器自訂規則】\n" + custom_rules + "\n"

    # 嚴重違規規則（可能導致伺服器被檢舉的言論）
    severe_enabled = settings.get("ai_mod_severe_enabled", False)
    DEFAULT_SEVERE_RULES = (
        "【嚴重違規判定標準】（以下行為判定為 critical，可直接刪除訊息）\n"
        "S1. 公然支持或宣揚種族歧視、種族優越論（如宣稱某種族天生低劣或優越）\n"
        "S2. 支持或宣揚極端主義（法西斯、納粹、白人至上等意識形態）\n"
        "S3. 煽動針對特定族群的暴力或仇恨行為\n"
        "S4. 散布恐怖主義內容或為恐怖攻擊辯護\n"
        "S5. 鼓勵或指導自殘、自殺\n"
        "S6. 兒少性剝削內容（含暗示性）\n"
        "S7. 非法活動的明確教唆或組織（如毒品交易、武器買賣）\n"
        "S8. 針對個人的嚴重死亡威脅或具體恐嚇\n"
    )
    if severe_enabled:
        severe_rules = settings.get("ai_mod_severe_rules", "").strip()
        mod_prompt += "\n" + (severe_rules if severe_rules else DEFAULT_SEVERE_RULES)

    _nl = "\n"
    mod_prompt += (
        _nl + "【審查規則】" + _nl
        + "判斷標準：" + threshold_desc + "（low=只通報嚴重違規，medium=中等，high=敏感）" + _nl
        + "如果訊息是正常聊天、玩笑、討論，即使語氣不太好也不算違規。" + _nl
        + "只有真正違反上述規則的訊息才需要通報。" + _nl + _nl
        + "嚴重度分級：" + _nl
        + "- low: 輕微違規（如不當玩笑、邊界灰色言論）" + _nl
        + "- medium: 中等違規（如人身攻擊、騷擾）" + _nl
        + "- high: 嚴重違規（如仇恨言論、歧視）" + _nl
        + "- critical: 可能導致伺服器被 Discord 官方檢舉/封鎖的言論（種族歧視宣揚、極端主義、恐怖主義等）" + _nl + _nl
        + "請以 JSON 格式回覆，格式如下：" + _nl
        + '{"violation": true/false, "severity": "low/medium/high/critical", '
        + '"rule": "違反的規則簡述", "reason": "判斷理由"}' + _nl
        + '如果沒有違規，回覆 {"violation": false}。' + _nl
        + "只回覆 JSON，不要加其他文字。"
    )

    # 建構 API 請求
    user_content = "[頻道: #" + message.channel.name + "] [使用者: " + message.author.display_name + "]\n" + content[:500]

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = api_url.strip()
    if not url.endswith("/chat/completions"):
        if url.endswith("/v1"):
            url += "/chat/completions"
        else:
            url += "/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": mod_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": settings.get("ai_mod_max_tokens", 150),
        "stream": False,
        "temperature": 0.1,
    }

    timeout_sec = settings.get("ai_mod_timeout", 10)

    try:
        async with _ai_mod_semaphore:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_sec)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
    except asyncio.TimeoutError:
        return
    except Exception as e:
        print(f"⚠️ AI 網警請求失敗：{e}")
        return

    # 解析 AI 回覆
    try:
        reply_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not reply_text:
            return

        # 嘗試解析 JSON
        import json as _json
        # 去掉可能的 markdown code block 標記
        reply_text = reply_text.replace("```json", "").replace("```", "").strip()
        result = _json.loads(reply_text)

        if not result.get("violation", False):
            return

        severity = result.get("severity", "low")
        rule = result.get("rule", "未知")
        reason = result.get("reason", "未提供")

    except (_json.JSONDecodeError, IndexError, KeyError) as e:
        print(f"⚠️ AI 網警回覆解析失敗：{e} | 原始回覆：{reply_text[:200]}")
        return

    # 偵測到違規，通報到指定頻道
    _ai_mod_last_report[uid] = now

    severity_colors = {
        "low": discord.Color.yellow(),
        "medium": discord.Color.orange(),
        "high": discord.Color.red(),
        "critical": discord.Color.dark_red(),
    }
    severity_emojis = {"low": "⚠️", "medium": "🟠", "high": "🔴", "critical": "🚫"}

    embed = discord.Embed(
        title=f"{severity_emojis.get(severity, '⚠️')} AI 網警偵測到疑似違規",
        color=severity_colors.get(severity, discord.Color.yellow()),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="👤 使用者",
        value=f"{message.author.display_name} ({message.author.mention})\nID: `{message.author.id}`",
        inline=False,
    )
    embed.add_field(
        name="📍 頻道",
        value=f"#{message.channel.name} ({message.channel.mention})",
        inline=False,
    )

    # 嚴重違規：嘗試刪除訊息並在原頻道警告
    is_critical = (severity == "critical" and severe_enabled)
    message_deleted = False

    if is_critical:
        embed.add_field(
            name="💬 原始訊息內容（已刪除）",
            value=f"> {content[:500]}{'...' if len(content) > 500 else ''}",
            inline=False,
        )
        embed.add_field(name="🚫 處置", value="訊息已自動刪除 + 原頻道警告", inline=True)
    else:
        embed.add_field(
            name="💬 訊息內容",
            value=f"> {content[:500]}{'...' if len(content) > 500 else ''}",
            inline=False,
        )

    embed.add_field(name="📋 違反規則", value=rule, inline=True)
    embed.add_field(name="⚠️ 嚴重度", value=severity, inline=True)
    embed.add_field(name="🔍 判斷理由", value=reason[:500], inline=False)

    # 訊息連結（嚴重違規訊息已刪除，連結可能失效）
    if not is_critical:
        try:
            msg_url = message.jump_url
            embed.add_field(name="🔗 原始訊息", value=f"[點擊查看]({msg_url})", inline=False)
        except Exception:
            pass

    embed.set_footer(text=f"AI 網警 | 模型: {model} | 靈敏度: {confidence} | 自動偵測，非最終判定")

    # 嚴重違規處置：刪除訊息 + 原頻道警告
    if is_critical:
        # 刪除訊息
        try:
            await message.delete()
            message_deleted = True
            print(f"🚫 AI 網警：已刪除 {message.author.display_name} 在 #{message.channel.name} 的嚴重違規訊息")
        except discord.Forbidden:
            print(f"⚠️ AI 網警：無權限刪除訊息（缺少 Manage Messages 權限）")
        except discord.NotFound:
            print(f"⚠️ AI 網警：訊息已被刪除或不存在")
        except Exception as e:
            print(f"⚠️ AI 網警：刪除訊息失敗：{e}")

        # 在原頻道發送警告
        try:
            warn_embed = discord.Embed(
                title="🚫 嚴重違規警告",
                description=(
                    f"{message.author.mention} 你的訊息因涉嫌嚴重違規已被 AI 網警自動刪除。\n\n"
                    f"**違反規則：** {rule}\n"
                    f"**嚴重度：** critical\n"
                    f"**判斷理由：** {reason[:300]}\n\n"
                    f"此為自動偵測，非最終判定。如認為誤判，請聯繫管理員。"
                ),
                color=discord.Color.dark_red(),
                timestamp=discord.utils.utcnow(),
            )
            warn_embed.set_footer(text="AI 網警自動偵測 | 管理員可至網警記錄頻道查看詳情")
            await message.channel.send(embed=warn_embed)
            print(f"🚫 AI 網警：已在 #{message.channel.name} 發送嚴重違規警告")
        except discord.Forbidden:
            print(f"⚠️ AI 網警：無權限在原頻道發送警告")
        except Exception as e:
            print(f"⚠️ AI 網警：發送警告失敗：{e}")

    # 發送通報到網警記錄頻道
    try:
        report_ch_id = settings.get("ai_mod_report_channel")
        report_ch = message.guild.get_channel(int(report_ch_id))
        if report_ch is None:
            # 嘗試跨伺服器搜尋
            for g in bot.guilds:
                ch = g.get_channel(int(report_ch_id))
                if ch:
                    report_ch = ch
                    break
        if report_ch:
            await report_ch.send(embed=embed)
            print(f"🚨 AI 網警通報：{message.author.display_name} 在 #{message.channel.name} | {rule} | {severity}{' [已刪除]' if message_deleted else ''}")
        else:
            print(f"⚠️ AI 網警：找不到通報頻道 ID={report_ch_id}")
    except discord.Forbidden:
        print(f"⚠️ AI 網警：無權限在通報頻道發送訊息")
    except Exception as e:
        print(f"⚠️ AI 網警通報發送失敗：{e}")


@bot.event
async def on_message(message):
    global _last_global_reply

    # ── AI 海龜湯頻道偵測（最先檢查，攔截頻道內所有訊息）──
    try:
        handled = await _handle_turtle_soup_message(message)
        if handled:
            return  # 訊息已被海龜湯消化，不繼續後續處理
    except Exception as e:
        print(f"⚠️ Turtle soup on_message error: {e}")

    # ── 提案區偵測（在所有其他檢查之前）──
    # If this message is in a proposal channel, trigger auto-analysis
    # regardless of chat AI settings. This runs BEFORE the bot-message
    # check so forum thread starter messages (which are from the author)
    # are also caught here.
    if proposal_settings.get("enabled") and message.guild and str(message.guild.id) == ICEA_GUILD_ID:
        proposal_channels = proposal_settings.get("proposal_channels", [])
        ch_id = message.channel.id
        parent_id = getattr(message.channel, 'parent_id', None)
        if ch_id in proposal_channels or (parent_id and parent_id in proposal_channels):
            if not message.author.bot:
                try:
                    await _process_new_proposal(message, message.channel)
                except Exception as e:
                    print(f"⚠️ 提案處理錯誤：{e}")

    # ── 入盟申請區偵測（秘書處 + 理事國 分開）──
    if application_settings.get("enabled") and message.guild and str(message.guild.id) == ICEA_GUILD_ID:
        sec_channels = application_settings.get("application_channels", [])
        council_channels = application_settings.get("council_channels", [])
        ch_id = message.channel.id
        parent_id = getattr(message.channel, 'parent_id', None)

        # Determine which system this channel belongs to
        system_type = None
        if ch_id in sec_channels or (parent_id and parent_id in sec_channels):
            system_type = "secretariat"
        elif ch_id in council_channels or (parent_id and parent_id in council_channels):
            system_type = "council"

        if system_type and not message.author.bot:
            # Check for pending flag uploads first
            if message.attachments:
                _now = _time.time()
                expired_keys = [k for k, v in _pending_flag_uploads.items() if v.get("expires", 0) < _now]
                for k in expired_keys:
                    _pending_flag_uploads.pop(k, None)
                for app_id, info in list(_pending_flag_uploads.items()):
                    if info.get("user_id") != str(message.author.id):
                        continue
                    entry_ch = info.get("channel_id")
                    entry_thread = info.get("thread_id")
                    if str(entry_ch) == str(ch_id) or (entry_thread and str(entry_thread) == str(message.channel.id)):
                        image_url = str(message.attachments[0].url)
                        print(f"🚩 收到國旗圖片上傳：app {app_id} by {message.author.display_name}")
                        _pending_flag_uploads.pop(app_id, None)

                        entry = None
                        for a in _applications.get("entries", []):
                            if a.get("id") == app_id:
                                entry = a
                                break
                        if entry and entry.get("status") not in ("accepted", "rejected"):
                            flag_valid = await _verify_flag_image(image_url)
                            if flag_valid:
                                entry["flag_status"] = "ok"
                                entry["flag_valid"] = True
                                entry["flag_image_url"] = image_url
                                entry.setdefault("field_status", {})["國旗"] = True
                                entry["missing_fields"] = [f for f in entry.get("missing_fields", []) if "國旗" not in f]
                                save_applications()

                                remaining_missing = entry.get("missing_fields", [])
                                if not remaining_missing:
                                    try:
                                        reviewer_name = "理事國" if entry.get("system_type") == "council" else "秘書處"
                                        done_embed = discord.Embed(
                                            title="✅ 國旗已收到",
                                            description=f"國旗圖片已通過視覺 AI 驗證，所有欄位齊全！正在送交{reviewer_name}審核...",
                                            color=discord.Color.green(),
                                        )
                                        await message.reply(embed=done_embed, mention_author=False)
                                    except Exception:
                                        pass

                                    entry["secretariat_notified"] = True
                                    save_applications()

                                    # Notify the correct reviewer based on system_type
                                    notify_target_id = application_settings.get("council_channel") if entry.get("system_type") == "council" else application_settings.get("secretariat_channel")
                                    notify_title = "📝 新入盟申請（理事國審核）" if entry.get("system_type") == "council" else "📝 新入盟申請"
                                    notify_footer = "請理事國點擊下方按鈕審核通過或退回此申請" if entry.get("system_type") == "council" else "請管理員點擊下方按鈕審核通過或退回此申請"
                                    notify_color = discord.Color.dark_gold() if entry.get("system_type") == "council" else discord.Color.gold()

                                    if notify_target_id:
                                        notify_ch = None
                                        for guild in bot.guilds:
                                            ch = guild.get_channel(int(notify_target_id))
                                            if ch:
                                                notify_ch = ch
                                                break
                                        if notify_ch:
                                            notify_embed = discord.Embed(
                                                title=notify_title,
                                                color=notify_color,
                                                timestamp=discord.utils.utcnow(),
                                            )
                                            notify_embed.add_field(name="申請人", value=entry.get("applicant_name", "?"), inline=True)
                                            notify_embed.add_field(name="申請頻道", value=f"#{entry.get('channel_name', '?')}", inline=True)
                                            notify_embed.add_field(name="申請時間", value=entry.get("date", "?"), inline=True)
                                            if entry.get("applicant_nation"):
                                                notify_embed.add_field(name="申請國家", value=entry["applicant_nation"], inline=True)
                                            notify_embed.add_field(name="欄位檢查", value="✅ 全部必填欄位齊全（含國旗圖片）", inline=False)
                                            if image_url:
                                                notify_embed.set_thumbnail(url=image_url)
                                            notify_embed.add_field(name="原文連結", value=entry.get("message_url", "(無)"), inline=False)
                                            notify_embed.add_field(name="申請 ID", value=entry["id"], inline=False)
                                            notify_embed.set_footer(text=notify_footer)
                                            try:
                                                await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(entry["id"]))
                                                print(f"✅ 入盟申請通知已發送至{'理事國' if entry.get('system_type') == 'council' else '秘書處'} #{notify_ch.name}")
                                            except Exception as e:
                                                print(f"❌ 通知發送失敗：{e}")
                                else:
                                    try:
                                        still_missing_embed = discord.Embed(
                                            title="⚠️ 國旗已收到，但仍有缺漏",
                                            description=(
                                                f"國旗圖片已通過驗證！\n\n"
                                                f"但以下欄位仍需補齊：\n"
                                                + "\n".join(f"❌ {f}" for f in remaining_missing)
                                                + "\n\n請編輯原貼文補齊上述欄位。"
                                            ),
                                            color=discord.Color.orange(),
                                        )
                                        await message.reply(embed=still_missing_embed, mention_author=False)
                                    except Exception:
                                        pass
                            else:
                                try:
                                    fail_embed = discord.Embed(
                                        title="❌ 國旗驗證未通過",
                                        description="AI 判定此圖片不像旗幟，請重新上傳一張國旗圖片。",
                                        color=discord.Color.red(),
                                    )
                                    await message.reply(embed=fail_embed, mention_author=False)
                                except Exception:
                                    pass
                        return  # Consumed the image, don't process further

            # Only treat this message as an application submission if it's
            # genuinely the post itself — for forum threads that means the
            # thread's own opening/starter message (message.id == thread.id).
            # Any OTHER message posted in the thread (a casual reply like
            # "謝謝" or "之後再想吧", congratulations, follow-up chat, etc.)
            # must NOT be re-checked against the required-fields list — its
            # message_id never matches the stored application entry, so it
            # was previously treated as a brand-new (empty) submission and
            # incorrectly re-triggered the "尚不完整" warning even after the
            # application had already been accepted/rejected.
            # Plain text-channel applications (no thread) keep the old
            # behavior since there's no starter/reply distinction there.
            is_forum_reply = (
                isinstance(message.channel, discord.Thread)
                and message.id != message.channel.id
            )
            if not is_forum_reply:
                try:
                    await _process_new_application(message, message.channel, system_type=system_type)
                except Exception as e:
                    print(f"⚠️ 入盟申請處理錯誤：{e}")

    # Ignore bot messages
    if message.author.bot:
        return

    # ── Blacklist check: block ALL messages from blacklisted users ──
    if is_blacklisted(message.author.id):
        print(f"🚫 黑名單用戶 {message.author.display_name} ({message.author.id}) 訊息已屏蔽")
        return

    # Ignore slash commands (but allow messages starting with "!" which could be normal text)
    if message.content.startswith("/"):
        return

    # ── 伺服器分級：非主伺服器封鎖聊天+行政 AI（防止其他伺服器消耗高頻 Token）──
    # 娛樂功能（搶答、海龜湯、狼人殺、文生圖、股市、賽馬、Galgame、攻城戰、WW1）不受此旗標影響
    _is_guest_server = (message.guild and str(message.guild.id) != ICEA_GUILD_ID
                        and message.author.id != BOT_OWNER_ID)

    # ── AI 網警：非阻塞式自動審查 ──
    # Fire-and-forget — 不等待結果，不影響正常訊息流程。
    # 只有啟用時才建立 task，否則零開銷。
    if chat_ai_settings.get("ai_mod_enabled") and message.guild and not _is_guest_server:
        asyncio.create_task(_ai_moderate_message(message))

    # Debug: log all human messages
    content_preview = message.content[:80].replace("\n", " ") if message.content else "(empty)"
    is_mentioned = bot.user in message.mentions
    print(f"📩 on_message: #{message.channel} | {message.author.display_name}: {content_preview}")
    print(f"   enabled={chat_ai_settings.get('enabled')}, key={'✅' if chat_ai_settings.get('api_key') else '❌'}, mentioned={is_mentioned}, filter={chat_ai_settings.get('filter_strength', 'mention')}")

    # ── 非主伺服器：封鎖聊天 + 行政 AI，但允許娛樂功能 ──
    if _is_guest_server:
        # AI 聊天室 — 不存在於 guest 伺服器，安全攔截
        if is_ai_chat_room(message.channel.id):
            return
        # 被提及時告知可用功能
        if bot.user in message.mentions and not message.content.startswith("/"):
            # 檢查是否為文生圖請求（娛樂功能，允許）
            _t2i_quick = _detect_t2i_keyword(message.content)
            if _t2i_quick:
                pass  # 讓文生圖流程繼續，不攔截
            else:
                try:
                    await message.reply(
                        "🔒 AI 聊天僅限 ICEA 主伺服器使用。\n\n"
                        "**✅ 可用功能：**\n"
                        "🎮 `/quiz` `/soup` `/ww` `/vn` `/siege`\n"
                        "🎨 `/draw` — 文生圖\n"
                        "📈 `/stock` `/horse` — 股市 & 賽馬\n"
                        "⚔️ `/cyber_war` — 跨伺服器 WW1 大戰\n"
                        "📊 `/poll` `/meeting` `/schedule`\n"
                        "💰 `/economy` — 經濟系統",
                        mention_author=False,
                    )
                except Exception:
                    pass
                return

    # ── 專屬 AI 聊天室處理（在所有 AI 聊天過濾之前）──
    # If this message is in an AI chat room channel, bypass ALL the normal
    # filters (mention, cooldown, whitelist, worthiness, abuse detection)
    # and go straight to AI reply with full channel history.
    if not _is_guest_server and is_ai_chat_room(message.channel.id):
        print(f"🤖 AI 聊天室訊息：#{message.channel.name} | {message.author.display_name}")
        # Only the room owner can chat here (others can't see the channel anyway,
        # but double-check in case permissions were misconfigured)
        room_owner = get_ai_chat_room_owner(message.channel.id)
        if room_owner and str(message.author.id) != room_owner:
            print(f"   ⏭️ 非聊天室主人，跳過")
            return

        # Still need AI enabled and API key
        if not chat_ai_settings.get("enabled"):
            try:
                await message.reply("AI 聊天功能目前未開啟。", mention_author=False)
            except Exception:
                pass
            return
        if not chat_ai_settings.get("api_key"):
            try:
                await message.reply("AI API Key 尚未設定，請聯繫管理員。", mention_author=False)
            except Exception:
                pass
            return

        # Skip empty messages (but allow if they have image attachments)
        if not message.content or len(message.content.strip()) == 0:
            if not message.attachments:
                return

        # Check if this user already has a reply being generated
        uid_str = str(message.author.id)
        if uid_str in _user_generating:
            print(f"   ⏭️ Already generating for this user.")
            return

        # Global rate limit still applies (prevents anti-spam kick)
        _global_interval = chat_ai_settings.get("min_response_interval", 0)
        if _global_interval > 0:
            _global_remaining = _global_interval - (_time.time() - _last_global_reply)
            if _global_remaining > 0:
                print(f"   ⏭️ 全域回應間隔：還需 {_global_remaining:.1f}s")
                return

        # Generate reply with full channel history
        _user_generating.add(uid_str)
        try:
            sem = _chat_semaphore or asyncio.Semaphore(5)
            # ── 文生圖 AI 意圖判定（AI 聊天室也支援） ──
            _t2i_prompt = await _detect_t2i_request_ai(message.content, chat_ai_settings)
            if _t2i_prompt:
                _uid = str(message.author.id)
                _allowed, _reason = _check_t2i_rate_limit(_uid, chat_ai_settings)
                if not _allowed:
                    try:
                        await message.reply(_reason, mention_author=False)
                    except Exception:
                        pass
                    _user_generating.discard(_uid)
                    return
                print(f"🎨 聊天室偵測到生圖請求: {_t2i_prompt[:80]}...")
                async with message.channel.typing():
                    _t2i_result = await _generate_image(_t2i_prompt, chat_ai_settings)
                if _t2i_result.get("success"):
                    _record_t2i_usage(_uid)
                    await _send_t2i_result(message, _t2i_prompt, _t2i_result, chat_ai_settings)
                else:
                    await _send_t2i_result(message, _t2i_prompt, _t2i_result, chat_ai_settings, is_command=False)
                _user_generating.discard(_uid)
                return

            async with sem:
                async with message.channel.typing():
                    result = await generate_chat_room_reply(message, chat_ai_settings)
            # Unpack 4-tuple (like generate_chat_reply)
            if len(result) == 4:
                reply, model_info, new_facts, mod_action = result
            else:
                reply, model_info = result
                new_facts = None
                mod_action = None

            # Save user memory if AI extracted facts
            if new_facts:
                try:
                    _update_user_memory(str(message.author.id), message.author.display_name, new_facts)
                    print(f"🧠 已更新 {message.author.display_name} 的記憶：{new_facts}")
                except Exception as mem_err:
                    print(f"⚠️ 記憶更新失敗（不影響回覆）：{mem_err}")

            # Update message count
            room = ai_chat_rooms.get("rooms", {}).get(str(message.channel.id))
            if room:
                room["message_count"] = room.get("message_count", 0) + 1
                save_ai_chat_rooms()

            # Log to AI log channel if configured
            log_cfg = chat_ai_settings.get("log_channel_id")
            if log_cfg:
                try:
                    await _send_chat_log(message, message.content or "(圖片)", reply or "(空回覆)", model_info=model_info)
                except Exception as log_exc:
                    print(f"   ⚠️ _send_chat_log 例外：{log_exc}")
            else:
                print(f"   ⏭️ 跳過對話紀錄：log_channel_id 未設定（值={log_cfg!r}）")

            if reply and reply.strip():
                _last_global_reply = _time.time()
                # Strip raw tool dumps before sending (same safety net as regular chat)
                reply = _strip_raw_tool_dump(reply)
                try:
                    view = CorrectionButtonView(
                        question=message.content or "(圖片)",
                        original_answer=reply[:500],
                        user_id=str(message.author.id),
                        user_name=message.author.display_name,
                        guild_id=message.guild.id if message.guild else 0,
                    )
                    await message.reply(reply[:2000], mention_author=False, view=view)
                    print(f"   ✅ AI 聊天室回覆已發送")
                except discord.Forbidden:
                    print(f"   ❌ 發送失敗：無權限")
                except Exception as send_err:
                    print(f"   ❌ 發送失敗：{send_err}")
                    try:
                        await message.reply(reply[:2000], mention_author=False)
                    except Exception:
                        pass
            else:
                if _ai_circuit_breaker["tripped"]:
                    print(f"🚫 AI 熔斷器開啟中，不發送 fallback")
                else:
                    print(f"   ⚠️ AI 回覆為空")
        except Exception as room_err:
            # Previously uncaught — an exception here meant the user got NO
            # reply at all (not even an error message) and nothing was logged.
            print(f"⚠️ AI 聊天室處理例外：{room_err}")
            try:
                if chat_ai_settings.get("log_channel_id"):
                    await _send_chat_log(message, message.content or "(圖片)", f"❌ 處理例外：{room_err}", model_info=None)
            except Exception as log_exc2:
                print(f"   ⚠️ 例外記錄失敗：{log_exc2}")
            try:
                await message.reply("⚠️ 發生錯誤，請稍後再試。", mention_author=False)
            except Exception:
                pass
        finally:
            _user_generating.discard(uid_str)
        return  # AI chat room message fully handled

    # Check if chat AI is enabled and has API key
    if not chat_ai_settings.get("enabled"):
        print(f"   ⏭️ Chat AI is disabled. Run /chat toggle to enable.")
        return
    if not chat_ai_settings.get("api_key"):
        print(f"   ⏭️ No API key set.")
        return

    # Check if message content is empty — but allow image-only messages
    # (message.content is "" when the user sent ONLY an image with no text).
    if not message.content or len(message.content.strip()) == 0:
        _has_image = any(
            att.content_type and att.content_type.startswith("image/")
            for att in message.attachments
        )
        if not _has_image:
            print(f"   ⚠️ message.content is empty! Message Content Intent may not be enabled in Discord Developer Portal.")
            return
        print(f"   📷 純圖片訊息（無文字），允許通過進入 AI 處理")

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

    # ── 全域最短回應間隔（防止機器人被防炸踢）──
    # 這是硬性限制，不管是不是@提及都適用。即使多人同時@，
    # 機器人也不會在間隔內連續發訊息。
    _global_interval = chat_ai_settings.get("min_response_interval", 0)
    if _global_interval > 0:
        _global_remaining = _global_interval - (_time.time() - _last_global_reply)
        if _global_remaining > 0:
            print(f"   ⏭️ 全域回應間隔：還需 {_global_remaining:.1f}s（設定 {_global_interval}s）")
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
        except Exception as e:
            print(f"⚠️ fetch ref_msg 例外: {e}")

    # Worthiness check — skip the LENGTH/greeting heuristics if the message
    # is image-only (no text but has image attachments), since _is_worth_replying
    # would reject empty content outright. BUT this bypass must still respect
    # filter_strength — a bare image with no @mention must NOT get a reply
    # when the filter is set to "mention" (bug: previously replied to every
    # image regardless of mention, because the bypass ran before the mention
    # check and never looked at filter_strength at all).
    _has_image_att = any(
        att.content_type and att.content_type.startswith("image/")
        for att in message.attachments
    )
    _filter_strength = chat_ai_settings.get("filter_strength", "mention")
    _image_only = _has_image_att and (not message.content or not message.content.strip())

    if _image_only:
        # "mention" strength: only bypass if actually mentioned or replying to the bot.
        # Any other strength: image-only messages are still allowed through
        # (same relaxed behavior as before, just now mention-gated correctly).
        if _filter_strength == "mention" and not is_mentioned and not is_reply_to_bot:
            print(f"   ⏭️ Not worth replying (image-only, not mentioned, filter=mention).")
            return
        worth = True
        clean = "(圖片)"
        print(f"   ✅ Worth replying! (image-only, bypassing length/greeting checks)")
    else:
        worth, clean = _is_worth_replying(
            message.content, is_mentioned, bot.user.id,
            _filter_strength,
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
            # (no alternative send method — just return cleanly)
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
        # ── 文生圖 AI 意圖判定 ──
        # 用 AI 判斷使用者是否想生圖（取代舊的關鍵字正則匹配）
        _t2i_prompt = await _detect_t2i_request_ai(clean or message.content, chat_ai_settings)
        if _t2i_prompt:
            _uid = str(message.author.id)
            _allowed, _reason = _check_t2i_rate_limit(_uid, chat_ai_settings)
            if not _allowed:
                try:
                    await message.reply(_reason, mention_author=False)
                except Exception:
                    pass
                _user_generating.discard(_uid)
                return
            print(f"🎨 偵測到生圖請求: {_t2i_prompt[:80]}...")
            async with message.channel.typing():
                _t2i_result = await _generate_image(_t2i_prompt, chat_ai_settings)
            if _t2i_result.get("success"):
                _record_t2i_usage(_uid)
                await _send_t2i_result(message, _t2i_prompt, _t2i_result, chat_ai_settings)
            else:
                await _send_t2i_result(message, _t2i_prompt, _t2i_result, chat_ai_settings, is_command=False)
            _user_generating.discard(_uid)
            return

        # ── 非主伺服器：封鎖 AI 聊天回覆（防止高頻 Token 消耗）──
        # 娛樂功能（T2I 等）已在上方處理完畢，以下僅為 AI 聊天回覆路徑
        if _is_guest_server:
            _user_generating.discard(uid_str)
            return

        async with sem:
            async with message.channel.typing():
                reply, new_facts, mod_action, model_info = await generate_chat_reply(message, chat_ai_settings)
        # Save user memory if AI extracted facts (regardless of reply success)
        # Wrapped in try/except — a memory save failure must NEVER block the
        # conversation log or the reply itself.
        if new_facts:
            try:
                _update_user_memory(str(message.author.id), message.author.display_name, new_facts)
                print(f"🧠 已更新 {message.author.display_name} 的記憶：{new_facts}")
            except Exception as mem_err:
                print(f"⚠️ 記憶更新失敗（不影響回覆）：{mem_err}")

        # Log conversation to log channel if configured
        log_cfg = chat_ai_settings.get("log_channel_id")
        if log_cfg:
            try:
                await _send_chat_log(message, clean or message.content, reply or "(空回覆)", model_info=model_info)
            except Exception as log_exc:
                print(f"   ⚠️ _send_chat_log 拋出例外（不影響回覆）：{log_exc}")
        else:
            print(f"   ⏭️ 跳過對話紀錄：log_channel_id 未設定（值={log_cfg!r}）")

        if reply and reply.strip():
            chat_cooldowns[(message.channel.id, message.author.id)] = _time.time()
            _last_global_reply = _time.time()
            print(f"   📤 發送回覆（{len(reply[:2000])} chars）到 #{message.channel}...")
            try:
                # Attach "修正建議" button to the reply — only the original
                # question author can use it.
                view = CorrectionButtonView(
                    question=clean or message.content,
                    original_answer=reply[:500],
                    user_id=str(message.author.id),
                    user_name=message.author.display_name,
                    guild_id=message.guild.id if message.guild else 0,
                )
                await message.reply(reply[:2000], mention_author=False, view=view)
                print(f"   ✅ 回覆已發送（含修正按鈕）")
            except discord.Forbidden:
                print(f"   ❌ 發送失敗：Bot 沒有在 #{message.channel} 發送訊息的權限！")
                return
            except Exception as send_err:
                print(f"   ❌ 發送失敗：{send_err}")
                # Fallback: send without the button
                try:
                    await message.reply(reply[:2000], mention_author=False)
                except Exception:
                    return
        else:
            # If the circuit breaker is open, the API is blocked — stay silent
            # rather than spamming "讓我想想" on every message during the cooldown.
            if _ai_circuit_breaker["tripped"]:
                remaining = _ai_circuit_breaker["cooldown_seconds"] - (_time.time() - _ai_circuit_breaker["trip_time"])
                print(f"🚫 AI 熔斷器開啟中（剩餘 {remaining:.0f}s），不發送 fallback 訊息")
            else:
                print(f"⚠️ AI 回覆為空，發送 fallback 訊息")
                try:
                    await message.reply("🤔 這個問題我目前查不到明確資料，你可以換個問法或補充更多細節，我再幫你查一次看看？", mention_author=False)
                except Exception as e:
                    print(f"⚠️ fallback 回覆發送失敗: {e}")
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
            if chat_ai_settings.get("log_channel_id"):
                await _send_chat_log(message, message.content or "(圖片)", "❌ 逾時（API 呼叫時間過長）", model_info=None)
        except Exception as log_exc3:
            print(f"   ⚠️ 逾時記錄失敗：{log_exc3}")
        try:
            await message.reply("⏰ 回覆逾時，請稍後再試。", mention_author=False)
        except Exception as e:
            print(f"⚠️ AI 回覆後處理例外: {e}")
    except Exception as e:
        print(f"⚠️ Chat AI error: {e}")
        try:
            if chat_ai_settings.get("log_channel_id"):
                await _send_chat_log(message, message.content or "(圖片)", f"❌ 處理例外：{e}", model_info=None)
        except Exception as log_exc4:
            print(f"   ⚠️ 例外記錄失敗：{log_exc4}")
        # If the circuit breaker is tripped, don't spam error messages in Discord
        if _ai_circuit_breaker["tripped"]:
            print(f"🚫 AI 熔斷器開啟中，不發送錯誤訊息到 Discord")
        else:
            try:
                await message.reply("⚠️ 發生錯誤，請稍後再試。", mention_author=False)
            except Exception as e:
                print(f"⚠️ on_message finally 例外: {e}")
    finally:
        _user_generating.discard(uid_str)


# ──────────────────────────────────────────────
# 波達計數法投票 View
# ──────────────────────────────────────────────

# >>> 20_poll extracted to modules/20_poll.py <<<
async def token_log_loop():
    """Background task: post token usage to the AI log channel every 30 minutes."""
    await asyncio.sleep(120)
    _first_check = True
    while True:
        try:
            log_channel_id = chat_ai_settings.get("log_channel_id")
            if not log_channel_id:
                if _first_check:
                    print(f"📊 Token Log: log_channel_id 未設定（值={log_channel_id!r}），跳過。請用 /chat log_channel 設定或從 dashboard 設定。")
                    _first_check = False
                await asyncio.sleep(1800)
                continue
            if _first_check:
                print(f"📊 Token Log: log_channel_id={log_channel_id}，開始監控。")
                _first_check = False
            log_ch = None
            for guild in bot.guilds:
                ch = guild.get_channel(int(log_channel_id))
                if ch:
                    log_ch = ch
                    break
            if not log_ch:
                print(f"⚠️ Token Log: 找不到頻道 ID {log_channel_id}（可能已刪除或 Bot 無權限），bot.guilds={len(bot.guilds)}")
                await asyncio.sleep(1800)
                continue
            today = datetime.now(GMT8).strftime("%Y-%m-%d")
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
                embed.set_footer(text="⚠️ 尚無 API 呼叫記錄 | 每 30 分鐘自動更新")
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
TURTLE_SOUP_FILE = os.path.join(DATA_DIR, "turtle_soup.json")
QUIZ_ASKED_FILE = os.path.join(DATA_DIR, "quiz_asked_questions.json")
QUIZ_RECENT_TITLES_FILE = os.path.join(DATA_DIR, "quiz_recent_titles.json")

# ── AI 精煉系統（自動交叉比對頻道訊息與百科資料，萃取冷門知識存入資料庫）──
REFINE_SETTINGS_FILE = os.path.join(DATA_DIR, "ai_refine_settings.json")
REFINE_KNOWLEDGE_FILE = os.path.join(DATA_DIR, "ai_refined_knowledge.json")
ai_refine_settings = {
    "enabled": False,
    "channel_id": None,       # 機器人自言自語的頻道
    "guild_id": None,
    "interval_minutes": 5,     # 預設每 5 分鐘精煉一次
    "max_knowledge_entries": 500,  # 最多保留幾條精煉知識（防止無限增長）
}
ai_refined_knowledge = []  # [{date, source, topic, summary, details}]
_refine_last_run = 0  # 上次精煉的時間戳
_refine_empty_streak = 0  # 連續空手（無新知識）的次數，用於動態退避

def save_refine_settings():
    _save_json_file(REFINE_SETTINGS_FILE, ai_refine_settings, indent=None)

def save_refine_knowledge():
    _save_json_file(REFINE_KNOWLEDGE_FILE, ai_refined_knowledge)

def load_refine_settings():
    global ai_refine_settings
    try:
        if os.path.exists(REFINE_SETTINGS_FILE):
            with open(REFINE_SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
                if isinstance(loaded, dict):
                    ai_refine_settings.update(loaded)
    except Exception as e:
        print(f"⚠️ AI精煉設定載入失敗: {e}")

def load_refine_knowledge():
    global ai_refined_knowledge
    try:
        if os.path.exists(REFINE_KNOWLEDGE_FILE):
            with open(REFINE_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
                if isinstance(loaded, list):
                    ai_refined_knowledge = loaded
                    print(f"✅ AI精煉知識庫載入：{len(ai_refined_knowledge)} 條知識")
    except Exception as e:
        print(f"⚠️ AI精煉知識庫載入失敗: {e}")

# ── Emoji aliases: map cryptic emoji names to human-readable descriptions ──
EMOJI_ALIASES_FILE = os.path.join(DATA_DIR, "emoji_aliases.json")
emoji_aliases = {}  # {original_name: {"alias": "人類可讀名", "emoji_id": "...", "animated": false}}

def save_emoji_aliases():
    _save_json_file(EMOJI_ALIASES_FILE, emoji_aliases)

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
    "guild_channels": {},     # {guild_id_str: channel_id_str} for guest servers
}
quiz_scores = {}      # {user_id_str: {username, daily_score, total_score, date}}
quiz_champions = []   # [{date, champion_id, champion_name, champion_score, runner_up_name, runner_up_score}]
# Active questions: {message_id_str: {question, options, correct_index, source_title, source_url, answered_by, created_at}}
quiz_active_questions = {}
# Previously asked questions (dedup history) — list of normalized question strings
quiz_asked_questions = []
_QUIZ_MAX_HISTORY = 200  # keep last N questions to avoid unbounded growth

# Recently used micropedia article titles — even when the AI rewords the
# question, reusing the same source article back-to-back feels like "the
# same quiz repeating". We actively steer away from these for a while.
quiz_recent_titles = []
_QUIZ_RECENT_TITLES_MAX = 10  # avoid re-picking any of the last 10 articles


def save_quiz_data():
    """Save quiz settings, scores, champions, and active state to disk."""
    global _quiz_last_question_time
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        _save_json_file(QUIZ_SETTINGS_FILE, quiz_settings)
        _save_json_file(QUIZ_SCORES_FILE, quiz_scores)
        _save_json_file(QUIZ_CHAMPIONS_FILE, quiz_champions)
        quiz_state = {
            "active_questions": quiz_active_questions,
            "last_question_time": _quiz_last_question_time,
        }
        _save_json_file(QUIZ_STATE_FILE, quiz_state)
        _save_json_file(QUIZ_ASKED_FILE, quiz_asked_questions)
        _save_json_file(QUIZ_RECENT_TITLES_FILE, quiz_recent_titles)
    except Exception as e:
        print(f"⚠️ Quiz data save failed: {e}")


# >>> 70_quiz extracted to modules/70_quiz.py <<<
# ──────────────────────────────────────────────

async def _ai_refine_fetch_channel_snippets(guild, max_channels=15, msgs_per_channel=30):
    """Fetch recent messages from a sample of non-excluded text channels.
    Returns a concatenated string of channel snippets for the AI to analyze."""
    _log_ch_id = chat_ai_settings.get("log_channel_id")
    _EXCLUDE_MARKERS = ("測試", "test", "log", "紀錄")

    def _is_excluded(ch):
        if _log_ch_id and ch.id == _log_ch_id:
            return True
        name_lower = ch.name.lower()
        return any(m.lower() in name_lower for m in _EXCLUDE_MARKERS)

    # Also exclude the refine channel itself (would create a feedback loop)
    refine_ch_id = ai_refine_settings.get("channel_id")

    candidates = [
        ch for ch in guild.text_channels
        if ch.type in (discord.ChannelType.text, discord.ChannelType.news)
        and not _is_excluded(ch)
        and ch.id != refine_ch_id
    ]
    _quiz_random.shuffle(candidates)
    selected = candidates[:max_channels]

    snippets = []
    for ch in selected:
        try:
            msgs = []
            async for msg in ch.history(limit=msgs_per_channel):
                text_parts = []
                if msg.content and msg.content.strip():
                    text_parts.append(msg.content.strip())
                for emb in msg.embeds:
                    if emb.title:
                        text_parts.append(str(emb.title))
                    if emb.description:
                        text_parts.append(str(emb.description))
                    for field in emb.fields:
                        text_parts.append(f"{field.name}: {field.value}")
                full = "\n".join(p for p in text_parts if p).strip()
                if full and len(full) >= 5 and not msg.author.bot:
                    msgs.append(f"[{msg.author.display_name}]: {full[:200]}")
            if msgs:
                snippets.append(f"── 頻道 #{ch.name} ──\n" + "\n".join(msgs[:15]))
        except Exception:
            continue
    return "\n\n".join(snippets)


def _char_bigrams(text: str) -> set:
    """Character-bigram shingles of a string, for cheap fuzzy similarity
    on Chinese text (which has no whitespace word boundaries)."""
    t = "".join(text.split())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i+2] for i in range(len(t) - 1)}


def _text_similarity(a: str, b: str) -> float:
    """Jaccard similarity over character bigrams. 0.0-1.0."""
    sa, sb = _char_bigrams(a), _char_bigrams(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _is_near_duplicate(topic: str, summary: str, existing_entries: list, threshold: float = 0.45) -> str:
    """Check if a candidate entry is a near-duplicate of anything already in
    the knowledge base — catches cases where the AI rephrases the same
    underlying content under a different topic name across cycles.
    Compares against the last 150 entries (bounded cost) using combined
    topic+summary text. Returns the matched existing topic if found, else ''."""
    candidate_text = f"{topic} {summary}"
    for existing in existing_entries[-150:]:
        existing_text = f"{existing.get('topic', '')} {existing.get('summary', '')}"
        if _text_similarity(candidate_text, existing_text) >= threshold:
            return existing.get("topic", "")
    return ""


async def _ai_refine_extract_from_discord(channel_snippets: str, existing_topics: list) -> list:
    """STEP 1 (API call 1): Extract PRELIMINARY knowledge from Discord
    community discussions. This is raw community knowledge — may contain
    errors, jokes, or speculation, and Step 3 will verify it against the
    encyclopedia. BUT it must still be a concrete, substantive claim about
    THIS micronation community — not generic philosophy or vague musing.
    Returns a list of {topic, summary, details, search_terms} dicts.
    Retries once (same strict bar, not loosened) if the result is empty."""
    if not chat_ai_settings.get("api_key") or not channel_snippets:
        return []

    existing_list = ", ".join(existing_topics[-40:]) if existing_topics else "（無）"

    system_prompt = (
        "你是一個微國家社群知識萃取師，標準非常嚴格，寧缺勿濫。你會收到 Discord\n"
        "伺服器中多個頻道的近期訊息，任務是從中萃取 0-3 條「初步知識」。\n\n"
        "【合格的知識】必須符合以下至少一項，且要具體、可查證：\n"
        "- 具名的事件、決策、投票結果、制度變更（有國家名/人名/日期/具體規則）\n"
        "- 某個微國家實際採行的具體制度、政策、慶典、外交行動（不是泛泛而談）\n"
        "- 社群歷史上真實發生過的具體事件或先例\n"
        "- 具體的文化習俗、國旗/國歌/憲法等具體內容的討論\n\n"
        "【一律拒絕，即使跟微國家沾上邊也不要萃取】：\n"
        "- 個人哲學觀點、形上學辯論（例如「自我連續性」「忒修斯之船」之類的\n"
        "  身份哲學討論，即使是拿刪頻道、換頭銜等小事當引子講的也算）\n"
        "- 空泛的通則或猜測，沒有指名道姓（例如「部分微國家會模仿現實政治\n"
        "  體系」——這種沒說是哪個國家、什麼具體制度，就是空話，拒絕）\n"
        "- 純粹的個人意見、抱怨、開玩笑、閒聊、心情發言\n"
        "- 未經證實的臆測、八卦、「聽說」「可能」等不確定語氣的內容\n\n"
        "判斷原則：如果把這條知識讀給不熟悉當下對話情境的人聽，他能不能明確\n"
        "指出「哪個國家/哪個事件/哪個具體規則」？不能的話就不合格，直接跳過。\n\n"
        "每條合格知識需要：\n"
        '- topic: 簡短主題（10字以內，需包含具體名稱或事件）\n'
        '- summary: 一句話摘要\n'
        '- details: 詳細說明（50-200字，需包含具體細節）\n'
        '- search_terms: 用於百科搜尋的關鍵詞（1-3個，用於驗證這條知識）\n\n'
        f"現有知識庫已有這些主題和摘要，內容相近的請勿重複萃取：{existing_list}\n\n"
        "嚴格回覆 JSON 陣列，不要加 markdown code block 或其他文字：\n"
        '[{"topic": "...", "summary": "...", "details": "...", "search_terms": ["詞1", "詞2"]}]\n'
        "如果訊息中沒有符合上述嚴格標準的知識，寧可回傳空陣列 []，也不要硬湊。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": channel_snippets[:3500]},
    ]

    for attempt in range(2):  # Max 2 attempts — SAME strict bar both times, no loosening
        prompt_label = "首次萃取" if attempt == 0 else "重試萃取（標準不變，只是再看一次）"
        print(f"🔍 AI精煉: {prompt_label} from Discord...")

        try:
            result = await asyncio.wait_for(
                call_chat_api(messages, chat_ai_settings, max_tokens=1500, fallback_mode="disabled", category="admin"), timeout=40
            )
        except Exception as e:
            print(f"🔍 AI精煉: {prompt_label}失敗: {e}")
            continue

        raw = result.get("content", "")
        if not raw:
            tool_calls = result.get("tool_calls", [])
            if tool_calls:
                raw = tool_calls[0].get("function", {}).get("arguments", "")

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        try:
            data = json_module.loads(raw)
        except Exception:
            import re
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                try:
                    data = json_module.loads(match.group())
                except Exception:
                    print(f"🔍 AI精煉: {prompt_label}無法解析: {raw[:200]}")
                    continue
            else:
                print(f"🔍 AI精煉: {prompt_label}無法解析: {raw[:200]}")
                continue

        if not isinstance(data, list):
            if isinstance(data, dict) and data.get("topic"):
                data = [data]
            else:
                print(f"🔍 AI精煉: {prompt_label}結果非陣列，跳過")
                continue

        # Filter: must have topic + summary, not duplicate
        entries = []
        for item in data[:3]:
            if not isinstance(item, dict):
                continue
            topic = item.get("topic", "").strip()
            summary = item.get("summary", "").strip()
            details = item.get("details", "").strip()
            search_terms = item.get("search_terms", [])
            if not isinstance(search_terms, list):
                search_terms = [str(search_terms)]
            search_terms = [str(t).strip() for t in search_terms if t and str(t).strip()]
            if not topic or not summary or len(summary) < 5:
                continue
            if topic in existing_topics:
                print(f"🔍 AI精煉: 跳過重複主題「{topic}」")
                continue
            entries.append({
                "topic": topic,
                "summary": summary,
                "details": details,
                "search_terms": search_terms or [topic],
                "confidence": "preliminary",
            })

        if entries:
            print(f"🔍 AI精煉: {prompt_label}產出 {len(entries)} 條初步知識")
            return entries
        else:
            print(f"🔍 AI精煉: {prompt_label}結果為空或無效")

    return []  # Both attempts failed


async def _ai_refine_fetch_wiki_for_knowledge(preliminary_entries: list) -> dict:
    """STEP 2 (no API): For each preliminary knowledge entry, search the
    encyclopedia using the entry's search_terms. Returns a dict mapping
    entry index → wiki article text. No AI API calls — just parallel HTTP."""
    if not preliminary_entries:
        return {}

    async def _safe_fetch(term):
        try:
            return await asyncio.wait_for(_fetch_micropedia(term, max_results=2), timeout=8)
        except Exception:
            return ""

    # Collect all unique search terms across all entries
    all_terms = []
    for entry in preliminary_entries:
        for term in entry.get("search_terms", [entry["topic"]]):
            if term and term not in all_terms:
                all_terms.append(term)
    all_terms = all_terms[:8]  # Cap at 8 total searches

    print(f"🔍 AI精煉: 百科搜尋詞: {all_terms}")
    results = await asyncio.gather(*[_safe_fetch(t) for t in all_terms], return_exceptions=True)

    # Map search term → article text
    term_to_article = {}
    for i, r in enumerate(results):
        if isinstance(r, str) and r.strip():
            term_to_article[all_terms[i]] = r.strip()[:2000]

    # For each entry, collect relevant articles based on its search terms
    entry_wiki = {}
    for i, entry in enumerate(preliminary_entries):
        articles = []
        for term in entry.get("search_terms", [entry["topic"]]):
            if term in term_to_article:
                articles.append(term_to_article[term])
        if articles:
            entry_wiki[i] = "\n\n── 下一篇 ──\n\n".join(articles)

    print(f"🔍 AI精煉: 百科找到 {len(entry_wiki)}/{len(preliminary_entries)} 條知識的相關文章")
    return entry_wiki


async def _ai_refine_verify_and_reorganize(preliminary_entries: list, wiki_articles: dict) -> list:
    """STEP 3 (API call 2): Take preliminary knowledge from Discord + wiki
    articles from encyclopedia. For each entry:
    - Cross-reference with wiki to remove errors
    - Add context or corrections from wiki
    - Reorganize into a clean, verified knowledge entry
    - If wiki contradicts the knowledge entirely, mark it low-confidence
    Returns list of verified knowledge entries."""
    if not chat_ai_settings.get("api_key"):
        return []

    verified = []
    for i, entry in enumerate(preliminary_entries):
        wiki_text = wiki_articles.get(i, "")
        has_wiki = bool(wiki_text)

        if has_wiki:
            system_prompt = (
                "你是一個微國家知識驗證師。你會收到：\n"
                "1. 一條從 Discord 社群討論中萃取的「初步知識」（可能包含錯誤）\n"
                "2. 相關的百科文章（可信參考資料）\n\n"
                "你的任務是：\n"
                "1. 對照百科文章，檢查初步知識中是否有錯誤\n"
                "2. 移除錯誤內容，補充百科中提供的正確資訊\n"
                "3. 重新統整成一條乾淨、準確的知識條目\n"
                "4. 如果百科文章完全否定了初步知識（整條都是錯的），回傳空結果\n"
                "5. 如果百科沒有覆蓋到該主題，保留初步知識但標記為低可信度\n\n"
                "嚴格回覆 JSON，不要加 markdown code block：\n"
                '{"topic": "修正後主題", "summary": "修正後摘要", "details": "修正後詳細說明", "confidence": "high或low"}\n'
                "- confidence=high：百科文章有覆蓋該主題，知識已驗證\n"
                "- confidence=low：百科文章沒有覆蓋，僅保留社群討論內容\n"
                "如果整條知識都是錯的，回傳 "
                '{\"topic\": \"\", \"summary\": \"\", \"details\": \"\", \"confidence\": \"\"}'
            )
            user_content = (
                f"── 初步知識（來自社群討論，可能有誤）──\n"
                f"主題：{entry['topic']}\n"
                f"摘要：{entry['summary']}\n"
                f"詳細：{entry['details']}\n\n"
                f"── 百科文章（可信參考資料）──\n{wiki_text[:2500]}"
            )
        else:
            # No wiki article found — only keep if it's still concrete and
            # specific (named event/decision/practice). Reject vague
            # generalizations or philosophical musing that slipped through
            # extraction — these are common false positives.
            system_prompt = (
                "你是一個微國家知識審核員，標準嚴格。你會收到一條從 Discord\n"
                "社群討論中萃取的初步知識。沒有找到相關百科文章來驗證，所以你\n"
                "需要自行判斷這條知識是否夠具體、值得保留。\n\n"
                "保留的條件（必須全部符合）：\n"
                "- 內容具體，指名道姓（有國家名/人名/日期/明確規則或事件）\n"
                "- 不是個人哲學觀點、形上學辯論、空泛通則、意見或閒聊\n"
                "- 讀者不需要對話情境也能看懂「哪個國家/哪個事件」\n\n"
                "如果符合，整理成清晰簡潔的知識條目，標記低可信度（未經百科驗證）。\n"
                "如果不符合（太空泛、太哲學、太主觀），直接回傳空結果，不要保留。\n\n"
                "嚴格回覆 JSON：\n"
                '{"topic": "...", "summary": "...", "details": "...", "confidence": "low"}\n'
                "不符合標準時回傳 "
                '{"topic": "", "summary": "", "details": "", "confidence": ""}'
            )
            user_content = (
                f"主題：{entry['topic']}\n"
                f"摘要：{entry['summary']}\n"
                f"詳細：{entry['details']}"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            result = await asyncio.wait_for(
                call_chat_api(messages, chat_ai_settings, max_tokens=800, fallback_mode="disabled", category="admin"), timeout=40
            )
        except Exception as e:
            print(f"🔍 AI精煉: 驗證「{entry['topic']}」失敗: {e}")
            continue

        raw = result.get("content", "")
        if not raw:
            tool_calls = result.get("tool_calls", [])
            if tool_calls:
                raw = tool_calls[0].get("function", {}).get("arguments", "")

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        try:
            data = json_module.loads(raw)
        except Exception:
            import re
            match = re.search(r'\{[^{}]*"topic"[^{}]*\}', raw, re.DOTALL)
            if match:
                try:
                    data = json_module.loads(match.group())
                except Exception:
                    print(f"🔍 AI精煉: 驗證「{entry['topic']}」無法解析: {raw[:200]}")
                    continue
            else:
                print(f"🔍 AI精煉: 驗證「{entry['topic']}」無法解析: {raw[:200]}")
                continue

        if not isinstance(data, dict):
            continue

        topic = data.get("topic", "").strip()
        summary = data.get("summary", "").strip()
        details = data.get("details", "").strip()
        confidence = data.get("confidence", "low").strip().lower()

        if not topic or not summary:
            print(f"🔍 AI精煉: 驗證結果為空（「{entry['topic']}」可能整條有誤，丟棄）")
            continue

        # Normalize confidence
        if confidence in ("high", "高", "true", "1"):
            confidence = "high"
        else:
            confidence = "low"

        print(f"🔍 AI精煉: 驗證完成「{topic}」(confidence={confidence})")
        verified.append({
            "topic": topic,
            "summary": summary,
            "details": details,
            "confidence": confidence,
        })

    return verified


async def _ai_refine_post_to_channel(channel, knowledge_entry):
    """Post the refined knowledge as a self-talk message in the designated channel."""
    conf = knowledge_entry.get("confidence", "high")
    conf_label = "✅ 高可信度（百科驗證）" if conf == "high" else "⚠️ 低可信度（社群未驗證）"
    embed = discord.Embed(
        title=f"🔬 AI 精煉 — {knowledge_entry['topic']}",
        description=f"{knowledge_entry['summary']}\n\n{conf_label}",
        color=discord.Color.teal() if conf == "high" else discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="📖 詳細說明",
        value=knowledge_entry["details"][:1024],
        inline=False,
    )
    embed.set_footer(text=f"知識庫累計 {len(ai_refined_knowledge)} 條 | AI 自主學習")
    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"🔍 AI精煉: 發布到頻道失敗: {e}")


# Concurrency support: multiple refine cycles can run in parallel.
# _refine_active_tasks tracks in-flight cycles (for visibility/status).
# _refine_write_lock protects the "check duplicate topic → append → save"
# critical section so two concurrent cycles can't both write the same
# topic or clobber each other's save.
_refine_active_tasks: set = set()
_refine_write_lock: asyncio.Lock = None  # initialised in on_ready (needs running loop)


async def _ai_refine_one_cycle(guild, channel, cycle_id: int):
    """Run ONE full refine cycle (Discord extraction → wiki verify → save).
    Designed to be launched as an independent asyncio.Task so multiple
    cycles can be in flight concurrently — a slow cycle never blocks
    the next one from starting."""
    global ai_refined_knowledge, _refine_empty_streak
    now = _time.time()
    try:
        # Step 0: Fetch Discord channel snippets (no API)
        channel_snippets = await _ai_refine_fetch_channel_snippets(guild)
        if isinstance(channel_snippets, Exception):
            print(f"🔍 AI精煉#{cycle_id}: 頻道抓取失敗: {channel_snippets}")
            channel_snippets = ""

        if not channel_snippets:
            print(f"🔍 AI精煉#{cycle_id}: 無頻道訊息，跳過")
            _refine_empty_streak += 1
            return

        # Step 1 (API call 1): Extract preliminary knowledge from Discord.
        # Pass topic+summary (not just bare topics) so the AI can recognize
        # near-duplicate CONTENT, not just exact-name matches.
        existing_context = [f"{k.get('topic', '')}：{k.get('summary', '')}" for k in ai_refined_knowledge]
        preliminary = await _ai_refine_extract_from_discord(channel_snippets, existing_context)

        if not preliminary:
            print(f"🔍 AI精煉#{cycle_id}: Discord 萃取為空（連續空手 {_refine_empty_streak + 1} 次）")
            _refine_empty_streak += 1
            return

        # Step 2 (no API): Search encyclopedia for each entry's search terms
        wiki_articles = await _ai_refine_fetch_wiki_for_knowledge(preliminary)

        # Step 3 (API call 2): Verify, correct, and reorganize using wiki
        verified = await _ai_refine_verify_and_reorganize(preliminary, wiki_articles)

        if not verified:
            print(f"🔍 AI精煉#{cycle_id}: 驗證後無有效知識（連續空手 {_refine_empty_streak + 1} 次）")
            _refine_empty_streak += 1
            return

        # Step 4: Save all verified entries + post to channel.
        # Locked so concurrent cycles don't both write the same topic or
        # race on save_refine_knowledge().
        async with _refine_write_lock:
            max_entries = ai_refine_settings.get("max_knowledge_entries", 500)
            saved = []
            for entry_data in verified:
                # Re-check against the LATEST state (another concurrent
                # cycle may have just added something) using FUZZY
                # similarity — catches rephrased duplicates, not just
                # exact topic-string matches.
                dup_of = _is_near_duplicate(entry_data["topic"], entry_data["summary"], ai_refined_knowledge)
                if dup_of:
                    print(f"🔍 AI精煉#{cycle_id}: 「{entry_data['topic']}」與現有「{dup_of}」內容相近，跳過")
                    continue
                entry = {
                    "date": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M"),
                    "_ts": now,
                    "topic": entry_data["topic"],
                    "summary": entry_data["summary"],
                    "details": entry_data["details"],
                    "confidence": entry_data.get("confidence", "low"),
                }
                ai_refined_knowledge.append(entry)
                saved.append(entry)
                if len(ai_refined_knowledge) > max_entries:
                    ai_refined_knowledge = ai_refined_knowledge[-max_entries:]
                print(f"🔍 AI精煉#{cycle_id}: 已儲存「{entry['topic']}」({entry['confidence']}) (累計 {len(ai_refined_knowledge)} 條)")

            if saved:
                save_refine_knowledge()

        for entry in saved:
            await _ai_refine_post_to_channel(channel, entry)

        if saved:
            api_calls = 1 + len(verified)  # 1 for extraction + 1 per entry for verification
            print(f"🔍 AI精煉#{cycle_id}: 本輪產出 {len(saved)} 條知識（{api_calls} 次 API call）")
            _refine_empty_streak = 0  # Reset backoff
        else:
            _refine_empty_streak += 1

    except Exception as e:
        print(f"⚠️ AI精煉#{cycle_id}循環錯誤: {e}")
    finally:
        _refine_active_tasks.discard(cycle_id)


async def ai_refine_loop():
    """Background dispatcher: every dynamic interval, LAUNCH a new refine
    cycle as an independent task — without waiting for any previous cycle
    to finish. This means multiple cycles can run concurrently; a slow
    cycle (waiting on AI API latency) never blocks the next dispatch."""
    global _refine_last_run
    await asyncio.sleep(90)  # Wait for bot to be fully ready
    cycle_counter = 0
    while True:
        try:
            if not ai_refine_settings.get("enabled"):
                await asyncio.sleep(20)
                continue

            # Dynamic interval: adjusts based on API traffic and knowledge base fullness
            interval_secs = _compute_dynamic_refine_interval()
            now = _time.time()
            if _refine_last_run and (now - _refine_last_run) < interval_secs:
                await asyncio.sleep(5)
                continue

            guild_id = ai_refine_settings.get("guild_id")
            channel_id = ai_refine_settings.get("channel_id")
            if not guild_id or not channel_id:
                await asyncio.sleep(20)
                continue

            guild = bot.get_guild(int(guild_id))
            if not guild:
                await asyncio.sleep(20)
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                print(f"🔍 AI精煉: 找不到頻道 {channel_id}")
                await asyncio.sleep(20)
                continue

            if not chat_ai_settings.get("api_key"):
                print("🔍 AI精煉: 沒有 AI API Key，跳過")
                await asyncio.sleep(20)
                continue

            if _refine_write_lock is None:
                await asyncio.sleep(5)
                continue

            # Dispatch NOW — mark as running so the next dispatch respects
            # the interval, but DON'T await the cycle. It runs independently;
            # if it's still going when the next interval hits, a second
            # cycle is dispatched alongside it (concurrent, not blocking).
            _refine_last_run = now
            cycle_counter += 1
            cid = cycle_counter
            cpm = _get_api_calls_per_minute()
            kb_full = len(ai_refined_knowledge) / max(1, ai_refine_settings.get("max_knowledge_entries", 500))
            print(f"🔍 AI精煉#{cid}: 開始（併發中 {len(_refine_active_tasks)} 個, API {cpm} calls/min, 知識庫 {len(ai_refined_knowledge)}/{ai_refine_settings.get('max_knowledge_entries', 500)} ({kb_full:.0%}), 派工間隔 {interval_secs}s）")
            _refine_active_tasks.add(cid)
            asyncio.create_task(_ai_refine_one_cycle(guild, channel, cid))

        except Exception as e:
            print(f"⚠️ AI精煉派工錯誤: {e}")

        await asyncio.sleep(5)



# ═════════════════════════════════════════════════════════════════
# Community Awareness System
# 讓 AI 像真實社群成員一樣理解微國家社群的人事物——
# 不是離散的知識條目，而是對社群動態的整體感知。
#
# 四個維度：
# 1. 社交關係感知 — 誰活躍、誰安靜、誰跟誰有什麼關係
# 2. 事件脈絡記憶 — 近期發生了什麼事，因果鏈
# 3. 即時話題意識 — 現在在討論什麼
# 4. 頻道文化理解 — 每個頻道的氛圍和生態
#
# 每 20 分鐘掃描一次近期訊息，由 AI 綜合分析後存檔。
# 聊天回覆時自動注入到 system prompt，讓 AI「知道現在社群的狀態」。
# ═════════════════════════════════════════════════════════════════

COMMUNITY_AWARENESS_FILE = os.path.join(DATA_DIR, "community_awareness.json")
COMMUNITY_AWARENESS_SETTINGS_FILE = os.path.join(DATA_DIR, "community_awareness_settings.json")

_community_awareness = {
    "last_updated": "",
    "social_dynamics": {
        "active_members": [],   # [{name, activity, topics}]
        "relationships": [],     # [{a, b, type, context}]
    },
    "recent_events": [],         # [{summary, participants, context}]
    "current_topics": [],       # [{topic, channels, summary}]
    "channel_cultures": {},     # {"#channel": {vibe, typical_content, key_people}}
}

_community_awareness_settings = {
    "enabled": True,
    "interval_minutes": 20,
    "guild_id": None,
}

_awareness_last_run = 0


def _save_community_awareness():
    _save_json_file(COMMUNITY_AWARENESS_FILE, _community_awareness)


def _save_awareness_settings():
    _save_json_file(COMMUNITY_AWARENESS_SETTINGS_FILE, _community_awareness_settings, indent=None)


def _load_community_awareness():
    global _community_awareness
    try:
        if os.path.exists(COMMUNITY_AWARENESS_FILE):
            with open(COMMUNITY_AWARENESS_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
                if isinstance(loaded, dict):
                    _community_awareness.update(loaded)
                    print(f"🧠 社群感知：已載入（更新於 {loaded.get('last_updated', '?')}）")
    except Exception as e:
        print(f"⚠️ 社群感知載入失敗：{e}")


def _load_awareness_settings():
    global _community_awareness_settings
    try:
        if os.path.exists(COMMUNITY_AWARENESS_SETTINGS_FILE):
            with open(COMMUNITY_AWARENESS_SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
                if isinstance(loaded, dict):
                    _community_awareness_settings.update(loaded)
    except Exception as e:
        print(f"⚠️ 社群感知設定載入失敗：{e}")


async def _gather_community_messages(guild, max_channels=20, msgs_per_channel=20) -> str:
    """Gather recent messages across the server's channels for community
    analysis. Broader sampling than the refine snippet gatherer — covers
    more channels with fewer messages each, prioritizing recent activity."""
    _log_ch_id = chat_ai_settings.get("log_channel_id")
    _EXCLUDE_MARKERS = ("測試", "test", "log", "紀錄")

    def _is_excluded(ch):
        if _log_ch_id and ch.id == _log_ch_id:
            return True
        name_lower = ch.name.lower()
        return any(m.lower() in name_lower for m in _EXCLUDE_MARKERS)

    candidates = [
        ch for ch in guild.text_channels
        if ch.type in (discord.ChannelType.text, discord.ChannelType.news)
        and not _is_excluded(ch)
    ]

    # Sort by recent activity (last message timestamp) — most active first
    async def _last_msg_ts(ch):
        try:
            async for m in ch.history(limit=1):
                return m.created_at.timestamp()
            return 0  # Channel has no messages
        except Exception:
            return 0
    # Quick check — just grab last message timestamp for sorting
    channel_ts = []
    for ch in candidates:
        ts = await _last_msg_ts(ch)
        channel_ts.append((ts, ch))
    channel_ts.sort(key=lambda x: -x[0])
    selected = [ch for _, ch in channel_ts[:max_channels]]

    snippets = []
    for ch in selected:
        try:
            msgs = []
            async for msg in ch.history(limit=msgs_per_channel):
                text_parts = []
                if msg.content and msg.content.strip():
                    text_parts.append(msg.content.strip())
                for emb in msg.embeds:
                    if emb.title:
                        text_parts.append(str(emb.title))
                    if emb.description:
                        text_parts.append(str(emb.description))
                    for field in emb.fields:
                        text_parts.append(f"{field.name}: {field.value}")
                full = "\n".join(p for p in text_parts if p).strip()
                if full and len(full) >= 5 and not msg.author.bot:
                    ts_str = msg.created_at.astimezone(GMT8).strftime("%m/%d %H:%M")
                    msgs.append(f"[{ts_str}] {msg.author.display_name}: {full[:150]}")
            if msgs:
                snippets.append(f"── #{ch.name} ──\n" + "\n".join(msgs))
        except Exception:
            continue
    return "\n\n".join(snippets)


async def _analyze_community(guild) -> bool:
    """Run one community awareness analysis cycle: gather recent messages,
    have the AI synthesize a community awareness profile, and save it."""
    global _community_awareness
    if not chat_ai_settings.get("api_key"):
        return False

    messages_text = await _gather_community_messages(guild)
    if not messages_text or len(messages_text.strip()) < 100:
        print("🧠 社群感知：訊息太少，跳過本次分析")
        return False

    # Build the previous awareness as context so the AI can build on it
    prev = _community_awareness
    prev_summary = ""
    if prev.get("last_updated"):
        prev_lines = []
        for m in prev.get("social_dynamics", {}).get("active_members", [])[:10]:
            prev_lines.append(f"- {m.get('name', '?')}: {m.get('activity', '')}")
        for r in prev.get("social_dynamics", {}).get("relationships", [])[:5]:
            prev_lines.append(f"- {r.get('a', '?')}↔{r.get('b', '?')} ({r.get('type', '?')}): {r.get('context', '')}")
        for e in prev.get("recent_events", [])[:5]:
            prev_lines.append(f"- [{e.get('date', '?')}] {e.get('summary', '')}")
        for t in prev.get("current_topics", [])[:5]:
            prev_lines.append(f"- 話題：{t.get('topic', '?')} — {t.get('summary', '')}")
        prev_summary = "\n".join(prev_lines)

    system_prompt = (
        "你是一個微國家 Discord 社群的觀察員，你的任務是分析近期訊息，"
        "建立一份「社群感知報告」——就像一個長期泡在社群裡的老成員"
        "對社群狀態的理解一樣。\n\n"
        "你會收到多個頻道的近期訊息。請從中分析出以下四個維度：\n\n"
        "1. 社交動態（social_dynamics）：\n"
        "   - active_members: 最近活躍的成員（最多 15 人），每人附上他們"
        "近期在做什麼、聊什麼話題\n"
        "   - relationships: 成員之間值得注意的關係動態（最多 10 條）——"
        "誰跟誰在合作、誰跟誰有分歧、誰跟誰有特殊互動。type 用以下值："
        "ally（盟友/友好）、rival（對立/分歧）、collaborator（合作）、"
        "mentor（指導）、tension（緊張）。只有真的觀察到明確互動的才寫，"
        "不要憑空推測。\n\n"
        "2. 近期事件（recent_events）：最近發生的重要事件（最多 8 條），"
        "每條包含：summary（一句話描述）、participants（參與者）、"
        "context（為什麼發生、導致了什麼——因果脈絡）。\n"
        "   - 事件要有實質意義（決策、衝突、合作、公告、投票、人事變動等），"
        "不是閒聊\n"
        "   - 盡量捕捉因果鏈：因為 A，所以 B\n\n"
        "3. 當前話題（current_topics）：現在正在討論的熱門話題（最多 8 條），"
        "每條包含：topic（話題名）、channels（在哪些頻道討論）、"
        "summary（一句話概要）。\n\n"
        "4. 頻道文化（channel_cultures）：每個頻道的氛圍和特色，"
        "每個頻道包含：vibe（一句話描述氛圍）、typical_content（通常聊什麼）、"
        "key_people（常在這裡發言的人）。只寫有足夠訊息判斷的頻道。\n\n"
        "【重要原則】\n"
        "- 你是在觀察和理解社群動態，不是在寫百科全書\n"
        "- 只寫從訊息中能觀察到的東西，不要編造或過度推測\n"
        "- 關係動態要基於實際互動（回覆、提及、對話），不是猜測\n"
        "- 寫繁體中文\n"
        "- 盡量精簡，每個欄位的文字不要超過 100 字\n"
        "- 【反幻覺鐵律】絕對不要自己判定「A 其實就是 B 的別名／同一人」這類等同關係，"
        "除非訊息中有人明確這樣說過。名稱只是同時出現在對話中，不代表有任何關聯。\n\n"
    )

    if prev_summary:
        system_prompt += (
            f"以下是上一次分析的結果（作為參考，請在此基礎上更新）：\n"
            f"{prev_summary}\n\n"
            "請以最新訊息為準更新以上內容。如果某些關係或事件已經過時，"
            "就移除它們。新的觀察要取代舊的。\n\n"
        )

    system_prompt += (
        "嚴格回覆以下 JSON 格式（不要加 markdown code block，不要加其他文字）：\n"
        '{"last_updated": "", "social_dynamics": {"active_members": [{"name": "", "activity": "", "topics": []}], "relationships": [{"a": "", "b": "", "type": "", "context": ""}]}, "recent_events": [{"date": "", "summary": "", "participants": [], "context": ""}], "current_topics": [{"topic": "", "channels": [], "summary": ""}], "channel_cultures": {"#頻道名": {"vibe": "", "typical_content": "", "key_people": []}}}'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"以下是近期 Discord 社群訊息：\n\n{messages_text[:6000]}"},
    ]

    try:
        result = await asyncio.wait_for(
            call_chat_api(messages, chat_ai_settings, max_tokens=2500, fallback_mode="disabled", category="admin"), timeout=60
        )
    except Exception as e:
        print(f"🧠 社群感知：AI 分析失敗：{e}")
        return False

    raw = result.get("content", "")
    if not raw:
        tool_calls = result.get("tool_calls", [])
        if tool_calls:
            raw = tool_calls[0].get("function", {}).get("arguments", "")

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json_module.loads(raw)
    except Exception:
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                data = json_module.loads(match.group())
            except Exception:
                print(f"🧠 社群感知：JSON 解析失敗：{raw[:200]}")
                return False
        else:
            print(f"🧠 社群感知：無法解析 AI 回應：{raw[:200]}")
            return False

    if not isinstance(data, dict):
        print(f"🧠 社群感知：回應非 dict")
        return False

    # Update the awareness data
    now_str = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M")
    data["last_updated"] = now_str
    _community_awareness = data
    _save_community_awareness()

    # Stats for log
    n_members = len(data.get("social_dynamics", {}).get("active_members", []))
    n_rels = len(data.get("social_dynamics", {}).get("relationships", []))
    n_events = len(data.get("recent_events", []))
    n_topics = len(data.get("current_topics", []))
    n_channels = len(data.get("channel_cultures", {}))
    print(f"🧠 社群感知已更新（{now_str}）：{n_members} 成員, {n_rels} 關係, {n_events} 事件, {n_topics} 話題, {n_channels} 頻道文化")

    return True


async def community_awareness_loop():
    """Background task: analyze community dynamics every ~20 minutes."""
    global _awareness_last_run
    await asyncio.sleep(120)  # Wait for bot to be fully ready
    while True:
        try:
            if not _community_awareness_settings.get("enabled"):
                await asyncio.sleep(30)
                continue

            if not chat_ai_settings.get("api_key"):
                await asyncio.sleep(30)
                continue

            guild_id = _community_awareness_settings.get("guild_id")
            if not guild_id:
                # Auto-detect: use the first available guild
                if bot.guilds:
                    _community_awareness_settings["guild_id"] = str(bot.guilds[0].id)
                    _save_awareness_settings()
                    guild_id = _community_awareness_settings["guild_id"]
                else:
                    await asyncio.sleep(30)
                    continue

            guild = bot.get_guild(int(guild_id))
            if not guild:
                await asyncio.sleep(30)
                continue

            interval = _community_awareness_settings.get("interval_minutes", 20) * 60
            now = _time.time()
            if _awareness_last_run and (now - _awareness_last_run) < interval:
                await asyncio.sleep(15)
                continue

            _awareness_last_run = now
            print(f"🧠 社群感知：開始分析 {guild.name} 的社群動態...")
            success = await _analyze_community(guild)
            if not success:
                _awareness_last_run = now  # Still count as attempted to avoid rapid retry

        except Exception as e:
            print(f"⚠️ 社群感知迴圈錯誤：{e}")

        await asyncio.sleep(15)


def _get_community_awareness_context() -> str:
    """Render the current community awareness as a compact text block
    for injection into the AI's system prompt. This is what gives the AI
    a 'real member's understanding' of the community."""
    aw = _community_awareness
    if not aw.get("last_updated"):
        return ""

    lines = [f"─── 社群感知（僅供參考的次要背景資訊，非查證來源，更新於 {aw['last_updated']}）───"]

    sd = aw.get("social_dynamics", {})

    # Active members
    active = sd.get("active_members", [])
    if active:
        member_parts = []
        for m in active[:12]:
            name = m.get("name", "?")
            activity = m.get("activity", "")
            topics = m.get("topics", [])
            topic_str = f"（聊：{', '.join(topics[:3])}）" if topics else ""
            member_parts.append(f"{name}：{activity}{topic_str}")
        lines.append(f"\n👥 活躍成員：\n" + "\n".join(f"  • {p}" for p in member_parts))

    # Relationships
    rels = sd.get("relationships", [])
    if rels:
        rel_parts = []
        type_emoji = {"ally": "🤝", "rival": "⚔️", "collaborator": "🔧", "mentor": "🎓", "tension": "⚡"}
        for r in rels[:8]:
            emoji = type_emoji.get(r.get("type", ""), "→")
            rel_parts.append(f"  {emoji} {r.get('a', '?')} ↔ {r.get('b', '?')}：{r.get('context', '')}")
        lines.append(f"\n🔗 關係動態：\n" + "\n".join(rel_parts))

    # Recent events
    events = aw.get("recent_events", [])
    if events:
        event_parts = []
        for e in events[:6]:
            date = e.get("date", "")
            summary = e.get("summary", "")
            context = e.get("context", "")
            participants = e.get("participants", [])
            p_str = f"（參與：{', '.join(participants[:4])}）" if participants else ""
            ctx_str = f" → {context}" if context else ""
            event_parts.append(f"  • [{date}] {summary}{p_str}{ctx_str}")
        lines.append(f"\n📅 近期事件：\n" + "\n".join(event_parts))

    # Current topics
    topics = aw.get("current_topics", [])
    if topics:
        topic_parts = []
        for t in topics[:6]:
            ch_str = ", ".join(t.get("channels", [])[:3])
            topic_parts.append(f"  • {t.get('topic', '?')}（{ch_str}）：{t.get('summary', '')}")
        lines.append(f"\n🔥 當前話題：\n" + "\n".join(topic_parts))

    # Channel cultures
    cultures = aw.get("channel_cultures", {})
    if cultures:
        culture_parts = []
        for ch_name, info in list(cultures.items())[:10]:
            vibe = info.get("vibe", "")
            key = info.get("key_people", [])
            key_str = f"（常客：{', '.join(key[:4])}）" if key else ""
            culture_parts.append(f"  • {ch_name}：{vibe}{key_str}")
        lines.append(f"\n🎭 頻道氛圍：\n" + "\n".join(culture_parts))

    lines.append(
        "\n⚠️ 以上是 AI 自動分析近期訊息的結果，代表社群的近期動態。"
        "請自然地運用這些理解來回應使用者，就像你一直都在社群裡一樣。"
        "但不要主動提起「社群感知報告」這個詞——表現得像一個自然而然了解社群的人。\n"
        "🚫 反幻覺鐵律：不要把上面不同人物/事件的資訊自行連結、合併、或推論出"
        "沒有明確記錄的關係（例如宣稱兩個名稱是同一個人/國家）。"
        "資料不足時就誠實表示不清楚，不要編造。"
    )

    return "\n".join(lines)



# ══════════════════════════════════════════════════════════════════
# AI 自動計票系統（論壇貼文投票）
# ──────────────────────────────────────────────
# 秘書處在論壇貼文（原po）用文字說明投票格式與選項對應的 Emoji（可能是
# 自訂 Emoji，也可能是 Unicode Emoji，例如 1️⃣2️⃣3️⃣ 或 🟩🟧🟥），會員國
# 代表則直接在貼文底下回覆「國家代號+國名+Emoji」來投票。
#
# 本功能會：
#   1. 讀取原po文字（含 embed），自動抓出裡面出現過的所有 Emoji token。
#   2. 用 AI 判斷這是「單選」還是「排序（波達計數法）」投票，並將每個
#      Emoji 對應到候選人/選項名稱（AI 無法判斷時可用 legend 參數手動指定）。
#   3. 掃描貼文底下所有回覆，只挑出「含有合法選項 Emoji」的訊息視為有效
#      投票，其餘閒聊訊息自動忽略。
#   4. 每位使用者僅計最後一筆有效投票（避免重複/更正造成的重複計票）。
#   5. 依偵測到的計票方式計算結果（單選＝計數；排序＝波達計數法）。
# ──────────────────────────────────────────────

# 自訂 Emoji：<a?:名稱:數字ID>；一般 Emoji：常見的 Unicode Emoji 區段
# （含 keycap 數字組合 1️⃣2️⃣...、色塊方塊 🟩🟧🟥、常見符號 ✅❌⭐ 等）。
_CUSTOM_EMOJI_RE_SRC = r'<a?:[A-Za-z0-9_~]+:[0-9]+>'
_KEYCAP_EMOJI_RE_SRC = r'[0-9#\*]\uFE0F?\u20E3'
_UNICODE_EMOJI_RE_SRC = (
    r'[\U0001F1E6-\U0001F1FF]{2}'          # 國旗（區域指示符號對）
    r'|[\U0001F300-\U0001FAFF]\uFE0F?'      # 主要 Emoji 區段
    r'|[\u2600-\u27BF]\uFE0F?'              # 雜項符號 & Dingbats（✅❌➡️ 等）
    r'|[\u2B00-\u2BFF]\uFE0F?'              # 雜項符號與箭頭（⭐⬛⬜ 等）
)
_EMOJI_TOKEN_RE = re.compile(
    "(?:" + _CUSTOM_EMOJI_RE_SRC + ")"
    "|(?:" + _KEYCAP_EMOJI_RE_SRC + ")"
    "|(?:" + _UNICODE_EMOJI_RE_SRC + ")"
)


def _extract_emoji_tokens(text: str) -> list:
    """依出現順序抓出文字中所有 Emoji token（自訂或 Unicode），保留原始寫法。"""
    if not text:
        return []
    return _EMOJI_TOKEN_RE.findall(text)


def _dedup_preserve_order(items: list) -> list:
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _gather_thread_text(starter_message) -> str:
    """把貼文原po的文字內容 + embed 內容合併成一段文字，供 Emoji 偵測與 AI 判讀使用。"""
    parts = []
    if starter_message is None:
        return ""
    if starter_message.content:
        parts.append(starter_message.content)
    for embed in starter_message.embeds:
        if embed.title:
            parts.append(str(embed.title))
        if embed.description:
            parts.append(str(embed.description))
        for f in embed.fields:
            parts.append(f"{f.name} {f.value}")
        if embed.footer and embed.footer.text:
            parts.append(str(embed.footer.text))
    return "\n".join(parts)


async def _ai_detect_vote_legend(op_text: str, emoji_tokens: list) -> dict:
    """用 AI 判斷投票方式（單選/排序）與每個 Emoji 對應的候選人/選項名稱。
    回傳格式：{"mode": "single"|"ranked", "legend": {emoji: label, ...}, "notes": str}
    若 AI 不可用或解析失敗，回傳一個保底結果（單選模式，選項名稱＝Emoji 本身）。"""
    fallback = {
        "mode": "single",
        "legend": {tok: tok for tok in emoji_tokens},
        "notes": "AI 無法使用，採用保底規則（每個 Emoji 視為獨立選項，單選計票）。",
    }
    if not emoji_tokens:
        return {"mode": "single", "legend": {}, "notes": "貼文中沒有偵測到任何 Emoji。"}

    ps_ai = proposal_settings.get("ai_settings", {})
    settings = {
        "api_url": ps_ai.get("api_url") or chat_ai_settings.get("api_url", ""),
        "api_key": ps_ai.get("api_key") or chat_ai_settings.get("api_key", ""),
        "model": ps_ai.get("model") or chat_ai_settings.get("model", "gpt-4o-mini"),
        "system_prompt": "你是投票制度分析助手，負責判讀 Discord 論壇投票貼文的計票規則。",
    }
    if not settings["api_url"] or not settings["api_key"]:
        return fallback

    emoji_list_str = "、".join(emoji_tokens)
    prompt = (
        "以下是一篇 Discord 論壇投票貼文的原文內容（可能包含投票格式說明、候選人清單等）：\n\n"
        f"「{op_text[:2000]}」\n\n"
        f"這篇貼文中偵測到以下 Emoji（依出現順序，只能使用這些，不要自己發明新的）：\n{emoji_list_str}\n\n"
        "請判斷：\n"
        "1. mode：這是「single」（每人只能選一個選項投票）還是「ranked」"
        "（每人需依偏好排序多個選項，即波達計數法）？"
        "判斷依據：如果投票格式要求填入多個 Emoji 代表偏好順序（例如「請依序填入你的第一、第二、第三選擇」），"
        "就是 ranked；如果只是從幾個選項中選一個，就是 single。\n"
        "2. legend：把每一個列出的 Emoji 對應到它代表的候選人/選項名稱"
        "（依貼文中該 Emoji 附近的文字或 @提及來判斷，例如「1️⃣ @張作霖 張作霖」代表 1️⃣ 對應「張作霖」）。"
        "如果貼文文字中完全找不到對應名稱（可能寫在圖片裡），"
        "就用「選項（Emoji本身）」這種格式當作 label，不要亂猜名字。\n\n"
        "請直接回覆 JSON（不要加 markdown code block），格式：\n"
        '{"mode": "single", "legend": {"emoji1": "候選人A", "emoji2": "候選人B"}, "notes": "簡短說明"}\n'
        "只回覆 JSON，不要加其他文字。"
    )

    try:
        result = await call_ai_api(prompt, settings)
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json_module.loads(result)
        mode = parsed.get("mode", "single")
        if mode not in ("single", "ranked"):
            mode = "single"
        legend_raw = parsed.get("legend", {}) or {}
        # 只保留真的出現在貼文裡的 Emoji，避免 AI 幻覺出新 Emoji
        legend = {tok: legend_raw.get(tok, tok) for tok in emoji_tokens if tok in legend_raw or tok in emoji_tokens}
        if not legend:
            legend = {tok: tok for tok in emoji_tokens}
        return {
            "mode": mode,
            "legend": legend,
            "notes": str(parsed.get("notes", ""))[:200],
        }
    except Exception as e:
        print(f"⚠️ 計票 AI 判讀失敗，改用保底規則：{e}")
        return fallback


def _parse_manual_legend(legend_str: str) -> dict:
    """解析手動指定的 legend 字串，格式：'代碼1=名稱1,代碼2=名稱2'。
    代碼可以是 Emoji，也可以是純英數文字代碼（例如 a、RHV）——純英數代碼
    會自動轉大寫，確保跟投票內文比對時不受大小寫影響。"""
    mapping = {}
    if not legend_str:
        return mapping
    for pair in legend_str.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        code_part, label_part = pair.split("=", 1)
        code_part = code_part.strip()
        label_part = label_part.strip()
        if not code_part or not label_part:
            continue
        if re.fullmatch(r'[A-Za-z0-9]{1,6}', code_part):
            code_part = code_part.upper()
        mapping[code_part] = label_part
    return mapping


def _guess_token_type_from_legend(legend: dict) -> str:
    """判斷 legend 裡的代碼是 Emoji 還是純文字代碼。只要有一個 key 不是純英數，就當作 emoji 模式。"""
    if not legend:
        return "emoji"
    for k in legend.keys():
        if not re.fullmatch(r'[A-Za-z0-9]{1,6}', k):
            return "emoji"
    return "text_code"


# 候選人代碼清單行，例如「a大斯皇帝國」「e厂万共和國」——1~3 個英文字母緊接著中文名稱，
# 中間沒有空格。這是本組織實際選舉貼文最常見的候選人代碼列表寫法（無 Emoji）。
_CANDIDATE_LEGEND_LINE_RE = re.compile(r'^([A-Za-z]{1,3})([\u4e00-\u9fff].{0,40})$')


def _extract_text_code_legend(op_text: str) -> dict:
    """從原po文字中，依「代碼+中文候選人名稱」的行格式，抓出候選人代碼對照表（不靠 AI）。"""
    legend = {}
    for line in op_text.split("\n"):
        line = line.strip()
        if not line or "http" in line.lower():
            continue
        m = _CANDIDATE_LEGEND_LINE_RE.match(line)
        if m:
            code = m.group(1).upper()
            label = m.group(2).strip()
            if label:
                legend[code] = label
    return legend


def _detect_mode_from_text(op_text: str) -> str:
    """依貼文文字中的關鍵字，判斷是排序（波達計數法）還是單選投票。找不到明確訊號則回傳空字串。"""
    if not op_text:
        return ""
    t_lower = op_text.lower()
    if "波達計數法" in op_text or "波达计数法" in op_text or "borda" in t_lower:
        return "ranked"
    if any(k in op_text for k in ("排序偏好", "依序填入", "依偏好排序", "偏好順序")):
        return "ranked"
    if " 或 " in op_text or "或\n" in op_text:
        return "single"
    return ""


# 純文字排序投票的填寫格式，例如「1.E」「2. F」「12.G」——數字 + 分隔符號 + 1~6 個英文字母代碼。
# ── FIX：negative lookahead 原本排除「代碼後面緊接中文字」（\u4e00-\u9fff），
# 目的是避免抓到英文單字的一部分，但這連帶誤殺了「代碼+國名黏在一起寫」這種
# 完全合法、且很常見的投票格式（因為候選人清單本身就是「e厂万共和國」這種
# 代碼緊接中文名稱、沒有空格的寫法，投票者很自然會模仿同樣格式投票，例如
# 「1.e厂万共和國」）。這種票之前會整張被 regex 直接抓不到任何代碼，變成
# 完全被當成閒聊濾掉（比誤判成廢票更嚴重——連廢票統計都看不到）。
# 修正：只排除代碼後面緊接「英文字母/數字」（避免真的抓到英文單字的一部分，
# 例如「1. buy milk」的 buy），不再排除中文字——中文字接在代碼後面反而是
# 「這確實是候選人代碼」的強烈正訊號，不該排除。最終有沒有抓對還是要看
# 抓到的代碼是否存在於 legend_keys 裡，這才是真正防止誤判的關卡。
# >>> 90_tally extracted to modules/90_tally.py <<<
# ──────────────────────────────────────────────
# 將所有已受理（status=="accepted"）的提案自動彙整成會議排程通知圖，
# 套用固定的視覺樣式（例行會議/簡務會議），AI 負責把提案內容整理成
# 精簡的公告顯示文字，圖片用 Pillow 繪製（非 AI 生圖，避免文字失真）。
# 秘書處在確認頻道預覽圖片後，點擊「發送」才會真正發到目標頻道並
# @ 指定身分組；發送成功後，這批提案會從待辦清單中刪除，避免下次
# 排程重複列出。
schedule_settings = {
    "enabled": True,
    "review_channel_id": None,      # 秘書處確認頻道（留空則沿用提案系統的秘書處頻道）
    "target_channel_id": None,      # 排程通知圖最終發送頻道
    "mention_role_id": None,        # 發送時 @ 提及的身分組 ID
    "checkin_start": "13:00",
    "checkin_end": "21:00",
    "review_time": "15:00",         # 升格案／提案審理標示時間
    "motion_time": "20:00",         # 臨時動議標示時間
    "vote_time": "21:00",           # 投票結算＆散會標示時間
    "regular_meeting_no": 1,        # 下一次「例行會議」編號（發送成功後自動 +1）
    "briefing_meeting_no": 1,       # 下一次「簡務會議」編號（發送成功後自動 +1）
}

# 待確認的排程通知（記憶體暫存，不落地存檔）：
# schedule_id -> {png, proposal_ids, target_channel_id, mention_role_id, meta}
# 機器人重啟會清空此暫存，屆時秘書處需重新執行 /schedule generate。
_pending_schedules: dict = {}


def load_schedule_settings():
    global schedule_settings
    try:
        if os.path.exists(SCHEDULE_SETTINGS_FILE):
            with open(SCHEDULE_SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.loads(f.read())
            schedule_settings.update(loaded)
            print(f"📅 會議排程設定已載入（下次例行會議#{schedule_settings.get('regular_meeting_no')}，簡務會議#{schedule_settings.get('briefing_meeting_no')}）")
    except Exception as e:
        print(f"⚠️ 會議排程設定載入失敗：{e}")


def save_schedule_settings():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _save_json_file(SCHEDULE_SETTINGS_FILE, schedule_settings)
    except Exception as e:
        print(f"⚠️ 會議排程設定儲存失敗：{e}")

# 提案區 AI 自動受理系統
# When a new thread/message appears in a designated proposal channel, the AI
# auto-analyzes it (type + summary) and sends a notification to the secretariat
# channel with 受理/駁回 buttons. Admin decision is relayed back to the original
# proposal channel/thread so the proposer knows the outcome.
proposal_settings = {
    "enabled": False,
    "proposal_channels": [],     # list of channel IDs to monitor for proposals
    "secretariat_channel": None, # channel ID where admin gets notifications
    "ai_settings": {             # separate AI config for proposal analysis (falls back to chat AI)
        "api_url": "",
        "api_key": "",
        "model": "",
    },
}

# Pending/reviewed proposals. Each entry:
#   {id, date, guild_id, proposer_id, proposer_name, channel_id, thread_id,
#    message_id, raw_content, proposal_type, summary, status: "pending"/"accepted"/"rejected",
#    reviewed_by, review_date, reject_reason}
_proposals = {"entries": []}

# Blacklisted users are completely blocked from using the bot:
# - on_message returns immediately (AI never sees their messages)
# - all slash commands are rejected (interaction_check)
# - AI system prompt explicitly instructs ignoring their messages
_blacklist = {"users": []}  # list of {id, user_id, user_name, reason, date, added_by}

# User-submitted corrections to AI answers. Each entry:
#   {id, date, user_id, user_name, question, original_answer,
#    correction, ai_validation, validated, guild_id}
# When validated, corrections are injected into the AI system prompt as
# ground-truth context (same layer as micropedia auto-context), so the AI
# learns from user feedback and stops repeating the same wrong answers.
_corrections = {"entries": []}
_correction_cooldowns = {}  # user_id -> last submission timestamp
_knowledge_base = {"summaries": []}

FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback.json")

# User 讚/倒讚 feedback on AI replies. Completely separate from _corrections —
# different file, different cooldown, different purpose (sentiment + reason
# tracking vs. factual correction). Each entry:
#   {id, date, rating: "like"/"dislike", reason, custom_text, image_url,
#    question, ai_answer, user_id, user_name, guild_id, channel_id}
_feedback = {"entries": []}
_feedback_cooldowns = {}  # user_id -> last submission timestamp


def load_knowledge_base():
    """Load the permanent knowledge base from local file (synced from Drive)."""
    global _knowledge_base
    try:
        if os.path.exists(KNOWLEDGE_BASE_FILE):
            with open(KNOWLEDGE_BASE_FILE, "r", encoding="utf-8") as f:
                _knowledge_base = json_module.loads(f.read())
            print(f"📚 知識庫已載入：{len(_knowledge_base.get('summaries', []))} 篇每日摘要")
    except Exception as e:
        print(f"⚠️ 知識庫載入失敗：{e}")
        _knowledge_base = {"summaries": []}


def save_knowledge_base():
    """Save the knowledge base to local file (auto-synced to Drive)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _save_json_file(KNOWLEDGE_BASE_FILE, _knowledge_base)
    except Exception as e:
        print(f"⚠️ 知識庫儲存失敗：{e}")


def load_corrections():
    """Load user-submitted corrections from local file (synced from Drive)."""
    global _corrections
    try:
        if os.path.exists(CORRECTIONS_FILE):
            with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
                _corrections = json_module.loads(f.read())
            print(f"📝 修正資料已載入：{len(_corrections.get('entries', []))} 筆")
    except Exception as e:
        print(f"⚠️ 修正資料載入失敗：{e}")
        _corrections = {"entries": []}


def save_corrections():
    """Save corrections to local file (auto-synced to Drive)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _save_json_file(CORRECTIONS_FILE, _corrections)
    except Exception as e:
        print(f"⚠️ 修正資料儲存失敗：{e}")


def load_feedback():
    """Load like/dislike feedback from local file (synced from Drive)."""
    global _feedback
    try:
        if os.path.exists(FEEDBACK_FILE):
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                _feedback = json_module.loads(f.read())
            print(f"👍 評價資料已載入：{len(_feedback.get('entries', []))} 筆")
    except Exception as e:
        print(f"⚠️ 評價資料載入失敗：{e}")
        _feedback = {"entries": []}


def save_feedback():
    """Save like/dislike feedback to local file (auto-synced to Drive)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _save_json_file(FEEDBACK_FILE, _feedback)
    except Exception as e:
        print(f"⚠️ 評價資料儲存失敗：{e}")


def load_proposal_settings():
    """Load proposal system settings from local file (synced from Drive)."""
    global proposal_settings
    try:
        if os.path.exists(PROPOSAL_SETTINGS_FILE):
            with open(PROPOSAL_SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.loads(f.read())
            proposal_settings.update(loaded)
            print(f"📋 提案系統設定已載入：{'啟用' if proposal_settings.get('enabled') else '停用'}，監控 {len(proposal_settings.get('proposal_channels', []))} 個頻道")
    except Exception as e:
        print(f"⚠️ 提案系統設定載入失敗：{e}")


def save_proposal_settings():
    _save_json_file(PROPOSAL_SETTINGS_FILE, proposal_settings)


def load_proposals():
    global _proposals
    try:
        if os.path.exists(PROPOSALS_FILE):
            with open(PROPOSALS_FILE, "r", encoding="utf-8") as f:
                _proposals = json_module.loads(f.read())
            print(f"📋 提案記錄已載入：{len(_proposals.get('entries', []))} 筆")
    except Exception as e:
        print(f"⚠️ 提案記錄載入失敗：{e}")
        _proposals = {"entries": []}


def save_proposals():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _save_json_file(PROPOSALS_FILE, _proposals)
    except Exception as e:
        print(f"⚠️ 提案記錄儲存失敗：{e}")


def load_blacklist():
    """Load blacklist from local file (synced from Drive)."""
    global _blacklist
    try:
        if os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
                _blacklist = json_module.loads(f.read())
            print(f"🚫 黑名單已載入：{len(_blacklist.get('users', []))} 人")
    except Exception as e:
        print(f"⚠️ 黑名單載入失敗：{e}")
        _blacklist = {"users": []}


def save_blacklist():
    """Save blacklist to local file (auto-synced to Drive)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _save_json_file(BLACKLIST_FILE, _blacklist)
    except Exception as e:
        print(f"⚠️ 黑名單儲存失敗：{e}")




async def _check_guild(interaction: discord.Interaction) -> bool:
    """Returns True if guild is available, sends error message if not."""
    if not interaction.guild:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ 此指令只能在伺服器中使用，無法在私訊中使用。", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ 此指令只能在伺服器中使用，無法在私訊中使用。", ephemeral=True
            )
        return False
    return True


def is_blacklisted(user_id) -> bool:
    """Check if a user ID is blacklisted."""
    uid = str(user_id)
    return any(str(u.get("user_id")) == uid for u in _blacklist.get("users", []))


def _search_corrections(query: str, top_n: int = 3) -> list:
    """Search validated corrections using bigram + keyword matching.
    Only returns validated entries (AI-confirmed or admin-approved)."""
    query = query.strip()
    query_bg = _bigrams(query)
    if not query_bg:
        return []
    entries = [e for e in _corrections.get("entries", []) if e.get("validated")]
    if not entries:
        return []
    scored = []
    for e in entries:
        text = e.get("correction", "") + " " + e.get("question", "")
        text_bg = _bigrams(text)
        if not text_bg:
            continue
        overlap = query_bg & text_bg
        substring_hit = bool(query) and query in text
        keyword_hit = _keyword_substring_hit(query, text)
        if not overlap and not substring_hit and not keyword_hit:
            continue
        containment = len(overlap) / len(query_bg) if query_bg else 0
        if not substring_hit and not keyword_hit and containment < 0.2:
            continue
        score = max(containment, 0.5) if keyword_hit else containment
        scored.append((e, score, substring_hit or keyword_hit))
    scored.sort(key=lambda x: (-x[2], -x[1]))
    return [e for e, _, _ in scored[:top_n]]


def _search_knowledge_base(query: str, top_n: int = 3) -> list:
    """Search the permanent knowledge base using bigram matching, with a
    keyword-substring fallback for long conversational queries (see
    _extract_search_keywords)."""
    query = query.strip()
    query_bg = _bigrams(query)
    if not query_bg:
        return []
    summaries = _knowledge_base.get("summaries", [])
    if not summaries:
        return []
    scored = []
    for s in summaries:
        text = s.get("summary", "") + " " + s.get("date", "")
        text_bg = _bigrams(text)
        if not text_bg:
            continue
        overlap = query_bg & text_bg
        substring_hit = bool(query) and query in text
        keyword_hit = _keyword_substring_hit(query, text)
        if not overlap and not substring_hit and not keyword_hit:
            continue
        containment = len(overlap) / len(query_bg) if query_bg else 0
        if not substring_hit and not keyword_hit and containment < 0.2:
            continue
        score = max(containment, 0.5) if keyword_hit else containment
        scored.append((s, score, substring_hit or keyword_hit))
    scored.sort(key=lambda x: (-x[2], -x[1]))
    return [s for s, _, _ in scored[:top_n]]


# ── 智慧過濾：惡意訊息 / 玩笑 / 噪音偵測 ──
# 預先過濾明顯的噪音和惡意/玩笑訊息，避免污染知識庫。
_NOISE_PATTERNS = (
    "笑死", "假的", "亂講", "唬爛", "幻覺", "幻想", "做夢",
    "我以為", "我猜", "隨便說", "開玩笑", "說說而已", "鬧的",
    "北七", "白痴", "智障", "笨蛋",  # 純辱罵，無資訊量
    "+1", "-1", "111", "www", "WWW", "wwww",  # 純湊字數
    "https://tenor.com", "https://media.giphy.com",  # GIF 連結
)

# 信任來源標記：管理員/版主的訊息可信度高，一般成員的可信度低
def _author_authority_tag(msg) -> str:
    """Return a trust-level tag for the message author."""
    if not msg.author:
        return "?"
    if msg.author.bot:
        # Bot messages (official announcements, system messages) — high trust
        return "[機器人]"
    perms = msg.author.guild_permissions
    if perms.administrator or perms.manage_guild:
        return "[管理員]"
    # Check for moderator-ish roles
    role_names = [r.name.lower() for r in msg.author.roles if not r.managed]
    mod_keywords = ("管理", "版主", "mod", "admin", "行政", "議長", "秘書長", "官員", "部長")
    if any(kw in rn for rn in role_names for kw in mod_keywords):
        return "[幹部]"
    return "[成員]"

def _is_noise_message(text: str) -> bool:
    """Check if a message is likely noise/joke/spam that should be filtered
    out before AI summarization."""
    text_lower = text.lower().strip()
    if len(text_lower) < 3:
        return True
    # Pure emoji / reaction spam
    import re as _re
    if _re.fullmatch(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\ufe0f\u200d]+", text):
        return True
    # All-same character spam (wwww, 哈哈哈哈, 啊啊啊啊)
    if len(set(text_lower.replace(" ", ""))) <= 2 and len(text_lower) > 3:
        return True
    # Check noise patterns
    for pattern in _NOISE_PATTERNS:
        if pattern in text_lower:
            # But don't filter if the message is long enough to contain real content
            # alongside the noise word
            if len(text_lower) < 20:
                return True
            # If the noise pattern IS the message, filter it
            if text_lower.strip() == pattern.lower():
                return True
    return False


async def _collect_daily_messages(guild, hours=24) -> str:
    """Collect all messages from the past `hours` across all text channels.
    Returns a condensed text dump grouped by channel for AI summarization.

    Smart filtering:
    - Skips noise messages (jokes, spam, emoji-only, all-same-char)
    - Tags each message with author authority level so the AI can weight
      information by source credibility (admin > officer > member > bot-for-announcements)
    - Pre-filters obvious misinformation patterns
    - Skips bot commands and very short messages
    Staggered to respect Discord rate limits."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    _t0 = _time.time()
    _ch_count = 0
    _msg_count = 0
    _filtered_count = 0
    channel_chunks = []

    text_channels = [
        ch for ch in guild.text_channels
        if ch.type in (discord.ChannelType.text, discord.ChannelType.news)
    ]

    for ch in text_channels:
        _ch_count += 1
        ch_lines = []
        try:
            async for msg in ch.history(limit=200, after=cutoff):
                # Extract text content
                text = msg.content.strip()
                if not text or len(text) < 10:
                    # Check embeds for official announcements
                    embed_parts = []
                    for emb in msg.embeds:
                        if emb.title:
                            embed_parts.append(str(emb.title))
                        if emb.description:
                            embed_parts.append(str(emb.description))
                        for field in emb.fields:
                            embed_parts.append(f"{field.name}: {field.value}")
                    text = "\n".join(embed_parts).strip()
                    if not text or len(text) < 5:
                        continue
                # Skip slash/prefix commands
                if text.startswith("/") or text.startswith("!"):
                    continue
                # Smart noise filter
                if _is_noise_message(text):
                    _filtered_count += 1
                    continue

                # Tag with author authority level
                authority_tag = _author_authority_tag(msg)
                author = msg.author.display_name if msg.author else "未知"
                time_str = (msg.created_at + timedelta(hours=8)).strftime("%H:%M") if msg.created_at else "??:??"
                ch_lines.append(f"[{time_str}]{authority_tag} {author}: {text[:250]}")
                _msg_count += 1
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"⚠️ 每日收集「{ch.name}」失敗：{e}")

        if ch_lines:
            channel_chunks.append(f"\n── #{ch.name} ──\n" + "\n".join(ch_lines[-50:]))

        if _ch_count % 5 == 0:
            await asyncio.sleep(0.5)

    print(f"📚 每日訊息收集：{_ch_count} 頻道，{_msg_count} 則收錄，{_filtered_count} 則過濾，耗時 {_time.time() - _t0:.1f}s")
    return "\n".join(channel_chunks)


async def _generate_daily_summary(messages_text: str, date_str: str) -> str:
    """Send collected messages to AI and get a structured daily summary.

    The prompt includes strict anti-misinformation rules: the AI must
    cross-reference claims, weight by author authority tags, and mark
    unverified rumors as such rather than presenting them as fact."""
    if not messages_text or len(messages_text.strip()) < 50:
        return "本日無顯著活動。"

    prompt = (
        f"以下是某微國家組織 Discord 伺服器在 {date_str} 的全天訊息記錄。\n"
        f"每則訊息前面標有發言者的身份標籤：\n"
        f"- [管理員] = 伺服器管理員（可信度最高）\n"
        f"- [幹部] = 具有管理/版主/議長/秘書長/官員等角色（可信度高）\n"
        f"- [機器人] = 機器人發布的官方公告（可信度高）\n"
        f"- [成員] = 一般成員（可信度普通，需交叉驗證）\n\n"
        f"請整理成一份結構化的每日重點摘要。格式如下：\n\n"
        f"## {date_str} 伺服器日報\n\n"
        f"### 重要公告與人事變動\n（選舉結果、任命、罷免、政策發布等，列出人名和具體事件）\n\n"
        f"### 重要討論與決議\n（論壇提案進展、投票結果、會議結論等）\n\n"
        f"### 其他值得記錄的事\n（活動、事件、爭議、值得注意的互動等）\n\n"
        f"### 未經證實的傳聞\n（僅來自[成員]的單方面宣稱，未經管理員或幹部確認的說法，明確標注為未經證實）\n\n"
        f"⚠️ 資訊可信度判斷規則（極重要）：\n"
        f"1. 只將[管理員]、[幹部]、[機器人]發布的內容記錄為「已確認事實」\n"
        f"2. [成員]發布的宣稱（如「某人當選」「某案通過」「某人被罷免」）必須在記錄中看到\n"
        f"   [管理員]或[幹部]的確認訊息，才能記為事實。否則歸入「未經證實的傳聞」\n"
        f"3. 如果[成員]宣稱的事實後來被[管理員]或[幹部]否認或更正，以更正後的版本為準\n"
        f"4. 玩笑話、反串、惡搞訊息一律忽略，不要記錄\n"
        f"5. 如果某事件只有一個人提到且沒有其他人附和或確認，歸入「未經證實的傳聞」\n"
        f"6. 同一事件多則訊息有矛盾時，以權威來源（管理員>幹部>成員）為準\n\n"
        f"其他要求：\n"
        f"- 只記錄有事實根據的內容，絕不編造或腦補\n"
        f"- 每個條目盡量包含人名和具體事件\n"
        f"- 如果某個分類沒有內容就寫「無」\n"
        f"- 用繁體中文\n"
        f"- 總字數控制在 500-1500 字\n\n"
        f"訊息記錄：\n{messages_text[:12000]}"
    )

    messages = [
        {"role": "system", "content": "你是微國家社群的歷史記錄員，負責整理每日重點。你非常嚴謹，不會被玩笑話或惡意訊息誤導。用繁體中文，語氣客觀簡潔。"},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await call_chat_api(
            messages,
            {"api_url": ai_settings["api_url"], "api_key": ai_settings["api_key"], "model": ai_settings.get("model", "gpt-4o-mini"), "model_fallback_chain": ai_settings.get("model_fallback_chain", "")},
            max_tokens=2500, fallback_mode="disabled", category="admin",  # briefing asks for 500-1500 中文字 output — needs a
                              # much bigger budget than the 300-token chat default,
                              # plus headroom for reasoning-model preamble
        )
        # call_chat_api returns the assistant MESSAGE dict directly, not a
        # full {"choices": [...]} response.
        text = result.get("content", "") if isinstance(result, dict) else ""
        return text.strip() if text else "AI 整理失敗（空回應）。"
    except Exception as e:
        return f"AI 整理失敗：{e}"


async def daily_summary_loop():
    """Background task: every day at 3 AM Taipei time (UTC+8), collect all
    messages from the past 24 hours, send to AI for summarization, and
    store the result in the permanent knowledge base on Google Drive."""
    from datetime import datetime, timezone, timedelta
    taipei_tz = timezone(timedelta(hours=8))

    while True:
        try:
            now_taipei = datetime.now(taipei_tz)
            # Calculate next 3 AM Taipei time
            next_run = now_taipei.replace(hour=3, minute=0, second=0, microsecond=0)
            if now_taipei >= next_run:
                next_run += timedelta(days=1)
            wait_seconds = (next_run - now_taipei).total_seconds()
            print(f"📚 每日摘要排程：下次執行 {next_run.strftime('%Y-%m-%d %H:%M')} Taipei（等待 {wait_seconds/3600:.1f} 小時）")
            await asyncio.sleep(wait_seconds)

            # Collect messages from the past 24 hours
            for guild in bot.guilds:
                print(f"📚 開始每日摘要：{guild.name}")
                messages_text = await _collect_daily_messages(guild, hours=24)
                date_str = (datetime.now(taipei_tz) - timedelta(hours=24)).strftime("%Y-%m-%d")

                # Generate AI summary
                summary = await _generate_daily_summary(messages_text, date_str)

                # Save to knowledge base
                _knowledge_base.setdefault("summaries", []).append({
                    "date": date_str,
                    "summary": summary,
                    "guild": guild.name,
                    "message_count": messages_text.count("\n") + 1,
                })
                # Cap knowledge base at 365 entries to prevent unbounded growth
                if len(_knowledge_base["summaries"]) > 365:
                    _knowledge_base["summaries"] = _knowledge_base["summaries"][-365:]
                save_knowledge_base()
                print(f"📚 每日摘要已儲存：{date_str}（知識庫共 {len(_knowledge_base['summaries'])} 篇）")

                await asyncio.sleep(5)  # stagger between guilds
        except Exception as e:
            print(f"⚠️ 每日摘要排程失敗：{e}")
            await asyncio.sleep(3600)  # retry in 1 hour on error


# ──────────────────────────────────────────────
# 啟動
# ──────────────────────────────────────────────

async def _graceful_shutdown_save():
    """Save every local state file and force one last upload to Drive.
    Called on SIGTERM (Render redeploy/restart) so we never lose the last
    few seconds of activity between periodic sync cycles."""
    print("🛑 收到終止訊號，儲存所有暫存資料到雲端硬碟...")
    # 先保存正在進行的遊戲狀態 + 發送重啟公告（需要在 bot 還連線時完成）
    try:
        await save_active_game_states()
        save_economy()
        save_stock_market()
        save_horse_racing()
    except Exception as e:
        print(f"⚠️ 遊戲狀態保存失敗：{e}")
    try:
        save_polls_to_disk()
        save_quiz_data()
        save_token_usage()
        save_briefing_settings()
        save_chat_ai_settings()
        save_user_memories()
        save_knowledge_base()
        save_economy()
        save_stock_market()
        save_horse_racing()
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
        if sub_bot is not None and SUB_BOT_TOKEN_ENV:
            print("[INFO] Starting main bot + sub bot...")
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
        pass



# ═══════════════════════════════════════════════════════════════════
# Load feature modules (exec into this namespace — zero code changes needed)
# ═══════════════════════════════════════════════════════════════════
import os as _os_mod
_modules_dir = _os_mod.path.join(_os_mod.path.dirname(_os_mod.path.abspath(__file__)), "modules")
_loaded_mods = 0
if _os_mod.path.isdir(_modules_dir):
    for _fname in sorted(_os_mod.listdir(_modules_dir)):
        if _fname.endswith(".py") and not _fname.startswith("__"):
            _path = _os_mod.path.join(_modules_dir, _fname)
            with open(_path, encoding="utf-8") as _f:
                _code = compile(_f.read(), _path, "exec")
            exec(_code, globals())
            _loaded_mods += 1
            print(f"  ✅ Loaded module: {_fname}")
print(f"📦 Loaded {_loaded_mods} feature modules from modules/")


# ================================================================
# Sub-bot (Entertainment Bot) -- separate Discord bot for other servers
# Only registers entertainment + AI-free commands. Shares all state
# (economy, WW1, AI API, data) with the main bot since same process.
# ================================================================
sub_bot = None
SUB_BOT_TOKEN_ENV = os.getenv("SUB_BOT_TOKEN")

# ================================================================
# Sub-bot command catalog & per-command dashboard toggle
# ================================================================
# 只有這些「純娛樂」指令群組才是子機器人的候選指令——不像先前版本整包
# 複製全部群組（含 /schedule /system /economy 這種行政指令）。
# 對照 modules/015_server_registry.py 的 ENTERTAINMENT_COMMANDS 分類。
_SUB_BOT_GROUP_CLASSES = [
    QuizGroup, TurtleSoupGroup, WerewolfGroup, StockGroup,
    HorseRacingGroup, SiegeGroup, CyberWarGroup, GalgameGroup,
]
_SUB_BOT_CMD_CONFIG_FILE = "data/sub_bot_commands.json"
_sub_bot_cmd_config = {}

# 指令描述文字常寫「機器人擁有者限定」，但實際權限檢查未必真的鎖死在單一
# BOT_OWNER_ID——例如 /quiz channel、/soup channel 實際呼叫的是 is_admin()
# （檢查該伺服器的 Manage Server / Administrator 權限），任何 Guest 伺服器
# 自己的管理員都能用，跟文字描述誤導的「僅機器人擁有者」完全是两回事。
# 這裡改用「實際程式碼權限檢查」而非「描述文字關鍵字」來決定預設開關：
# 只有真的寫死比對單一 BOT_OWNER_ID（跨伺服器都只有那一個人能用）的指令
# 才預設關閉，其餘一律預設開啟，讓子機器人真正做到「Guest 伺服器自治」。
_SUB_BOT_TRUE_OWNER_ONLY_KEYS = {
    "ww.toggle", "ww.channel", "ww.end", "ww.test",          # modules/010_werewolf.py: is_owner()
    "horse.set_channel", "horse.start_now",                   # modules/140_horse_racing.py: is_owner()
    "siege.start", "siege.settle", "siege.setup", "siege.toggle",  # modules/160_siege.py: 寫死 BOT_OWNER_ID
    "cyber_war.start", "cyber_war.end",   # modules/180_cyber_war.py: 寫死 BOT_OWNER_ID（set_channel 已改為 is_admin() 管理員限定，見上方 quiz/soup 註解邏輯）
    "vn.set_channel", "vn.admin",                              # modules/190_galgame.py: 寫死 BOT_OWNER_ID
}


def _sub_bot_cmd_default_enabled(key: str) -> bool:
    """真正被鎖死在單一機器人擁有者 ID 的指令預設關閉；其餘（含 is_admin()
    每伺服器管理員可用的指令，如 /quiz channel、/soup channel）預設開啟。"""
    return key not in _SUB_BOT_TRUE_OWNER_ONLY_KEYS


def _load_sub_bot_cmd_config():
    global _sub_bot_cmd_config
    try:
        if os.path.exists(_SUB_BOT_CMD_CONFIG_FILE):
            with open(_SUB_BOT_CMD_CONFIG_FILE, "r", encoding="utf-8") as f:
                _sub_bot_cmd_config = json.load(f)
            print(f"[INFO] 子機器人指令設定：已載入 {len(_sub_bot_cmd_config)} 條覆寫設定")
    except Exception as e:
        print(f"[WARN] 子機器人指令設定載入失敗：{e}")
        _sub_bot_cmd_config = {}


def _save_sub_bot_cmd_config():
    try:
        os.makedirs("data", exist_ok=True)
        with open(_SUB_BOT_CMD_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_sub_bot_cmd_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] 子機器人指令設定儲存失敗：{e}")


def _get_sub_bot_command_catalog():
    """列出子機器人所有「候選」指令（含群組子指令 + 頂層 /draw），
    附上目前有效的開關狀態，供 Dashboard 渲染逐指令開關 UI。"""
    catalog = []
    for cls in _SUB_BOT_GROUP_CLASSES:
        try:
            inst = cls()
        except Exception as e:
            print(f"[WARN] 指令目錄：無法建立 {cls.__name__} 實例：{e}")
            continue
        for cmd in inst.commands:
            key = f"{inst.name}.{cmd.name}"
            default_enabled = _sub_bot_cmd_default_enabled(key)
            catalog.append({
                "key": key,
                "group": inst.name,
                "group_description": inst.description,
                "name": cmd.name,
                "description": cmd.description,
                "default_enabled": default_enabled,
                "enabled": _sub_bot_cmd_config.get(key, default_enabled),
            })
    catalog.append({
        "key": "draw",
        "group": None,
        "group_description": None,
        "name": "draw",
        "description": getattr(draw_command, "description", "文生圖"),
        "default_enabled": True,
        "enabled": _sub_bot_cmd_config.get("draw", True),
    })
    return catalog


def _register_sub_bot_commands():
    """依目前的開關設定，重建子機器人的指令樹（僅記憶體內，尚未 sync）。
    群組若被關到剩 0 個子指令則整組跳過（Discord 規定群組至少要有 1 個子指令）。"""
    if sub_bot is None:
        return 0
    sub_bot.tree.clear_commands(guild=None)
    registered = 0
    for cls in _SUB_BOT_GROUP_CLASSES:
        try:
            grp = cls()
        except Exception as e:
            print(f"[WARN] Sub-bot 無法建立群組 {cls.__name__}: {e}")
            continue
        for cmd in list(grp.commands):
            key = f"{grp.name}.{cmd.name}"
            default_enabled = _sub_bot_cmd_default_enabled(key)
            if not _sub_bot_cmd_config.get(key, default_enabled):
                grp.remove_command(cmd.name)
        if len(grp.commands) == 0:
            continue
        try:
            sub_bot.tree.add_command(grp)
            registered += len(grp.commands)
        except Exception as e:
            print(f"[WARN] Sub-bot 無法註冊群組 {cls.__name__}: {e}")
    if _sub_bot_cmd_config.get("draw", True):
        try:
            sub_bot.tree.add_command(draw_command)
            registered += 1
        except Exception as e:
            print(f"[WARN] Sub-bot 無法註冊 /draw: {e}")
    print(f"[INFO] Sub-bot 指令樹重建完成：{registered} 個已啟用指令")
    return registered


async def _sync_sub_bot_tree():
    """重建 + 同步子機器人指令樹到 Discord（每個伺服器各別 sync 即時生效，
    全域 sync 當備援）。可在啟動時或 Dashboard 更新設定後呼叫。"""
    if sub_bot is None:
        return
    _register_sub_bot_commands()
    for g in sub_bot.guilds:
        try:
            synced = await sub_bot.tree.sync(guild=g)
            print(f"[OK] Sub-bot 已同步 {len(synced)} 個指令到伺服器 {g.name} ({g.id})")
        except Exception as e:
            print(f"[ERR] Sub-bot 伺服器同步失敗 {g.name}: {e}")
    try:
        synced_global = await sub_bot.tree.sync()
        print(f"[OK] Sub-bot 全域同步：{len(synced_global)} 個指令")
    except Exception as e:
        print(f"[ERR] Sub-bot 全域同步失敗: {e}")


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

    async def _sub_setup_hook():
        _load_sub_bot_cmd_config()
        _register_sub_bot_commands()

    async def _sub_tree_interaction_check(interaction: discord.Interaction) -> bool:
        if interaction.user and is_blacklisted(interaction.user.id):
            try:
                await interaction.response.send_message(
                    "\U0001f6ab You are blacklisted.", ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    sub_bot.setup_hook = _sub_setup_hook
    sub_bot.tree.interaction_check = _sub_tree_interaction_check
    print("[INFO] Sub-bot (Entertainment) initialized, waiting for connection...")
else:
    print("[INFO] SUB_BOT_TOKEN not set, skipping sub-bot initialization.")

# Register setup_hook so discord.py calls it before connecting
# Register persistent views for AI Chat Room buttons (survives bot restarts)
bot.add_view(AIChatRoomPanelView())
bot.add_view(AIChatRoomCloseView())
bot.add_view(TurtleSoupStartView())  # 只有開始按鈕是持久化的
bot.add_view(WerewolfSignupView())  # 狼人殺報名按鈕持久化
bot.add_view(EconomyPanelButtonsView())  # 經濟看板下方的股票/公司管理快捷按鈕持久化
bot.add_view(GalgamePanelView())  # Galgame 互動小說面板按鈕持久化
bot.add_view(MinerPanelView())  # 琉璃幣礦工面板按鈕持久化（僅主機器人）
bot.add_view(HorseBettingView("persistent"))  # 賽馬下注按鈕持久化（重啟後復原用）
bot.add_view(_WerewolfResumeView())  # 狼人殺重啟恢復按鈕持久化
bot.add_view(SiegePanelView())  # 攻城戰按鈕持久化
if os.getenv("HOI4_ENABLED", "true").lower() not in ("false", "0", "no", "off"):
    bot.add_view(HOI4PanelView())  # HOI4 戰略指揮部面板按鈕持久化

bot.setup_hook = setup_hook

# ================================================================
# Sub-bot event handlers (only if sub_bot exists)
# ================================================================
if sub_bot is not None:
    _sub_persistent_views = [
        ("TurtleSoupStartView", lambda: TurtleSoupStartView()),
        ("WerewolfSignupView", lambda: WerewolfSignupView()),
        ("EconomyPanelButtonsView", lambda: EconomyPanelButtonsView()),
        ("GalgamePanelView", lambda: GalgamePanelView()),
        ("HorseBettingView", lambda: HorseBettingView("persistent")),
        ("_WerewolfResumeView", lambda: _WerewolfResumeView()),
        ("SiegePanelView", lambda: SiegePanelView()),
    ]
    for _view_name, _view_factory in _sub_persistent_views:
        try:
            sub_bot.add_view(_view_factory())
        except Exception as e:
            print(f"[WARN] Sub-bot persistent view {_view_name} failed: {e}")

    @sub_bot.event
    async def on_ready():
        """Sub-bot on_ready: register guilds and sync the (already-filtered) command tree."""
        if not getattr(on_ready, "_sub_done", False):
            on_ready._sub_done = True
            print(f"[OK] Sub-bot online: {sub_bot.user}")
            for g in sub_bot.guilds:
                _is_owner = (str(g.id) == ICEA_GUILD_ID)
                register_server(g.id, g.name, is_owner_server=_is_owner)
            await _sync_sub_bot_tree()

    @sub_bot.event
    async def on_guild_join(guild):
        _is_owner = (str(guild.id) == ICEA_GUILD_ID)
        register_server(guild.id, guild.name, is_owner_server=_is_owner)
        print(f"[INFO] Sub-bot joined guild: {guild.name} ({guild.id})")
        try:
            ch = guild.system_channel or next(
                (c for c in guild.text_channels
                 if c.permissions_for(guild.me).send_messages), None)
            if ch:
                await ch.send(
                    "\U0001f389 **Entertainment Bot has joined!**\n\n"
                    "Available features:\n"
                    "\U0001f3ad Quiz \u2022 \U0001f422 Turtle Soup \u2022 \U0001f43a Werewolf \u2022 \U0001f4c8 Stock \u2022 \U0001f3c7 Horse Racing\n"
                    "\U0001f3f0 Siege \u2022 \u2694\ufe0f WW1 \u2022 \U0001f3ae Galgame \u2022 \U0001f3a8 Text-to-Image\n"
                    "\U0001f4ca Poll \u2022 \U0001f4c5 Meeting/Schedule \u2022 \U0001f4b0 Economy\n\n"
                    "Type `/` to get started!"
                )
        except Exception:
            pass

    @sub_bot.event
    async def on_guild_remove(guild):
        unregister_server(guild.id)
        print(f"[INFO] Sub-bot left guild: {guild.name} ({guild.id})")

    @sub_bot.event
    async def on_message(message):
        """Sub-bot on_message: only handle turtle soup."""
        if message.author.bot or not message.guild:
            return
        try:
            handled = await _handle_turtle_soup_message(message)
        except Exception as e:
            print(f"[WARN] Sub-bot turtle soup on_message error: {e}")



if __name__ == "__main__":
    main()

