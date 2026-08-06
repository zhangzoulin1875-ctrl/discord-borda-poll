# ═══════════════════════════════════════════════════════════════════
# Module: 120_economy (auto-extracted pattern)
# ICEA 經濟系統 — 琉璃幣
# ═══════════════════════════════════════════════════════════════════

import random as _eco_random
from datetime import datetime

# ── 狀態 ──
ECONOMY_FILE = os.path.join(DATA_DIR, "economy.json")

economy_settings = {
    "currency_name": "琉璃幣",       # 擁有者可改
    "starting_balance": 1000,         # 新用戶初始餘額
    "quiz_reward": 5,                 # 答對一題的獎勵
    "turtle_soup_reward": 50,         # 海龜湯破案獎勵
    "daily_bonus_min": 50,            # 每日簽到最低
    "daily_bonus_max": 100,           # 每日簽到最高
    "daily_top3": [50, 30, 20],       # 每日問答前三名額外獎勵
    "info_channel_id": None,          # 經濟資訊看板固定頻道（擁有者設定）
    "info_message_id": None,          # 看板目前的訊息 ID（用於即時編輯）
}

# {user_id_str: {"balance": int, "username": str, "last_daily": "YYYY-MM-DD"}}
economy_balances = {}

# 每日問答排行 {date: {user_id_str: {"name": str, "correct": int}}}
economy_quiz_daily = {"date": "", "scores": {}}

# 經濟看板即時刷新的防抖動任務（避免短時間內多次餘額變動觸發多次編輯）
_economy_panel_refresh_task = None


# ── 存檔/載入 ──
def save_economy():
    """儲存經濟資料到本地 JSON。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        data = {
            "settings": economy_settings,
            "balances": economy_balances,
            "quiz_daily": economy_quiz_daily,
        }
        _save_json_file(ECONOMY_FILE, data)
    except Exception as e:
        print(f"⚠️ 經濟系統存檔失敗：{e}")
    _schedule_economy_panel_refresh()  # 資料有變動 → 排程即時更新看板


def _schedule_economy_panel_refresh():
    """事件驅動的看板即時更新：任何餘額變動後排程一次刷新（2秒防抖動，
    避免短時間內連續多次變動（例如每日排行結算連續發獎）觸發多次 API 編輯）。"""
    global _economy_panel_refresh_task
    if not economy_settings.get("info_channel_id"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 沒有正在運行的事件循環（例如啟動載入階段），略過
    if _economy_panel_refresh_task is not None and not _economy_panel_refresh_task.done():
        return  # 已有排程中的刷新任務，之後會用最新資料執行
    _economy_panel_refresh_task = loop.create_task(_debounced_economy_panel_refresh())


async def _debounced_economy_panel_refresh():
    await asyncio.sleep(2)
    try:
        await refresh_economy_panel()
    except Exception as e:
        print(f"⚠️ 經濟看板即時刷新失敗：{e}")


def load_economy():
    """從本地 JSON 載入經濟資料。"""
    global economy_settings, economy_balances, economy_quiz_daily
    try:
        if os.path.exists(ECONOMY_FILE):
            with open(ECONOMY_FILE, "r", encoding="utf-8") as f:
                data = json_module.loads(f.read())
            if isinstance(data.get("settings"), dict):
                economy_settings.update(data["settings"])
            if isinstance(data.get("balances"), dict):
                economy_balances = data["balances"]
            if isinstance(data.get("quiz_daily"), dict):
                economy_quiz_daily = data["quiz_daily"]
            print(f"💰 經濟系統已載入：{len(economy_balances)} 位用戶，幣名「{economy_settings['currency_name']}」")
    except Exception as e:
        print(f"⚠️ 經濟系統載入失敗（使用預設值）：{e}")


# ── 核心函數 ──
def _ensure_user(user_id_str: str, username: str = "") -> dict:
    """確保用戶存在於經濟系統中，不存在則建立帳戶。"""
    if user_id_str not in economy_balances:
        economy_balances[user_id_str] = {
            "balance": economy_settings["starting_balance"],
            "username": username or user_id_str,
            "last_daily": "",
        }
        save_economy()
    else:
        if username and economy_balances[user_id_str].get("username") != username:
            economy_balances[user_id_str]["username"] = username
    return economy_balances[user_id_str]


def get_balance(user_id_str: str) -> int:
    """取得用戶餘額。"""
    user = economy_balances.get(user_id_str)
    return user["balance"] if user else 0


def add_balance(user_id_str: str, amount: int, username: str = "") -> int:
    """增加用戶餘額（可正可負），回傳新餘額。"""
    user = _ensure_user(user_id_str, username)
    user["balance"] += amount
    save_economy()
    return user["balance"]


def transfer_balance(from_id: str, to_id: str, amount: int) -> bool:
    """轉帳，回傳是否成功。"""
    if amount <= 0:
        return False
    from_user = economy_balances.get(from_id)
    if not from_user or from_user["balance"] < amount:
        return False
    to_user = _ensure_user(to_id)
    from_user["balance"] -= amount
    to_user["balance"] += amount
    save_economy()
    return True


def currency_name() -> str:
    """取得目前幣名。"""
    return economy_settings.get("currency_name", "琉璃幣")


# ── 遊戲獎勵 ──
def reward_quiz_correct(user_id_str: str, username: str = ""):
    """答對問答題的獎勵。"""
    reward = economy_settings["quiz_reward"]
    new_bal = add_balance(user_id_str, reward, username)

    # 記錄每日排行
    today = datetime.now(GMT8).strftime("%Y-%m-%d")
    if economy_quiz_daily["date"] != today:
        # 日期變了 — 結算昨日前三名
        _distribute_daily_quiz_rewards()
        economy_quiz_daily["date"] = today
        economy_quiz_daily["scores"] = {}

    scores = economy_quiz_daily["scores"]
    if user_id_str not in scores:
        scores[user_id_str] = {"name": username or user_id_str, "correct": 0}
    scores[user_id_str]["correct"] += 1
    save_economy()
    return reward, new_bal


def _distribute_daily_quiz_rewards():
    """結算昨日問答前三名，發放額外獎勵。"""
    if not economy_quiz_daily["scores"]:
        return

    yesterday = economy_quiz_daily["date"]
    scores = economy_quiz_daily["scores"]
    if not scores:
        return

    # 依答對次數排序取前三名
    ranked = sorted(scores.items(), key=lambda x: x[1]["correct"], reverse=True)[:3]
    top3_rewards = economy_settings["daily_top3"]

    results = []
    for i, (uid, info) in enumerate(ranked):
        if info["correct"] == 0:
            continue
        bonus = top3_rewards[i] if i < len(top3_rewards) else 0
        if bonus > 0:
            add_balance(uid, bonus, info["name"])
            results.append((info["name"], bonus, info["correct"]))

    if results:
        print(f"💰 每日問答結算（{yesterday}）：")
        for name, bonus, correct in results:
            print(f"  {name}：答對 {correct} 題，額外 {bonus} {currency_name()}")


def reward_turtle_soup_win(user_id_str: str, username: str = ""):
    """海龜湯破案獎勵。"""
    reward = economy_settings["turtle_soup_reward"]
    new_bal = add_balance(user_id_str, reward, username)
    return reward, new_bal


# ── 經濟資訊看板（固定頻道、即時更新）──
ECONOMY_PANEL_EMBED_TITLE_MARKER = "經濟系統"  # 用於掃描/辨識殘留看板訊息


def _build_economy_info_embed() -> "discord.Embed":
    """建構經濟系統資訊看板的 embed 內容（指令與固定看板共用）。"""
    total = sum(u.get("balance", 0) for u in economy_balances.values())
    embed = discord.Embed(
        title=f"💰 {currency_name()} 經濟系統",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="幣名", value=currency_name(), inline=True)
    embed.add_field(name="用戶數", value=str(len(economy_balances)), inline=True)
    embed.add_field(name="貨幣總量", value=f"{total} {currency_name()}", inline=True)
    embed.add_field(name="新用戶初始", value=f"{economy_settings['starting_balance']} {currency_name()}", inline=True)
    embed.add_field(name="問答獎勵", value=f"{economy_settings['quiz_reward']} {currency_name()}/題", inline=True)
    embed.add_field(name="海龜湯獎勵", value=f"{economy_settings['turtle_soup_reward']} {currency_name()}/破案", inline=True)
    embed.add_field(
        name="每日排行獎勵",
        value=f"🥇 {economy_settings['daily_top3'][0]} | 🥈 {economy_settings['daily_top3'][1]} | 🥉 {economy_settings['daily_top3'][2]} {currency_name()}",
        inline=False
    )
    embed.add_field(
        name="可用指令",
        value="`/economy balance` 餘額 | `/economy pay` 轉帳 | `/economy daily` 簽到\n"
              "`/economy leaderboard` 排行榜 | `/economy info` 資訊\n"
              "`/economy mint` 發錢(擁有者) | `/economy set_currency` 改幣名(擁有者)",
        inline=False
    )
    embed.set_footer(text="此看板即時更新，反映最新經濟數據")
    return embed


def _get_economy_panel_channel():
    """取得目前設定的經濟看板頻道物件（若已設定且仍存在）。"""
    ch_id = economy_settings.get("info_channel_id")
    if not ch_id:
        return None
    for guild in bot.guilds:
        ch = guild.get_channel(int(ch_id))
        if ch:
            return ch
    return None


async def setup_economy_panel():
    """(重新)發送經濟看板到設定的頻道，並清除任何殘留/過期的舊看板訊息。
    用於：擁有者設定頻道時，以及每次機器人重啟時（自動偵測廢棄面板刪掉重開）。"""
    channel = _get_economy_panel_channel()
    if not channel:
        return None

    # 1) 用儲存的 message_id 快速刪除舊看板
    old_msg_id = economy_settings.get("info_message_id")
    if old_msg_id:
        try:
            old_msg = await channel.fetch_message(int(old_msg_id))
            await old_msg.delete()
            print(f"🧹 已刪除舊的經濟看板訊息（ID: {old_msg_id}）")
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"⚠️ 刪除舊經濟看板失敗（by ID）：{e}")

    # 2) 安全網：掃描頻道近期歷史，清除任何殘留/廢棄的看板訊息
    #    （涵蓋 message_id 遺失、或曾經手動重複執行導致殘留多則的情況）
    try:
        async for msg in channel.history(limit=30):
            if msg.author.id == bot.user.id and msg.embeds:
                if msg.embeds[0].title and ECONOMY_PANEL_EMBED_TITLE_MARKER in msg.embeds[0].title:
                    try:
                        await msg.delete()
                        print(f"🧹 已清除殘留的經濟看板訊息（ID: {msg.id}）")
                    except Exception:
                        pass
    except Exception as e:
        print(f"⚠️ 掃描舊經濟看板失敗：{e}")

    # 3) 發送全新的看板訊息
    try:
        new_msg = await channel.send(embed=_build_economy_info_embed())
        economy_settings["info_message_id"] = new_msg.id
        save_economy()
        print(f"✅ 經濟看板已（重新）發送至 #{channel.name}（訊息 ID: {new_msg.id}）")
        return new_msg
    except Exception as e:
        print(f"❌ 發送經濟看板失敗：{e}")
        return None


async def refresh_economy_panel():
    """就地編輯現有的經濟看板訊息以反映最新資料；若訊息已不存在則重新發送。"""
    channel = _get_economy_panel_channel()
    if not channel:
        return

    msg_id = economy_settings.get("info_message_id")
    if not msg_id:
        await setup_economy_panel()
        return

    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=_build_economy_info_embed())
    except discord.NotFound:
        await setup_economy_panel()
    except Exception as e:
        print(f"⚠️ 更新經濟看板失敗：{e}")


async def economy_panel_loop():
    """經濟看板背景循環：機器人重啟時自動清除廢棄面板並重新發送一次；
    之後每 60 秒兜底刷新一次（即時性主要由 save_economy() 觸發的事件驅動
    更新負責，這裡只是保險，避免漏掉任何未經過 save_economy() 的變動）。"""
    await bot.wait_until_ready()
    await asyncio.sleep(3)  # 稍等其他啟動流程（頻道快取等）就緒
    try:
        await setup_economy_panel()
    except Exception as e:
        print(f"⚠️ 經濟看板啟動初始化失敗：{e}")
    while True:
        await asyncio.sleep(60)
        try:
            await refresh_economy_panel()
        except Exception as e:
            print(f"⚠️ 經濟看板定期刷新失敗：{e}")


# ── 事件：新成員加入 ──
@bot.event
async def on_member_join(member):
    """新成員加入伺服器時自動給予初始餘額。"""
    try:
        if member.guild.id != GUILD_ID:
            return  # 只在 ICEA 伺服器生效
        user_id_str = str(member.id)
        if user_id_str not in economy_balances:
            _ensure_user(user_id_str, member.display_name)
            balance = economy_balances[user_id_str]["balance"]
            print(f"💰 新成員 {member.display_name}({user_id_str}) 加入，自動給予 {balance} {currency_name()}")
            # 嘗試發歡迎訊息到系統頻道
            try:
                if member.guild.system_channel:
                    await member.guild.system_channel.send(
                        f"歡迎 {member.mention} 加入 ICEA！\n"
                        f"已自動發放 **{balance} {currency_name()}**，使用 `/economy balance` 查看餘額。"
                    )
            except Exception:
                pass  # 發不出來就算了
    except Exception as e:
        print(f"⚠️ on_member_join 經濟系統錯誤：{e}")


# ── 指令群組 ──
class EconomyGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="economy", description="經濟系統")

    @app_commands.command(name="balance", description="查看你的餘額")
    async def eco_balance(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        _ensure_user(user_id_str, interaction.user.display_name)
        bal = get_balance(user_id_str)
        embed = discord.Embed(
            title=f"💰 {interaction.user.display_name} 的餘額",
            description=f"目前持有 **{bal}** {currency_name()}",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pay", description="轉帳給其他成員")
    @app_commands.describe(user="收款人", amount="轉帳金額")
    async def eco_pay(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message("❌ 金額必須大於 0。", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ 不能轉帳給自己。", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("❌ 不能轉帳給機器人。", ephemeral=True)
            return

        from_id = str(interaction.user.id)
        to_id = str(user.id)
        _ensure_user(from_id, interaction.user.display_name)

        if get_balance(from_id) < amount:
            await interaction.response.send_message(
                f"❌ 餘額不足。你只有 {get_balance(from_id)} {currency_name()}。", ephemeral=True
            )
            return

        success = transfer_balance(from_id, to_id, amount)
        if success:
            _ensure_user(to_id, user.display_name)
            new_bal = get_balance(from_id)
            embed = discord.Embed(
                title="💸 轉帳成功",
                description=(
                    f"{interaction.user.mention} → {user.mention}\n"
                    f"金額：**{amount}** {currency_name()}\n"
                    f"你的餘額：**{new_bal}** {currency_name()}"
                ),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("❌ 轉帳失敗。", ephemeral=True)

    @app_commands.command(name="mint", description="發行貨幣給指定用戶（僅擁有者）")
    @app_commands.describe(user="收款人", amount="發行金額")
    async def eco_mint(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("❌ 金額必須大於 0。", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("❌ 不能發給機器人。", ephemeral=True)
            return

        to_id = str(user.id)
        new_bal = add_balance(to_id, amount, user.display_name)
        embed = discord.Embed(
            title="🪙 發行成功",
            description=(
                f"已發行 **{amount}** {currency_name()} 給 {user.mention}\n"
                f"{user.display_name} 的餘額：**{new_bal}** {currency_name()}"
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="set_currency", description="更改貨幣名稱（僅擁有者）")
    @app_commands.describe(name="新幣名")
    async def eco_set_currency(self, interaction: discord.Interaction, name: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        name = name.strip()[:20]  # 限制長度
        if not name:
            await interaction.response.send_message("❌ 幣名不可為空。", ephemeral=True)
            return

        old_name = currency_name()
        economy_settings["currency_name"] = name
        save_economy()
        embed = discord.Embed(
            title="✅ 幣名已更改",
            description=f"{old_name} → **{name}**",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="查看財富排行榜")
    async def eco_leaderboard(self, interaction: discord.Interaction):
        if not economy_balances:
            await interaction.response.send_message("目前沒有任何用戶資料。", ephemeral=True)
            return

        ranked = sorted(
            economy_balances.items(),
            key=lambda x: x[1].get("balance", 0),
            reverse=True
        )[:10]

        embed = discord.Embed(
            title=f"🏆 {currency_name()}財富排行榜",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        medal = ["🥇", "🥈", "🥉"]
        desc_lines = []
        for i, (uid, data) in enumerate(ranked):
            name = data.get("username", uid)
            bal = data.get("balance", 0)
            prefix = medal[i] if i < 3 else f"`{i+1}.`"
            desc_lines.append(f"{prefix} **{name}** — {bal} {currency_name()}")
        embed.description = "\n".join(desc_lines) if desc_lines else "無資料"
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="每日簽到領取獎勵")
    async def eco_daily(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        user = _ensure_user(user_id_str, interaction.user.display_name)

        today = datetime.now(GMT8).strftime("%Y-%m-%d")
        if user.get("last_daily") == today:
            await interaction.response.send_message(
                "❌ 今天已經簽到了！明天再來。", ephemeral=True
            )
            return

        reward = _eco_random.randint(
            economy_settings["daily_bonus_min"],
            economy_settings["daily_bonus_max"]
        )
        user["last_daily"] = today
        new_bal = add_balance(user_id_str, reward, interaction.user.display_name)

        embed = discord.Embed(
            title="📅 每日簽到",
            description=(
                f"獲得 **{reward}** {currency_name()}！\n"
                f"目前餘額：**{new_bal}** {currency_name()}"
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="info", description="查看經濟系統資訊")
    async def eco_info(self, interaction: discord.Interaction):
        panel_channel = _get_economy_panel_channel()
        if panel_channel:
            await interaction.response.send_message(
                f"📌 經濟系統資訊看板已固定在 {panel_channel.mention}，會即時更新，前往查看即可！",
                ephemeral=True
            )
            return
        # 尚未設定固定看板頻道 → 直接顯示一次性資訊（僅擁有者看得到頻道設定提示）
        embed = _build_economy_info_embed()
        if is_owner(interaction):
            embed.set_footer(text="提示：使用 /economy set_info_channel 可設定固定看板頻道，之後會自動即時更新")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="set_info_channel", description="設定經濟系統資訊看板的固定頻道（僅擁有者）")
    @app_commands.describe(channel="要固定顯示看板的頻道")
    async def eco_set_info_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        economy_settings["info_channel_id"] = channel.id
        economy_settings["info_message_id"] = None  # 強制重新發送（清除舊頻道殘留的關聯）
        save_economy()

        await interaction.response.send_message(f"⏳ 正在於 {channel.mention} 設定經濟看板...", ephemeral=True)
        new_msg = await setup_economy_panel()
        if new_msg:
            await interaction.followup.send(f"✅ 經濟看板已設定至 {channel.mention}，將即時更新。", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ 看板設定已儲存，但發送失敗，請確認機器人在 {channel.mention} 有發言權限。", ephemeral=True)
