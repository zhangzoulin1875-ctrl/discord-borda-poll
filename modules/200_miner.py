# ═══════════════════════════════════════════════════════════════════
# Module: 200_miner (琉璃幣礦工 · 掘金之旅)
# 放置型礦業大亨小遊戲：解鎖礦坑、招募礦工、升級產量、雇用經理自動收礦。
# 固定頻道持久化公開面板（比照 galgame/siege 多伺服器主/子面板模式）。
# 掛機邏輯：last_tick 時間戳持久化到 Drive，重啟/離線期間的產出會在
# 下次結算時自動補算（真正的放置型玩法，不需要玩家一直盯著）。
# ═══════════════════════════════════════════════════════════════════

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

MINER_FILE = os.path.join(DATA_DIR, "miner.json")
MINER_TICK_SECONDS = 60          # 背景結算週期
MINER_CAP_HOURS = 8              # 未雇經理礦坑的最大累積時數（超過就浪費，鼓勵手動收取）

# ── 礦坑定義（固定五層，越深產量越高、解鎖越貴）──
SHAFT_DEFS = [
    {"id": "1", "name": "🪨 淺層礦坑", "unlock_cost": 0,      "base_output": 2,   "recruit_base": 50,   "upgrade_base": 200,   "manager_cost": 5000},
    {"id": "2", "name": "⛏️ 岩層礦坑", "unlock_cost": 3000,   "base_output": 6,   "recruit_base": 150,  "upgrade_base": 600,   "manager_cost": 15000},
    {"id": "3", "name": "🕳️ 深層礦坑", "unlock_cost": 15000,  "base_output": 16,  "recruit_base": 400,  "upgrade_base": 1600,  "manager_cost": 40000},
    {"id": "4", "name": "🌋 熔岩礦坑", "unlock_cost": 60000,  "base_output": 40,  "recruit_base": 1000, "upgrade_base": 4000,  "manager_cost": 120000},
    {"id": "5", "name": "💎 水晶礦坑", "unlock_cost": 250000, "base_output": 100, "recruit_base": 2500, "upgrade_base": 10000, "manager_cost": 400000},
]

def _shaft_def(shaft_id: str) -> dict:
    return next(s for s in SHAFT_DEFS if s["id"] == shaft_id)

miner_settings = {
    "enabled": True,
    "channel_id": None,            # 礦工主面板頻道
    "message_id": None,            # 主面板訊息 ID
    "guild_channels": {},          # 訪客伺服器子面板 {guild_id_str: channel_id_str}
    "guild_panel_messages": {},    # 訪客伺服器子面板訊息 ID {guild_id_str: message_id}
}

# {user_id_str: {"username": str, "lifetime_earned": int, "shafts": {shaft_id: {...}}}}
miner_players = {}


# ── 存檔/載入 ──
def save_miner():
    try:
        _save_json_file(MINER_FILE, {"settings": miner_settings, "players": miner_players}, indent=2)
        try:
            asyncio.ensure_future(_immediate_drive_upload("miner.json"))
        except Exception:
            pass  # 沒有 event loop（例如啟動載入階段）時靜默跳過
    except Exception as e:
        print(f"⚠️ 礦工遊戲存檔失敗：{e}")


def load_miner():
    """從本地載入礦工遊戲狀態。last_tick 時間戳會保留，離線期間的產出
    在下次背景結算時自動補算，達成真正的放置型掛機玩法。"""
    global miner_settings, miner_players
    try:
        if os.path.exists(MINER_FILE):
            with open(MINER_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            if isinstance(data.get("settings"), dict):
                miner_settings.update(data["settings"])
            if isinstance(data.get("players"), dict):
                miner_players.update(data["players"])
            print(f"⛏️ 礦工遊戲已載入：{len(miner_players)} 位玩家")
    except Exception as e:
        print(f"⚠️ 礦工遊戲載入失敗：{e}")


# ── 核心公式 ──
def _production_rate(sdef: dict, miners: int, level: int) -> float:
    """每分鐘產量 = 基礎產量 × 礦工數 × (1 + 0.5×(等級-1))。"""
    return sdef["base_output"] * miners * (1 + 0.5 * (level - 1))


def _storage_cap(sdef: dict, miners: int, level: int) -> float:
    return _production_rate(sdef, miners, level) * MINER_CAP_HOURS * 60


def _recruit_cost(sdef: dict, current_miners: int) -> int:
    return int(sdef["recruit_base"] * (1.15 ** current_miners))


def _upgrade_cost(sdef: dict, current_level: int) -> int:
    return int(sdef["upgrade_base"] * (1.35 ** max(0, current_level - 1)))


def _ensure_miner_player(uid: str, username: str = "") -> dict:
    """確保玩家存在，不存在則初始化（第一座礦坑免費解鎖並附贈1名礦工）。"""
    if uid not in miner_players:
        shafts = {}
        for s in SHAFT_DEFS:
            if s["id"] == "1":
                shafts[s["id"]] = {"unlocked": True, "miners": 1, "level": 1, "storage": 0.0, "last_tick": _time.time(), "manager": False}
            else:
                shafts[s["id"]] = {"unlocked": False, "miners": 0, "level": 0, "storage": 0.0, "last_tick": _time.time(), "manager": False}
        miner_players[uid] = {"username": username or uid, "lifetime_earned": 0, "shafts": shafts}
        save_miner()
    else:
        p = miner_players[uid]
        if username and p.get("username") != username:
            p["username"] = username
        # 補齊未來新增的礦坑（向前相容）
        for s in SHAFT_DEFS:
            if s["id"] not in p.setdefault("shafts", {}):
                p["shafts"][s["id"]] = {"unlocked": False, "miners": 0, "level": 0, "storage": 0.0, "last_tick": _time.time(), "manager": False}
    return miner_players[uid]


def _apply_shaft_tick(uid: str, shaft_id: str) -> int:
    """結算單一礦坑自 last_tick 以來累積的產出。
    有雇經理：自動即時入帳（無上限，真正掛機）。
    沒雇經理：累積進 storage，超過上限就丟棄（鼓勵手動收取）。
    回傳本次經理自動入帳的金額（沒有經理則回傳0）。"""
    player = miner_players.get(uid)
    if not player:
        return 0
    shaft = player["shafts"].get(shaft_id)
    if not shaft:
        return 0
    now = _time.time()
    if not shaft.get("unlocked") or shaft.get("miners", 0) <= 0:
        shaft["last_tick"] = now
        return 0
    elapsed_min = max(0.0, (now - shaft.get("last_tick", now)) / 60)
    shaft["last_tick"] = now
    if elapsed_min <= 0:
        return 0
    sdef = _shaft_def(shaft_id)
    rate = _production_rate(sdef, shaft["miners"], shaft["level"])
    produced = rate * elapsed_min
    shaft["storage"] = shaft.get("storage", 0.0) + produced
    if shaft.get("manager"):
        collected = int(shaft["storage"])
        if collected > 0:
            shaft["storage"] -= collected
            add_balance(uid, collected, player.get("username", ""))
            player["lifetime_earned"] = player.get("lifetime_earned", 0) + collected
        return collected
    else:
        cap = _storage_cap(sdef, shaft["miners"], shaft["level"])
        if shaft["storage"] > cap:
            shaft["storage"] = cap
        return 0


def _apply_all_ticks_for_player(uid: str) -> int:
    total_auto = 0
    player = miner_players.get(uid)
    if not player:
        return 0
    for s in SHAFT_DEFS:
        total_auto += _apply_shaft_tick(uid, s["id"])
    return total_auto


# ── 面板 Embed ──
def _build_miner_embed() -> "discord.Embed":
    total_players = len(miner_players)
    total_miners = sum(sh.get("miners", 0) for p in miner_players.values() for sh in p.get("shafts", {}).values())
    total_unlocked = sum(1 for p in miner_players.values() for sh in p.get("shafts", {}).values() if sh.get("unlocked"))

    embed = discord.Embed(
        title="⛏️ 琉璃幣礦工 · 掘金之旅",
        color=discord.Color.dark_gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.description = (
        "🌄 **歡迎來到礦業大亨！** 招募礦工、升級礦坑、雇用經理，讓琉璃幣自動流進你的口袋！\n\n"
        "💎 **礦坑** — 逐層解鎖更深的礦坑，產量越高\n"
        "👷 **礦工** — 招募越多礦工，開採速度越快\n"
        "📈 **升級** — 升級礦坑等級，提升每位礦工的產量\n"
        "🧑‍💼 **經理** — 雇用經理後自動收礦，離線也照樣賺，不會滿倉浪費\n\n"
        "點擊下方「⛏️ 我的礦場」開始你的挖礦事業！"
    )
    embed.add_field(
        name="📊 伺服器統計",
        value=f"礦工總數：{total_miners} 人\n已解鎖礦坑數：{total_unlocked}\n投入玩家數：{total_players}",
        inline=False,
    )

    ranked = sorted(miner_players.items(), key=lambda kv: kv[1].get("lifetime_earned", 0), reverse=True)[:3]
    if ranked:
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i]} {p.get('username', uid)} — {p.get('lifetime_earned', 0):,} {currency_name()}" for i, (uid, p) in enumerate(ranked)]
        embed.add_field(name="🏆 開採排行榜 TOP3", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"ICEA 礦業大亨 · 未雇經理的礦坑最多累積{MINER_CAP_HOURS}小時產量，記得常來收礦！")
    return embed


def _build_player_mine_embed(uid: str, username: str) -> "discord.Embed":
    _apply_all_ticks_for_player(uid)
    p = _ensure_miner_player(uid, username)
    embed = discord.Embed(title=f"⛏️ {username} 的礦場", color=discord.Color.dark_gold())

    lines = []
    total_rate = 0.0
    total_storage = 0.0
    for sdef in SHAFT_DEFS:
        sh = p["shafts"][sdef["id"]]
        if not sh.get("unlocked"):
            lines.append(f"🔒 **{sdef['name']}** — 解鎖費用 {sdef['unlock_cost']:,} {currency_name()}")
            continue
        rate = _production_rate(sdef, sh["miners"], sh["level"])
        cap = _storage_cap(sdef, sh["miners"], sh["level"])
        total_rate += rate
        if sh.get("manager"):
            status = "🧑‍💼 經理已雇用（自動收礦）"
        else:
            status = f"📦 庫存 {sh['storage']:.0f} / {cap:.0f}"
            total_storage += sh["storage"]
        lines.append(f"**{sdef['name']}** Lv.{sh['level']}｜👷{sh['miners']}人｜{rate:.1f}/分\n　{status}")

    embed.description = "\n\n".join(lines)
    embed.add_field(name="⚡ 總產量", value=f"{total_rate:.1f} {currency_name()}/分鐘", inline=True)
    embed.add_field(name="📦 待收取", value=f"{total_storage:.0f} {currency_name()}", inline=True)
    embed.add_field(name="💰 累積開採", value=f"{p.get('lifetime_earned', 0):,} {currency_name()}", inline=True)
    embed.set_footer(text="點擊「🗂️ 管理礦坑」選擇礦坑進行解鎖／招募／升級／雇用經理")
    return embed


def _build_shaft_detail_embed(uid: str, shaft_id: str) -> "discord.Embed":
    _apply_shaft_tick(uid, shaft_id)
    p = miner_players[uid]
    sh = p["shafts"][shaft_id]
    sdef = _shaft_def(shaft_id)
    embed = discord.Embed(title=sdef["name"], color=discord.Color.dark_gold())

    if not sh.get("unlocked"):
        embed.description = f"🔒 尚未解鎖\n解鎖費用：**{sdef['unlock_cost']:,}** {currency_name()}"
        return embed

    rate = _production_rate(sdef, sh["miners"], sh["level"])
    cap = _storage_cap(sdef, sh["miners"], sh["level"])
    recruit_cost = _recruit_cost(sdef, sh["miners"])
    upgrade_cost = _upgrade_cost(sdef, sh["level"])

    embed.add_field(name="等級", value=f"Lv.{sh['level']}", inline=True)
    embed.add_field(name="礦工數", value=f"{sh['miners']} 人", inline=True)
    embed.add_field(name="產量", value=f"{rate:.1f} {currency_name()}/分", inline=True)

    if sh.get("manager"):
        embed.add_field(name="經理", value="🧑‍💼 已雇用（自動收礦，無上限）", inline=False)
    else:
        embed.add_field(name="庫存", value=f"{sh['storage']:.0f} / {cap:.0f}", inline=False)
        embed.add_field(name="雇用經理費用", value=f"{sdef['manager_cost']:,} {currency_name()}", inline=False)

    embed.add_field(name="👷 招募下一位礦工", value=f"{recruit_cost:,} {currency_name()}", inline=True)
    embed.add_field(name=f"📈 升級至 Lv.{sh['level'] + 1}", value=f"{upgrade_cost:,} {currency_name()}", inline=True)
    return embed


def _build_miner_leaderboard_embed() -> "discord.Embed":
    ranked = sorted(miner_players.items(), key=lambda kv: kv[1].get("lifetime_earned", 0), reverse=True)[:10]
    embed = discord.Embed(title="🏆 琉璃幣礦工 · 開採排行榜", color=discord.Color.gold())
    if not ranked:
        embed.description = "目前還沒有玩家開始挖礦。"
        return embed
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, p) in enumerate(ranked):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} **{p.get('username', uid)}** — {p.get('lifetime_earned', 0):,} {currency_name()}")
    embed.description = "\n".join(lines)
    return embed


MINER_HELP_TEXT = (
    "⛏️ **琉璃幣礦工 · 掘金之旅 玩法說明**\n\n"
    "🏔️ **礦坑系統**\n"
    "共5層礦坑，由淺入深依序解鎖，越深的礦坑基礎產量越高，但解鎖費用也越貴。\n\n"
    "👷 **招募礦工**\n"
    "每座已解鎖礦坑可招募更多礦工，礦工越多產量越高。招募費用會隨人數遞增。\n\n"
    "📈 **升級礦坑**\n"
    "升級礦坑等級可提升每位礦工的產量（每級+50%）。升級費用會隨等級遞增。\n\n"
    "📦 **庫存與收取**\n"
    "沒有雇用經理的礦坑，產出會累積在庫存中，最多累積8小時份量，超過就不再增加——記得常來點「收取全部」！\n\n"
    "🧑‍💼 **雇用經理**\n"
    "雇用經理後，該礦坑會自動即時把產出存進你的餘額，不受8小時庫存上限，離線期間也持續累積，是真正的放置收益。\n\n"
    "💤 **掛機機制**\n"
    "即使你沒有點開面板，重啟或離線期間的產出都會在下次結算時自動補算——放著不管也會慢慢賺錢！"
)


# ── 頻道管理 ──
def _get_all_miner_channels():
    channels = []
    seen = set()
    main_id = miner_settings.get("channel_id")
    if main_id:
        ch = get_channel_any(int(main_id))
        if ch:
            channels.append(ch)
            seen.add(str(main_id))
    for g_id, ch_id in miner_settings.get("guild_channels", {}).items():
        if ch_id and str(ch_id) not in seen:
            ch = get_channel_any(int(ch_id))
            if ch:
                channels.append(ch)
                seen.add(str(ch_id))
    return channels


async def setup_miner_panel():
    """(重新)發送礦工面板到所有設定的頻道（主面板 + 訪客伺服器子面板）。"""
    channels = _get_all_miner_channels()
    if not channels:
        return None
    guild_msgs = miner_settings.get("guild_panel_messages", {})
    result = None
    for channel in channels:
        ch_id_str = str(channel.id)
        is_main = (ch_id_str == str(miner_settings.get("channel_id", "")))
        old_msg_id = miner_settings.get("message_id") if is_main else guild_msgs.get(ch_id_str)
        if old_msg_id:
            try:
                old_msg = await channel.fetch_message(int(old_msg_id))
                await old_msg.delete()
            except Exception:
                pass
        try:
            new_msg = await channel.send(embed=_build_miner_embed(), view=MinerPanelView())
            if is_main:
                miner_settings["message_id"] = new_msg.id
                result = new_msg
            else:
                guild_msgs[ch_id_str] = new_msg.id
            print(f"✅ 礦工面板已發送至 #{channel.name}（ID: {new_msg.id}）")
        except Exception as e:
            print(f"❌ 發送礦工面板至頻道 {ch_id_str} 失敗：{e}")
    miner_settings["guild_panel_messages"] = guild_msgs
    save_miner()
    return result


async def refresh_miner_panel():
    """就地更新所有頻道的礦工面板。"""
    channels = _get_all_miner_channels()
    if not channels:
        return
    guild_msgs = miner_settings.get("guild_panel_messages", {})
    for channel in channels:
        ch_id_str = str(channel.id)
        is_main = (ch_id_str == str(miner_settings.get("channel_id", "")))
        msg_id = miner_settings.get("message_id") if is_main else guild_msgs.get(ch_id_str)
        if not msg_id:
            continue
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=_build_miner_embed())
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"⚠️ 更新礦工面板失敗 ({ch_id_str})：{e}")


async def miner_loop():
    """礦工遊戲背景循環：定期結算所有玩家產出（含經理自動入帳）+ 刷新面板。"""
    await bot.wait_until_ready()
    await asyncio.sleep(8)
    try:
        await setup_miner_panel()
    except Exception as e:
        print(f"⚠️ 礦工面板初始化失敗：{e}")
    tick_count = 0
    while True:
        await asyncio.sleep(MINER_TICK_SECONDS)
        try:
            if miner_settings.get("enabled", True):
                for uid in list(miner_players.keys()):
                    _apply_all_ticks_for_player(uid)
                save_miner()
            tick_count += 1
            if tick_count % 2 == 0:  # 每2分鐘刷新一次面板，避免過於頻繁的 API 編輯
                await refresh_miner_panel()
        except Exception as e:
            print(f"⚠️ 礦工遊戲背景結算失敗：{e}")


# ═══════════════════════════════════════════════════════════════════════
# 面板按鈕（持久化 View — 公開面板）
# ═══════════════════════════════════════════════════════════════════════

class MinerPanelView(discord.ui.View):
    """礦工遊戲主面板的持久化按鈕（公開頻道）。所有按鈕都以 ephemeral
    新訊息回應，絕不對公開面板本體執行 edit_message。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="我的礦場", style=discord.ButtonStyle.primary, emoji="⛏️", custom_id="miner:my_mine")
    async def my_mine(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        _ensure_miner_player(uid, interaction.user.display_name)
        embed = _build_player_mine_embed(uid, interaction.user.display_name)
        view = MinerMainView(uid)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="排行榜", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="miner:leaderboard")
    async def leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_miner_leaderboard_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="說明", style=discord.ButtonStyle.secondary, emoji="📖", custom_id="miner:help")
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(MINER_HELP_TEXT, ephemeral=True)


class MinerMainView(discord.ui.View):
    """個人礦場總覽（ephemeral 訊息）：收取全部 / 管理礦坑。"""

    def __init__(self, uid: str):
        super().__init__(timeout=180)
        self.uid = uid

    @discord.ui.button(label="收取全部", style=discord.ButtonStyle.success, emoji="💰")
    async def collect_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        _apply_all_ticks_for_player(self.uid)
        p = miner_players[self.uid]
        total = 0
        for sdef in SHAFT_DEFS:
            sh = p["shafts"][sdef["id"]]
            if sh.get("unlocked") and not sh.get("manager") and sh["storage"] > 0:
                amt = int(sh["storage"])
                if amt > 0:
                    sh["storage"] -= amt
                    total += amt
        if total > 0:
            add_balance(self.uid, total, p.get("username", ""))
            p["lifetime_earned"] = p.get("lifetime_earned", 0) + total
        save_miner()
        embed = _build_player_mine_embed(self.uid, interaction.user.display_name)
        await interaction.response.edit_message(embed=embed, view=self)
        if total > 0:
            await interaction.followup.send(f"💰 收取了 **{total:,}** {currency_name()}！", ephemeral=True)
        else:
            await interaction.followup.send("目前沒有可收取的產出（或所有礦坑都已雇用經理自動收礦）。", ephemeral=True)

    @discord.ui.button(label="管理礦坑", style=discord.ButtonStyle.primary, emoji="🗂️")
    async def manage_shafts(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        view = ShaftSelectView(self.uid)
        await interaction.response.edit_message(content="⛏️ 請選擇要管理的礦坑：", embed=None, view=view)


class ShaftSelectView(discord.ui.View):
    """礦坑選擇下拉選單（同一則 ephemeral 訊息內導航）。"""

    def __init__(self, uid: str):
        super().__init__(timeout=180)
        self.uid = uid
        p = _ensure_miner_player(uid)
        options = []
        for sdef in SHAFT_DEFS:
            sh = p["shafts"][sdef["id"]]
            status = "✅" if sh.get("unlocked") else "🔒"
            options.append(discord.SelectOption(label=f"{status} {sdef['name']}", value=sdef["id"]))
        select = discord.ui.Select(placeholder="選擇礦坑", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        shaft_id = interaction.data["values"][0]
        embed = _build_shaft_detail_embed(self.uid, shaft_id)
        view = ShaftDetailView(self.uid, shaft_id)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, emoji="⬅️", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        embed = _build_player_mine_embed(self.uid, interaction.user.display_name)
        view = MinerMainView(self.uid)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ShaftDetailView(discord.ui.View):
    """單一礦坑管理面板：解鎖／招募礦工／升級／雇用經理。"""

    def __init__(self, uid: str, shaft_id: str):
        super().__init__(timeout=180)
        self.uid = uid
        self.shaft_id = shaft_id

    def _check_owner(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == self.uid

    async def _refresh(self, interaction: discord.Interaction):
        embed = _build_shaft_detail_embed(self.uid, self.shaft_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="解鎖", style=discord.ButtonStyle.success, emoji="🔓")
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        p = miner_players[self.uid]
        sh = p["shafts"][self.shaft_id]
        sdef = _shaft_def(self.shaft_id)
        if sh.get("unlocked"):
            await interaction.response.send_message("✅ 這座礦坑已經解鎖了。", ephemeral=True)
            return
        cost = sdef["unlock_cost"]
        bal = get_balance(self.uid)
        if bal < cost:
            await interaction.response.send_message(f"❌ 餘額不足，解鎖需要 {cost:,} {currency_name()}，你目前只有 {bal:,}。", ephemeral=True)
            return
        add_balance(self.uid, -cost, p.get("username", ""))
        sh.update({"unlocked": True, "miners": 1, "level": 1, "storage": 0.0, "last_tick": _time.time(), "manager": False})
        save_miner()
        await self._refresh(interaction)

    @discord.ui.button(label="招募礦工", style=discord.ButtonStyle.primary, emoji="👷")
    async def recruit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        p = miner_players[self.uid]
        sh = p["shafts"][self.shaft_id]
        sdef = _shaft_def(self.shaft_id)
        if not sh.get("unlocked"):
            await interaction.response.send_message("🔒 請先解鎖這座礦坑。", ephemeral=True)
            return
        _apply_shaft_tick(self.uid, self.shaft_id)
        cost = _recruit_cost(sdef, sh["miners"])
        bal = get_balance(self.uid)
        if bal < cost:
            await interaction.response.send_message(f"❌ 餘額不足，招募需要 {cost:,} {currency_name()}，你目前只有 {bal:,}。", ephemeral=True)
            return
        add_balance(self.uid, -cost, p.get("username", ""))
        sh["miners"] += 1
        save_miner()
        await self._refresh(interaction)

    @discord.ui.button(label="升級礦坑", style=discord.ButtonStyle.primary, emoji="📈")
    async def upgrade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        p = miner_players[self.uid]
        sh = p["shafts"][self.shaft_id]
        sdef = _shaft_def(self.shaft_id)
        if not sh.get("unlocked"):
            await interaction.response.send_message("🔒 請先解鎖這座礦坑。", ephemeral=True)
            return
        _apply_shaft_tick(self.uid, self.shaft_id)
        cost = _upgrade_cost(sdef, sh["level"])
        bal = get_balance(self.uid)
        if bal < cost:
            await interaction.response.send_message(f"❌ 餘額不足，升級需要 {cost:,} {currency_name()}，你目前只有 {bal:,}。", ephemeral=True)
            return
        add_balance(self.uid, -cost, p.get("username", ""))
        sh["level"] += 1
        save_miner()
        await self._refresh(interaction)

    @discord.ui.button(label="雇用經理", style=discord.ButtonStyle.secondary, emoji="🧑‍💼")
    async def hire_manager(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        p = miner_players[self.uid]
        sh = p["shafts"][self.shaft_id]
        sdef = _shaft_def(self.shaft_id)
        if not sh.get("unlocked"):
            await interaction.response.send_message("🔒 請先解鎖這座礦坑。", ephemeral=True)
            return
        if sh.get("manager"):
            await interaction.response.send_message("✅ 這座礦坑已經有經理了。", ephemeral=True)
            return
        _apply_shaft_tick(self.uid, self.shaft_id)
        cost = sdef["manager_cost"]
        bal = get_balance(self.uid)
        if bal < cost:
            await interaction.response.send_message(f"❌ 餘額不足，雇用經理需要 {cost:,} {currency_name()}，你目前只有 {bal:,}。", ephemeral=True)
            return
        # 先把現有庫存收進餘額，再切換成經理自動收礦模式，避免玩家損失既有產出
        existing = int(sh.get("storage", 0))
        if existing > 0:
            add_balance(self.uid, existing, p.get("username", ""))
            p["lifetime_earned"] = p.get("lifetime_earned", 0) + existing
            sh["storage"] -= existing
        add_balance(self.uid, -cost, p.get("username", ""))
        sh["manager"] = True
        save_miner()
        await self._refresh(interaction)

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, emoji="⬅️", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("這不是你的礦場！", ephemeral=True)
            return
        view = ShaftSelectView(self.uid)
        await interaction.response.edit_message(content="⛏️ 請選擇要管理的礦坑：", embed=None, view=view)


# ═══════════════════════════════════════════════════════════════════════
# 斜線指令
# ═══════════════════════════════════════════════════════════════════════

class MinerGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="miner", description="琉璃幣礦工 · 掘金之旅")

    @app_commands.command(name="status", description="查看我的礦場狀態")
    async def miner_status(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        _ensure_miner_player(uid, interaction.user.display_name)
        embed = _build_player_mine_embed(uid, interaction.user.display_name)
        view = MinerMainView(uid)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="leaderboard", description="查看礦工開採排行榜")
    async def miner_leaderboard(self, interaction: discord.Interaction):
        embed = _build_miner_leaderboard_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setup", description="設定礦工面板頻道（管理員限定）")
    @app_commands.describe(channel="礦工面板所在頻道")
    async def miner_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        guild_id_str = str(interaction.guild.id) if interaction.guild else None
        if guild_id_str == ICEA_GUILD_ID:
            miner_settings["channel_id"] = channel.id
            msg = "✅ 礦工主面板頻道已設為"
        elif guild_id_str:
            miner_settings.setdefault("guild_channels", {})[guild_id_str] = str(channel.id)
            msg = "✅ 本伺服器的礦工子面板頻道已設為"
        else:
            await interaction.response.send_message("❌ 此指令需要在伺服器內使用。", ephemeral=True)
            return
        save_miner()
        await interaction.response.send_message(f"{msg} {channel.mention}", ephemeral=True)
        try:
            await setup_miner_panel()
        except Exception as e:
            print(f"⚠️ 設定礦工頻道後立即發送面板失敗：{e}")

    @app_commands.command(name="toggle", description="開關礦工遊戲（管理員限定）")
    async def miner_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        miner_settings["enabled"] = not miner_settings.get("enabled", True)
        save_miner()
        status = "開啟" if miner_settings["enabled"] else "關閉"
        await interaction.response.send_message(f"⛏️ 礦工遊戲已{status}。", ephemeral=True)


load_miner()
print(f"⛏️ 礦工遊戲模組已載入：{len(miner_players)} 位玩家")


# ═══════════════════════════════════════════════════════════════════════
# Dashboard API
# ═══════════════════════════════════════════════════════════════════════

async def api_get_miner_settings(request):
    """取得礦工遊戲設定 + 統計。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    total_miners = sum(sh.get("miners", 0) for p in miner_players.values() for sh in p.get("shafts", {}).values())
    total_unlocked = sum(1 for p in miner_players.values() for sh in p.get("shafts", {}).values() if sh.get("unlocked"))
    return web.json_response({
        "enabled": miner_settings.get("enabled", True),
        "channel_id": miner_settings.get("channel_id"),
        "guild_channels": miner_settings.get("guild_channels", {}),
        "player_count": len(miner_players),
        "total_miners": total_miners,
        "total_unlocked": total_unlocked,
        "shaft_defs": [{"id": s["id"], "name": s["name"], "unlock_cost": s["unlock_cost"],
                        "base_output": s["base_output"], "recruit_base": s["recruit_base"],
                        "upgrade_base": s["upgrade_base"], "manager_cost": s["manager_cost"]}
                       for s in SHAFT_DEFS],
        "cap_hours": MINER_CAP_HOURS,
    })


async def api_set_miner_settings(request):
    """更新礦工遊戲設定。"""
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    if "enabled" in body:
        miner_settings["enabled"] = body["enabled"]
    if "channel_id" in body:
        miner_settings["channel_id"] = body["channel_id"] if body["channel_id"] else None
    save_miner()
    if "channel_id" in body:
        asyncio.ensure_future(setup_miner_panel())
    return web.json_response({"ok": True})


_miner_api_routes = [
    ("/api/miner-settings", "GET", api_get_miner_settings),
    ("/api/miner-settings", "PUT", api_set_miner_settings),
]
