# ═════════════════════════════════════════════════════════════════
# Module: 180_cyber_war (賽博一戰)
# 持久化面板 WWI 策略遊戲，每局3天，全服自動參戰，軍官→小隊長→士兵指揮鏈
# AI 裁判每回合根據雙方行動判定戰況推進，敗方淘汰即提前結束
# ═════════════════════════════════════════════════════════════════

import random as _cw_random
import time as _cw_time
from datetime import datetime, timedelta, timezone

CYBER_WAR_FILE = os.path.join(DATA_DIR, "cyber_war_data.json")

# ── 常量 ──
GAME_DURATION_DAYS = 3
TURN_INTERVAL_HOURS = 8       # 每回合 8 小時 → 3天共9回合
DEPOSIT_PER_PLAYER = 100     # 每人押金（琉璃幣）
OFFICERS_PER_SIDE = 2
SQUAD_LEADERS_PER_SIDE = 5
ARTILLERY_COST = 500          # 每次砲擊/空襲花費
MAX_ARTILLERY_PER_TURN = 2   # 每回合每方最多呼叫2次
PROGRESS_WIN_THRESHOLD = 100 # 進度達100%即勝利
MORALE_DEFEAT_THRESHOLD = 0  # 士氣降至0即敗北

# ── 陣營 & 戰場 ──
# 德國固定為一方，對手隨機
_FACTION_OPPONENTS = [
    ("英國", "UK", "🇬🇧"),
    ("法國", "FR", "🇫🇷"),
    ("美國", "US", "🇺🇸"),
    ("俄國", "RU", "🇷🇺"),
    ("義大利", "IT", "🇮🇹"),
    ("日本", "JP", "🇯🇵"),
    ("鄂圖曼帝國", "OT", "🇹🇷"),
    ("奧匈帝國", "AH", "🇦🇹"),
]
_FACTION_GERMANY = ("德國", "DE", "🇩🇪")

_BATTLEFIELDS = [
    "索姆河", "馬恩河", "凡爾登", "伊普爾", "坦能堡",
    "加里波利", "卡波雷托", "帕森達勒", "康布雷", "聖米耶爾",
    "加札", "洛林", "亞眠", "香檳", "戈里斯",
]

# ── 設定 & 狀態 ──
_cyber_war_settings = {
    "channel_id": None,           # 持久面板頻道
    "panel_message_id": None,    # 面板訊息 ID
    "deposit": DEPOSIT_PER_PLAYER,
    "turn_interval_hours": TURN_INTERVAL_HOURS,
}

_cyber_war_state = {
    "active": False,
    "game_id": 0,
    "start_time": None,         # ISO string
    "end_time": None,            # ISO string
    "battlefield": "",
    "factions": {},              # {"A": {...}, "B": {...}}
    "turn": 0,
    "next_turn_time": None,      # ISO string
    "phase": "idle",             # idle / command / action / processing
    "actions": {},               # {turn: {"A": {uid: action_str}, "B": {uid: action_str}}}
    "orders": {},                # {turn: {"A": {officer_uid: {sl_uid: order}}, "B": ...}}
    "artillery": {},             # {turn: {"A": [{officer_uid, target, cost}], "B": [...]}}
    "winner": None,              # "A" / "B" / None
    "prize_multiplier": 0,
    "total_deposits": 0,
    "settlement_done": False,
    "turn_summary": "",          # AI 產生的本回合戰報
}

_ROLE_NAMES = {"officer": "軍官", "squad_leader": "小隊長", "soldier": "士兵"}

# ── 持久化 ──
def save_cyber_war():
    try:
        _save_json_file(CYBER_WAR_FILE, {
            "settings": _cyber_war_settings,
            "state": _cyber_war_state,
        }, indent=2)
        try:
            asyncio.ensure_future(_immediate_drive_upload("cyber_war_data.json"))
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ 賽博一戰存檔失敗：{e}")

def load_cyber_war():
    global _cyber_war_settings, _cyber_war_state
    try:
        if os.path.exists(CYBER_WAR_FILE):
            with open(CYBER_WAR_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            if "settings" in data:
                for k, v in data["settings"].items():
                    _cyber_war_settings[k] = v
            if "state" in data:
                _cyber_war_state.update(data["state"])
            print(f"⚔️ 賽博一戰資料已載入（active={_cyber_war_state.get('active')}）")
    except Exception as e:
        print(f"⚠️ 載入賽博一戰資料失敗：{e}")

# ── 時間工具 ──
_TZ = timezone(timedelta(hours=8))

def _now_iso():
    return datetime.now(_TZ).isoformat()

def _from_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _fmt_dt(s):
    dt = _from_iso(s)
    if not dt:
        return "—"
    return dt.strftime("%m/%d %H:%M")

def _time_remaining(end_iso):
    dt = _from_iso(end_iso)
    if not dt:
        return "—"
    remaining = dt - datetime.now(_TZ)
    total_sec = int(remaining.total_seconds())
    if total_sec <= 0:
        return "已結束"
    days, rem = divmod(total_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    parts.append(f"{hours}小時")
    if days == 0:
        parts.append(f"{mins}分")
    return "".join(parts)

# ── 遊戲初始化 ──
async def _start_new_game(guild):
    """開始新一局賽博一戰，自動分配陣營、角色、戰場。"""
    global _cyber_war_state

    # 取得所有成員（排除 bot）
    members = [m for m in guild.members if not m.bot]
    if len(members) < 4:
        return False, "伺服器成員太少（至少需要4人），無法開始遊戲。"

    # 隨機戰場
    battlefield = _cw_random.choice(_BATTLEFIELDS)

    # 隨機對手（德國 vs ?）
    opponent = _cw_random.choice(_FACTION_OPPONENTS)

    # 隨機倍率 50~75
    multiplier = _cw_random.randint(50, 75)

    # 分配陣營 — 隨機打散，盡量平衡人數
    _cw_random.shuffle(members)
    mid = len(members) // 2
    side_a_members = members[:mid]
    side_b_members = members[mid:]

    # 活躍度排序：online > idle/dnd > offline
    _status_order = {"online": 0, "idle": 1, "dnd": 2, "offline": 3}
    def _sort_key(m):
        return _status_order.get(str(m.status), 3)
    side_a_members.sort(key=_sort_key)
    side_b_members.sort(key=_sort_key)

    def _build_faction(faction_key, faction_info, member_list):
        officers = []
        squad_leaders = []
        soldiers = []
        n = len(member_list)
        n_officers = min(OFFICERS_PER_SIDE, n)
        n_sl = min(SQUAD_LEADERS_PER_SIDE, n - n_officers)
        for i in range(n_officers):
            m = member_list[i]
            officers.append({"id": str(m.id), "name": m.display_name})
        for i in range(n_officers, n_officers + n_sl):
            m = member_list[i]
            sl_officer = officers[i % len(officers)]["id"] if officers else ""
            squad_leaders.append({"id": str(m.id), "name": m.display_name, "officer_id": sl_officer})
        for i in range(n_officers + n_sl, n):
            m = member_list[i]
            sl = squad_leaders[(i - n_officers) % len(squad_leaders)]["id"] if squad_leaders else ""
            soldiers.append({"id": str(m.id), "name": m.display_name, "squad_leader_id": sl})

        return {
            "name": faction_info[0],
            "code": faction_info[1],
            "flag": faction_info[2],
            "officers": officers,
            "squad_leaders": squad_leaders,
            "soldiers": soldiers,
            "progress": 0,
            "morale": 100,
            "supplies": 100,
            "casualties": 0,
            "defeated": False,
        }

    fac_a = _build_faction("A", _FACTION_GERMANY, side_a_members)
    fac_b = _build_faction("B", opponent, side_b_members)

    # 押金改為自由投注 — 不自動扣款，玩家自行下注
    now = datetime.now(_TZ)
    end = now + timedelta(days=GAME_DURATION_DAYS)
    next_turn = now + timedelta(hours=TURN_INTERVAL_HOURS)

    _cyber_war_state = {
        "active": True,
        "game_id": _cyber_war_state.get("game_id", 0) + 1,
        "start_time": now.isoformat(),
        "end_time": end.isoformat(),
        "battlefield": battlefield,
        "factions": {"A": fac_a, "B": fac_b},
        "turn": 1,
        "next_turn_time": next_turn.isoformat(),
        "phase": "command",
        "actions": {},
        "orders": {},
        "artillery": {},
        "winner": None,
        "prize_multiplier": multiplier,
        "deposits": {},             # {uid: {"amount": int, "name": str}}
        "deposits_locked": False,   # 第一回合後鎖定
        "total_deposits": 0,
        "settlement_done": False,
        "turn_summary": "",
    }
    save_cyber_war()
    return True, f"✅ 第{_cyber_war_state['game_id']}局賽博一戰已開始！\n戰場：{battlefield}\n陣營：{fac_a['flag']} {fac_a['name']} vs {fac_b['flag']} {fac_b['name']}\n參戰人數：{len(all_players)}\n押金：自由投注（第一回合後鎖定）\n倍率：{multiplier}x\n回合間隔：{TURN_INTERVAL_HOURS}小時\n結束時間：{_fmt_dt(end.isoformat())}"

# ── 角色查詢 ──
def _get_player_role(uid_str: str):
    """回傳 (faction_key, role, faction_data) 或 None。role = 'officer'/'squad_leader'/'soldier'"""
    for fkey, fac in _cyber_war_state.get("factions", {}).items():
        for o in fac.get("officers", []):
            if o["id"] == uid_str:
                return fkey, "officer", fac
        for sl in fac.get("squad_leaders", []):
            if sl["id"] == uid_str:
                return fkey, "squad_leader", fac
        for s in fac.get("soldiers", []):
            if s["id"] == uid_str:
                return fkey, "soldier", fac
    return None

def _get_subordinates(faction_key: str, uid_str: str, role: str):
    """取得下屬列表。"""
    fac = _cyber_war_state["factions"].get(faction_key, {})
    if role == "officer":
        return [sl for sl in fac.get("squad_leaders", []) if sl.get("officer_id") == uid_str]
    elif role == "squad_leader":
        return [s for s in fac.get("soldiers", []) if s.get("squad_leader_id") == uid_str]
    return []

def _get_superior_order(faction_key: str, uid_str: str, role: str, turn: int):
    """取得上級給的指令。"""
    orders = _cyber_war_state.get("orders", {}).get(str(turn), {}).get(faction_key, {})
    if role == "squad_leader":
        for officer_uid, sl_orders in orders.items():
            if uid_str in sl_orders:
                return sl_orders[uid_str], _get_officer_name(faction_key, officer_uid)
    elif role == "soldier":
        # 找該士兵所屬小隊長的指令
        fac = _cyber_war_state["factions"].get(faction_key, {})
        soldier = next((s for s in fac.get("soldiers", []) if s["id"] == uid_str), None)
        if soldier:
            sl_id = soldier.get("squad_leader_id", "")
            for officer_uid, sl_orders in orders.items():
                if sl_id in sl_orders and isinstance(sl_orders[sl_id], dict) and uid_str in sl_orders[sl_id]:
                    return sl_orders[sl_id][uid_str], _get_sl_name(faction_key, sl_id)
    return None, ""

def _get_officer_name(faction_key, officer_uid):
    fac = _cyber_war_state["factions"].get(faction_key, {})
    o = next((x for x in fac.get("officers", []) if x["id"] == officer_uid), None)
    return o["name"] if o else "軍官"

def _get_sl_name(faction_key, sl_uid):
    fac = _cyber_war_state["factions"].get(faction_key, {})
    sl = next((x for x in fac.get("squad_leaders", []) if x["id"] == sl_uid), None)
    return sl["name"] if sl else "小隊長"

# ── 面板 Embed ──
def _build_war_embed():
    s = _cyber_war_state
    if not s.get("active") and not s.get("winner"):
        return discord.Embed(
            title="⚔️ 賽博一戰 — 等待開戰",
            description="目前沒有進行中的戰局。\n使用 `/cyber_war start` 開始新一局。",
            color=discord.Color.dark_gray(),
        )

    fac_a = s.get("factions", {}).get("A", {})
    fac_b = s.get("factions", {}).get("B", {})
    winner = s.get("winner")

    if winner:
        wf = s["factions"][winner]
        prize = s.get("total_deposits", 0) * s.get("prize_multiplier", 0)
        total_dep = sum(d.get("amount", 0) for d in s.get("deposits", {}).values())
        embed = discord.Embed(
            title=f"⚔️ 賽博一戰 — 戰局結束 (第{s['game_id']}局)",
            description=(
                f"🏆 **勝方：{wf['flag']} {wf['name']}**\n"
                f"💰 總押金池：{total_dep:,} {currency_name()}\n"
                f"📈 倍率：{s.get('prize_multiplier', 0)}x → 獎金總額：{prize:,} {currency_name()}\n"
                f"📋 戰報：{s.get('turn_summary', '—')[:200]}"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="戰場", value=s.get("battlefield", "—"), inline=True)
        embed.add_field(name="回合數", value=f"第{s.get('turn', 0)}回合", inline=True)
        embed.add_field(name="持續時間", value=f"{_fmt_dt(s.get('start_time'))} → {_fmt_dt(s.get('end_time'))}", inline=False)
        return embed

    # 進行中
    color = discord.Color.red() if s.get("phase") == "processing" else discord.Color.blue()
    embed = discord.Embed(
        title=f"⚔️ 賽博一戰 — 第{s.get('turn', 0)}回合 (第{s.get('game_id', 0)}局)",
        description=(
            f"🗺️ 戰場：**{s.get('battlefield', '—')}**\n"
            f"⏱️ 下回合結算：{_time_remaining(s.get('next_turn_time'))}\n"
            f"🏁 遊戲剩餘：{_time_remaining(s.get('end_time'))}\n"
            f"📋 本回合階段：**{'AI結算中' if s.get('phase') == 'processing' else '行動中'}**\n"
            f"💰 獎池：{sum(d.get('amount', 0) for d in s.get('deposits', {}).values()):,} {currency_name()} ×{s.get('prize_multiplier', 0)}\n"
            f"🔒 押金：**{'已鎖定' if s.get('deposits_locked') else '可自由投注（限第1回合）'}**"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )

    def _faction_field(key, label):
        f = s["factions"][key]
        defeated = " 💀 已敗" if f.get("defeated") else ""
        n_off = len(f.get("officers", []))
        n_sl = len(f.get("squad_leaders", []))
        n_sol = len(f.get("soldiers", []))
        # 進度條
        prog = f.get("progress", 0)
        bar_len = 20
        filled = int(prog / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        morale = f.get("morale", 100)
        supplies = f.get("supplies", 100)
        # 砲擊次數
        turn = str(s.get("turn", 0))
        arty = len(s.get("artillery", {}).get(turn, {}).get(key, []))
        return (
            f"{f['flag']} **{f['name']}**{defeated}\n"
            f"```\n推進 [{bar}] {prog}%\n士氣 ❤️ {morale}  補給 📦 {supplies}\n"
            f"軍官 {n_off} | 小隊長 {n_sl} | 士兵 {n_sol}\n"
            f"本回合砲擊/空襲：{arty}/{MAX_ARTILLERY_PER_TURN}```"
        )

    embed.add_field(name="陣營 A", value=_faction_field("A", "A"), inline=True)
    embed.add_field(name="陣營 B", value=_faction_field("B", "B"), inline=True)

    # 本回合戰報（如果有）
    summary = s.get("turn_summary", "")
    if summary:
        embed.add_field(name="📰 上一回合戰報", value=summary[:1024], inline=False)

    embed.set_footer(text="點擊下方按鈕進行操作 | 所有操作均為私人不公開")
    return embed

# ── 面板按鈕 ──
class CyberWarPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="分配指令", style=discord.ButtonStyle.primary, emoji="📋", custom_id="cw_assign_btn")
    async def assign_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        fkey, role, fac = info
        turn = _cyber_war_state.get("turn", 0)

        lines = [f"🏴 你的陣營：{fac['flag']} {fac['name']}"]
        _rn = {"officer": "\u8ecd\u5b98", "squad_leader": "\u5c0f\u968a\u9577", "soldier": "\u58eb\u5175"}.get(role, "?")
        lines.append(f"\U0001f396\ufe0f \u4f60\u7684\u89d2\u8272\uff1a**{_rn}**")
        lines.append(f"📅 第 {turn} 回合\n")

        if role == "officer":
            subs = _get_subordinates(fkey, uid, role)
            lines.append("👥 你的小隊長：")
            for sl in subs:
                lines.append(f"  • {sl['name']}")
            # 查看自己下達的指令
            orders = _cyber_war_state.get("orders", {}).get(str(turn), {}).get(fkey, {})
            my_orders = orders.get(uid, {})
            if my_orders:
                lines.append("\n📝 你已下達的指令：")
                for sl_id, order in my_orders.items():
                    sl_name = _get_sl_name(fkey, sl_id)
                    lines.append(f"  → {sl_name}：{order[:100]}")
            else:
                lines.append("\n⚠️ 你尚未下達任何指令。請使用「🎖️ 管理行動」按鈕分配指令。")
        elif role == "squad_leader":
            subs = _get_subordinates(fkey, uid, role)
            lines.append("👥 你的士兵：")
            for s in subs:
                lines.append(f"  • {s['name']}")
            order, superior = _get_superior_order(fkey, uid, role, turn)
            if order:
                lines.append(f"\n📢 軍官 {superior} 的指令：{order[:200]}")
            else:
                lines.append("\n⚠️ 尚未收到軍官指令。")
            # 自己下達給士兵的指令
            orders = _cyber_war_state.get("orders", {}).get(str(turn), {}).get(fkey, {})
            for o_uid, sl_orders in orders.items():
                if isinstance(sl_orders, dict) and uid in sl_orders and isinstance(sl_orders[uid], dict):
                    for s_id, s_order in sl_orders[uid].items():
                        s_name = next((x["name"] for x in fac.get("soldiers", []) if x["id"] == s_id), s_id)
                        lines.append(f"  → 士兵 {s_name}：{s_order[:80]}")
        elif role == "soldier":
            order, superior = _get_superior_order(fkey, uid, role, turn)
            if order:
                lines.append(f"\n📢 小隊長 {superior} 的指令：{order[:200]}")
            else:
                lines.append("\n⚠️ 尚未收到小隊長指令。")
            my_action = _cyber_war_state.get("actions", {}).get(str(turn), {}).get(fkey, {}).get(uid)
            if my_action:
                lines.append(f"\n✅ 你已提交的行動：{my_action[:200]}")
            else:
                lines.append("\n⚠️ 你尚未提交行動。請使用「📝 行動指令」按鈕提交。")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="呼叫砲擊", style=discord.ButtonStyle.danger, emoji="💥", custom_id="cw_artillery_btn")
    async def artillery_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        fkey, role, fac = info
        if role != "officer":
            await interaction.response.send_message("❌ 只有軍官可以呼叫砲擊/空襲。", ephemeral=True)
            return

        turn = str(_cyber_war_state.get("turn", 0))
        arty = _cyber_war_state.get("artillery", {}).setdefault(turn, {}).setdefault(fkey, [])
        if len(arty) >= MAX_ARTILLERY_PER_TURN:
            await interaction.response.send_message(f"❌ 本回合砲擊/空襲已達上限（{MAX_ARTILLERY_PER_TURN}次）。", ephemeral=True)
            return

        bal = get_balance(uid)
        cost = ARTILLERY_COST
        if bal < cost:
            await interaction.response.send_message(f"❌ 琉璃幣不足。呼叫砲擊需要 {cost} {currency_name()}，你目前只有 {bal}。", ephemeral=True)
            return

        await interaction.response.send_modal(CyberWarArtilleryModal(uid, fkey, interaction.user.display_name, turn))

    @discord.ui.button(label="管理行動", style=discord.ButtonStyle.secondary, emoji="🎖️", custom_id="cw_manage_btn")
    async def manage_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        fkey, role, fac = info
        if role not in ("officer", "squad_leader"):
            await interaction.response.send_message("❌ 只有軍官和小隊長可以使用「管理行動」。", ephemeral=True)
            return

        # 顯示即時戰況 + 下屬列表 → 選擇下屬 → Modal 輸入指令
        subs = _get_subordinates(fkey, uid, role)
        if not subs:
            await interaction.response.send_message("❌ 你沒有下屬可以分配指令。", ephemeral=True)
            return

        turn = _cyber_war_state.get("turn", 0)
        fac_a = _cyber_war_state["factions"].get(fkey, {})
        fac_b = _cyber_war_state["factions"].get("B" if fkey == "A" else "A", {})

        lines = [
            f"🎖️ 你是 {fac['flag']} {fac['name']} 的 **{'軍官' if role == 'officer' else '小隊長'}**",
            f"📅 第 {turn} 回合",
            f"\n📊 即時戰況：",
            f"  我方推進：{fac_a.get('progress', 0)}% | 士氣：{fac_a.get('morale', 100)}",
            f"  敵方推進：{fac_b.get('progress', 0)}% | 士氣：{fac_b.get('morale', 100)}",
            f"\n👥 你的下屬："
        ]
        # 使用 Select 讓使用者選擇要分配指令的對象
        options = []
        for sub in subs[:25]:  # Discord select max 25
            options.append(discord.SelectOption(
                label=sub["name"],
                value=sub["id"],
                description=f"ID: {sub['id'][:8]}"
            ))
        view = _OrderTargetSelect(uid, fkey, role, turn, options)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)

    @discord.ui.button(label="行動指令", style=discord.ButtonStyle.success, emoji="📝", custom_id="cw_action_btn")
    async def action_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        fkey, role, fac = info
        turn = _cyber_war_state.get("turn", 0)

        # 顯示上級指令
        order, superior = _get_superior_order(fkey, uid, role, turn)
        order_text = f"📢 {superior}的指令：{order[:300]}" if order else "⚠️ 尚未收到上級指令，你可以自由行動。"

        await interaction.response.send_modal(CyberWarActionModal(uid, fkey, interaction.user.display_name, turn, order_text))

    @discord.ui.button(label="下注", style=discord.ButtonStyle.secondary, emoji="💰", custom_id="cw_bet_btn")
    async def bet_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        # 第一回合後鎖定
        if _cyber_war_state.get("deposits_locked"):
            dep = _cyber_war_state.get("deposits", {}).get(uid, {}).get("amount", 0)
            await interaction.response.send_message(
                f"🔒 押金已鎖定，無法加碼或撤回。\n你目前的押金：{dep} {currency_name()}",
                ephemeral=True,
            )
            return
        fkey, role, fac = info
        current_dep = _cyber_war_state.get("deposits", {}).get(uid, {}).get("amount", 0)
        bal = get_balance(uid)
        await interaction.response.send_modal(
            CyberWarBetModal(uid, interaction.user.display_name, fkey, current_dep, bal)
        )

# ── 下屬選擇 Select ──
class _OrderTargetSelect(discord.ui.View):
    def __init__(self, commander_uid, fkey, role, turn, options):
        super().__init__(timeout=120)
        self.commander_uid = commander_uid
        self.fkey = fkey
        self.role = role
        self.turn = turn

        select = discord.ui.Select(
            placeholder="選擇要分配指令的下屬...",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        target_id = interaction.data["select_values"][0]
        fac = _cyber_war_state["factions"].get(self.fkey, {})
        target_name = "?"
        if self.role == "officer":
            sl = next((x for x in fac.get("squad_leaders", []) if x["id"] == target_id), None)
            if sl:
                target_name = sl["name"]
        else:
            s = next((x for x in fac.get("soldiers", []) if x["id"] == target_id), None)
            if s:
                target_name = s["name"]
        await interaction.response.send_modal(
            CyberWarOrderModal(self.commander_uid, self.fkey, self.role, self.turn, target_id, target_name)
        )

# ── 下注 Modal ──
class CyberWarBetModal(discord.ui.Modal, title="💰 下注 / 追加 / 撤回"):
    bet_amount = discord.ui.TextInput(
        label="金額（正數=追加，負數=撤回）",
        style=discord.TextStyle.short,
        placeholder="例如：500（追加500）或 -200（撤回200）",
        required=True,
        max_length=10,
    )

    def __init__(self, uid, user_name, fkey, current_dep, balance):
        self.uid = uid
        self.user_name = user_name
        self.fkey = fkey
        self._current_dep = current_dep
        self._balance = balance
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.bet_amount.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ 請輸入有效數字。", ephemeral=True)
            return
        if amount == 0:
            await interaction.response.send_message("❌ 金額不能為0。", ephemeral=True)
            return

        deposits = _cyber_war_state.setdefault("deposits", {})
        current = deposits.get(self.uid, {}).get("amount", 0)

        if amount > 0:
            # 追加
            if amount > self._balance:
                await interaction.response.send_message(
                    f"❌ 餘額不足。你需要 {amount}，目前只有 {self._balance} {currency_name()}。",
                    ephemeral=True,
                )
                return
            add_balance(self.uid, -amount, self.user_name)
            new_dep = current + amount
            deposits[self.uid] = {"amount": new_dep, "name": self.user_name}
            _cyber_war_state["total_deposits"] = sum(d.get("amount", 0) for d in deposits.values())
            save_cyber_war()
            await interaction.response.send_message(
                f"✅ 已追加下注 {amount} {currency_name()}。\n你目前的押金：{new_dep} {currency_name()}",
                ephemeral=True,
            )
        else:
            # 撤回
            withdraw = abs(amount)
            if withdraw > current:
                await interaction.response.send_message(
                    f"❌ 撤回金額超過你的押金。你目前的押金：{current} {currency_name()}。",
                    ephemeral=True,
                )
                return
            add_balance(self.uid, withdraw, self.user_name)
            new_dep = current - withdraw
            if new_dep > 0:
                deposits[self.uid] = {"amount": new_dep, "name": self.user_name}
            else:
                deposits.pop(self.uid, None)
            _cyber_war_state["total_deposits"] = sum(d.get("amount", 0) for d in deposits.values())
            save_cyber_war()
            await interaction.response.send_message(
                f"✅ 已撤回 {withdraw} {currency_name()}。\n你目前的押金：{new_dep} {currency_name()}",
                ephemeral=True,
            )

# ── 指令輸入 Modal ──
class CyberWarOrderModal(discord.ui.Modal, title="🎖️ 分配指令"):
    order_text = discord.ui.TextInput(
        label="指令內容",
        style=discord.TextStyle.paragraph,
        placeholder="根據即時戰況，向下屬下達具體指令...",
        required=True,
        max_length=500,
    )

    def __init__(self, commander_uid, fkey, role, turn, target_id, target_name):
        self.commander_uid = commander_uid
        self.fkey = fkey
        self.role = role
        self.turn = turn
        self.target_id = target_id
        self.target_name = target_name
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        order = self.order_text.value.strip()
        turn_str = str(self.turn)
        orders = _cyber_war_state.setdefault("orders", {})
        turn_orders = orders.setdefault(turn_str, {})
        fac_orders = turn_orders.setdefault(self.fkey, {})

        if self.role == "officer":
            # officer → squad_leader
            officer_orders = fac_orders.setdefault(self.commander_uid, {})
            officer_orders[self.target_id] = order
        else:
            # squad_leader → soldier
            # 需要找到該 sl 的 officer，結構: officer_uid → {sl_uid: {soldier_uid: order}}
            # 但為了簡化，直接用 sl_uid 作 key 存
            sl_orders = fac_orders.setdefault(self.commander_uid, {})
            if not isinstance(sl_orders, dict):
                sl_orders = {}
                fac_orders[self.commander_uid] = sl_orders
            sl_orders[self.target_id] = order

        save_cyber_war()
        await interaction.response.send_message(
            f"✅ 已向 **{self.target_name}** 下達指令：\n> {order[:200]}",
            ephemeral=True,
        )

# ── 砲擊輸入 Modal ──
class CyberWarArtilleryModal(discord.ui.Modal, title="💥 呼叫砲擊/空襲"):
    target_text = discord.ui.TextInput(
        label="攻擊目標/方向",
        style=discord.TextStyle.short,
        placeholder="例如：敵方右翼陣地 / 敵方補給線 / 敵方指揮所",
        required=True,
        max_length=200,
    )
    strategy_text = discord.ui.TextInput(
        label="戰術描述（可選）",
        style=discord.TextStyle.paragraph,
        placeholder="描述你的砲擊/空襲策略...",
        required=False,
        max_length=300,
    )

    def __init__(self, officer_uid, fkey, officer_name, turn_str):
        self.officer_uid = officer_uid
        self.fkey = fkey
        self.officer_name = officer_name
        self.turn_str = turn_str
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        target = self.target_text.value.strip()
        strategy = self.strategy_text.value.strip() or "無"
        cost = ARTILLERY_COST

        # 扣款
        new_bal = add_balance(self.officer_uid, -cost, self.officer_name)
        if new_bal < 0:
            # 不應該發生（前面已檢查），但防呆
            add_balance(self.officer_uid, cost, self.officer_name)
            await interaction.response.send_message("❌ 餘額不足。", ephemeral=True)
            return

        arty = _cyber_war_state.setdefault("artillery", {}).setdefault(self.turn_str, {}).setdefault(self.fkey, [])
        arty.append({
            "officer_uid": self.officer_uid,
            "officer_name": self.officer_name,
            "target": target,
            "strategy": strategy,
            "cost": cost,
        })
        save_cyber_war()
        await interaction.response.send_message(
            f"💥 砲擊/空襲已呼叫！\n目標：{target}\n花費：{cost} {currency_name()}\n剩餘餘額：{new_bal} {currency_name()}",
            ephemeral=True,
        )

# ── 行動輸入 Modal ──
class CyberWarActionModal(discord.ui.Modal, title="📝 提交行動指令"):
    action_text = discord.ui.TextInput(
        label="你的行動",
        style=discord.TextStyle.paragraph,
        placeholder="依據上級指示，描述你本回合的具體行動...",
        required=True,
        max_length=500,
    )

    def __init__(self, uid, fkey, user_name, turn, order_text):
        self.uid = uid
        self.fkey = fkey
        self.user_name = user_name
        self.turn = turn
        self._order_text = order_text
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        action = self.action_text.value.strip()
        turn_str = str(self.turn)
        actions = _cyber_war_state.setdefault("actions", {})
        turn_actions = actions.setdefault(turn_str, {})
        fac_actions = turn_actions.setdefault(self.fkey, {})
        fac_actions[self.uid] = action
        save_cyber_war()
        await interaction.response.send_message(
            f"✅ 行動已提交！\n{self._order_text}\n\n你的行動：{action[:200]}",
            ephemeral=True,
        )

# ── 面板管理 ──
async def _get_war_panel_channel():
    ch_id = _cyber_war_settings.get("channel_id")
    if not ch_id:
        return None
    return bot.get_channel(int(ch_id))

async def setup_war_panel():
    channel = await _get_war_panel_channel()
    if not channel:
        return None
    old_msg_id = _cyber_war_settings.get("panel_message_id")
    if old_msg_id:
        try:
            old_msg = await channel.fetch_message(int(old_msg_id))
            await old_msg.delete()
        except Exception:
            pass
    new_msg = await channel.send(embed=_build_war_embed(), view=CyberWarPanelView())
    _cyber_war_settings["panel_message_id"] = new_msg.id
    save_cyber_war()
    return new_msg

async def refresh_war_panel():
    channel = await _get_war_panel_channel()
    if not channel:
        return
    msg_id = _cyber_war_settings.get("panel_message_id")
    if not msg_id:
        await setup_war_panel()
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=_build_war_embed(), view=CyberWarPanelView())
    except discord.NotFound:
        await setup_war_panel()
    except Exception as e:
        print(f"⚠️ 賽博一戰面板更新失敗：{e}")

# ── AI 戰況判定 ──
async def _ai_evaluate_turn(turn: int):
    """AI 裁判：收集雙方行動+砲擊，判定本回合戰況變化。"""
    s = _cyber_war_state
    fac_a = s["factions"].get("A", {})
    fac_b = s["factions"].get("B", {})

    # 收集行動
    turn_str = str(turn)
    actions = s.get("actions", {}).get(turn_str, {})
    artillery = s.get("artillery", {}).get(turn_str, {})

    def _collect_side(fkey, fac):
        lines = []
        a = actions.get(fkey, {})
        for uid, action in a.items():
            role_info = _get_player_role(uid)
            role = role_info[1] if role_info else "unknown"
            name = role_info[2]["name"] if role_info and len(role_info) > 2 else uid
            lines.append(f"  [{role}] {name}：{action[:150]}")
        arty = artillery.get(fkey, [])
        if arty:
            lines.append("  砲擊/空襲：")
            for at in arty:
                lines.append(f"    → 目標：{at['target']}（{at.get('strategy', '')[:80]}）")
        if not lines:
            lines.append("  （無行動）")
        return "\n".join(lines)

    side_a_text = _collect_side("A", fac_a)
    side_b_text = _collect_side("B", fac_b)

    prompt = (
        f"你是第一次世界大戰的戰場裁判。以下是第{turn}回合的戰況：\n\n"
        f"戰場：{s.get('battlefield', '?')}\n"
        f"當前狀態：\n"
        f"  {fac_a.get('flag','')} {fac_a.get('name','')} — 推進{fac_a.get('progress',0)}%，士氣{fac_a.get('morale',100)}，補給{fac_a.get('supplies',100)}\n"
        f"  {fac_b.get('flag','')} {fac_b.get('name','')} — 推進{fac_b.get('progress',0)}%，士氣{fac_b.get('morale',100)}，補給{fac_b.get('supplies',100)}\n\n"
        f"{fac_a.get('name','A')}方行動：\n{side_a_text}\n\n"
        f"{fac_b.get('name','B')}方行動：\n{side_b_text}\n\n"
        "請根據雙方行動質量、策略、砲擊支援等因素，判定本回合戰況變化。\n"
        "考慮因素：行動的具體性、與上級指令的一致性、砲擊效果、補給消耗等。\n"
        "推進進度變化範圍：-10到+15，士氣變化：-20到+10，補給變化：-15到+5。\n\n"
        "請用以下格式回覆（不要加其他文字）：\n"
        "===A_PROGRESS_DELTA===\n數字\n"
        "===B_PROGRESS_DELTA===\n數字\n"
        "===A_MORALE_DELTA===\n數字\n"
        "===B_MORALE_DELTA===\n數字\n"
        "===A_SUPPLIES_DELTA===\n數字\n"
        "===B_SUPPLIES_DELTA===\n數字\n"
        "===SUMMARY===\n一段100字以內的戰況描述（繁體中文）"
    )

    settings = {
        "api_url": chat_ai_settings.get("api_url", ""),
        "api_key": chat_ai_settings.get("api_key", ""),
        "model": chat_ai_settings.get("model", ""),
        "fallback_enabled": chat_ai_settings.get("fallback_enabled", False),
        "fallback_api_url": chat_ai_settings.get("fallback_api_url", ""),
        "fallback_api_key": chat_ai_settings.get("fallback_api_key", ""),
        "fallback_model": chat_ai_settings.get("fallback_model", ""),
        "owner_skip_model_chain": chat_ai_settings.get("owner_skip_model_chain", True),
    }

    try:
        result = await asyncio.wait_for(
            call_chat_api(
                [{"role": "user", "content": prompt}], settings,
                max_tokens=800, timeout_total=60, timeout_read=50,
                is_background=False, fallback_mode="full", category="admin",
                fallback_user_id="cyber_war",
            ),
            timeout=65,
        )
        text = (result.get("content") or "").strip()
        if not text or result.get("circuit_open"):
            print(f"⚠️ 賽博一戰AI裁判失敗：circuit_open={result.get('circuit_open')}, error={result.get('error','')[:200]}")
            return _default_turn_result()

        # 解析分隔符格式
        def _extract(marker, default=0):
            pattern = f"=== {marker} ==="
            if marker in text:
                idx = text.index(marker)
                after = text[idx + len(marker):]
                # 取下一行
                lines = after.strip().split("\n")
                if lines:
                    try:
                        return int(lines[0].strip())
                    except ValueError:
                        pass
            return default

        a_prog = _extract("A_PROGRESS_DELTA", 0)
        b_prog = _extract("B_PROGRESS_DELTA", 0)
        a_mor = _extract("A_MORALE_DELTA", 0)
        b_mor = _extract("B_MORALE_DELTA", 0)
        a_sup = _extract("A_SUPPLIES_DELTA", 0)
        b_sup = _extract("B_SUPPLIES_DELTA", 0)

        summary = ""
        if "===SUMMARY===" in text:
            idx = text.index("===SUMMARY===")
            summary = text[idx + len("===SUMMARY==="):].strip()[:500]
        if not summary:
            summary = f"第{turn}回合戰況已更新。"

        return {
            "a_progress": max(-10, min(15, a_prog)),
            "b_progress": max(-10, min(15, b_prog)),
            "a_morale": max(-20, min(10, a_mor)),
            "b_morale": max(-20, min(10, b_mor)),
            "a_supplies": max(-15, min(5, a_sup)),
            "b_supplies": max(-15, min(5, b_sup)),
            "summary": summary,
        }
    except Exception as e:
        print(f"⚠️ 賽博一戰AI裁判例外：{e}")
        return _default_turn_result()

def _default_turn_result():
    """AI 失敗時的預設結果（微小隨機變化）。"""
    return {
        "a_progress": _cw_random.randint(-3, 5),
        "b_progress": _cw_random.randint(-3, 5),
        "a_morale": _cw_random.randint(-5, 2),
        "b_morale": _cw_random.randint(-5, 2),
        "a_supplies": _cw_random.randint(-5, 0),
        "b_supplies": _cw_random.randint(-5, 0),
        "summary": "本回合戰況膠著，雙方各有小幅推進。",
    }

# ── 回合結算 ──
async def _process_turn_end():
    """處理回合結束：AI判定 → 更新狀態 → 檢查勝負。"""
    s = _cyber_war_state
    turn = s.get("turn", 1)
    print(f"⚔️ 賽博一戰：開始處理第{turn}回合結算...")

    s["phase"] = "processing"
    await refresh_war_panel()

    result = await _ai_evaluate_turn(turn)

    fac_a = s["factions"]["A"]
    fac_b = s["factions"]["B"]

    # 更新狀態
    fac_a["progress"] = max(0, min(100, fac_a.get("progress", 0) + result["a_progress"]))
    fac_b["progress"] = max(0, min(100, fac_b.get("progress", 0) + result["b_progress"]))
    fac_a["morale"] = max(0, min(100, fac_a.get("morale", 100) + result["a_morale"]))
    fac_b["morale"] = max(0, min(100, fac_b.get("morale", 100) + result["b_morale"]))
    fac_a["supplies"] = max(0, min(100, fac_a.get("supplies", 100) + result["a_supplies"]))
    fac_b["supplies"] = max(0, min(100, fac_b.get("supplies", 100) + result["b_supplies"]))

    # 補給耗盡影響士氣
    if fac_a["supplies"] <= 0:
        fac_a["morale"] = max(0, fac_a["morale"] - 5)
    if fac_b["supplies"] <= 0:
        fac_b["morale"] = max(0, fac_b["morale"] - 5)

    s["turn_summary"] = result["summary"]

    # 檢查勝負
    a_defeated = fac_a["morale"] <= MORALE_DEFEAT_THRESHOLD or fac_a["progress"] <= 0 and fac_b["progress"] >= PROGRESS_WIN_THRESHOLD
    b_defeated = fac_b["morale"] <= MORALE_DEFEAT_THRESHOLD or fac_b["progress"] <= 0 and fac_a["progress"] >= PROGRESS_WIN_THRESHOLD

    if fac_a["progress"] >= PROGRESS_WIN_THRESHOLD and not a_defeated:
        b_defeated = True
    if fac_b["progress"] >= PROGRESS_WIN_THRESHOLD and not b_defeated:
        a_defeated = True

    if a_defeated and not b_defeated:
        s["winner"] = "B"
        fac_a["defeated"] = True
    elif b_defeated and not a_defeated:
        s["winner"] = "A"
        fac_b["defeated"] = True
    elif a_defeated and b_defeated:
        # 同歸於盡 → 進度高的贏
        s["winner"] = "A" if fac_a["progress"] >= fac_b["progress"] else "B"
        fac_a["defeated"] = True
        fac_b["defeated"] = True

    if s["winner"]:
        await _settle_game()
    else:
        # 進入下一回合
        s["turn"] = turn + 1
        s["phase"] = "command"
        # 第一回合結束後鎖定押金
        if turn == 1:
            s["deposits_locked"] = True
            # 更新 total_deposits 為最終值
            s["total_deposits"] = sum(d.get("amount", 0) for d in s.get("deposits", {}).values())
        now = datetime.now(_TZ)
        next = now + timedelta(hours=s.get("turn_interval_hours", TURN_INTERVAL_HOURS))
        s["next_turn_time"] = next.isoformat()
        # 檢查遊戲是否到時間
        end_dt = _from_iso(s.get("end_time"))
        if end_dt and now >= end_dt:
            # 時間到 → 進度高的贏
            s["winner"] = "A" if fac_a["progress"] >= fac_b["progress"] else "B"
            if fac_a["progress"] == fac_b["progress"]:
                s["winner"] = "A" if fac_a["morale"] >= fac_b["morale"] else "B"
            other = "B" if s["winner"] == "A" else "A"
            s["factions"][other]["defeated"] = True
            await _settle_game()

    save_cyber_war()
    await refresh_war_panel()
    print(f"⚔️ 賽博一戰：第{turn}回合結算完成。winner={s.get('winner')}")

# ── 結算發獎 ──
async def _settle_game():
    """結算遊戲：勝方按個人押金比例分配 total_pool × multiplier 獎金。"""
    s = _cyber_war_state
    if s.get("settlement_done"):
        return
    winner_key = s.get("winner")
    if not winner_key:
        return

    winner_fac = s["factions"][winner_key]
    deposits = s.get("deposits", {})
    multiplier = s.get("prize_multiplier", 0)

    # 計算獎池 = 全部押金總和（勝方+敗方）× 倍率
    total_pool = sum(d.get("amount", 0) for d in deposits.values())
    total_prize = total_pool * multiplier

    # 勝方中有下注的成員，按押金比例分配
    all_winners = (
        [o["id"] for o in winner_fac.get("officers", [])] +
        [sl["id"] for sl in winner_fac.get("squad_leaders", [])] +
        [sol["id"] for sol in winner_fac.get("soldiers", [])]
    )
    winner_deposits = {uid: deposits.get(uid, {}).get("amount", 0) for uid in all_winners}
    winner_pool = sum(winner_deposits.values())

    if winner_pool > 0 and total_prize > 0:
        for uid, dep in winner_deposits.items():
            if dep > 0:
                share = int(total_prize * dep / winner_pool)
                add_balance(uid, share, winner_fac.get("name", ""))

    s["settlement_done"] = True
    s["active"] = False
    s["phase"] = "ended"
    print(f"⚔️ 賽博一戰結算完成：勝方={winner_fac.get('name')}，獎池={total_prize}，{len([v for v in winner_deposits.values() if v > 0])}人分獲")

# ── 背景迴圈 ──
async def cyber_war_loop():
    """每60秒檢查是否需要處理回合結算。"""
    await bot.wait_until_ready()
    print("⚔️ 賽博一戰背景迴圈已啟動")
    while not bot.is_closed():
        try:
            s = _cyber_war_state
            if s.get("active") and not s.get("winner"):
                next_turn = _from_iso(s.get("next_turn_time"))
                if next_turn and datetime.now(_TZ) >= next_turn:
                    await _process_turn_end()

                # 確保面板存在
                if _cyber_war_settings.get("channel_id") and not _cyber_war_settings.get("panel_message_id"):
                    await setup_war_panel()
                else:
                    # 定期刷新面板（每5分鐘）
                    await refresh_war_panel()
            elif s.get("active") == False and s.get("winner") and _cyber_war_settings.get("channel_id"):
                # 遊戲已結束但面板可能需要更新
                if not _cyber_war_settings.get("panel_message_id"):
                    await setup_war_panel()
        except Exception as e:
            print(f"⚠️ 賽博一戰迴圈例外：{e}")
        await asyncio.sleep(60)

# ── 指令群組 ──
class CyberWarGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="cyber_war", description="賽博一戰 — WWI 策略遊戲")

    @app_commands.command(name="start", description="開始新一局賽博一戰（機器人擁有者限定）")
    async def cw_start(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(BOT_OWNER_ID):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if _cyber_war_state.get("active"):
            await interaction.response.send_message("⚠️ 已有進行中的戰局，請先結束再開新局。", ephemeral=True)
            return
        if not _cyber_war_settings.get("channel_id"):
            await interaction.response.send_message("⚠️ 請先使用 `/cyber_war set_channel` 設定面板頻道。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        ok, msg = await _start_new_game(interaction.guild)
        if ok:
            await setup_war_panel()
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)

    @app_commands.command(name="set_channel", description="設定賽博一戰面板頻道（機器人擁有者限定）")
    async def cw_set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if str(interaction.user.id) != str(BOT_OWNER_ID):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        _cyber_war_settings["channel_id"] = str(channel.id)
        _cyber_war_settings["panel_message_id"] = None
        save_cyber_war()
        if _cyber_war_state.get("active"):
            await setup_war_panel()
        await interaction.response.send_message(f"✅ 賽博一戰面板頻道已設為 {channel.mention}", ephemeral=True)

    @app_commands.command(name="end", description="手動結束當前戰局（機器人擁有者限定）")
    async def cw_end(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(BOT_OWNER_ID):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚠️ 目前沒有進行中的戰局。", ephemeral=True)
            return

        s = _cyber_war_state
        # 進度高的贏
        fac_a = s["factions"].get("A", {})
        fac_b = s["factions"].get("B", {})
        if fac_a.get("progress", 0) >= fac_b.get("progress", 0):
            s["winner"] = "A"
            fac_b["defeated"] = True
        else:
            s["winner"] = "B"
            fac_a["defeated"] = True
        await _settle_game()
        save_cyber_war()
        await refresh_war_panel()
        wf = s["factions"][s["winner"]]
        await interaction.response.send_message(
            f"⚔️ 戰局已手動結束。\n🏆 勝方：{wf['flag']} {wf['name']}\n💰 獎金已發放。",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="查看賽博一戰狀態")
    async def cw_status(self, interaction: discord.Interaction):
        s = _cyber_war_state
        if not s.get("active") and not s.get("winner"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return

        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        role_text = ""
        if info:
            fkey, role, fac = info
            rn = _ROLE_NAMES.get(role, "?")
            my_dep = s.get("deposits", {}).get(uid, {}).get("amount", 0)
            my_potential = my_dep * s.get("prize_multiplier", 0)
            role_text = f"\n🎖️ 你的角色：{fac['flag']} {fac['name']} — {rn}\n💰 你的押金：{my_dep} {currency_name()}（勝可得 {my_potential:,}）"

        fac_a = s.get("factions", {}).get("A", {})
        fac_b = s.get("factions", {}).get("B", {})

        lines = [
            f"⚔️ **第{s.get('game_id', 0)}局賽博一戰**",
            f"🗺️ 戰場：{s.get('battlefield', '—')}",
            f"📅 第{s.get('turn', 0)}回合 | 階段：{s.get('phase', '?')}",
            f"⏱️ 下回合結算：{_time_remaining(s.get('next_turn_time'))}",
            f"🏁 遊戲剩餘：{_time_remaining(s.get('end_time'))}",
            "",
            f"{fac_a.get('flag','')} {fac_a.get('name','')} — 推進{fac_a.get('progress',0)}% | 士氣{fac_a.get('morale',100)}",
            f"{fac_b.get('flag','')} {fac_b.get('name','')} — 推進{fac_b.get('progress',0)}% | 士氣{fac_b.get('morale',100)}",
            "",
            f"💰 獎池：{s.get('total_deposits', 0):,} {currency_name()} ×{s.get('prize_multiplier', 0)}",
            f"🔒 押金：{'已鎖定' if s.get('deposits_locked') else '可自由投注（限第1回合）'}",
            role_text,
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="bet", description="下注/追加押金（限第1回合）")
    async def cw_bet(self, interaction: discord.Interaction, amount: int):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        if _cyber_war_state.get("deposits_locked"):
            dep = _cyber_war_state.get("deposits", {}).get(uid, {}).get("amount", 0)
            await interaction.response.send_message(
                f"🔒 押金已鎖定（第一回合結束後不可變更）。\n你目前的押金：{dep} {currency_name()}",
                ephemeral=True,
            )
            return
        if amount <= 0:
            await interaction.response.send_message("❌ 下注金額必須為正整數。要撤回請用 /cyber_war withdraw。", ephemeral=True)
            return
        bal = get_balance(uid)
        if amount > bal:
            await interaction.response.send_message(
                f"❌ 餘額不足。你需要 {amount}，目前只有 {bal} {currency_name()}。",
                ephemeral=True,
            )
            return
        deposits = _cyber_war_state.setdefault("deposits", {})
        current = deposits.get(uid, {}).get("amount", 0)
        add_balance(uid, -amount, interaction.user.display_name)
        new_dep = current + amount
        deposits[uid] = {"amount": new_dep, "name": interaction.user.display_name}
        _cyber_war_state["total_deposits"] = sum(d.get("amount", 0) for d in deposits.values())
        save_cyber_war()
        await interaction.response.send_message(
            f"✅ 下注成功！追加 {amount} {currency_name()}。\n你目前的押金：{new_dep} {currency_name()}\n剩餘餘額：{bal - amount} {currency_name()}",
            ephemeral=True,
        )

    @app_commands.command(name="withdraw", description="撤回押金（限第1回合）")
    async def cw_withdraw(self, interaction: discord.Interaction, amount: int):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        if _cyber_war_state.get("deposits_locked"):
            dep = _cyber_war_state.get("deposits", {}).get(uid, {}).get("amount", 0)
            await interaction.response.send_message(
                f"🔒 押金已鎖定，無法撤回。\n你目前的押金：{dep} {currency_name()}",
                ephemeral=True,
            )
            return
        if amount <= 0:
            await interaction.response.send_message("❌ 撤回金額必須為正整數。", ephemeral=True)
            return
        deposits = _cyber_war_state.get("deposits", {})
        current = deposits.get(uid, {}).get("amount", 0)
        if amount > current:
            await interaction.response.send_message(
                f"❌ 撤回金額超過你的押金。你目前的押金：{current} {currency_name()}。",
                ephemeral=True,
            )
            return
        add_balance(uid, amount, interaction.user.display_name)
        new_dep = current - amount
        if new_dep > 0:
            deposits[uid] = {"amount": new_dep, "name": interaction.user.display_name}
        else:
            deposits.pop(uid, None)
        _cyber_war_state["total_deposits"] = sum(d.get("amount", 0) for d in deposits.values())
        save_cyber_war()
        await interaction.response.send_message(
            f"✅ 撤回成功！退回 {amount} {currency_name()}。\n你目前的押金：{new_dep} {currency_name()}",
            ephemeral=True,
        )

    @app_commands.command(name="my_bet", description="查看自己的押金")
    async def cw_my_bet(self, interaction: discord.Interaction):
        if not _cyber_war_state.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的戰局。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        info = _get_player_role(uid)
        if not info:
            await interaction.response.send_message("❌ 你不在本局參戰名單中。", ephemeral=True)
            return
        dep = _cyber_war_state.get("deposits", {}).get(uid, {}).get("amount", 0)
        locked = _cyber_war_state.get("deposits_locked", False)
        bal = get_balance(uid)
        multiplier = _cyber_war_state.get("prize_multiplier", 0)
        potential = dep * multiplier if dep > 0 else 0
        await interaction.response.send_message(
            f"💰 你的押金：{dep} {currency_name()}\n"
            f"🏦 剩餘餘額：{bal} {currency_name()}\n"
            f"倍率：{multiplier}x\n"
            f"若勝方可得：{potential:,} {currency_name()}\n"
            f"狀態：{'🔒 已鎖定' if locked else '🔓 可調整（限第1回合）'}",
            ephemeral=True,
        )
