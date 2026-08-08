# ═══════════════════════════════════════════════════════════════════
# Module: 160_siege (AI 攻城戰)
# 每日10:00自動開城，玩家每20分鐘可輸入提示詞攻城，AI判定傷害
# 22:00結算排名贈予琉璃幣，城提早被攻破則直接結算
# ═══════════════════════════════════════════════════════════════════

import random as _siege_random
import time as _siege_time

try:
    ICEA_GUILD_ID
except NameError:
    try:
        from discord_borda_poll import ICEA_GUILD_ID
    except ImportError:
        ICEA_GUILD_ID = "1425065927027720286"
try:
    get_channel_any
except NameError:
    try:
        from discord_borda_poll import get_channel_any
    except ImportError:
        def get_channel_any(ch_id): return None
try:
    is_admin
except NameError:
    try:
        from discord_borda_poll import is_admin
    except ImportError:
        def is_admin(inter): return inter.user.guild_permissions.manage_guild if inter.guild else False

SIEGE_DATA_FILE = os.path.join(DATA_DIR, "siege_data.json")

# ── 攻城戰設定 ──
_siege_settings = {
    "enabled": True,
    "channel_id": None,            # 攻城戰主面板頻道
    "panel_message_id": None,      # 當前主面板的訊息 ID
    "guild_channels": {},          # 訪客伺服器子面板 {guild_id_str: channel_id_str}
    "guild_panel_messages": {},    # 訪客伺服器子面板訊息 ID {guild_id_str: message_id}
    "reward_pool": 5000,            # 每日獎池總額（琉璃幣）
    "attack_cooldown": 1200,        # 每人攻城冷卻（秒，預設20分鐘）
    "min_hp": 80000,               # 城池最低血量
    "max_hp": 120000,              # 城池最高血量
    "min_defense": 10,             # 最低防禦減傷%
    "max_defense": 35,             # 最高防禦減傷%
    "min_damage": 100,             # AI 判定最低傷害
    "max_damage": 2000,            # AI 判定最高傷害
}

# ── 當日攻城戰狀態 ──
_siege_state = {
    "active": False,               # 是否正在進行
    "nation_name": "",             # 當日被攻城的國家名
    "max_hp": 0,                   # 城池最大血量
    "current_hp": 0,               # 城池剩餘血量
    "defense_pct": 0,              # 防禦減傷百分比
    "total_damage_dealt": 0,       # 累計傷害
    "attacks": [],                 # [{"user_id","user_name","prompt","damage","timestamp"}, ...]
    "player_damage": {},           # {user_id_str: total_damage}
    "player_last_attack": {},      # {user_id_str: timestamp}
    "started_at": 0,               # 開始時間
    "settled": False,              # 是否已結算
    "broken": False,               # 是否被攻破
    "date_str": "",                # 日期字串 YYYY-MM-DD
    "result_message_id": None,     # 結算面板訊息 ID（下次開城前5分鐘刪除）
}

# ── 持久化 ──
def save_siege_data():
    try:
        _save_json_file(SIEGE_DATA_FILE, {
            "settings": _siege_settings,
            "state": _siege_state,
        }, indent=2)
        # 立即上傳到 Drive，避免「剛存檔→容器重啟→週期同步還沒跑→Drive 上還是舊資料」的競態
        try:
            asyncio.ensure_future(_immediate_drive_upload("siege_data.json"))
        except Exception:
            pass  # 沒有 event loop 時靜默跳過（load_siege_data 在 startup 呼叫 save 的情況）
    except Exception as e:
        print(f"⚠️ 攻城戰資料存檔失敗：{e}")

def load_siege_data():
    global _siege_settings, _siege_state
    try:
        if os.path.exists(SIEGE_DATA_FILE):
            with open(SIEGE_DATA_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            if "settings" in data:
                _s = data["settings"]
                for k in _siege_settings:
                    if k in _s:
                        _siege_settings[k] = _s[k]
            if "state" in data:
                _siege_state.update(data["state"])
            print(f"⚔️ 攻城戰資料已載入（active={_siege_state.get('active')}）")
    except Exception as e:
        print(f"⚠️ 載入攻城戰資料失敗：{e}")

# ── 從會員國隨機抽取國家名 ──
def _siege_pick_nation():
    """從會員國清單隨機抽取一個國名。優先用成員國/理事國，沒有就全部。"""
    entries = _member_nations.get("entries", [])
    # 只取未被除籍的
    active = [e for e in entries if e.get("category") != "removed"]
    if not active:
        return None
    # 優先用成員國和理事國
    preferred = [e for e in active if e.get("category") in ("member", "council")]
    pool = preferred if preferred else active
    if not pool:
        return None
    chosen = _siege_random.choice(pool)
    return chosen.get("name_zh") or chosen.get("name_en") or "未知國"

# ── 開始新的一天攻城戰 ──
async def _siege_start_new_day():
    """每天10:00刷新一個新的攻城目標。"""
    global _siege_state

    # 刪除上一場的結算面板（避免結算面板越堆越多）
    await _siege_delete_old_result()

    # 先結算上一局（如果還在進行中且尚未結算）
    if _siege_state.get("active") and not _siege_state.get("settled"):
        await _siege_settle(broken=False)

    nation = _siege_pick_nation()
    if not nation:
        print("⚔️ 攻城戰：沒有會員國可抽取，跳過今日")
        return

    _today = datetime.now(GMT8).strftime("%Y-%m-%d")
    _siege_state = {
        "active": True,
        "nation_name": nation,
        "max_hp": _siege_random.randint(_siege_settings["min_hp"], _siege_settings["max_hp"]),
        "current_hp": 0,  # 設好後再填
        "defense_pct": _siege_random.randint(_siege_settings["min_defense"], _siege_settings["max_defense"]),
        "total_damage_dealt": 0,
        "attacks": [],
        "player_damage": {},
        "player_last_attack": {},
        "started_at": _siege_time.time(),
        "settled": False,
        "broken": False,
        "date_str": _today,
    }
    _siege_state["current_hp"] = _siege_state["max_hp"]
    save_siege_data()

    print(f"⚔️ 攻城戰開始：{nation}（HP {_siege_state['max_hp']}，防禦 {_siege_state['defense_pct']}%）")
    await _siege_setup_panel()

# ── 結算 ──
async def _siege_settle(broken: bool = False):
    """結算攻城戰，依傷害排名發放琉璃幣。"""
    global _siege_state
    if not _siege_state.get("active") or _siege_state.get("settled"):
        return
    if not broken and _siege_state.get("broken"):
        return  # 已經被攻破過了

    _siege_state["settled"] = True
    _siege_state["broken"] = broken
    save_siege_data()

    # 依傷害排名分配獎池
    player_dmg = _siege_state.get("player_damage", {})
    if not player_dmg:
        print("⚔️ 攻城戰結算：無人參與")
        await _siege_send_result_embed(broken, [])
        _siege_state["active"] = False
        save_siege_data()
        return

    ranked = sorted(player_dmg.items(), key=lambda x: x[1], reverse=True)
    reward_pool = _siege_settings.get("reward_pool", 5000)

    # 獎勵分配：前 N 名依比例分獎池
    # 第1名 35%，第2名 25%，第3名 18%，第4名 12%，第5名 10%
    # 被攻破額外加碼 50%（從系統額外撥出，不動原本獎池）
    reward_ratios = [0.35, 0.25, 0.18, 0.12, 0.10]
    extra_multiplier = 1.5 if broken else 1.0

    rewards = []
    for i, (uid_str, dmg) in enumerate(ranked):
        ratio = reward_ratios[i] if i < len(reward_ratios) else 0
        if ratio == 0 and i >= 5:
            # 第6名以後給參與獎 50 幣
            reward = 50
        else:
            reward = int(reward_pool * ratio * extra_multiplier)
        if reward > 0:
            add_balance(uid_str, reward)
            rewards.append({
                "user_id": uid_str,
                "user_name": _siege_state["attacks"] and next(
                    (a["user_name"] for a in reversed(_siege_state["attacks"]) if a["user_id"] == uid_str),
                    f"User {uid_str}"
                ),
                "damage": dmg,
                "reward": reward,
            })

    # 如果城被攻破，找出最後一擊的玩家，給予 bonus
    last_attacker = None
    if broken and _siege_state.get("attacks"):
        last_attack = _siege_state["attacks"][-1]
        last_attacker = {
            "user_id": last_attack["user_id"],
            "user_name": last_attack["user_name"],
        }
        # 最後一擊 bonus：500 琉璃幣
        add_balance(last_attack["user_id"], 500)
        # 如果還沒在 rewards 裡，加一筆
        found = False
        for r in rewards:
            if r["user_id"] == last_attack["user_id"]:
                r["reward"] += 500
                found = True
                break
        if not found:
            rewards.append({
                "user_id": last_attack["user_id"],
                "user_name": last_attack["user_name"],
                "damage": 0,
                "reward": 500,
            })

    save_siege_data()
    print(f"⚔️ 攻城戰結算：{len(rewards)} 人獲獎，城破={broken}")
    await _siege_send_result_embed(broken, rewards, last_attacker)

    _siege_state["active"] = False
    save_siege_data()

    # 結算後更新面板為「已結算」狀態
    await _siege_update_panel()

# ── 面板 Embed 構建 ──
def _build_siege_embed():
    s = _siege_state
    if not s.get("active"):
        # 沒有進行中——顯示「等待開城」
        embed = discord.Embed(
            title="⚔️ AI 攻城戰",
            description=(
                "今日攻城戰已結束或尚未開始。\n"
                f"每日 **10:00** 自動開城，**22:00** 結算排名。\n\n"
                "點擊下方按鈕輸入你的攻城提示詞，AI 會判定你的傷害！"
            ),
            color=discord.Color.dark_gray(),
        )
        embed.set_footer(text="每20分鐘可攻城一次")
        return embed

    hp_pct = (s["current_hp"] / s["max_hp"] * 100) if s["max_hp"] > 0 else 0
    # 血量條
    bar_len = 20
    filled = int(hp_pct / 100 * bar_len)
    hp_bar = "█" * filled + "░" * (bar_len - filled)

    embed = discord.Embed(
        title=f"⚔️ 攻城戰 — {s['nation_name']}",
        color=discord.Color.red() if hp_pct < 30 else discord.Color.orange(),
    )
    embed.add_field(
        name="🏰 城池血量",
        value=f"`{hp_bar}` {s['current_hp']}/{s['max_hp']} ({hp_pct:.0f}%)",
        inline=False,
    )
    embed.add_field(name="🛡️ 防禦減傷", value=f"{s['defense_pct']}%", inline=True)
    embed.add_field(name="⚔️ 累計傷害", value=f"{s['total_damage_dealt']:,}", inline=True)
    embed.add_field(name="👥 參與人數", value=f"{len(s['player_damage'])} 人", inline=True)

    # 排行榜 Top 5
    ranked = sorted(s["player_damage"].items(), key=lambda x: x[1], reverse=True)
    if ranked:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        lines = []
        for i, (uid, dmg) in enumerate(ranked[:5]):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            name = next(
                (a["user_name"] for a in reversed(s["attacks"]) if a["user_id"] == uid),
                f"User {uid[-4:]}"
            )
            lines.append(f"{medal} {name} — {dmg:,} 傷害")
        embed.add_field(name="📊 傷害排行", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📊 傷害排行", value="尚無人攻城", inline=False)

    embed.set_footer(text=f"每日10:00開城 · 22:00結算 · 每20分鐘可攻城一次 · {s.get('date_str','')}")
    return embed

# ── 攻城戰持久化按鈕面板 ──
class SiegePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="攻城",
        style=discord.ButtonStyle.danger,
        emoji="⚔️",
        custom_id="siege_attack_btn",
    )
    async def attack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _siege_state.get("active") or _siege_state.get("settled"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的攻城戰。", ephemeral=True)
            return

        uid_str = str(interaction.user.id)

        # 冷卻檢查
        last_attack = _siege_state["player_last_attack"].get(uid_str, 0)
        cooldown = _siege_settings.get("attack_cooldown", 1200)
        elapsed = _siege_time.time() - last_attack
        if elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            mins = remaining // 60
            secs = remaining % 60
            await interaction.response.send_message(
                f"⏳ 攻城冷卻中，還需等待 **{mins}分{secs}秒**。",
                ephemeral=True,
            )
            return

        # 彈出 Modal 讓玩家輸入攻城提示詞
        await interaction.response.send_modal(SiegeAttackModal(uid_str, interaction.user.display_name))

# ── 攻城輸入 Modal ──
class SiegeAttackModal(discord.ui.Modal, title="⚔️ 輸入攻城提示詞"):
    attack_prompt = discord.ui.TextInput(
        label="攻城策略",
        style=discord.TextStyle.paragraph,
        placeholder="描述你的攻城方式（例如：我用投石機轟炸城牆...）",
        required=True,
        max_length=500,
    )

    def __init__(self, user_id_str: str, user_name: str):
        self._user_id_str = user_id_str
        self._user_name = user_name
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        prompt = self.attack_prompt.value.strip()
        if len(prompt) < 3:
            await interaction.response.send_message("❌ 提示詞太短了，至少3個字。", ephemeral=True)
            return

        # 立刻回應（Discord 3秒規則）
        await interaction.response.send_message(
            f"⚔️ {self._user_name} 正在攻城... AI 計算傷害中！",
            ephemeral=True,
        )

        # AI 判定傷害
        damage = await _siege_judge_damage(prompt)

        # 計算實際傷害（扣防禦）
        defense_pct = _siege_state.get("defense_pct", 0)
        actual_damage = int(damage * (1 - defense_pct / 100))
        if actual_damage < 1:
            actual_damage = 1

        # 記錄攻擊
        _ts = _siege_time.time()
        _siege_state["attacks"].append({
            "user_id": self._user_id_str,
            "user_name": self._user_name,
            "prompt": prompt[:300],
            "raw_damage": damage,
            "damage": actual_damage,
            "timestamp": _ts,
        })
        _siege_state["player_damage"][self._user_id_str] = _siege_state["player_damage"].get(self._user_id_str, 0) + actual_damage
        _siege_state["player_last_attack"][self._user_id_str] = _ts
        _siege_state["total_damage_dealt"] += actual_damage
        _siege_state["current_hp"] -= actual_damage
        if _siege_state["current_hp"] < 0:
            _siege_state["current_hp"] = 0

        save_siege_data()

        # 更新面板
        await _siege_update_panel()

        # 回報傷害給玩家
        hp_remaining = _siege_state["current_hp"]
        defense_note = f"（AI判定 {damage} → 防禦減傷{defense_pct}% → 實際 {actual_damage}）"
        if hp_remaining <= 0:
            await interaction.followup.send(
                f"💥 **城破了！** 你的攻擊造成 **{actual_damage:,}** 點傷害{defense_note}\n"
                f"🏰 {_siege_state['nation_name']} 的城池已被攻陷！即將結算排名...",
                ephemeral=True,
            )
            await _siege_settle(broken=True)
        else:
            await interaction.followup.send(
                f"⚔️ 你對 **{_siege_state['nation_name']}** 造成 **{actual_damage:,}** 點傷害！\n"
                f"{defense_note}\n"
                f"🏰 剩餘血量：{hp_remaining:,}/{_siege_state['max_hp']:,}",
                ephemeral=True,
            )

# ── AI 傷害判定 ──
async def _siege_judge_damage(prompt: str) -> int:
    """呼叫 AI 判定攻城提示詞的傷害值。回傳 100~2000 之間的整數。"""
    _c_url = chat_ai_settings.get("api_url", "").strip()
    _c_key = chat_ai_settings.get("api_key", "").strip()
    _c_model = chat_ai_settings.get("model", "").strip()

    settings = {
        "api_url": _c_url,
        "api_key": _c_key,
        "model": _c_model,
        "system_prompt": "",
    }
    if chat_ai_settings.get("fallback_enabled") and not _ai_circuit_breaker["tripped"]:
        settings["fallback_enabled"] = True
    # 補上降級鏈（跟其他娛樂功能一致的 pattern）
    settings["model_fallback_chain"] = chat_ai_settings.get("model_fallback_chain", "")

    messages = [
        {
            "role": "system",
            "content": (
                "你是一個攻城戰遊戲的裁判。玩家會描述他的攻城策略，你需要根據策略的創意、"
                "合理性、攻擊力來判定一個傷害數值。\n\n"
                "規則：\n"
                "1. 只回覆一個數字（100 到 2000 之間的整數），不要有任何其他文字、標點或解釋。\n"
                "2. 判定標準：\n"
                "   - 簡單/普通攻擊（直接砍、普通射箭）→ 100~500\n"
                "   - 有創意的策略（投石機、火攻、挖地道）→ 500~1200\n"
                "   - 精彩且具體的戰術（連環計、水淹七軍、借東風）→ 1200~1800\n"
                "   - 極其出色的奇策（歷史名將級別的戰術）→ 1800~2000\n"
                "3. 越有創意、越具體、越合理的策略分數越高。\n"
                "4. 只回覆數字本身，例如：850"
            ),
        },
        {"role": "user", "content": f"我的攻城策略：{prompt[:400]}"},
    ]

    try:
        result = await call_chat_api(
            messages, settings,
            max_tokens=20,
            timeout_total=15,
            timeout_read=12,
            is_background=True,
            fallback_mode="full",
            fallback_user_id="siege_judge",
            category="entertainment",
        )
        if result.get("circuit_open"):
            print("⚔️ 攻城戰 AI 判定：熔斷器開啟，使用隨機傷害")
            return _siege_random.randint(_siege_settings["min_damage"], 800)

        text = (result.get("content") or "").strip()
        # 解析數字——容錯：去掉所有非數字字元後取整數
        import re as _re
        nums = _re.findall(r"\d+", text)
        if nums:
            damage = int(nums[0])
            # 鉗制在合法範圍
            damage = max(_siege_settings["min_damage"], min(_siege_settings["max_damage"], damage))
            return damage
        else:
            print(f"⚔️ 攻城戰 AI 判定：無法解析數字「{text[:50]}」，使用隨機傷害")
            return _siege_random.randint(_siege_settings["min_damage"], 800)
    except Exception as e:
        print(f"⚔️ 攻城戰 AI 判定例外：{type(e).__name__}: {e}，使用隨機傷害")
        return _siege_random.randint(_siege_settings["min_damage"], 800)

# ── 面板管理 ──
async def _siege_get_channel():
    """Return the main panel channel (legacy single-channel callers)."""
    ch_id = _siege_settings.get("channel_id")
    if not ch_id:
        return None
    ch = get_channel_any(int(ch_id))
    return ch

def _siege_get_all_channels():
    """Return list of all panel channels (main + guest guild sub-panels)."""
    channels = []
    seen = set()
    main_id = _siege_settings.get("channel_id")
    if main_id:
        ch = get_channel_any(int(main_id))
        if ch:
            channels.append(ch)
            seen.add(str(main_id))
    for g_id, ch_id in _siege_settings.get("guild_channels", {}).items():
        if ch_id and str(ch_id) not in seen:
            ch = get_channel_any(int(ch_id))
            if ch:
                channels.append(ch)
                seen.add(str(ch_id))
    return channels

async def _siege_setup_panel():
    """發送新的攻城戰面板到所有頻道（刪除舊的）。"""
    channels = _siege_get_all_channels()
    if not channels:
        print("⚔️ 攻城戰：頻道未設定，跳過面板發送")
        return None

    guild_msgs = _siege_settings.get("guild_panel_messages", {})
    for channel in channels:
        ch_id_str = str(channel.id)
        is_main = (ch_id_str == str(_siege_settings.get("channel_id", "")))
        old_msg_id = _siege_settings.get("panel_message_id") if is_main else guild_msgs.get(ch_id_str)
        if old_msg_id:
            try:
                old_msg = await channel.fetch_message(int(old_msg_id))
                await old_msg.delete()
            except Exception:
                pass
        try:
            new_msg = await channel.send(embed=_build_siege_embed(), view=SiegePanelView())
            if is_main:
                _siege_settings["panel_message_id"] = new_msg.id
            else:
                guild_msgs[ch_id_str] = new_msg.id
        except Exception as e:
            print(f"⚠️ 攻城戰面板發送至頻道 {ch_id_str} 失敗：{e}")

    _siege_settings["guild_panel_messages"] = guild_msgs
    save_siege_data()
    return True

async def _siege_update_panel():
    """就地更新所有頻道的面板 embed（保持按鈕不變）。"""
    channels = _siege_get_all_channels()
    if not channels:
        return
    guild_msgs = _siege_settings.get("guild_panel_messages", {})
    for channel in channels:
        ch_id_str = str(channel.id)
        is_main = (ch_id_str == str(_siege_settings.get("channel_id", "")))
        msg_id = _siege_settings.get("panel_message_id") if is_main else guild_msgs.get(ch_id_str)
        if not msg_id:
            continue
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=_build_siege_embed())
        except discord.NotFound:
            pass  # Will be recreated on next setup_panel
        except Exception as e:
            print(f"⚠️ 攻城戰面板更新失敗（頻道 {ch_id_str}）：{e}")

# ── 結算結果 Embed + 發送 ──
async def _siege_delete_old_result():
    """刪除上一場的結算面板（如果有）。"""
    old_id = _siege_state.get("result_message_id")
    if not old_id:
        return
    channel = await _siege_get_channel()
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(old_id))
        await msg.delete()
        print("⚔️ 已刪除舊結算面板")
    except Exception:
        pass  # 訊息可能已被手動刪除
    _siege_state["result_message_id"] = None
    save_siege_data()

async def _siege_send_result_embed(broken: bool, rewards: list, last_attacker: dict = None):
    channel = await _siege_get_channel()
    if not channel:
        return

    nation = _siege_state.get("nation_name", "未知")
    title = f"{'💥 城破！' if broken else '🏰 攻城戰結算'} — {nation}"

    embed = discord.Embed(
        title=title,
        color=discord.Color.gold() if broken else discord.Color.blue(),
        timestamp=datetime.now(GMT8),
    )

    embed.add_field(
        name="📊 戰況",
        value=(
            f"總傷害：{_siege_state.get('total_damage_dealt', 0):,}\n"
            f"參與人數：{len(_siege_state.get('player_damage', {}))} 人\n"
            f"城池血量：{_siege_state.get('max_hp', 0):,}\n"
            f"防禦減傷：{_siege_state.get('defense_pct', 0)}%"
        ),
        inline=False,
    )

    if last_attacker:
        embed.add_field(
            name="🏆 最後一擊",
            value=f"{last_attacker['user_name']} 給予了致命一擊！（+500 琉璃幣）",
            inline=False,
        )

    if rewards:
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(rewards[:10]):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{medal} {r['user_name']} — {r['damage']:,} 傷害 → **{r['reward']:,} {currency_name()}**")
        embed.add_field(name="💰 獎勵排行", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="💰 獎勵排行", value="本日無人參與攻城", inline=False)

    if broken:
        embed.description = f"⚔️ **{nation}** 的城池在 22:00 前被攻陷！獎勵加碼 50%！"
    else:
        embed.description = f"⚔️ **{nation}** 的城池成功堅守到 22:00！"

    embed.set_footer(text="每日10:00開城 · 22:00結算 · 明日再戰")

    try:
        msg = await channel.send(embed=embed)
        _siege_state["result_message_id"] = msg.id
        save_siege_data()
        print(f"⚔️ 結算面板已發送（msg_id={msg.id}），將於下次開城前5分鐘刪除")
    except Exception as e:
        print(f"⚠️ 攻城戰結算結果發送失敗：{e}")

# ── 排程循環 ──
async def siege_loop():
    """攻城戰主循環：
    - 重啟時自動重建面板（清除廢棄面板、發送新的）
    - 每秒檢查是否到 10:00（開城）或 22:00（結算）
    - 60秒刷新一次面板（顯示即時血量/排行）
    """
    await bot.wait_until_ready()
    await asyncio.sleep(8)  # 等待其他初始化完成

    # 重啟時重建面板（如果有進行中的攻城戰）
    _has_any_channel = _siege_settings.get("channel_id") or _siege_settings.get("guild_channels")
    if _siege_state.get("active") and _has_any_channel:
        try:
            await _siege_setup_panel()
            print("⚔️ 攻城戰面板已重建")
        except Exception as e:
            print(f"⚠️ 攻城戰面板重建失敗：{e}")
    elif _has_any_channel:
        # 沒有進行中的攻城戰也發一個「等待開城」面板
        try:
            await _siege_setup_panel()
            print("⚔️ 攻城戰面板已重建（待機狀態）")
        except Exception as e:
            print(f"⚠️ 攻城戰面板重建失敗：{e}")

    last_check_minute = -1
    while True:
        try:
            now = datetime.now(GMT8)
            today_str = now.strftime("%Y-%m-%d")
            hour = now.hour
            minute = now.minute

            # 09:55 刪除昨日結算面板（開城前5分鐘清理）
            if hour == 9 and minute == 55 and last_check_minute != minute:
                if _siege_state.get("result_message_id"):
                    await _siege_delete_old_result()
                last_check_minute = minute

            # 10:00 開城（只在整點觸發一次）
            if hour == 10 and minute == 0 and last_check_minute != minute:
                if _siege_settings.get("enabled"):
                    await _siege_start_new_day()
                last_check_minute = minute

            # 22:00 結算
            if hour == 22 and minute == 0 and last_check_minute != minute:
                if _siege_state.get("active") and not _siege_state.get("settled"):
                    await _siege_settle(broken=False)
                last_check_minute = minute

            # 每 60 秒刷新面板
            if minute != last_check_minute and _siege_state.get("active"):
                await _siege_update_panel()
                last_check_minute = minute

            # 如果城已破但還沒結算（保險）
            if _siege_state.get("broken") and not _siege_state.get("settled"):
                await _siege_settle(broken=True)

        except Exception as e:
            print(f"⚠️ 攻城戰循環例外：{e}")

        await asyncio.sleep(30)  # 30秒檢查一次

# ── 指令群組 ──
class SiegeGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="siege", description="AI 攻城戰")

    @app_commands.command(name="start", description="手動開始一場攻城戰（管理員）")
    async def siege_start(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _siege_start_new_day()
        await interaction.followup.send("⚔️ 攻城戰已開始！", ephemeral=True)

    @app_commands.command(name="settle", description="手動結算攻城戰（管理員）")
    async def siege_settle_cmd(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not _siege_state.get("active"):
            await interaction.response.send_message("❌ 目前沒有進行中的攻城戰。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _siege_settle(broken=False)
        await interaction.followup.send("⚔️ 攻城戰已結算！", ephemeral=True)

    @app_commands.command(name="status", description="查看攻城戰狀態")
    async def siege_status(self, interaction: discord.Interaction):
        s = _siege_state
        if not s.get("active"):
            await interaction.response.send_message("⚔️ 目前沒有進行中的攻城戰。每日 10:00 自動開城。", ephemeral=True)
            return

        hp_pct = (s["current_hp"] / s["max_hp"] * 100) if s["max_hp"] > 0 else 0
        embed = discord.Embed(
            title=f"⚔️ 攻城戰狀態 — {s['nation_name']}",
            color=discord.Color.red() if hp_pct < 30 else discord.Color.orange(),
        )
        embed.add_field(name="🏰 血量", value=f"{s['current_hp']:,}/{s['max_hp']:,} ({hp_pct:.0f}%)", inline=True)
        embed.add_field(name="🛡️ 防禦", value=f"{s['defense_pct']}%", inline=True)
        embed.add_field(name="⚔️ 總傷害", value=f"{s['total_damage_dealt']:,}", inline=True)

        uid_str = str(interaction.user.id)
        last_attack = s["player_last_attack"].get(uid_str, 0)
        cooldown = _siege_settings.get("attack_cooldown", 1200)
        elapsed = _siege_time.time() - last_attack
        if elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            embed.add_field(name="⏳ 你的冷卻", value=f"{remaining//60}分{remaining%60}秒", inline=True)
        else:
            embed.add_field(name="✅ 攻城就緒", value="可以攻城！", inline=True)

        my_dmg = s["player_damage"].get(uid_str, 0)
        embed.add_field(name="你的傷害", value=f"{my_dmg:,}", inline=True)

        ranked = sorted(s["player_damage"].items(), key=lambda x: x[1], reverse=True)
        my_rank = next((i+1 for i, (uid, _) in enumerate(ranked) if uid == uid_str), "未上榜")
        embed.add_field(name="你的排名", value=f"#{my_rank}", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setup", description="設定攻城戰頻道（管理員限定）")
    @app_commands.describe(channel="攻城戰面板所在頻道")
    async def siege_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        guild_id_str = str(interaction.guild.id) if interaction.guild else None
        if guild_id_str == ICEA_GUILD_ID:
            _siege_settings["channel_id"] = channel.id
            msg = "✅ 攻城戰主面板頻道已設為"
        elif guild_id_str:
            _siege_settings.setdefault("guild_channels", {})[guild_id_str] = str(channel.id)
            msg = "✅ 本伺服器的攻城戰子面板頻道已設為"
        save_siege_data()
        await interaction.response.send_message(f"{msg} {channel.mention}", ephemeral=True)

        # 如果有進行中的攻城戰，重新發送面板
        if _siege_state.get("active"):
            await _siege_setup_panel()

    @app_commands.command(name="toggle", description="開關攻城戰功能（管理員限定）")
    async def siege_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        _siege_settings["enabled"] = not _siege_settings.get("enabled", True)
        save_siege_data()
        status = "開啟" if _siege_settings["enabled"] else "關閉"
        await interaction.response.send_message(f"⚔️ 攻城戰已{status}。", ephemeral=True)
