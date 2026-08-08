# ═══════════════════════════════════════════════════════════════════════
# Module: 190_galgame — 純文字互動小說（Galgame）系統
# ═══════════════════════════════════════════════════════════════════════
# 功能：
#   - 固定頻道持久面板，顯示角色清單與好感度排行
#   - 玩家選擇角色進入對話，AI 根據角色人設 + 好感度生成回應
#   - 送禮系統消耗琉璃幣，不同角色有偏好禮物類型
#   - 每日互動冷卻，防止刷好感度
#   - 好感度等級系統：陌生人 → 認識 → 朋友 → 親密 → 戀人
#   - 高好感度觸發特殊劇情事件
#   - 擁有者可透過 Dashboard 新增/編輯/刪除角色
#   - 狀態持久化至本地 JSON + Drive 同步
# ═══════════════════════════════════════════════════════════════════════

import asyncio
import random
import time as _time
from datetime import datetime, timedelta, timezone

GMT8 = timezone(timedelta(hours=8))

# ── 資料檔案 ──
GALGAME_FILE = os.path.join(DATA_DIR, "galgame.json")

# ── 全域狀態 ──
galgame_settings = {
    "channel_id": None,           # Galgame 面板頻道
    "message_id": None,           # 面板訊息 ID
    "interaction_cooldown": 300,  # 每次互動冷卻（秒）
    "chat_annoyance_threshold": 15,  # 同日對話達此次數後角色開始不耐煩
    "chat_annoyance_severe": 25,      # 同日對話達此次數後角色明�不耐煩、好感度不再增長
    "chat_annoyance_max": 35,         # 同日對話達此次數後角色拒絕再聊、好感度微降
    "gift_min_cost": 50,          # 最低送禮金額
    "gift_max_cost": 5000,        # 最高送禮金額
    "affection_per_gift_base": 5, # 基礎好感度增加
    "affection_per_chat": 1,      # 每次對話好感度增加
}

# 角色定義：{char_id: {name, background, personality, appearance, gift_preferences, ...}}
galgame_characters = {}

# 玩家進度：{user_id_str: {char_id: {affection, last_interact, daily_count, gifts_given, flags, story_stage}}}
galgame_progress = {}

_galgame_panel_refresh_task = None

# ── 好感度等級 ──
AFFECTION_LEVELS = [
    (0,    "❔ 陌生人",   "初次見面，幾乎不認識。"),
    (20,   "👋 認識",     "知道對方的名字和基本背景。"),
    (50,   "🙂 朋友",     "能聊上幾句，偶爾一起相處。"),
    (100,  "😊 親密",     "無話不談，關係深厚。"),
    (200,  "💕 戀人",     "彼此心意相通，已經在一起了。"),
    (500,  "💍 命運伴侶", "命中注定的另一半，無可取代。"),
]

# ── 禮物類型 ──
GIFT_TYPES = {
    "flower":   {"name": "🌸 花朵",   "min_cost": 50,   "multiplier": 1.0},
    "sweets":   {"name": "🍰 甜點",   "min_cost": 100,  "multiplier": 1.2},
    "book":     {"name": "📚 書籍",   "min_cost": 150,  "multiplier": 1.0},
    "jewelry":  {"name": "💎 首飾",   "min_cost": 500,  "multiplier": 1.5},
    "custom":   {"name": "🎁 自訂禮物", "min_cost": 50,  "multiplier": 1.0},
}

def _affection_level(affection: int) -> tuple:
    """回傳 (等級名稱, 等級描述)"""
    current = AFFECTION_LEVELS[0]
    for threshold, name, desc in AFFECTION_LEVELS:
        if affection >= threshold:
            current = (name, desc)
    return current


def _next_threshold(affection: int) -> int:
    """回傳下一個好感度等級的門檻"""
    for threshold, _, _ in AFFECTION_LEVELS:
        if affection < threshold:
            return threshold
    return AFFECTION_LEVELS[-1][0]  # 已達最高


# ── 不耐煩系統 ──
# 同一天對同一角色講太多次話，角色會逐漸不耐煩：
#   0 ~ threshold-1：正常（好感度 +1/次）
#   threshold ~ severe-1：微不耐煩（好感度 +0/次，AI 語氣略煩）
#   severe ~ max-1：明顯不耐煩（好感度 -1/次，AI 語氣很不耐煩）
#   max+：角色不想再聊了（好感度 -2/次，AI 直接敷衍/拒絕）
def _get_annoyance_state(daily_count: int) -> tuple:
    """回傳 (state_key, label, affection_delta, prompt_hint)。"""
    th = galgame_settings.get("chat_annoyance_threshold", 15)
    severe = galgame_settings.get("chat_annoyance_severe", 25)
    mx = galgame_settings.get("chat_annoyance_max", 35)
    if daily_count < th:
        return ("normal", "😊 正常", 0, "")
    elif daily_count < severe:
        return ("slight", "😤 稍微不耐煩", 0,
                f"你今天已經跟玩家聊了{daily_count}次了，開始覺得有點煩，回應會比平時更短、更敷衍一些，但還是會回答。")
    elif daily_count < mx:
        return ("annoyed", "😡 不耐煩", -1,
                f"你今天已經跟玩家聊了{daily_count}次了，真的很煩，回應要明顯不耐煩，可能會抱怨「你怎麼又來了」「夠了沒」，想趕快結束對話。")
    else:
        return ("refusing", "🚫 不想再聊", -2,
                f"你今天已經跟玩家聊了{daily_count}次了，你完全不想再理這個人了，回應極度敷衍甚至直接拒絕對話（例：「……」「我很忙」「你能不能別來了」），最多一兩句話。")

# ── 持久化 ──
def save_galgame():
    """儲存 Galgame 狀態到本地 JSON。
    Drive 同步由全域 drive_sync_loop 自動處理（每 60 秒掃描 data/*.json）。"""
    _save_json_file(GALGAME_FILE, {
        "settings": galgame_settings,
        "characters": galgame_characters,
        "progress": galgame_progress,
    })
    _schedule_galgame_panel_refresh()


def load_galgame():
    """從本地載入 Galgame 狀態。"""
    global galgame_settings, galgame_characters, galgame_progress
    try:
        if os.path.exists(GALGAME_FILE):
            with open(GALGAME_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            if isinstance(data.get("settings"), dict):
                galgame_settings.update(data["settings"])
            if isinstance(data.get("characters"), dict):
                galgame_characters.update(data["characters"])
            if isinstance(data.get("progress"), dict):
                galgame_progress.update(data["progress"])
            print(f"💬 Galgame 系統已載入：{len(galgame_characters)} 個角色，{len(galgame_progress)} 位玩家")
    except Exception as e:
        print(f"⚠️ Galgame 載入失敗：{e}")


# ── 面板刷新排程 ──
def _schedule_galgame_panel_refresh():
    global _galgame_panel_refresh_task
    if not galgame_settings.get("channel_id"):
        return
    loop = asyncio.get_event_loop()
    if _galgame_panel_refresh_task is not None and not _galgame_panel_refresh_task.done():
        return
    _galgame_panel_refresh_task = loop.create_task(_debounced_galgame_panel_refresh())


async def _debounced_galgame_panel_refresh():
    await asyncio.sleep(1.5)
    try:
        await refresh_galgame_panel()
    except Exception as e:
        print(f"⚠️ Galgame 面板刷新失敗：{e}")


# ── 面板建構 ──
GALGAME_PANEL_TITLE = "💬 互動小說 · 角色花園"

def _build_galgame_embed() -> "discord.Embed":
    """建構 Galgame 面板 Embed。"""
    embed = discord.Embed(
        title=GALGAME_PANEL_TITLE,
        color=discord.Color.pink(),
        timestamp=datetime.now(timezone.utc),
    )

    if not galgame_characters:
        embed.description = (
            "🌸 **歡迎來到角色花園！** 🌸\n\n"
            "目前還沒有任何角色。管理員可以透過 Dashboard 新增角色。\n\n"
            "在這裡，你可以：\n"
            "💬 **對話** — 與角色聊天，增進好感度\n"
            "🎁 **送禮** — 消耗琉璃幣送禮物，加速好感度提升\n"
            "📊 **查看進度** — 查看自己與各角色的好感度\n\n"
            "每個角色都有獨特的性格和故事，等著你來探索～"
        )
    else:
        char_lines = []
        for cid, ch in galgame_characters.items():
            # 統計該角色的總互動人數
            interactors = sum(1 for uid, pd in galgame_progress.items() if cid in pd)
            avg_aff = 0
            if interactors:
                total = sum(pd[cid].get("affection", 0) for pd in galgame_progress.values() if cid in pd)
                avg_aff = total // interactors
            lvl_name, _ = _affection_level(avg_aff)
            char_lines.append(
                f"**{ch['name']}** — {ch.get('tagline', ch.get('personality', '')[:20])}\n"
                f"  {lvl_name} | 平均好感度 {avg_aff} | {interactors} 人互動"
            )
        embed.description = "\n".join(char_lines)

    embed.add_field(
        name="📖 使用說明",
        value=(
            "點擊下方按鈕開始互動。對話不消耗琉璃幣，送禮需要消耗。\n"
            f"冷卻時間：{galgame_settings['interaction_cooldown']} 秒｜"
            f"同日對話過多角色會不耐煩，好感度會遞減甚至倒退"
        ),
        inline=False,
    )

    # 好感度等級表
    level_text = " → ".join(f"{name}" for _, name, _ in AFFECTION_LEVELS)
    embed.add_field(name="💛 好感度等級", value=level_text, inline=False)

    embed.set_footer(text="ICEA 互動小說系統")
    return embed


def _get_galgame_panel_channel():
    ch_id = galgame_settings.get("channel_id")
    if not ch_id:
        return None
    return get_channel_any(int(ch_id))


async def setup_galgame_panel():
    """(重新)發送 Galgame 面板到設定的頻道。"""
    channel = _get_galgame_panel_channel()
    if not channel:
        return None

    # 刪除舊面板
    old_msg_id = galgame_settings.get("message_id")
    if old_msg_id:
        try:
            old_msg = await channel.fetch_message(int(old_msg_id))
            await old_msg.delete()
            print(f"🧹 已刪除舊的 Galgame 面板訊息（ID: {old_msg_id}）")
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"⚠️ 刪除舊 Galgame 面板失敗：{e}")

    # 清掃殘留
    try:
        async for msg in channel.history(limit=30):
            if msg.author.id == bot.user.id and msg.embeds:
                if msg.embeds[0].title and GALGAME_PANEL_TITLE in msg.embeds[0].title:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
    except Exception:
        pass

    # 發送新面板
    try:
        new_msg = await channel.send(embed=_build_galgame_embed(), view=GalgamePanelView())
        galgame_settings["message_id"] = new_msg.id
        save_galgame()
        print(f"✅ Galgame 面板已發送至 #{channel.name}（ID: {new_msg.id}）")
        return new_msg
    except Exception as e:
        print(f"❌ 發送 Galgame 面板失敗：{e}")
        return None


async def refresh_galgame_panel():
    """就地更新現有的 Galgame 面板。"""
    channel = _get_galgame_panel_channel()
    if not channel:
        return
    msg_id = galgame_settings.get("message_id")
    if not msg_id:
        await setup_galgame_panel()
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=_build_galgame_embed())
    except discord.NotFound:
        await setup_galgame_panel()
    except Exception as e:
        print(f"⚠️ 更新 Galgame 面板失敗：{e}")


async def galgame_panel_loop():
    """Galgame 面板背景循環。"""
    await bot.wait_until_ready()
    await asyncio.sleep(5)
    try:
        await setup_galgame_panel()
    except Exception as e:
        print(f"⚠️ Galgame 面板初始化失敗：{e}")
    while True:
        await asyncio.sleep(120)
        try:
            await refresh_galgame_panel()
        except Exception as e:
            print(f"⚠️ Galgame 面板定期刷新失敗：{e}")


# ═══════════════════════════════════════════════════════════════════════
# 面板按鈕（持久化 View）
# ═══════════════════════════════════════════════════════════════════════

def _build_galgame_admin_embed() -> "discord.Embed":
    """建構 Galgame 管理面板 embed（供 /vn admin 指令與面板管理按鈕共用）。"""
    embed = discord.Embed(
        title="🌸 Galgame 管理面板",
        description=(
            f"頻道 ID：{galgame_settings.get('channel_id', '未設定')}\n"
            f"冷卻時間：{galgame_settings.get('interaction_cooldown', 300)} 秒\n"
            f"不耐煩門檻：{galgame_settings.get('chat_annoyance_threshold', 15)}/{galgame_settings.get('chat_annoyance_severe', 25)}/{galgame_settings.get('chat_annoyance_max', 35)} 次\n"
            f"送禮範圍：{galgame_settings.get('gift_min_cost', 50)}-{galgame_settings.get('gift_max_cost', 5000)} {currency_name()}\n"
            f"角色數量：{len(galgame_characters)}\n"
            f"玩家數量：{len(galgame_progress)}\n\n"
            "角色管理請至 Dashboard 操作。"
        ),
        color=discord.Color.pink(),
    )

    if galgame_characters:
        char_list = []
        for cid, ch in galgame_characters.items():
            char_list.append(f"• **{ch['name']}** — {ch.get('tagline', '')[:30]} (ID: `{cid}`)")
        embed.add_field(name="角色清單", value="\n".join(char_list[:10]), inline=False)

    return embed


class GalgamePanelView(discord.ui.View):
    """Galgame 面板的持久化按鈕。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="角色選擇", style=discord.ButtonStyle.primary, emoji="💬",
        custom_id="galgame:select_char"
    )
    async def select_char(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not galgame_characters:
            await interaction.response.send_message("❌ 目前沒有可互動的角色。", ephemeral=True)
            return

        # 建立角色選擇下拉選單
        options = []
        for cid, ch in galgame_characters.items():
            tagline = ch.get('tagline', ch.get('personality', '')[:30])
            options.append(discord.SelectOption(
                label=ch['name'],
                description=tagline[:100],
                value=cid,
            ))
        if len(options) > 25:
            options = options[:25]

        view = CharacterSelectView(str(interaction.user.id))
        select = view.select
        select.options = options
        await interaction.response.send_message(
            "🌸 請選擇要互動的角色：", view=view, ephemeral=True
        )

    @discord.ui.button(
        label="我的進度", style=discord.ButtonStyle.secondary, emoji="📊",
        custom_id="galgame:my_progress"
    )
    async def my_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        embed = _build_user_progress_embed(uid, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="管理", style=discord.ButtonStyle.danger, emoji="⚙️",
        custom_id="galgame:admin"
    )
    async def admin_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != str(BOT_OWNER_ID):
            await interaction.response.send_message("❌ 只有擁有者可以使用此功能。", ephemeral=True)
            return
        embed = _build_galgame_admin_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CharacterSelectView(discord.ui.View):
    """角色選擇下拉選單。"""

    def __init__(self, user_id_str: str):
        super().__init__(timeout=120)
        self.user_id_str = user_id_str
        self.select = discord.ui.Select(
            placeholder="選擇角色…",
            min_values=1, max_values=1,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id_str:
            await interaction.response.send_message("這不是你的面板！", ephemeral=True)
            return

        char_id = interaction.data["values"][0]
        ch = galgame_characters.get(char_id)
        if not ch:
            await interaction.response.send_message("❌ 角色不存在。", ephemeral=True)
            return

        # 確認初始化玩家進度
        if self.user_id_str not in galgame_progress:
            galgame_progress[self.user_id_str] = {}
        if char_id not in galgame_progress[self.user_id_str]:
            galgame_progress[self.user_id_str][char_id] = {
                "affection": 0,
                "last_interact": 0,
                "daily_count": 0,
                "daily_date": "",
                "gifts_given": 0,
                "flags": [],
                "story_stage": 0,
            }

        prog = galgame_progress[self.user_id_str][char_id]

        # 冷卻檢查
        now_ts = _time.time()
        cooldown = galgame_settings["interaction_cooldown"]
        last = prog.get("last_interact", 0)
        if now_ts - last < cooldown:
            remaining = int(cooldown - (now_ts - last))
            await interaction.response.send_message(
                f"⏳ 冷卻中，請等待 {remaining} 秒後再互動。", ephemeral=True
            )
            return

        # 每日計數重置（不再硬性擋，交由不耐煩系統處理）
        today = datetime.now(GMT8).strftime("%Y-%m-%d")
        if prog.get("daily_date") != today:
            prog["daily_date"] = today
            prog["daily_count"] = 0

        # 顯示互動面板
        embed = _build_interaction_embed(char_id, prog)
        view = InteractionView(self.user_id_str, char_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def _build_interaction_embed(char_id: str, prog: dict) -> "discord.Embed":
    """建構角色互動面板 Embed。"""
    ch = galgame_characters[char_id]
    lvl_name, lvl_desc = _affection_level(prog.get("affection", 0))
    next_th = _next_threshold(prog.get("affection", 0))

    embed = discord.Embed(
        title=f"💬 {ch['name']}",
        description=ch.get("tagline", ""),
        color=discord.Color.pink(),
    )
    embed.add_field(
        name="好感度",
        value=f"{lvl_name}（{prog.get('affection', 0)} / 下一階 {next_th}）\n{lvl_desc}",
        inline=False,
    )
    # 顯示角色簡介（好感度夠高才解鎖更多背景）
    affection = prog.get("affection", 0)
    bg = ch.get("background", "")
    if affection < 20:
        bg = bg[:100] + "…" if len(bg) > 100 else bg
    embed.add_field(name="角色背景", value=bg or "（未知）", inline=False)

    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    daily_count = prog.get("daily_count", 0) if prog.get("daily_date") == today else 0
    _annoy_state, _annoy_label, _, _ = _get_annoyance_state(daily_count)
    embed.add_field(
        name="互動資訊",
        value=f"今日已對話 {daily_count} 次　{_annoy_label}\n"
              f"已送禮 {prog.get('gifts_given', 0)} 次",
        inline=False,
    )
    embed.set_footer(text=f"角色ID: {char_id}")
    return embed


def _build_user_progress_embed(user_id_str: str, username: str) -> "discord.Embed":
    """建構玩家進度總覽 Embed。"""
    embed = discord.Embed(
        title=f"📊 {username} 的互動進度",
        color=discord.Color.pink(),
        timestamp=datetime.now(timezone.utc),
    )
    prog = galgame_progress.get(user_id_str, {})
    if not prog:
        embed.description = "你還沒有與任何角色互動過。點擊「角色選擇」開始你的故事吧～"
        return embed

    for cid, p in prog.items():
        ch = galgame_characters.get(cid)
        if not ch:
            continue
        lvl_name, _ = _affection_level(p.get("affection", 0))
        next_th = _next_threshold(p.get("affection", 0))
        embed.add_field(
            name=f"{ch['name']}",
            value=f"{lvl_name}（好感度 {p.get('affection', 0)} / {next_th}）\n"
                  f"送禮 {p.get('gifts_given', 0)} 次｜劇情階段 {p.get('story_stage', 0)}",
            inline=True,
        )
    embed.set_footer(text="ICEA 互動小說系統")
    return embed


# ═══════════════════════════════════════════════════════════════════════
# 互動面板（對話 + 送禮）
# ═══════════════════════════════════════════════════════════════════════

class InteractionView(discord.ui.View):
    """角色互動面板：對話、送禮、返回。"""

    def __init__(self, user_id_str: str, char_id: str):
        super().__init__(timeout=300)
        self.user_id_str = user_id_str
        self.char_id = char_id

    @discord.ui.button(label="對話", style=discord.ButtonStyle.primary, emoji="💬")
    async def chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id_str:
            await interaction.response.send_message("這不是你的面板！", ephemeral=True)
            return

        # 冷卻 + 每日上限檢查
        uid = self.user_id_str
        cid = self.char_id
        prog = galgame_progress.get(uid, {}).get(cid, {})
        now_ts = _time.time()
        cooldown = galgame_settings["interaction_cooldown"]
        if now_ts - prog.get("last_interact", 0) < cooldown:
            remaining = int(cooldown - (now_ts - prog.get("last_interact", 0)))
            await interaction.response.send_message(
                f"⏳ 冷卻中，請等待 {remaining} 秒。", ephemeral=True
            )
            return

        today = datetime.now(GMT8).strftime("%Y-%m-%d")
        if prog.get("daily_date") != today:
            prog["daily_date"] = today
            prog["daily_count"] = 0

        # 不再硬性擋每日上限，改為不耐煩系統：
        # 達到 max 後角色會在對話中表現出拒絕態度（AI 語氣 + 好感度懲罰），
        # 但玩家仍可強行傳訊——不像舊版直接擋掉。
        # 開啟 Modal 讓玩家輸入對話
        modal = ChatModal(uid, cid)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="送禮", style=discord.ButtonStyle.success, emoji="🎁")
    async def gift(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id_str:
            await interaction.response.send_message("這不是你的面板！", ephemeral=True)
            return

        ch = galgame_characters.get(self.char_id)
        if not ch:
            await interaction.response.send_message("❌ 角色不存在。", ephemeral=True)
            return

        # 顯示送禮面板
        embed = discord.Embed(
            title=f"🎁 送禮給 {ch['name']}",
            description=(
                "選擇禮物類型和金額。送禮會消耗琉璃幣，增加好感度。\n\n"
                f"角色偏好：{ch.get('gift_preferences', '無特別偏好')}\n\n"
                f"當前餘額：{get_balance(self.user_id_str)} {currency_name()}"
            ),
            color=discord.Color.gold(),
        )
        view = GiftView(self.user_id_str, self.char_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 重新顯示角色選擇
        if not galgame_characters:
            await interaction.response.send_message("❌ 沒有角色。", ephemeral=True)
            return
        options = []
        for cid, ch in galgame_characters.items():
            tagline = ch.get('tagline', ch.get('personality', '')[:30])
            options.append(discord.SelectOption(label=ch['name'], description=tagline[:100], value=cid))
        if len(options) > 25:
            options = options[:25]
        view = CharacterSelectView(self.user_id_str)
        view.select.options = options
        await interaction.response.send_message("🌸 請選擇要互動的角色：", view=view, ephemeral=True)


# ── 對話 Modal ──
class ChatModal(discord.ui.Modal):
    """玩家輸入對話內容的 Modal。"""

    def __init__(self, user_id_str: str, char_id: str):
        super().__init__(title="💬 輸入對話", timeout=120)
        self.user_id_str = user_id_str
        self.char_id = char_id
        self.input = discord.ui.TextInput(
            label="你想說什麼？",
            placeholder="輸入你想對角色說的話…",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        uid = self.user_id_str
        cid = self.char_id
        ch = galgame_characters.get(cid)
        if not ch:
            await interaction.response.send_message("❌ 角色不存在。", ephemeral=True)
            return

        user_msg = self.input.value.strip()
        prog = galgame_progress.get(uid, {}).get(cid, {})
        affection = prog.get("affection", 0)
        lvl_name, _ = _affection_level(affection)

        # 先回應一個「正在思考」的佔位訊息
        await interaction.response.send_message(
            f"💬 {ch['name']} 正在思考…", ephemeral=True
        )

        try:
            # 建構 AI prompt（含不耐煩提示注入）
            _daily_cnt = prog.get("daily_count", 0) if prog.get("daily_date") == datetime.now(GMT8).strftime("%Y-%m-%d") else 0
            _annoy_state, _, _, _annoy_hint = _get_annoyance_state(_daily_cnt)
            system_prompt = _build_chat_system_prompt(ch, affection, lvl_name, prog)
            if _annoy_hint:
                system_prompt += f"\n\n== 今日情緒狀態 ==\n{_annoy_hint}"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]

            result = await call_chat_api(
                messages, chat_ai_settings,
                max_tokens=600,
                timeout_total=45,
                timeout_read=40,
                is_background=False,
                fallback_mode="rate_limited",
                fallback_user_id=uid,
                category="entertainment",
            )

            reply = result.get("content", "").strip()
            if not reply:
                reply = "（沉默了一會兒，似乎不知道該說什麼…）"

            # 更新好感度（不耐煩系統：同日對話過多時遞減/懲罰）
            _daily = prog.get("daily_count", 0)
            _annoy_state, _annoy_label, _annoy_delta, _ = _get_annoyance_state(_daily)
            base_gain = galgame_settings["affection_per_chat"]
            if _annoy_delta == 0:
                # normal: 正常增長；slight: 不增不減
                affection_gain = base_gain if _annoy_state == "normal" else 0
            else:
                # annoyed/refusing: 好感度倒退
                affection_gain = _annoy_delta
            prog["affection"] = max(0, prog.get("affection", 0) + affection_gain)
            prog["last_interact"] = _time.time()
            prog["daily_count"] = _daily + 1
            prog["daily_date"] = datetime.now(GMT8).strftime("%Y-%m-%d")

            # 檢查好感度升級
            new_lvl_name, _ = _affection_level(prog["affection"])
            level_up_msg = ""
            if new_lvl_name != lvl_name:
                level_up_msg = f"\n\n✨ **好感度升級！** 現在與 {ch['name']} 的關係：{new_lvl_name}"

            # 檢查劇情事件
            story_msg = _check_story_event(uid, cid, prog)

            save_galgame()

            # 編輯回應
            embed = discord.Embed(
                title=f"💬 {ch['name']}",
                description=reply[:2000],
                color=discord.Color.pink(),
            )
            _aff_str = f"{new_lvl_name}（{prog['affection']}）"
            if affection_gain > 0:
                _aff_str += f" +{affection_gain}"
            elif affection_gain < 0:
                _annoy_state2, _annoy_label2, _, _ = _get_annoyance_state(prog.get("daily_count", 1) - 1)
                _aff_str += f" {affection_gain}（{_annoy_label2}）"
            embed.add_field(
                name="好感度",
                value=_aff_str,
                inline=True,
            )
            if level_up_msg or story_msg:
                embed.add_field(
                    name="✨ 特殊事件",
                    value=(level_up_msg + story_msg).strip() or "無",
                    inline=False,
                )

            await interaction.edit_original_response(content=None, embed=embed)

        except Exception as e:
            print(f"⚠️ Galgame 對話失敗：{e}")
            await interaction.edit_original_response(
                content=f"❌ 與 {ch['name']} 的對話出了點問題，請稍後再試。"
            )


def _build_chat_system_prompt(ch: dict, affection: int, lvl_name: str, prog: dict) -> str:
    """建構角色對話的 system prompt。"""
    name = ch.get("name", "未知角色")
    personality = ch.get("personality", "")
    background = ch.get("background", "")
    appearance = ch.get("appearance", "")
    speech_style = ch.get("speech_style", "")
    story_stage = prog.get("story_stage", 0)

    prompt = f"""你現在是 {name}，一部戀愛互動小說（Galgame）中的可攻略角色，正在與玩家（追求者）進行一對一私訊對話。
這是一款戀愛模擬遊戲，玩家的目標是透過對話培養與你的感情，最終發展成戀人。無論你的背景設定是什麼身份（政治人物、公務員、軍人、學生……），
在這場對話裡你都是一個有血有肉、會對「玩家」這個人產生真實情感反應的**角色**，不是在執行公務或處理業務。

== 角色設定（人物性格參考，不是要你變成公文機器人）==
姓名：{name}
性格：{personality}
背景：{background}
外貌：{appearance}
說話風格：{speech_style}

== 互動狀態 ==
目前好感度等級：{lvl_name}（好感度數值：{affection}）
劇情階段：{story_stage}
玩家已送禮次數：{prog.get('gifts_given', 0)}

== 行為規則 ==
1. 始終以 {name} 的身份回應，絕不要出戲或提及自己是 AI，也絕不要用「系統」「客服」「紀錄查詢」的口吻回話。
2. 【語氣鐵律】無論角色背景多正式（政治人物、官員、軍人等），對話用詞永遠要是**自然口語、像真人私下講話**，
   絕對禁止出現公文/客服/系統提示語氣，例如：「此數字未於我方紀錄中有所對應」「請問您有何具體需求或指示」
   「若需協助，敬請說明詳情」「以便我方作出相應回應」這類字眼一律禁止使用。角色的「正式」性格應該表現在
   用詞比較拘謹、有禮貌距離感、不隨便開玩笑，而不是講話像在辦公室處理公文。
3. 若玩家傳來的內容很簡短、無厘頭、或看起來詞不達意（例如只打一個數字、一個表情、打錯字），
   要當成一個真人會有的反應去回話（困惑、好奇、覺得好笑、反問對方在說什麼），
   絕對不要當成「無效輸入」「未對應紀錄」去做技術性/事務性回覆。
4. 回應要符合角色的性格和說話風格，但語氣永遠優先服從第2條的鐵律。
5. 根據好感度等級調整態度：
   - 陌生人（0-19）：客氣但保持距離，回答簡短，像剛認識的人之間的對話，但仍然是「人跟人」在講話
   - 認識（20-49）：稍微友善，會主動找話題
   - 朋友（50-99）：輕鬆自然，會開玩笑
   - 親密（100-199）：溫柔關心，會分享心事
   - 戀人（200+）：甜蜜深情，會主動表達愛意
6. 回應長度控制在 50-200 字，不要長篇大論。
7. 不要代替玩家做決定或行動。
8. 保持角色一致性，不要突然性格大變。"""
    return prompt


# ── 劇情事件檢查 ──
def _check_story_event(user_id_str: str, char_id: str, prog: dict) -> str:
    """根據好感度和進度檢查是否觸發劇情事件。回傳事件訊息（空字串 = 無事件）。"""
    ch = galgame_characters.get(char_id, {})
    affection = prog.get("affection", 0)
    stage = prog.get("story_stage", 0)
    flags = prog.get("flags", [])

    events = ch.get("story_events", [])
    if not events:
        return ""

    for evt in events:
        evt_affection = evt.get("affection_threshold", 0)
        evt_stage = evt.get("stage", 0)
        evt_flag = evt.get("flag", "")
        # 觸發條件：好感度達標 + 劇情階段匹配 + 旗標未觸發
        if affection >= evt_affection and stage >= evt_stage and evt_flag and evt_flag not in flags:
            flags.append(evt_flag)
            prog["flags"] = flags
            if evt.get("advance_stage"):
                prog["story_stage"] = stage + 1
            return f"\n🎬 **劇情事件觸發！** {evt.get('title', '')}\n{evt.get('description', '')}"

    return ""


# ── 送禮面板 ──
class GiftView(discord.ui.View):
    """送禮面板：選擇禮物類型。"""

    def __init__(self, user_id_str: str, char_id: str):
        super().__init__(timeout=120)
        self.user_id_str = user_id_str
        self.char_id = char_id

        # 禮物類型按鈕
        for gtype, ginfo in GIFT_TYPES.items():
            btn = discord.ui.Button(
                label=ginfo["name"],
                style=discord.ButtonStyle.secondary,
                custom_id=f"galgame_gift:{gtype}",
            )
            btn.callback = self._make_gift_callback(gtype)
            self.add_item(btn)

    def _make_gift_callback(self, gtype: str):
        async def callback(interaction: discord.Interaction):
            if str(interaction.user.id) != self.user_id_str:
                await interaction.response.send_message("這不是你的面板！", ephemeral=True)
                return
            # 開啟金額輸入 Modal
            modal = GiftModal(self.user_id_str, self.char_id, gtype)
            await interaction.response.send_modal(modal)
        return callback


class GiftModal(discord.ui.Modal):
    """送禮金額輸入 Modal。"""

    def __init__(self, user_id_str: str, char_id: str, gift_type: str):
        ginfo = GIFT_TYPES.get(gift_type, {})
        super().__init__(title=f"🎁 送禮：{ginfo.get('name', gift_type)}", timeout=120)
        self.user_id_str = user_id_str
        self.char_id = char_id
        self.gift_type = gift_type

        min_cost = ginfo.get("min_cost", 50)
        self.amount_input = discord.ui.TextInput(
            label=f"花費金額（最低 {min_cost} 琉璃幣）",
            placeholder=f"輸入要花費的金額（{min_cost}-{galgame_settings['gift_max_cost']}）",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.amount_input)

        self.message_input = discord.ui.TextInput(
            label="附帶訊息（選填）",
            placeholder="想對角色說的話…",
            style=discord.TextStyle.paragraph,
            max_length=200,
            required=False,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        uid = self.user_id_str
        cid = self.char_id
        ch = galgame_characters.get(cid)
        if not ch:
            await interaction.response.send_message("❌ 角色不存在。", ephemeral=True)
            return

        # 解析金額
        try:
            amount = int(self.amount_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ 金額必須是數字。", ephemeral=True)
            return

        ginfo = GIFT_TYPES.get(self.gift_type, {})
        min_cost = ginfo.get("min_cost", galgame_settings["gift_min_cost"])
        max_cost = galgame_settings["gift_max_cost"]
        if amount < min_cost:
            await interaction.response.send_message(f"❌ 最低消費 {min_cost} {currency_name()}。", ephemeral=True)
            return
        if amount > max_cost:
            await interaction.response.send_message(f"❌ 最高消費 {max_cost} {currency_name()}。", ephemeral=True)
            return

        # 檢查餘額
        balance = get_balance(uid)
        if balance < amount:
            await interaction.response.send_message(
                f"❌ 餘額不足。你需要 {amount} {currency_name()}，目前只有 {balance} {currency_name()}。",
                ephemeral=True
            )
            return

        # 扣款
        add_balance(uid, -amount, interaction.user.display_name)

        # 計算好感度增加
        base = galgame_settings["affection_per_gift_base"]
        multiplier = ginfo.get("multiplier", 1.0)

        # 角色偏好加成
        preferences = ch.get("gift_preferences", "").lower()
        pref_bonus = 1.0
        pref_keywords = {
            "flower": ["花", "flower"],
            "sweets": ["甜", "sweet", "甜點", "蛋糕"],
            "book": ["書", "book"],
            "jewelry": ["珠寶", "jewelry", "首飾", "飾品"],
        }
        if self.gift_type in pref_keywords:
            for kw in pref_keywords[self.gift_type]:
                if kw in preferences:
                    pref_bonus = 1.5
                    break

        # 金額加成（每 100 琉璃幣 +1 好感度）
        amount_bonus = amount // 100

        affection_gain = int((base + amount_bonus) * multiplier * pref_bonus)
        if affection_gain < 1:
            affection_gain = 1

        # 更新進度
        if uid not in galgame_progress:
            galgame_progress[uid] = {}
        if cid not in galgame_progress[uid]:
            galgame_progress[uid][cid] = {
                "affection": 0, "last_interact": 0, "daily_count": 0,
                "daily_date": "", "gifts_given": 0, "flags": [], "story_stage": 0,
            }
        prog = galgame_progress[uid][cid]
        old_affection = prog.get("affection", 0)
        old_lvl_name, _ = _affection_level(old_affection)
        prog["affection"] = old_affection + affection_gain
        prog["gifts_given"] = prog.get("gifts_given", 0) + 1
        prog["last_interact"] = _time.time()

        # 檢查升級和劇情
        new_lvl_name, _ = _affection_level(prog["affection"])
        level_up_msg = ""
        if new_lvl_name != old_lvl_name:
            level_up_msg = f"\n\n✨ **好感度升級！** {old_lvl_name} → {new_lvl_name}"
        story_msg = _check_story_event(uid, cid, prog)

        save_galgame()

        # 偏好提示
        pref_msg = ""
        if pref_bonus > 1.0:
            pref_msg = f"\n💝 {ch['name']} 很喜歡這個禮物！好感度加成 ×{pref_bonus}"
        else:
            pref_msg = f"\n🎁 {ch['name']} 收下了你的禮物。"

        embed = discord.Embed(
            title=f"🎁 送禮成功！",
            description=(
                f"送給 **{ch['name']}** 的禮物：{ginfo.get('name', self.gift_type)}\n"
                f"花費：{amount} {currency_name()}\n"
                f"好感度 +{affection_gain}"
                f"{pref_msg}{level_up_msg}{story_msg}"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="好感度",
            value=f"{new_lvl_name}（{prog['affection']}）",
            inline=True,
        )
        embed.add_field(
            name="餘額",
            value=f"{get_balance(uid)} {currency_name()}",
            inline=True,
        )

        # 附帶訊息
        msg = self.message_input.value.strip()
        if msg:
            embed.add_field(name="你的訊息", value=msg[:200], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════════════
# 管理員指令（Dashboard API）
# ═══════════════════════════════════════════════════════════════════════

async def api_get_galgame_settings(request):
    """取得 Galgame 設定。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({
        "channel_id": galgame_settings.get("channel_id"),
        "interaction_cooldown": galgame_settings.get("interaction_cooldown", 300),
        "daily_interact_limit": galgame_settings.get("daily_interact_limit", 20),
        "gift_min_cost": galgame_settings.get("gift_min_cost", 50),
        "gift_max_cost": galgame_settings.get("gift_max_cost", 5000),
        "affection_per_gift_base": galgame_settings.get("affection_per_gift_base", 5),
        "affection_per_chat": galgame_settings.get("affection_per_chat", 1),
        "characters": galgame_characters,
    })


async def api_set_galgame_settings(request):
    """更新 Galgame 設定。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    for key in ("channel_id", "interaction_cooldown", "daily_interact_limit",
                "gift_min_cost", "gift_max_cost",
                "affection_per_gift_base", "affection_per_chat"):
        if key in body:
            if key == "channel_id":
                galgame_settings[key] = body[key] if body[key] else None
            else:
                galgame_settings[key] = int(body[key])
    save_galgame()
    # 如果頻道變了，重新發送面板
    if "channel_id" in body:
        asyncio.ensure_future(setup_galgame_panel())
    return web.json_response({"ok": True})


async def api_galgame_add_character(request):
    """新增角色。只有擁有者可以操作。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    if str(user.get("user_id", "")) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — 只有擁有者可以新增角色"}, status=403)

    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return web.json_response({"error": "角色名稱為必填"}, status=400)

    import uuid
    char_id = body.get("id", "").strip() or f"char_{uuid.uuid4().hex[:8]}"
    if char_id in galgame_characters:
        return web.json_response({"error": "角色ID已存在"}, status=400)

    galgame_characters[char_id] = {
        "name": name,
        "tagline": body.get("tagline", "").strip(),
        "background": body.get("background", "").strip(),
        "personality": body.get("personality", "").strip(),
        "appearance": body.get("appearance", "").strip(),
        "speech_style": body.get("speech_style", "").strip(),
        "gift_preferences": body.get("gift_preferences", "").strip(),
        "story_events": body.get("story_events", []),
        "created_date": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M"),
    }
    save_galgame()
    asyncio.ensure_future(refresh_galgame_panel())
    return web.json_response({"ok": True, "char_id": char_id})


async def api_galgame_update_character(request):
    """編輯角色。只有擁有者可以操作。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    if str(user.get("user_id", "")) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — 只有擁有者可以編輯角色"}, status=403)

    body = await request.json()
    char_id = body.get("id", "").strip()
    if char_id not in galgame_characters:
        return web.json_response({"error": "角色不存在"}, status=404)

    ch = galgame_characters[char_id]
    for key in ("name", "tagline", "background", "personality",
                "appearance", "speech_style", "gift_preferences", "story_events"):
        if key in body:
            ch[key] = body[key]
    save_galgame()
    asyncio.ensure_future(refresh_galgame_panel())
    return web.json_response({"ok": True})


async def api_galgame_delete_character(request):
    """刪除角色。只有擁有者可以操作。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    if str(user.get("user_id", "")) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — 只有擁有者可以刪除角色"}, status=403)

    body = await request.json()
    char_id = body.get("id", "").strip()
    if char_id not in galgame_characters:
        return web.json_response({"error": "角色不存在"}, status=404)

    del galgame_characters[char_id]
    # 清理玩家進度中該角色的資料
    for uid, pd in galgame_progress.items():
        pd.pop(char_id, None)
    save_galgame()
    asyncio.ensure_future(refresh_galgame_panel())
    return web.json_response({"ok": True})


# ═══════════════════════════════════════════════════════════════════════
# Discord 指令（使用 app_commands.Group，與其他模組一致）
# ═══════════════════════════════════════════════════════════════════════

class GalgameGroup(app_commands.Group):
    """互動小說系統指令群組。"""
    def __init__(self):
        super().__init__(name="vn", description="🌸 互動小說 — 角色花園")

    @app_commands.command(name="start", description="開啟互動小說面板（僅自己看得到，建議用 /vn set_channel 設定固定看板）")
    async def vn_start(self, interaction: discord.Interaction):
        embed = _build_galgame_embed()
        view = GalgamePanelView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="set_channel", description="設定互動小說固定看板頻道（僅擁有者）")
    @app_commands.describe(channel="要固定顯示看板的頻道")
    async def vn_set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if str(interaction.user.id) != str(BOT_OWNER_ID):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        galgame_settings["channel_id"] = channel.id
        galgame_settings["message_id"] = None  # 強制重新發送（清除舊頻道殘留的關聯）
        save_galgame()

        await interaction.response.send_message(f"⏳ 正在於 {channel.mention} 設定互動小說看板...", ephemeral=True)
        new_msg = await setup_galgame_panel()
        if new_msg:
            await interaction.followup.send(f"✅ 互動小說看板已設定至 {channel.mention}，將即時更新。", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ 看板設定已儲存，但發送失敗，請確認機器人在 {channel.mention} 有發言權限。", ephemeral=True)

    @app_commands.command(name="admin", description="互動小說管理（僅擁有者）")
    async def vn_admin(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(BOT_OWNER_ID):
            await interaction.response.send_message("❌ 只有擁有者可以使用此指令。", ephemeral=True)
            return
        embed = _build_galgame_admin_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)



# ── 微國家百科匯入角色 ──

async def api_galgame_import_micropedia(request):
    """從微國家百科 URL 抓取頁面內容，用 AI 整理成 Galgame 角色表單欄位。
    POST /api/galgame/import-micropedia
    body: {"url": "https://www.micropedia.site/wiki/某人"}
    回傳: {"ok": true, "character": {name, tagline, personality, ...}}"""
    import urllib.parse as _up
    import re as _re
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    if str(user.get("user_id", "")) != str(BOT_OWNER_ID):
        return web.json_response({"error": "forbidden — 只有擁有者可以匯入角色"}, status=403)

    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return web.json_response({"error": "請提供微國家百科 URL"}, status=400)

    # 從 URL 提取頁面標題
    # 格式: https://www.micropedia.site/wiki/頁面名稱
    parsed = _up.urlparse(url)
    if "micropedia.site" not in (parsed.netloc or "").lower():
        return web.json_response({"error": "URL 必須是 micropedia.site 的頁面"}, status=400)

    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) < 2 or path_parts[0].lower() != "wiki":
        return web.json_response({"error": "URL 格式應為 https://www.micropedia.site/wiki/頁面名稱"}, status=400)

    page_title = _up.unquote(path_parts[1])
    if not page_title:
        return web.json_response({"error": "無法從 URL 解析頁面標題"}, status=400)

    # 用 MediaWiki API 抓取頁面 wikitext
    try:
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        api_url = (
            f"https://www.micropedia.site/api.php?action=query"
            f"&titles={_up.quote(page_title)}"
            f"&prop=revisions&rvprop=content&format=json&redirects=1"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers={"User-Agent": "DiscordBot (galgame-import/1.0)"}, timeout=timeout) as resp:
                if resp.status != 200:
                    return web.json_response({"error": f"微國家百科 API 回傳 {resp.status}"}, status=502)
                data = await resp.json()

        pages = data.get("query", {}).get("pages", {})
        wikitext = ""
        resolved_title = page_title
        for pid, page in pages.items():
            if pid == "-1" or "missing" in page:
                continue
            revs = page.get("revisions", [])
            if revs:
                wikitext = revs[0].get("*", "")
                resolved_title = page.get("title", page_title)

        if not wikitext or len(wikitext) < 10:
            return web.json_response({"error": f"找不到頁面「{page_title}」或內容為空"}, status=404)

        # 清理 wikitext → 純文字
        clean_text = _clean_wikitext(wikitext)
        if len(clean_text) > 4000:
            clean_text = clean_text[:4000]

    except Exception as e:
        return web.json_response({"error": f"抓取頁面失敗：{e}"}, status=500)

    # 用 AI 把百科內容整理成 Galgame 角色欄位
    ai_prompt = f"""你是戀愛互動小說（Galgame）的角色設計師。以下是來自微國家百科的條目「{resolved_title}」的內容。
請根據這些資訊，把這個真實人物「改編」成一個**可攻略的戀愛遊戲角色**。

【最重要的要求】這個角色最終會被拿去跟玩家談戀愛、私訊聊天培養好感度，所以：
- personality 跟 speech_style 絕對不能是「這個人在公務/政治場合上表現出來的官方作風」（例如公文用詞、
  官腔、客服式應對、會議發言的語氣），而是要推演出這個人**私底下、面對感興趣的對象時**可能會有的個人特質
  與講話方式（例如：外表嚴肅但私下很會關心人、講話直接不拐彎、容易害羞、喜歡用反問句掩飾情緒……）。
- 可以保留這個人的職業/身份背景（寫在 background 裡沒問題），但 personality/speech_style 必須是
  「一個會跟人聊天談戀愛的活人」的樣子，不能寫成像在描述一份公務員績效報告或人物百科的行事風格條列。
- 絕對不要出現「制式回覆」「官方說法」「一律以...處理」這類會讓角色在對話中講話像客服機器人的措辭。

其他要求：
1. 把這個真實人物的資訊轉化為遊戲角色的設定
2. 欄位要豐富、有細節，但不要捏造百科中沒有的核心事實
3. 如果百科內容不足，可以用合理的想像補充，但要標明是推測

請直接回傳 JSON（不要加 markdown code block），格式如下：
{{
  "name": "角色名稱（直接用條目標題或人物本名）",
  "tagline": "一句話簡介（20字以內，要有戀愛遊戲角色的吸引力，不要寫成職稱介紹）",
  "personality": "性格描述（3-5個私下的個人性格特質，用頓號分隔，不是公務作風）",
  "background": "背景故事（根據百科內容改寫成的角色故事，200-400字，用故事化的口吻而非百科條目列舉）",
  "appearance": "外貌描述（如果百科有提到就忠實描述，沒有就根據角色形象推測，1-2句話）",
  "speech_style": "說話風格（私下聊天時會怎麼講話，1-2句話，要是自然口語不是公文腔）",
  "gift_preferences": "禮物偏好（根據角色特質推測2-3個適合的禮物類型，用頓號分隔）"
}}

百科內容：
{clean_text}"""

    try:
        result = await call_chat_api(
            messages=[
                {"role": "system", "content": "你是戀愛互動小說（Galgame）角色設計師，擅長把真實人物資料改編成有血有肉、會談戀愛的遊戲角色，性格與說話風格要是私下聊天的樣子而不是公務/官方作風。只回傳 JSON，不要加任何說明文字或 markdown 格式。"},
                {"role": "user", "content": ai_prompt},
            ],
            settings=chat_ai_settings,
            max_tokens=1500,
            timeout_total=60,
            timeout_read=55,
            is_background=True,
            fallback_mode="full",
            category="entertainment",
        )

        ai_text = result.get("content", "").strip()
        if not ai_text:
            return web.json_response({"error": "AI 生成角色設定失敗（空回應）"}, status=500)

        # 去除可能的 markdown code block 包裹
        ai_text = _re.sub(r"^```(?:json)?\s*", "", ai_text)
        ai_text = _re.sub(r"\s*```$", "", ai_text)

        character_data = json_module.loads(ai_text)

        # 確保所有欄位都是字串
        for key in ("name", "tagline", "personality", "background", "appearance", "speech_style", "gift_preferences"):
            val = character_data.get(key, "")
            if not isinstance(val, str):
                character_data[key] = str(val)
            character_data[key] = val.strip()

        return web.json_response({"ok": True, "character": character_data, "source_title": resolved_title})

    except json_module.JSONDecodeError as e:
        return web.json_response({"error": f"AI 回應解析失敗（非 JSON 格式）：{e}"}, status=500)
    except Exception as e:
        return web.json_response({"error": f"AI 生成角色設定失敗：{e}"}, status=500)

# ═══════════════════════════════════════════════════════════════════════
# 啟動
# ═══════════════════════════════════════════════════════════════════════

load_galgame()

# 註冊 API 路由（在模組載入時透過全域變數註冊，主程式會收集）
# 格式：(path, method, handler) — 與主程式 discord_borda_poll.py 的註冊迴圈
# 解包順序一致（該迴圈用 `for _path, _method, _handler in ...`）。
# 注意：先前這裡誤寫成 (method, path, handler)，導致 _method 變數實際拿到的是
# path 字串、永遠不等於 "GET"/"PUT"/"POST"/"DELETE"，所有 galgame API 路由
# 從模組建立以來就從未被 app.router 真正註冊過（迴圈本身不拋例外，靜默失敗），
# 造成 dashboard 呼叫全部回傳 aiohttp 預設的 404 頁面。已修正順序。
_galgame_api_routes = [
    ("/api/galgame-settings",       "GET",    api_get_galgame_settings),
    ("/api/galgame-settings",       "PUT",    api_set_galgame_settings),
    ("/api/galgame/character",      "POST",   api_galgame_add_character),
    ("/api/galgame/character",      "PUT",    api_galgame_update_character),
    ("/api/galgame/character",      "DELETE", api_galgame_delete_character),
    ("/api/galgame/import-micropedia", "POST", api_galgame_import_micropedia),
]

# 持久化 View 由主程式統一註冊（bot.add_view），這裡不重複

print(f"💬 Galgame 模組已載入：{len(galgame_characters)} 個角色")
