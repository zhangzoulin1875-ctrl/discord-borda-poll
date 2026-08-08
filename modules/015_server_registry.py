# -*- coding: utf-8 -*-
"""
伺服器註冊與分級系統
追蹤所有安裝了機器人的伺服器，依 tier 控制功能存取權限。

Tier 分級：
  - owner:   ICEA 主伺服器（全部功能，Owner 承擔 Token 費用）
  - premium: 白名單伺服器（可使用指定功能）
  - guest:   預設等級（娛樂 + 基本功能）

功能分類：
  - ai_free:        不消耗 AI Token 的功能（投票、會議、排程、經濟）
  - entertainment:  娛樂功能 — 跨伺服器開放（搶答、海龜湯、狼人殺、文生圖、股市、賽馬、Galgame、攻城戰、WW1）
  - icea_only:      僅限 ICEA 主伺服器 — AI 聊天、行政功能（提案/入盟、晨報、國家分析、性格分析、計票）
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

GMT8 = timezone(timedelta(hours=8))

# ── 伺服器註冊資料 ──
_server_registry = {}
_SERVER_REGISTRY_FILE = "data/server_registry.json"

# ── 功能分類 ──
# 不消耗 AI Token 的指令群組（所有伺服器可用）
AI_FREE_COMMANDS = {
    "poll",        # 投票
    "meeting",     # 會議
    "schedule",    # 排程
    "system",      # 系統管理
    "econ",        # 經濟系統（無 AI 消耗）
}

# 娛樂功能 — 跨伺服器開放（會消耗 AI Token 但頻率可控，不像聊天每訊息都燒）
ENTERTAINMENT_COMMANDS = {
    "quiz",           # AI 搶答
    "turtle_soup",    # AI 海龜湯
    "werewolf",       # AI 狼人殺
    "stock",          # AI 股市
    "horse",          # AI 賽馬
    "galgame",        # AI Galgame
    "siege",          # 攻城戰
    "cyber_war",      # WW1 賽博一戰（跨伺服器千人戰場）
    "draw",           # 文生圖
}

# 僅限 ICEA 主伺服器的功能（AI 聊天 + 行政功能）
ICEA_ONLY_COMMANDS = {
    "chat",           # AI 聊天 + 聊天室（高頻 per-message Token 消耗）
    "chat_room",       # AI 聊天室管理
    "proposal",       # AI 提案/入盟分析
    "briefing",       # AI 晨報
    "nation",         # AI 國家分析
    "analyze",        # AI 性格分析
    "member_nation",  # AI 會員國分析
    "awareness",      # AI 意識形態分析
    "tally",          # AI 計票
    "data_library",   # 資料庫管理
    "storm",          # HOI4（狀態管理複雜，暫不開放）
}


def _now_iso():
    return datetime.now(GMT8).isoformat()


def load_server_registry():
    """從本地檔案載入伺服器註冊資料。"""
    global _server_registry
    try:
        if os.path.exists(_SERVER_REGISTRY_FILE):
            with open(_SERVER_REGISTRY_FILE, "r", encoding="utf-8") as f:
                _server_registry = json.load(f)
            print(f"📋 伺服器註冊：已載入 {len(_server_registry)} 個伺服器")
    except Exception as e:
        print(f"⚠️ 伺服器註冊載入失敗：{e}")
        _server_registry = {}


def save_server_registry():
    """儲存伺服器註冊資料到本地檔案。"""
    try:
        os.makedirs("data", exist_ok=True)
        with open(_SERVER_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(_server_registry, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 伺服器註冊儲存失敗：{e}")


def register_server(guild_id: int, guild_name: str, owner_id: int = None, is_owner_server: bool = False):
    """註冊或更新伺服器資訊。新伺服器預設為 guest 等級。"""
    gid = str(guild_id)
    if gid not in _server_registry:
        tier = "owner" if is_owner_server else "guest"
        _server_registry[gid] = {
            "name": guild_name,
            "tier": tier,
            "joined_at": _now_iso(),
            "ai_enabled": is_owner_server,
            "ww1_channel_id": None,
            "ww1_panel_message_id": None,
            "member_count": 0,
        }
        print(f"📋 新伺服器註冊：{guild_name} ({guild_id}) → tier={tier}")
    else:
        _server_registry[gid]["name"] = guild_name
        if is_owner_server and _server_registry[gid]["tier"] != "owner":
            _server_registry[gid]["tier"] = "owner"
            _server_registry[gid]["ai_enabled"] = True
    save_server_registry()


def unregister_server(guild_id: int):
    """伺服器移除機器人時呼叫。保留歷史紀錄但標記為離開。"""
    gid = str(guild_id)
    if gid in _server_registry:
        _server_registry[gid]["left_at"] = _now_iso()
        _server_registry[gid]["ww1_channel_id"] = None
        _server_registry[gid]["ww1_panel_message_id"] = None
        save_server_registry()
        print(f"📋 伺服器離開：{_server_registry[gid].get('name', '?')} ({guild_id})")


def get_server_tier(guild_id) -> str:
    """取得伺服器等級。回傳 'owner' / 'premium' / 'guest'。"""
    gid = str(guild_id) if guild_id else None
    if not gid or gid not in _server_registry:
        return "guest"
    return _server_registry[gid].get("tier", "guest")


def is_ai_chat_enabled(guild_id) -> bool:
    """檢查伺服器是否啟用 AI 聊天功能（僅 owner）。"""
    tier = get_server_tier(guild_id)
    return tier == "owner"


def is_entertainment_enabled(guild_id) -> bool:
    """檢查伺服器是否啟用娛樂功能（owner + guest + premium）。"""
    # 娛樂功能所有伺服器都可使用
    return True


def is_ww1_server(guild_id) -> bool:
    """檢查伺服器是否已設定 WW1 頻道。"""
    gid = str(guild_id) if guild_id else None
    if not gid or gid not in _server_registry:
        return False
    return _server_registry[gid].get("ww1_channel_id") is not None


def set_ww1_channel(guild_id: int, channel_id: int):
    """設定伺服器的 WW1 頻道。"""
    gid = str(guild_id)
    if gid not in _server_registry:
        register_server(guild_id, "(unknown)")
    _server_registry[gid]["ww1_channel_id"] = channel_id
    save_server_registry()


def get_ww1_channel(guild_id) -> int:
    """取得伺服器的 WW1 頻道 ID。"""
    gid = str(guild_id) if guild_id else None
    if not gid or gid not in _server_registry:
        return None
    return _server_registry[gid].get("ww1_channel_id")


def set_ww1_panel_message(guild_id: int, message_id: int):
    """記錄伺服器 WW1 面板訊息 ID。"""
    gid = str(guild_id)
    if gid in _server_registry:
        _server_registry[gid]["ww1_panel_message_id"] = message_id
        save_server_registry()


def get_ww1_panel_message(guild_id) -> int:
    """取得伺服器 WW1 面板訊息 ID。"""
    gid = str(guild_id) if guild_id else None
    if not gid or gid not in _server_registry:
        return None
    return _server_registry[gid].get("ww1_panel_message_id")


def get_all_ww1_servers() -> list:
    """取得所有已設定 WW1 頻道的伺服器列表。回傳 [(guild_id, channel_id), ...]"""
    result = []
    for gid, info in _server_registry.items():
        ch_id = info.get("ww1_channel_id")
        if ch_id is not None and not info.get("left_at"):
            result.append((int(gid), int(ch_id)))
    return result


def set_server_tier(guild_id: int, tier: str, ai_enabled: bool = None):
    """設定伺服器等級（僅管理員可透過 dashboard 呼叫）。"""
    gid = str(guild_id)
    if gid not in _server_registry:
        register_server(guild_id, "(unknown)")
    _server_registry[gid]["tier"] = tier
    if ai_enabled is not None:
        _server_registry[gid]["ai_enabled"] = ai_enabled
    else:
        _server_registry[gid]["ai_enabled"] = (tier == "owner")
    save_server_registry()


def check_command_access(guild_id, command_group: str) -> tuple:
    """
    檢查伺服器是否有權使用某指令群組。
    回傳 (allowed: bool, reason: str)。
    """
    if guild_id is None:
        return True, ""

    tier = get_server_tier(guild_id)

    # Owner 伺服器：全部功能
    if tier == "owner":
        return True, ""

    # ── 以下為非 owner 伺服器（guest / premium）──

    # AI-free 功能：所有伺服器可用
    if command_group in AI_FREE_COMMANDS:
        return True, ""

    # 娛樂功能：所有伺服器可用（跨伺服器開放）
    if command_group in ENTERTAINMENT_COMMANDS:
        return True, ""

    # ICEA 限定功能：僅 owner 伺服器
    if command_group in ICEA_ONLY_COMMANDS:
        return False, "🔒 此功能僅限 ICEA 主伺服器使用。\n娛樂功能（搶答、海龜湯、狼人殺、文生圖、股市、賽馬、Galgame、攻城戰、WW1）可正常使用！"

    # 預設放行
    return True, ""


def get_registry_summary() -> dict:
    """取得伺服器註冊摘要（供 dashboard 顯示）。"""
    servers = []
    for gid, info in _server_registry.items():
        servers.append({
            "guild_id": gid,
            "name": info.get("name", "?"),
            "tier": info.get("tier", "guest"),
            "ai_enabled": info.get("ai_enabled", False),
            "ww1_channel_id": info.get("ww1_channel_id"),
            "member_count": info.get("member_count", 0),
            "joined_at": info.get("joined_at", ""),
            "left_at": info.get("left_at"),
        })
    return {
        "total": len(servers),
        "active": len([s for s in servers if not s.get("left_at")]),
        "owner": len([s for s in servers if s["tier"] == "owner"]),
        "guest": len([s for s in servers if s["tier"] == "guest"]),
        "ww1_servers": len([s for s in servers if s.get("ww1_channel_id") and not s.get("left_at")]),
        "servers": servers,
    }
