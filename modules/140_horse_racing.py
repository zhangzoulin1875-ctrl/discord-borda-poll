# ═══════════════════════════════════════════════════════════════════
# Module: 140_horse_racing
# 賽馬賭博（馬票）系統 — 每 30 分鐘一局，6 分鐘投注時間，AI 生成賽事結果
# 玩法：獨贏(猜第一名)、位置(猜前三名)、連贏(猜前兩名，順序不拘)
# ═══════════════════════════════════════════════════════════════════

import random as _hr_random
import time as _hr_time

# ── 設定常數 ──
HORSE_FILE = os.path.join(DATA_DIR, "horse_racing.json")
HORSE_RACE_INTERVAL_SEC = 30 * 60   # 每局間隔 30 分鐘（冷卻）
HORSE_RACE_INTERVAL_MIN = 30
HORSE_BETTING_DURATION_SEC = 6 * 60  # 投注時間 6 分鐘
HORSE_MIN_BET = 50
HORSE_MIN_HORSES = 12
HORSE_MAX_HORSES = 18

HORSE_NAME_POOL = [
    "疾風烈", "紫電追風", "黑旋風", "赤兔", "追風麒麟", "白龍捲", "雷霆蹄", "烈焰飛駒",
    "銀月奔雷", "狂沙暴", "天馬行空", "幻影快刀", "極速狂飆", "金鬃王者", "夜魅疾影",
    "破浪千里", "蒼穹之矢", "烽火連城", "玄鐵騎", "霜刃", "落日餘暉", "星辰墜",
    "龍捲風暴", "御風而行", "一箭穿雲", "鐵蹄震天", "幽冥快影", "烈日狂奔", "碧海蒼狼",
    "九天攬月", "赤焰狂飆", "踏雪無痕", "北境之狼", "流光影", "無雙蹄",
]

# 全域狀態（皆持久化到 HORSE_FILE）
horse_racing_settings = {"channel_id": None, "last_race_end_time": 0, "force_next": False}
current_race = None  # None 或 dict，見 _generate_race() 結構


# ── 存檔/載入 ──
def save_horse_racing():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        data = {"settings": horse_racing_settings, "current_race": current_race}
        _save_json_file(HORSE_FILE, data)
    except Exception as e:
        print(f"⚠️ 賽馬存檔失敗：{e}")


def load_horse_racing():
    global horse_racing_settings, current_race
    try:
        if os.path.exists(HORSE_FILE):
            with open(HORSE_FILE, "r", encoding="utf-8") as f:
                data = json_module.loads(f.read())
            if isinstance(data.get("settings"), dict):
                horse_racing_settings.update(data["settings"])
            if isinstance(data.get("current_race"), dict):
                current_race = data["current_race"]
            print(f"🏇 賽馬系統已載入（頻道：{horse_racing_settings.get('channel_id')}，目前賽事：{'有' if current_race else '無'}）")
    except Exception as e:
        print(f"⚠️ 賽馬載入失敗（使用預設值）：{e}")


load_horse_racing()


# ── 核心邏輯 ──
def _get_horse_racing_channel():
    ch_id = horse_racing_settings.get("channel_id")
    if not ch_id:
        return None
    for guild in bot.guilds:
        ch = guild.get_channel(int(ch_id))
        if ch:
            return ch
    return None


def _generate_race() -> dict:
    """建立一場新賽事：隨機 12-18 匹馬、隨機實力值、依實力值換算賠率。
    賠率公式為簡化模型（非精確排列組合機率），僅供遊戲娛樂使用：
    - 假設回吐率 85%（house edge 15%）
    - 獨贏隱含機率 = 該馬實力值 / 全部實力值總和
    - 位置隱含機率 粗略估算為 獨贏機率 x 3（封頂 92%）
    - 連贏（任兩匹馬皆進前二，順序不拘）機率粗略估算為 2 x p_i x p_j
    """
    num_horses = _hr_random.randint(HORSE_MIN_HORSES, HORSE_MAX_HORSES)
    names = _hr_random.sample(HORSE_NAME_POOL, num_horses)
    horses = []
    for i, name in enumerate(names, start=1):
        strength = _hr_random.randint(30, 100)
        horses.append({"num": i, "name": name, "strength": strength})

    total_strength = sum(h["strength"] for h in horses)
    for h in horses:
        p_win = h["strength"] / total_strength if total_strength else 1 / len(horses)
        odds_win = max(1.3, min(30.0, 0.85 / p_win)) if p_win > 0 else 30.0
        p_place = min(0.92, p_win * 3)
        odds_place = max(1.05, min(6.0, 0.85 / p_place)) if p_place > 0 else 6.0
        h["odds_win"] = round(odds_win, 1)
        h["odds_place"] = round(odds_place, 1)

    race_id = f"hr_{int(_hr_time.time())}_{_hr_random.randint(100, 999)}"
    betting_end_time = _hr_time.time() + HORSE_BETTING_DURATION_SEC
    return {
        "race_id": race_id,
        "horses": horses,
        "total_strength": total_strength,
        "betting_end_time": betting_end_time,
        "status": "betting",
        "bets": [],
        "message_id": None,
        "channel_id": None,
    }


def _quinella_odds(race: dict, h_i: dict, h_j: dict) -> float:
    total = race.get("total_strength") or sum(h["strength"] for h in race["horses"])
    if total <= 0:
        return 10.0
    p_i = h_i["strength"] / total
    p_j = h_j["strength"] / total
    p_q = 2 * p_i * p_j
    if p_q <= 0:
        return 150.0
    return round(max(2.0, min(150.0, 0.85 / p_q)), 1)


def _weighted_shuffle(horses: list) -> list:
    """AI 失敗時的備援：依實力值加權隨機排出完整名次（不放回抽樣）。"""
    pool = list(horses)
    order = []
    while pool:
        total = sum(h["strength"] for h in pool)
        r = _hr_random.uniform(0, total) if total > 0 else 0
        upto = 0
        picked = pool[0]
        for h in pool:
            upto += h["strength"]
            if upto >= r:
                picked = h
                break
        order.append(picked["num"])
        pool.remove(picked)
    return order


def _build_race_betting_embed(race: dict) -> "discord.Embed":
    embed = discord.Embed(
        title="🏇 賽馬開賭！",
        description=(
            f"本場共 **{len(race['horses'])}** 匹馬參賽，投注時間 **6 分鐘**！\n"
            f"⏰ 截止時間：<t:{int(race['betting_end_time'])}:R>\n\n"
            "**玩法說明**\n"
            "🥇 獨贏：猜中第一名的馬匹\n"
            "🎯 位置：猜中前三名的馬匹（名次不拘）\n"
            "🔗 連贏：猜中前兩名的馬匹（順序不拘，需選 2 匹）\n\n"
            "點擊下方按鈕開始下注（下注面板僅自己看得到）："
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    sorted_horses = sorted(race["horses"], key=lambda h: h["odds_win"])
    lines = [f"`#{h['num']:>2}` **{h['name']}** — 獨贏`{h['odds_win']}x` 位置`{h['odds_place']}x`" for h in sorted_horses]
    chunk_size = 9
    for i in range(0, len(lines), chunk_size):
        chunk = lines[i:i + chunk_size]
        embed.add_field(name="賠率表（依獨贏賠率排序）" if i == 0 else "\u200b", value="\n".join(chunk), inline=True)
    embed.set_footer(text=f"最低下注 {HORSE_MIN_BET} {currency_name()} ｜ 賠率為簡化模型，僅供娛樂")
    return embed


async def _start_new_race(channel):
    global current_race
    race = _generate_race()
    race["channel_id"] = channel.id
    embed = _build_race_betting_embed(race)
    view = HorseBettingView(race["race_id"])
    try:
        msg = await channel.send(embed=embed, view=view)
        race["message_id"] = msg.id
    except Exception as e:
        print(f"⚠️ 賽馬公告發送失敗：{e}")
    current_race = race
    save_horse_racing()
    print(f"🏇 新賽事開始：{race['race_id']}，{len(race['horses'])} 匹馬參賽，投注截止於 {int(race['betting_end_time'])}")


async def _reattach_betting_view():
    """機器人重啟後，若賽事仍在投注階段，重新掛載按鈕面板（舊面板按鈕在重啟後會失效）。"""
    if not current_race or current_race.get("status") != "betting":
        return
    ch_id = current_race.get("channel_id")
    msg_id = current_race.get("message_id")
    if not ch_id or not msg_id:
        return
    channel = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(ch_id))
        if ch:
            channel = ch
            break
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        view = HorseBettingView(current_race["race_id"])
        await msg.edit(view=view)
        print(f"🔄 賽馬投注面板已在重啟後重新掛載：{current_race['race_id']}")
    except Exception as e:
        print(f"⚠️ 賽馬投注面板重新掛載失敗：{e}")


async def _resolve_race():
    """投注時間結束，呼叫 AI 生成賽事結果並派彩。AI 失敗時自動改用加權隨機備援，
    確保賽事一定會結束（下注的錢不能卡住）。"""
    global current_race
    race = current_race
    if not race:
        return
    race["status"] = "resolving"
    horses = race["horses"]

    finish_order = None
    commentary = None
    try:
        horse_lines = "\n".join(f"#{h['num']} {h['name']}（實力值 {h['strength']}）" for h in horses)
        prompt = f"""你是一個賽馬比賽模擬器。以下是本場賽事的參賽馬匹與其實力值(1-100，越高越強)：
{horse_lines}

請根據實力值模擬一場精彩的賽馬比賽，實力強的馬獲勝機率較高，但弱馬仍有機會爆冷創造驚喜。
請完整排出全部 {len(horses)} 匹馬的名次，從第一名到最後一名，使用馬匹編號，不可重複或遺漏任何編號。

只回傳 JSON 格式（不要加 markdown code block）：
{{"finish_order": [第一名的馬匹編號, 第二名, 第三名, ...], "commentary": "一段精彩的賽事實況描述，50-100字"}}"""

        messages = [
            {"role": "system", "content": "你是一個賽馬比賽模擬器，只回傳JSON。"},
            {"role": "user", "content": prompt},
        ]
        result = await call_chat_api(
            messages, dict(chat_ai_settings),
            max_tokens=500,
            timeout_total=40,
            category="entertainment",
            timeout_read=35,
            is_background=True,
            fallback_mode="full",  # 娛樂功能降級鏈：主模型失敗直接切備援API（對齊海龜湯/狼人殺/占卜）
            fallback_user_id="horse_racing",
        )
        if not result.get("circuit_open"):
            text = result.get("content", "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json_module.loads(text)
            candidate_order = [int(x) for x in parsed.get("finish_order", [])]
            valid_nums = {h["num"] for h in horses}
            if set(candidate_order) == valid_nums and len(candidate_order) == len(horses):
                finish_order = candidate_order
                commentary = str(parsed.get("commentary", "")).strip()[:400]
            else:
                print(f"⚠️ 賽馬AI回傳名次不完整/不合法，改用加權隨機。原始內容：{text[:200]}")
        else:
            print("⚠️ 賽馬AI：熔斷器開啟，改用加權隨機")
    except Exception as e:
        print(f"⚠️ 賽馬AI生成結果失敗，改用加權隨機：{e}")

    if not finish_order:
        finish_order = _weighted_shuffle(horses)
        commentary = commentary or "本場賽事精彩激烈，選手們奮力奔馳到最後一刻！（AI暫時無法生成詳細賽評，已依實力值加權隨機產生結果）"

    # 派彩
    total_paid = 0
    winner_count = 0
    winner_lines = []
    for bet in race.get("bets", []):
        win_flag = False
        if bet["bet_type"] == "win":
            win_flag = bet["horses"][0] == finish_order[0]
        elif bet["bet_type"] == "place":
            win_flag = bet["horses"][0] in finish_order[:3]
        elif bet["bet_type"] == "quinella":
            win_flag = set(bet["horses"]) == set(finish_order[:2])
        if win_flag:
            payout = bet["potential_payout"]
            add_balance(bet["user_id"], payout, bet.get("username", ""))
            total_paid += payout
            winner_count += 1
            winner_lines.append(f"**{bet.get('username', '?')}** +{payout} {currency_name()}")

    # 找公告頻道
    channel = None
    try:
        ch_id = race.get("channel_id") or horse_racing_settings.get("channel_id")
        if ch_id:
            for guild in bot.guilds:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    channel = ch
                    break
    except Exception:
        pass

    horse_by_num = {h["num"]: h for h in horses}
    medal = ["🥇", "🥈", "🥉"]
    top3_lines = []
    for i, num in enumerate(finish_order[:3]):
        h = horse_by_num.get(num)
        if h:
            top3_lines.append(f"{medal[i]} **#{num} {h['name']}**")
    full_order_str = " → ".join(f"#{n}" for n in finish_order)

    result_embed = discord.Embed(
        title="🏁 賽馬結果",
        description=(
            "\n".join(top3_lines) + "\n\n" +
            f"📜 {commentary}\n\n" +
            f"**完整名次：**\n{full_order_str}"
        ),
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    result_embed.add_field(name="🎫 總下注", value=f"{len(race.get('bets', []))} 筆", inline=True)
    result_embed.add_field(name="🎉 中獎", value=f"{winner_count} 筆", inline=True)
    result_embed.add_field(name="💸 總派彩", value=f"{total_paid} {currency_name()}", inline=True)
    if winner_lines:
        result_embed.add_field(name="🏆 中獎名單", value="\n".join(winner_lines[:15]), inline=False)
    result_embed.set_footer(text=f"下一場賽事將於約 {HORSE_RACE_INTERVAL_MIN} 分鐘後開放（頻道有設定才會自動開賭）")

    if channel:
        # 移除舊投注面板按鈕，避免結算後還能點
        msg_id = race.get("message_id")
        if msg_id:
            try:
                old_msg = await channel.fetch_message(int(msg_id))
                await old_msg.edit(view=None)
            except Exception:
                pass
        try:
            await channel.send(embed=result_embed)
        except Exception as e:
            print(f"⚠️ 賽馬結果發送失敗：{e}")

    print(f"🏇 賽馬完賽：{race['race_id']}，{winner_count} 人中獎，共派彩 {total_paid} {currency_name()}")

    current_race = None
    horse_racing_settings["last_race_end_time"] = _hr_time.time()
    save_horse_racing()


# ── 下注 UI ──

class BetAmountModal(discord.ui.Modal, title="下注金額"):
    def __init__(self, race_id: str, user_id_str: str, bet_type: str, horse_nums: list, multiplier: float, bet_label: str):
        super().__init__(timeout=120)
        self.title = f"下注：{bet_label}"[:45]
        self.race_id = race_id
        self.user_id_str = user_id_str
        self.bet_type = bet_type
        self.horse_nums = horse_nums
        self.multiplier = multiplier
        self.bet_label = bet_label
        self.amount_input = discord.ui.TextInput(
            label=f"下注金額（倍率 {multiplier}x，最低 {HORSE_MIN_BET}）",
            placeholder=str(HORSE_MIN_BET),
            max_length=10,
            required=True,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not current_race or current_race.get("race_id") != self.race_id or current_race.get("status") != "betting":
            await interaction.response.send_message("❌ 這場賽事已經結束或不存在，無法下注。", ephemeral=True)
            return
        if _hr_time.time() >= current_race.get("betting_end_time", 0):
            await interaction.response.send_message("❌ 投注時間已截止。", ephemeral=True)
            return

        raw = self.amount_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("❌ 請輸入有效的正整數金額。", ephemeral=True)
            return
        amount = int(raw)
        if amount < HORSE_MIN_BET:
            await interaction.response.send_message(f"❌ 最低下注 {HORSE_MIN_BET} {currency_name()}。", ephemeral=True)
            return

        uid = self.user_id_str
        _ensure_user(uid, interaction.user.display_name)
        bal = get_balance(uid)
        if bal < amount:
            await interaction.response.send_message(f"❌ 餘額不足。你只有 {bal} {currency_name()}，下注需要 {amount}。", ephemeral=True)
            return

        add_balance(uid, -amount, interaction.user.display_name)
        potential_payout = int(amount * self.multiplier)
        bet_record = {
            "user_id": uid,
            "username": interaction.user.display_name,
            "bet_type": self.bet_type,
            "horses": self.horse_nums,
            "amount": amount,
            "multiplier": self.multiplier,
            "potential_payout": potential_payout,
        }
        current_race["bets"].append(bet_record)
        save_horse_racing()

        type_label = {"win": "獨贏", "place": "位置", "quinella": "連贏"}[self.bet_type]
        horse_desc = "、".join(f"#{n}" for n in self.horse_nums)
        embed = discord.Embed(
            title="🎫 下注成功",
            description=(
                f"類型：**{type_label}**\n"
                f"馬匹：**{horse_desc}**\n"
                f"下注金額：**{amount}** {currency_name()}\n"
                f"倍率：**{self.multiplier}x**\n"
                f"若中獎可得：**{potential_payout}** {currency_name()}\n"
                f"剩餘餘額：**{get_balance(uid)}** {currency_name()}"
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HorseSingleSelectView(discord.ui.View):
    """獨贏／位置 用的單選馬匹面板。"""

    def __init__(self, race_id: str, user_id_str: str, bet_type: str):
        super().__init__(timeout=120)
        self.race_id = race_id
        self.user_id_str = user_id_str
        self.bet_type = bet_type

        horses = current_race["horses"] if (current_race and current_race.get("race_id") == race_id) else []
        options = []
        for h in horses[:25]:
            odds = h["odds_win"] if bet_type == "win" else h["odds_place"]
            options.append(discord.SelectOption(
                label=f"#{h['num']} {h['name']}"[:100],
                description=f"{'獨贏' if bet_type == 'win' else '位置'} 賠率 {odds}x",
                value=str(h["num"]),
            ))
        if options:
            select = discord.ui.Select(placeholder="選擇馬匹…", options=options, min_values=1, max_values=1)
            select.callback = self._on_select
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id_str:
            await interaction.response.send_message("這不是你的下注面板！", ephemeral=True)
            return
        if not current_race or current_race.get("race_id") != self.race_id or current_race.get("status") != "betting":
            await interaction.response.send_message("❌ 這場賽事已結束，無法下注。", ephemeral=True)
            return

        num = int(interaction.data["values"][0])
        horse = next((h for h in current_race["horses"] if h["num"] == num), None)
        if not horse:
            await interaction.response.send_message("❌ 找不到該馬匹。", ephemeral=True)
            return

        multiplier = horse["odds_win"] if self.bet_type == "win" else horse["odds_place"]
        type_label = "獨贏" if self.bet_type == "win" else "位置"
        modal = BetAmountModal(self.race_id, self.user_id_str, self.bet_type, [num], multiplier, f"{type_label} #{num} {horse['name']}")
        await interaction.response.send_modal(modal)


class HorseQuinellaSelectView(discord.ui.View):
    """連贏 用的雙選馬匹面板（恰好 2 匹）。"""

    def __init__(self, race_id: str, user_id_str: str):
        super().__init__(timeout=120)
        self.race_id = race_id
        self.user_id_str = user_id_str

        horses = current_race["horses"] if (current_race and current_race.get("race_id") == race_id) else []
        options = [
            discord.SelectOption(
                label=f"#{h['num']} {h['name']}"[:100],
                description=f"獨贏賠率參考 {h['odds_win']}x",
                value=str(h["num"]),
            )
            for h in horses[:25]
        ]
        if options:
            select = discord.ui.Select(placeholder="選擇恰好 2 匹馬（順序不拘）…", options=options, min_values=2, max_values=2)
            select.callback = self._on_select
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id_str:
            await interaction.response.send_message("這不是你的下注面板！", ephemeral=True)
            return
        if not current_race or current_race.get("race_id") != self.race_id or current_race.get("status") != "betting":
            await interaction.response.send_message("❌ 這場賽事已結束，無法下注。", ephemeral=True)
            return

        nums = sorted(int(v) for v in interaction.data["values"])
        h_i = next((h for h in current_race["horses"] if h["num"] == nums[0]), None)
        h_j = next((h for h in current_race["horses"] if h["num"] == nums[1]), None)
        if not h_i or not h_j:
            await interaction.response.send_message("❌ 找不到該馬匹。", ephemeral=True)
            return

        multiplier = _quinella_odds(current_race, h_i, h_j)
        modal = BetAmountModal(self.race_id, self.user_id_str, "quinella", nums, multiplier, f"連贏 #{nums[0]}+#{nums[1]}")
        await interaction.response.send_modal(modal)


class HorseBettingView(discord.ui.View):
    """賽事公告訊息下方的公開下注按鈕面板。生命週期由賽馬循環手動控管（timeout=None），
    投注截止時由 _resolve_race() 主動移除按鈕，不依賴 Discord 內建 timeout 機制。"""

    def __init__(self, race_id: str):
        super().__init__(timeout=None)
        self.race_id = race_id

    def _race_open(self) -> bool:
        return bool(
            current_race
            and current_race.get("race_id") == self.race_id
            and current_race.get("status") == "betting"
            and _hr_time.time() < current_race.get("betting_end_time", 0)
        )

    @discord.ui.button(label="獨贏 Win", style=discord.ButtonStyle.success, emoji="🥇", custom_id="horse_racing:win")
    async def btn_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._race_open():
            await interaction.response.send_message("❌ 投注時間已截止或賽事不存在。", ephemeral=True)
            return
        view = HorseSingleSelectView(self.race_id, str(interaction.user.id), "win")
        await interaction.response.send_message("請選擇要獨贏下注的馬匹（猜中第一名）：", view=view, ephemeral=True)

    @discord.ui.button(label="位置 Place", style=discord.ButtonStyle.primary, emoji="🎯", custom_id="horse_racing:place")
    async def btn_place(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._race_open():
            await interaction.response.send_message("❌ 投注時間已截止或賽事不存在。", ephemeral=True)
            return
        view = HorseSingleSelectView(self.race_id, str(interaction.user.id), "place")
        await interaction.response.send_message("請選擇要位置下注的馬匹（前三名內即中）：", view=view, ephemeral=True)

    @discord.ui.button(label="連贏 Quinella", style=discord.ButtonStyle.secondary, emoji="🔗", custom_id="horse_racing:quinella")
    async def btn_quinella(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._race_open():
            await interaction.response.send_message("❌ 投注時間已截止或賽事不存在。", ephemeral=True)
            return
        view = HorseQuinellaSelectView(self.race_id, str(interaction.user.id))
        await interaction.response.send_message("請選擇恰好 2 匹馬（前兩名即中，順序不拘）：", view=view, ephemeral=True)


# ── 背景循環 ──

async def horse_racing_loop():
    """每 10 秒檢查一次：(1) 有無到期需結算的賽事 (2) 冷卻是否已過需開新賽事。
    啟動時若有未結束的賽事（例如重啟前正在投注），會自動復原：
    若投注時間已過就直接結算，否則重新掛載下注按鈕。"""
    global current_race
    await bot.wait_until_ready()
    await asyncio.sleep(8)

    if current_race:
        try:
            now = _hr_time.time()
            if current_race.get("status") == "betting":
                if now >= current_race.get("betting_end_time", 0):
                    await _resolve_race()
                else:
                    await _reattach_betting_view()
            else:
                # 卡在 resolving 等中間狀態（例如上次重啟時機不巧），直接清除避免卡死
                print("⚠️ 賽馬系統偵測到未完成的中間狀態，已清除以避免卡死")
                current_race = None
                save_horse_racing()
        except Exception as e:
            print(f"⚠️ 賽馬重啟復原失敗：{e}")
            current_race = None
            save_horse_racing()

    while True:
        try:
            now = _hr_time.time()
            if current_race is None:
                channel = _get_horse_racing_channel()
                if channel:
                    last_end = horse_racing_settings.get("last_race_end_time", 0)
                    force = horse_racing_settings.get("force_next", False)
                    if force or (now - last_end >= HORSE_RACE_INTERVAL_SEC):
                        horse_racing_settings["force_next"] = False
                        save_horse_racing()
                        await _start_new_race(channel)
            elif current_race.get("status") == "betting":
                if now >= current_race.get("betting_end_time", 0):
                    await _resolve_race()
        except Exception as e:
            print(f"⚠️ 賽馬循環錯誤：{e}")
        await asyncio.sleep(10)


# ── 指令群組 ──

class HorseRacingGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="horse", description="賽馬賭博系統")

    @app_commands.command(name="set_channel", description="設定賽馬新場次公告的頻道（僅擁有者）")
    @app_commands.describe(channel="要公告賽事的頻道")
    async def horse_set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        horse_racing_settings["channel_id"] = channel.id
        save_horse_racing()
        await interaction.response.send_message(
            f"✅ 賽馬公告頻道已設定為 {channel.mention}，將每 {HORSE_RACE_INTERVAL_MIN} 分鐘自動開一場賽事。",
            ephemeral=True
        )

    @app_commands.command(name="start_now", description="跳過冷卻，立即開始一場賽馬（僅擁有者）")
    async def horse_start_now(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if current_race:
            await interaction.response.send_message("❌ 目前已有一場賽事正在進行（投注中或結算中），無法開始新的一場。", ephemeral=True)
            return
        channel = _get_horse_racing_channel()
        if not channel:
            await interaction.response.send_message("❌ 尚未設定賽馬公告頻道，請先用 `/horse set_channel` 設定。", ephemeral=True)
            return

        await interaction.response.send_message(f"⏳ 正在於 {channel.mention} 開始新賽事…", ephemeral=True)
        await _start_new_race(channel)
        await interaction.followup.send(f"✅ 新賽事已在 {channel.mention} 開始！", ephemeral=True)

    @app_commands.command(name="status", description="查看目前賽馬狀態")
    async def horse_status(self, interaction: discord.Interaction):
        if current_race and current_race.get("status") == "betting":
            remaining = int(current_race.get("betting_end_time", 0) - _hr_time.time())
            remaining = max(0, remaining)
            embed = discord.Embed(
                title="🏇 賽馬進行中",
                description=(
                    f"本場共 **{len(current_race['horses'])}** 匹馬，投注截止倒數 **{remaining}** 秒。\n"
                    f"目前已有 **{len(current_race.get('bets', []))}** 筆下注。\n\n"
                    "前往賽馬公告頻道點擊按鈕即可下注！"
                ),
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        last_end = horse_racing_settings.get("last_race_end_time", 0)
        channel = _get_horse_racing_channel()
        if not channel:
            await interaction.response.send_message("❌ 尚未設定賽馬公告頻道。", ephemeral=True)
            return
        remaining = int(HORSE_RACE_INTERVAL_SEC - (_hr_time.time() - last_end))
        if remaining <= 0:
            desc = "下一場賽事即將開始（下個檢查週期內），請留意公告頻道。"
        else:
            desc = f"距離下一場賽事還有約 **{remaining // 60}** 分鐘，屆時將自動在 {channel.mention} 公告。"
        embed = discord.Embed(title="🏇 賽馬冷卻中", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="mybets", description="查看你在目前賽事的下注紀錄")
    async def horse_mybets(self, interaction: discord.Interaction):
        if not current_race or current_race.get("status") != "betting":
            await interaction.response.send_message("目前沒有正在進行的賽事。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        my_bets = [b for b in current_race.get("bets", []) if b["user_id"] == uid]
        if not my_bets:
            await interaction.response.send_message("你目前在本場賽事還沒有任何下注。", ephemeral=True)
            return

        type_label = {"win": "獨贏", "place": "位置", "quinella": "連贏"}
        lines = []
        total_bet = 0
        total_potential = 0
        for b in my_bets:
            horse_desc = "、".join(f"#{n}" for n in b["horses"])
            lines.append(
                f"{type_label[b['bet_type']]} {horse_desc} — 下注 {b['amount']} ({b['multiplier']}x) → 可得 {b['potential_payout']}"
            )
            total_bet += b["amount"]
            total_potential += b["potential_payout"]

        embed = discord.Embed(
            title=f"🎫 {interaction.user.display_name} 的本場下注",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.add_field(name="總下注", value=f"{total_bet} {currency_name()}", inline=True)
        embed.add_field(name="總潛在派彩", value=f"{total_potential} {currency_name()}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
