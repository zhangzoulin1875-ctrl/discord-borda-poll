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
}

# {user_id_str: {"balance": int, "username": str, "last_daily": "YYYY-MM-DD"}}
economy_balances = {}

# 每日問答排行 {date: {user_id_str: {"name": str, "correct": int}}}
economy_quiz_daily = {"date": "", "scores": {}}


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
        await interaction.response.send_message(embed=embed)
