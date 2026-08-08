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
    "daily_interact_limit": 20,   # 每日互動次數上限
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
            f"每日互動上限：{galgame_settings['daily_interact_limit']} 次"
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
    return bot.get_channel(int(ch_id))


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
            f"每日上限：{galgame_settings.get('daily_interact_limit', 20)} 次\n"
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

        # 每日上限檢查
        today = datetime.now(GMT8).strftime("%Y-%m-%d")
        if prog.get("daily_date") != today:
            prog["daily_date"] = today
            prog["daily_count"] = 0
        daily_limit = galgame_settings["daily_interact_limit"]
        if prog.get("daily_count", 0) >= daily_limit:
            await interaction.response.send_message(
                f"📅 今日與 {ch['name']} 的互動次數已達上限（{daily_limit} 次）。明天再來吧～", ephemeral=True
            )
            return

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
    embed.add_field(
        name="互動資訊",
        value=f"今日已互動 {daily_count} / {galgame_settings['daily_interact_limit']} 次\n"
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
        if prog.get("daily_count", 0) >= galgame_settings["daily_interact_limit"]:
            await interaction.response.send_message("📅 今日互動次數已達上限。", ephemeral=True)
            return

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
            # 建構 AI prompt
            system_prompt = _build_chat_system_prompt(ch, affection, lvl_name, prog)
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

            # 更新好感度
            affection_gain = galgame_settings["affection_per_chat"]
            prog["affection"] = prog.get("affection", 0) + affection_gain
            prog["last_interact"] = _time.time()
            prog["daily_count"] = prog.get("daily_count", 0) + 1
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
            embed.add_field(
                name="好感度",
                value=f"{new_lvl_name}（{prog['affection']}）{'+'+str(affection_gain) if affection_gain else ''}",
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

    prompt = f"""你現在是 {name}，一個互動小說遊戲中的角色。你正在與一位玩家進行對話。

== 角色設定（絕對不可違背）==
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
1. 始終以 {name} 的身份回應，絕不要出戲或提及自己是 AI。
2. 回應要符合角色的性格和說話風格。
3. 根據好感度等級調整態度：
   - 陌生人（0-19）：禮貌但疏遠，回答簡短
   - 認識（20-49）：稍微友善，會主動話題
   - 朋友（50-99）：輕鬆自然，會開玩笑
   - 親密（100-199）：溫柔關心，會分享心事
   - 戀人（200+）：甜蜜深情，會主動表達愛意
4. 回應長度控制在 50-200 字，不要長篇大論。
5. 不要代替玩家做決定或行動。
6. 保持角色一致性，不要突然性格大變。"""
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

    @app_commands.command(name="start", description="開啟互動小說面板")
    async def vn_start(self, interaction: discord.Interaction):
        embed = _build_galgame_embed()
        view = GalgamePanelView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="admin", description="互動小說管理（僅擁有者）")
    async def vn_admin(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(BOT_OWNER_ID):
            await interaction.response.send_message("❌ 只有擁有者可以使用此指令。", ephemeral=True)
            return
        embed = _build_galgame_admin_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════════════
# 啟動
# ═══════════════════════════════════════════════════════════════════════

load_galgame()

# 註冊 API 路由（在模組載入時透過全域變數註冊，主程式會收集）
_galgame_api_routes = [
    ("GET",  "/api/galgame-settings",       api_get_galgame_settings),
    ("PUT",  "/api/galgame-settings",       api_set_galgame_settings),
    ("POST", "/api/galgame/character",      api_galgame_add_character),
    ("PUT",  "/api/galgame/character",      api_galgame_update_character),
    ("DELETE", "/api/galgame/character",    api_galgame_delete_character),
]

# 持久化 View 由主程式統一註冊（bot.add_view），這裡不重複

print(f"💬 Galgame 模組已載入：{len(galgame_characters)} 個角色")
