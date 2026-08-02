import sys

with open('discord_borda_poll.py', 'r') as f:
    content = f.read()

# 1. Update imports
old_imp = 'from typing import List, Dict, Optional\nimport asyncio'
new_imp = 'from typing import List, Dict, Optional, Tuple\nfrom datetime import datetime, timedelta\nimport asyncio'
assert old_imp in content, "old_imp not found"
content = content.replace(old_imp, new_imp)

# 2. Update _poll_to_dict
old_poll_to_dict = '''def _poll_to_dict(poll):
    return {
        "poll_id": poll.poll_id,
        "title": poll.title,
        "mode": poll.mode,
        "status": poll.status,
        "vote_count": poll.vote_count(),
        "option_count": poll.option_count(),
        "allowed_roles": poll.allowed_roles,
        "options": [{"text": opt.text} for opt in poll.options],
        "votes": {str(uid): v for uid, v in poll.votes.items()},
    }'''

new_poll_to_dict = '''def _poll_to_dict(poll):
    return {
        "poll_id": poll.poll_id,
        "title": poll.title,
        "description": poll.description,
        "mode": poll.mode,
        "status": poll.status,
        "ends_at": poll.ends_at,
        "allow_revote": poll.allow_revote,
        "channel_id": poll.channel_id,
        "vote_count": poll.vote_count(),
        "option_count": poll.option_count(),
        "allowed_roles": poll.allowed_roles,
        "options": [{"text": opt.text} for opt in poll.options],
        "votes": {str(uid): v for uid, v in poll.votes.items()},
    }'''

assert old_poll_to_dict in content, "old_poll_to_dict not found"
content = content.replace(old_poll_to_dict, new_poll_to_dict)

# 3. Update Poll dataclass
old_poll_dataclass = '''@dataclass
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
        return len(self.votes)'''

new_poll_dataclass = '''@dataclass
class Poll:
    poll_id: str
    title: str
    description: str = ""
    mode: str = "borda"  # "borda" | "simple"
    options: List[PollOption] = field(default_factory=list)
    status: str = "drafting"  # "drafting" | "active" | "ended"
    # borda mode: Dict[int, List[int]]  (user_id -> ranked option indices)
    # simple mode: Dict[int, int]       (user_id -> option index)
    votes: dict = field(default_factory=dict)
    message_id: Optional[int] = None
    created_by: int = 0
    allowed_roles: List[int] = field(default_factory=list)  # empty = everyone
    allow_revote: bool = True
    ends_at: Optional[str] = None
    channel_id: Optional[int] = None

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

    def _break_ties_borda(self, tied_texts: List[str]) -> List[str]:
        text_to_idx = {opt.text: i for i, opt in enumerate(self.options)}
        stats = {}
        for text_a in tied_texts:
            idx_a = text_to_idx.get(text_a)
            wins = 0
            total_prefs = 0
            if idx_a is not None:
                for text_b in tied_texts:
                    if text_a == text_b:
                        continue
                    idx_b = text_to_idx.get(text_b)
                    if idx_b is None:
                        continue
                    a_over_b = 0
                    b_over_a = 0
                    for ranking in self.votes.values():
                        if isinstance(ranking, list) and idx_a in ranking and idx_b in ranking:
                            if ranking.index(idx_a) < ranking.index(idx_b):
                                a_over_b += 1
                            elif ranking.index(idx_b) < ranking.index(idx_a):
                                b_over_a += 1
                    if a_over_b > b_over_a:
                        wins += 1
                    total_prefs += a_over_b
            stats[text_a] = (wins, total_prefs)

        return sorted(
            tied_texts,
            key=lambda t: (-stats.get(t, (0, 0))[0], -stats.get(t, (0, 0))[1], t)
        )

    def ranked_results(self) -> List[Tuple[str, int, str]]:
        scores = self.tally()
        if not scores:
            return []

        from collections import defaultdict
        score_groups = defaultdict(list)
        for text, score in scores.items():
            score_groups[score].append(text)

        sorted_scores = sorted(score_groups.keys(), reverse=True)
        results = []
        for score in sorted_scores:
            group = score_groups[score]
            if len(group) == 1:
                results.append((group[0], score, ""))
            else:
                if self.mode == "simple":
                    sorted_group = sorted(group)
                    for text in sorted_group:
                        results.append((text, score, "（平局，依字母順序）"))
                else:
                    sorted_group = self._break_ties_borda(group)
                    for text in sorted_group:
                        results.append((text, score, "（平局，以正面對決排序）"))
        return results'''

assert old_poll_dataclass in content, "old_poll_dataclass not found"
content = content.replace(old_poll_dataclass, new_poll_dataclass)

# 4. Add auto_announce_results & auto_end_loop, update setup_hook
old_setup_hook = '''@bot.event
async def setup_hook():
    await keep_alive_server()
    asyncio.ensure_future(self_ping_loop())'''

new_setup_hook = '''async def auto_announce_results(guild_id: int, poll: Poll):
    guild = bot.get_guild(guild_id)
    if not guild:
        try:
            guild = await bot.fetch_guild(guild_id)
        except Exception:
            pass
    if not guild:
        return False

    channel = None
    if poll.channel_id:
        channel = guild.get_channel(poll.channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(poll.channel_id)
            except Exception:
                pass

    if not channel:
        return False

    results = poll.ranked_results()
    total_votes = poll.vote_count()
    n = poll.option_count()

    scoring_desc = (
        f"計分方式：波達計數法（第 1 名得 {n-1} 分，最後一名得 0 分）"
        if poll.mode == "borda"
        else "計分方式：一般投票（最高票獲勝）"
    )

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    if not results or total_votes == 0:
        desc = f"🗳️ 共 0 人投票 · {n} 個選項\n{scoring_desc}\n\n沒有收到任何投票。"
    else:
        for rank_pos, (opt_text, score, tie_note) in enumerate(results):
            medal = medals[rank_pos] if rank_pos < 3 else f"`{rank_pos+1}`"
            unit = "分" if poll.mode == "borda" else "票"
            note_str = f" {tie_note}" if tie_note else ""
            lines.append(f"{medal}  **{opt_text}** — {score} {unit}{note_str}")
        desc = f"🗳️ 共 {total_votes} 人投票 · {n} 個選項\n{scoring_desc}\n\n" + "\n".join(lines)

    embed = discord.Embed(
        title=f"📊 投票結果：{poll.title}",
        description=desc,
        color=discord.Color.gold(),
    )
    if poll.description:
        embed.add_field(name="投票說明", value=poll.description, inline=False)
    embed.set_footer(text=f"投票 ID: {poll.poll_id} · 投票已結束")

    try:
        await channel.send(embed=embed)
        return True
    except Exception as e:
        print(f"❌ 自動公告結果失敗：{e}")
        return False


async def auto_end_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = datetime.utcnow()
            for guild_id, polls in list(guild_polls.items()):
                for poll_id, poll in list(polls.items()):
                    if poll.status == "active" and poll.ends_at:
                        try:
                            ends_dt = datetime.fromisoformat(poll.ends_at)
                            if now >= ends_dt:
                                poll.status = "ended"
                                await auto_announce_results(guild_id, poll)
                        except Exception as e:
                            print(f"❌ 自動結束投票 `{poll_id}` 出錯: {e}")
        except Exception as e:
            print(f"❌ auto_end_loop 出錯: {e}")
        await asyncio.sleep(60)


@bot.event
async def setup_hook():
    await keep_alive_server()
    asyncio.ensure_future(self_ping_loop())
    asyncio.ensure_future(auto_end_loop())'''

assert old_setup_hook in content, "old_setup_hook not found"
content = content.replace(old_setup_hook, new_setup_hook)

# 5. SimpleVoteView check
old_simple_check = '''            if interaction.user.id in self.poll.votes:
                await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
                return'''

new_simple_check = '''            if interaction.user.id in self.poll.votes and not self.poll.allow_revote:
                await interaction.response.send_message("❌ 你已經投過票了，此投票不允許改票。", ephemeral=True)
                return'''

assert old_simple_check in content, "old_simple_check not found"
content = content.replace(old_simple_check, new_simple_check)

# 6. ManagePanelView.on_start
old_manage_start = '''        poll.status = "active"

        if poll.mode == "borda":
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：波達計數法\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方下拉選單，依偏好排序所有選項（第 1 名最偏好）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 波達計數法投票 · 排序所有選項即可投票")
        else:
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：一般投票\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方按鈕投給你支持的選項（每人一票）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 一般投票 · 每人一票")'''

new_manage_start = '''        poll.status = "active"
        poll.channel_id = interaction.channel_id

        desc_prefix = f"📝 {poll.description}\n\n" if poll.description else ""

        if poll.mode == "borda":
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"{desc_prefix}"
                    f"模式：波達計數法\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方下拉選單，依偏好排序所有選項（第 1 名最偏好）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 波達計數法投票 · 排序所有選項即可投票")
        else:
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"{desc_prefix}"
                    f"模式：一般投票\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方按鈕投給你支持的選項（每人一票）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 一般投票 · 每人一票")'''

assert old_manage_start in content, "old_manage_start not found"
content = content.replace(old_manage_start, new_manage_start)

# 7. /poll create
old_create = '''    @app_commands.command(name="create", description="建立新投票（管理員限定）")
    @app_commands.describe(
        title="投票標題",
        mode="投票模式：borda（波達計數法）或 simple（一般投票）",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="波達計數法（排序偏好）", value="borda"),
        app_commands.Choice(name="一般投票（單選）", value="simple"),
    ])
    async def create(self, interaction: discord.Interaction, title: str, mode: app_commands.Choice[str] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        if guild_id not in guild_polls:
            guild_polls[guild_id] = {}

        poll_mode = mode.value if mode else "borda"
        poll_id = gen_poll_id()
        poll = Poll(
            poll_id=poll_id,
            title=title,
            mode=poll_mode,
            created_by=interaction.user.id,
        )
        guild_polls[guild_id][poll_id] = poll

        await interaction.response.send_message(
            f"📝 投票「**{title}**」已建立！\n"
            f"**ID：** `{poll_id}`\n"
            f"**模式：** {mode_name(poll_mode)}\n\n"
            f"使用 `/poll add <poll_id> <option>` 新增選項，或用 `/poll manage` 開啟管理面板。"
        )'''

new_create = '''    @app_commands.command(name="create", description="建立新投票（管理員限定）")
    @app_commands.describe(
        title="投票標題",
        mode="投票模式：borda（波達計數法）或 simple（一般投票）",
        description="投票說明（選填）",
        duration_hours="限時（小時，0=不限時）",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="波達計數法（排序偏好）", value="borda"),
        app_commands.Choice(name="一般投票（單選）", value="simple"),
    ])
    async def create(
        self,
        interaction: discord.Interaction,
        title: str,
        mode: app_commands.Choice[str] = None,
        description: str = "",
        duration_hours: int = 0,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        if guild_id not in guild_polls:
            guild_polls[guild_id] = {}

        poll_mode = mode.value if mode else "borda"
        poll_id = gen_poll_id()
        ends_at = (datetime.utcnow() + timedelta(hours=duration_hours)).isoformat() if duration_hours > 0 else None
        poll = Poll(
            poll_id=poll_id,
            title=title,
            description=description,
            mode=poll_mode,
            created_by=interaction.user.id,
            ends_at=ends_at,
        )
        guild_polls[guild_id][poll_id] = poll

        desc_info = f"\n**說明：** {description}" if description else ""
        time_info = f"\n**限時：** {duration_hours} 小時" if duration_hours > 0 else ""

        await interaction.response.send_message(
            f"📝 投票「**{title}**」已建立！\n"
            f"**ID：** `{poll_id}`\n"
            f"**模式：** {mode_name(poll_mode)}"
            f"{desc_info}{time_info}\n\n"
            f"使用 `/poll add <poll_id> <option>` 新增選項，或用 `/poll manage` 開啟管理面板。"
        )'''

assert old_create in content, "old_create not found"
content = content.replace(old_create, new_create)

# 8. /poll start
old_start = '''    @app_commands.command(name="start", description="啟動指定投票（管理員限定）")
    @app_commands.describe(poll_id="投票 ID")
    async def start(self, interaction: discord.Interaction, poll_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束。", ephemeral=True)
            return
        if poll.option_count() < 2:
            await interaction.response.send_message("❌ 至少需要 2 個選項才能啟動投票。", ephemeral=True)
            return

        poll.status = "active"

        if poll.mode == "borda":
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：波達計數法\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方下拉選單，依偏好排序所有選項（第 1 名最偏好）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 波達計數法投票 · 排序所有選項即可投票")
        else:
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"模式：一般投票\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方按鈕投給你支持的選項（每人一票）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 一般投票 · 每人一票")

        # 公告訊息不帶 View — 成員請使用 /poll vote <id> 投票
        embed.set_footer(text=f"投票 ID: {poll.poll_id} · 請使用 /poll vote {poll.poll_id} 投票")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        poll.message_id = msg.id'''

new_start = '''    @app_commands.command(name="start", description="啟動指定投票（管理員限定）")
    @app_commands.describe(poll_id="投票 ID", duration_hours="限時（小時，0=不限時）")
    async def start(self, interaction: discord.Interaction, poll_id: str, duration_hours: int = 0):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束。", ephemeral=True)
            return
        if poll.option_count() < 2:
            await interaction.response.send_message("❌ 至少需要 2 個選項才能啟動投票。", ephemeral=True)
            return

        poll.status = "active"
        poll.channel_id = interaction.channel_id
        if duration_hours > 0:
            poll.ends_at = (datetime.utcnow() + timedelta(hours=duration_hours)).isoformat()

        desc_prefix = f"📝 {poll.description}\n\n" if poll.description else ""

        if poll.mode == "borda":
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"{desc_prefix}"
                    f"模式：波達計數法\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方下拉選單，依偏好排序所有選項（第 1 名最偏好）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 請使用 /poll vote {poll.poll_id} 投票")
        else:
            embed = discord.Embed(
                title=f"🗳️ 投票開始：{poll.title}",
                description=(
                    f"{desc_prefix}"
                    f"模式：一般投票\n"
                    f"共 {poll.option_count()} 個選項\n"
                    f"點擊下方按鈕投給你支持的選項（每人一票）。\n\n"
                    "📋 **選項：**\n"
                    + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"投票 ID: {poll.poll_id} · 請使用 /poll vote {poll.poll_id} 投票")

        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        poll.message_id = msg.id'''

assert old_start in content, "old_start not found"
content = content.replace(old_start, new_start)

# 9. /poll end
old_end = '''    @app_commands.command(name="end", description="結束指定投票並顯示結果（管理員限定）")
    @app_commands.describe(poll_id="投票 ID")
    async def end(self, interaction: discord.Interaction, poll_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未啟動。", ephemeral=True)
            return

        poll.status = "ended"
        scores = poll.tally()
        total_votes = poll.vote_count()
        n = poll.option_count()

        if not scores or total_votes == 0:
            await interaction.response.send_message(f"📊 投票「{poll.title}」已結束，但沒有收到任何投票。")
            return

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank_pos, (opt_text, score) in enumerate(ranked):
            medal = medals[rank_pos] if rank_pos < 3 else f"`{rank_pos+1}`"
            unit = "分" if poll.mode == "borda" else "票"
            lines.append(f"{medal}  **{opt_text}** — {score} {unit}")

        scoring_desc = (
            f"計分方式：波達計數法（第 1 名得 {n-1} 分，最後一名得 0 分）"
            if poll.mode == "borda"
            else "計分方式：一般投票（最高票獲勝）"
        )
        embed = discord.Embed(
            title=f"📊 投票結果：{poll.title}",
            description=(
                f"🗳️ 共 {total_votes} 人投票 · {n} 個選項\n"
                f"{scoring_desc}\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"投票 ID: {poll.poll_id} · 投票已結束")
        await interaction.response.send_message(embed=embed)'''

new_end = '''    @app_commands.command(name="end", description="結束指定投票並顯示結果（管理員限定）")
    @app_commands.describe(poll_id="投票 ID")
    async def end(self, interaction: discord.Interaction, poll_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未啟動。", ephemeral=True)
            return

        poll.status = "ended"
        if not poll.channel_id:
            poll.channel_id = interaction.channel_id

        results = poll.ranked_results()
        total_votes = poll.vote_count()
        n = poll.option_count()

        if not results or total_votes == 0:
            await interaction.response.send_message(f"📊 投票「{poll.title}」已結束，但沒有收到任何投票。")
            await auto_announce_results(interaction.guild.id, poll)
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank_pos, (opt_text, score, tie_note) in enumerate(results):
            medal = medals[rank_pos] if rank_pos < 3 else f"`{rank_pos+1}`"
            unit = "分" if poll.mode == "borda" else "票"
            note_str = f" {tie_note}" if tie_note else ""
            lines.append(f"{medal}  **{opt_text}** — {score} {unit}{note_str}")

        scoring_desc = (
            f"計分方式：波達計數法（第 1 名得 {n-1} 分，最後一名得 0 分）"
            if poll.mode == "borda"
            else "計分方式：一般投票（最高票獲勝）"
        )
        embed = discord.Embed(
            title=f"📊 投票結果：{poll.title}",
            description=(
                f"🗳️ 共 {total_votes} 人投票 · {n} 個選項\n"
                f"{scoring_desc}\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.gold(),
        )
        if poll.description:
            embed.add_field(name="投票說明", value=poll.description, inline=False)
        embed.set_footer(text=f"投票 ID: {poll.poll_id} · 投票已結束")

        await interaction.response.send_message(embed=embed)
        await auto_announce_results(interaction.guild.id, poll)'''

assert old_end in content, "old_end not found"
content = content.replace(old_end, new_end)

# 10. /poll vote
old_vote = '''    @app_commands.command(name="vote", description="投票（一般成員，依投票模式自動判斷）")
    @app_commands.describe(poll_id="投票 ID")
    async def vote(self, interaction: discord.Interaction, poll_id: str):
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
            return
        if interaction.user.id in poll.votes:
            await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
            return
        if not check_role_permission(interaction, poll):
            await interaction.response.send_message("❌ 你沒有參與此投票的身分組權限。", ephemeral=True)
            return

        if poll.mode == "borda":
            view = RankVoteView(poll, voter_id=interaction.user.id)
            await interaction.response.send_message(
                content=f"📊 **{poll.title}** — 排序你的偏好\n\n請選擇第 **1** 偏好：",
                view=view, ephemeral=True,
            )
        else:
            view = SimpleVoteView(poll, voter_id=interaction.user.id)
            await interaction.response.send_message(
                content=f"📊 **{poll.title}** — 點擊按鈕投給你支持的選項：",
                view=view, ephemeral=True,
            )'''

new_vote = '''    @app_commands.command(name="vote", description="投票（一般成員，依投票模式自動判斷）")
    @app_commands.describe(poll_id="投票 ID")
    async def vote(self, interaction: discord.Interaction, poll_id: str):
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
            return

        already_voted = interaction.user.id in poll.votes
        revote_prefix = ""
        if already_voted:
            if not poll.allow_revote:
                await interaction.response.send_message("你已經投過票了，此投票不允許改票。", ephemeral=True)
                return
            revote_prefix = "你之前投過票了，可以重新投票（將覆蓋之前的紀錄）\n\n"

        if not check_role_permission(interaction, poll):
            await interaction.response.send_message("❌ 你沒有參與此投票的身分組權限。", ephemeral=True)
            return

        if poll.mode == "borda":
            view = RankVoteView(poll, voter_id=interaction.user.id)
            await interaction.response.send_message(
                content=f"{revote_prefix}📊 **{poll.title}** — 排序你的偏好\n\n請選擇第 **1** 偏好：",
                view=view, ephemeral=True,
            )
        else:
            view = SimpleVoteView(poll, voter_id=interaction.user.id)
            await interaction.response.send_message(
                content=f"{revote_prefix}📊 **{poll.title}** — 點擊按鈕投給你支持的選項：",
                view=view, ephemeral=True,
            )'''

assert old_vote in content, "old_vote not found"
content = content.replace(old_vote, new_vote)

with open('discord_borda_poll.py', 'w') as f:
    f.write(content)

print("SUCCESS: All patches applied cleanly.")
