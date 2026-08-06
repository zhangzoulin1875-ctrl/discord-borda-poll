# ═════════════════════════════════════════════════════════════════
# Module: 20_poll (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

class RankVoteView(discord.ui.View):
    def __init__(self, poll: Poll, voter_id: int = 0):
        super().__init__(timeout=600)
        self.poll = poll
        self.voter_id = voter_id
        self._current_rank: List[int] = []

        options_for_select = [
            discord.SelectOption(
                label=f"{i+1}. {opt.text[:90]}",
                value=str(i),
                description=f"選項 {i+1}",
            )
            for i, opt in enumerate(poll.options)
        ]

        select = discord.ui.Select(
            placeholder="選擇你的第 1 偏好 👑",
            min_values=1, max_values=1,
            options=options_for_select,
            custom_id=f"borda_rank:{poll.poll_id}",
        )
        select.callback = self.on_rank_select
        self.add_item(select)

    async def on_rank_select(self, interaction: discord.Interaction):
        if self.voter_id and interaction.user.id != self.voter_id:
            await interaction.response.send_message("❌ 這不是你的投票面板。請用 /poll vote 取得你自己的投票面板。", ephemeral=True)
            return
        if self.poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
            return

        if not check_role_permission(interaction, self.poll):
            await interaction.response.send_message("❌ 你沒有參與此投票的身分組權限。", ephemeral=True)
            return

        opt_idx = int(interaction.data["values"][0])
        if opt_idx in self._current_rank:
            await interaction.response.send_message("⚠️ 你已經排過這個選項了。", ephemeral=True)
            return

        self._current_rank.append(opt_idx)
        rank_num = len(self._current_rank)
        n = self.poll.option_count()

        if rank_num >= n:
            self.poll.votes[interaction.user.id] = list(self._current_rank)
            save_polls_to_disk()
            ranking_text = "\n".join(
                f"{i+1}. {self.poll.options[idx].text}"
                for i, idx in enumerate(self._current_rank)
            )
            await interaction.response.edit_message(
                content=f"✅ **投票完成！** 你的排序：\n{ranking_text}\n\n謝謝投票，結果將在管理員結束投票後公布。",
                ephemeral=True,
                view=None,
            )
            return

        remaining = [
            discord.SelectOption(
                label=f"{i+1}. {self.poll.options[i].text[:90]}",
                value=str(i),
                description=f"選項 {i+1}",
            )
            for i in range(n) if i not in self._current_rank
        ]
        self.children[0].options = remaining
        self.children[0].placeholder = f"選擇你的第 {rank_num + 1} 偏好"

        progress = "\n".join(
            f"{i+1}. {self.poll.options[idx].text}"
            for i, idx in enumerate(self._current_rank)
        )
        await interaction.response.edit_message(
            content=f"📊 **{self.poll.title}** — 排序你的偏好\n\n目前已排：\n{progress}\n\n請選擇第 **{rank_num + 1}** 偏好：",
            view=self,
        )


# ──────────────────────────────────────────────
# 一般投票 View（按鈕單選）
# ──────────────────────────────────────────────

class SimpleVoteView(discord.ui.View):
    def __init__(self, poll: Poll, voter_id: int):
        super().__init__(timeout=600)  # 10 分鐘逾時
        self.poll = poll
        self.voter_id = voter_id

        for i, opt in enumerate(poll.options):
            btn = discord.ui.Button(
                label=opt.text[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"simple_vote:{poll.poll_id}:{i}",
                row=i // 5,
            )
            btn.callback = self.make_callback(i)
            self.add_item(btn)

    def make_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            if self.voter_id and interaction.user.id != self.voter_id:
                await interaction.response.send_message("❌ 這不是你的投票面板。請用 /poll vote 取得你自己的投票面板。", ephemeral=True)
                return
            if self.poll.status != "active":
                await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
                return

            if not check_role_permission(interaction, self.poll):
                await interaction.response.send_message("❌ 你沒有參與此投票的身分組權限。", ephemeral=True)
                return
            if interaction.user.id in self.poll.votes:
                await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
                return
            self.poll.votes[interaction.user.id] = idx
            save_polls_to_disk()
            await interaction.response.send_message(
                f"✅ 已投票給 **{self.poll.options[idx].text}**！謝謝參與。",
                ephemeral=True,
            )
        return callback


# ──────────────────────────────────────────────
# 投票管理面板 View
# ──────────────────────────────────────────────



# ──────────────────────────────────────────────
# 身分組選擇 View
# ──────────────────────────────────────────────

class RoleSelectView(discord.ui.View):
    def __init__(self, manage_view: "ManagePanelView", poll: Poll):
        super().__init__(timeout=120)
        self.manage_view = manage_view
        self.poll = poll

        role_select = discord.ui.RoleSelect(
            placeholder="選擇允許投票的身分組（可多選）…",
            min_values=0,
            max_values=25,
        )
        role_select.callback = self.on_roles_selected
        self.add_item(role_select)

        btn_clear = discord.ui.Button(label="清除限制（所有人可投）", style=discord.ButtonStyle.secondary, emoji="🔓")
        btn_clear.callback = self.on_clear
        self.add_item(btn_clear)

        btn_back = discord.ui.Button(label="返回管理面板", style=discord.ButtonStyle.primary, emoji="⬅️")
        btn_back.callback = self.on_back
        self.add_item(btn_back)

    async def on_roles_selected(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        selected = interaction.data.get("values", [])
        self.poll.allowed_roles = [int(r) for r in selected]
        if self.poll.allowed_roles:
            role_names = []
            for rid in self.poll.allowed_roles:
                role = interaction.guild.get_role(rid)
                if role:
                    role_names.append(role.name)
            await interaction.response.send_message(
                f"✅ 已設定身分組限制：{', '.join(role_names)}", ephemeral=True
            )
        else:
            await interaction.response.send_message("⚠️ 沒有選擇任何身分組，將不設限制。", ephemeral=True)
        self.manage_view._refresh_select()
        embed = self.manage_view._poll_detail_embed(self.poll)
        await interaction.message.edit(embed=embed, view=self.manage_view)

    async def on_clear(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        self.poll.allowed_roles = []
        save_polls_to_disk()
        await interaction.response.send_message("🔓 已清除身分組限制，所有人皆可投票。", ephemeral=True)
        self.manage_view._refresh_select()
        embed = self.manage_view._poll_detail_embed(self.poll)
        await interaction.message.edit(embed=embed, view=self.manage_view)

    async def on_back(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        self.manage_view._refresh_select()
        if self.manage_view.selected_poll_id:
            embed = self.manage_view._poll_detail_embed(self.poll)
        else:
            embed = self.manage_view._guild_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self.manage_view)


class ManagePanelView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.selected_poll_id: Optional[str] = None
        self._refresh_select()

    def _refresh_select(self):
        self.clear_items()
        polls = guild_polls.get(self.guild_id, {})
        if not polls:
            select = discord.ui.Select(
                placeholder="沒有投票",
                min_values=1, max_values=1,
                options=[discord.SelectOption(label="（尚無投票）", value="none")],
                disabled=True,
            )
            self.add_item(select)
            return

        options = []
        for pid, p in polls.items():
            status_text = {"drafting": "準備中", "active": "進行中", "ended": "已結束"}.get(p.status, p.status)
            label = f"[{pid}] {p.title[:80]}"
            options.append(discord.SelectOption(
                label=label,
                value=pid,
                description=f"{mode_name(p.mode)} · {status_text} · {p.vote_count()} 票 · {p.option_count()} 選項",
            ))

        select = discord.ui.Select(
            placeholder="選擇要管理的投票…",
            min_values=1, max_values=1,
            options=options,
        )
        select.callback = self.on_select
        self.add_item(select)

        if self.selected_poll_id:
            poll = get_poll(self.guild_id, self.selected_poll_id)
            if poll:
                if poll.status == "drafting":
                    btn_start = discord.ui.Button(label="啟動投票", style=discord.ButtonStyle.success, emoji="▶️")
                    btn_start.callback = self.on_start
                    self.add_item(btn_start)

                if poll.status == "active":
                    btn_end = discord.ui.Button(label="結束投票", style=discord.ButtonStyle.danger, emoji="⏹️")
                    btn_end.callback = self.on_end
                    self.add_item(btn_end)

                btn_view = discord.ui.Button(label="查看詳情", style=discord.ButtonStyle.secondary, emoji="📋")
                btn_view.callback = self.on_view
                self.add_item(btn_view)

                btn_roles = discord.ui.Button(label="身分組限制", style=discord.ButtonStyle.secondary, emoji="🔐")
                btn_roles.callback = self.on_roles
                self.add_item(btn_roles)

                btn_delete = discord.ui.Button(label="刪除投票", style=discord.ButtonStyle.danger, emoji="🗑️")
                btn_delete.callback = self.on_delete
                self.add_item(btn_delete)

    async def on_select(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        self.selected_poll_id = interaction.data["values"][0]
        self._refresh_select()
        await self._update_message(interaction)

    async def _update_message(self, interaction: discord.Interaction):
        poll = get_poll(self.guild_id, self.selected_poll_id) if self.selected_poll_id else None
        if poll:
            embed = self._poll_detail_embed(poll)
        else:
            embed = self._guild_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def _poll_detail_embed(self, poll: Poll) -> discord.Embed:
        opt_lines = []
        for i, opt in enumerate(poll.options):
            if poll.status in ("active", "ended"):
                tally = poll.tally()
                score = tally.get(opt.text, 0)
                unit = "分" if poll.mode == "borda" else "票"
                opt_lines.append(f"{i+1}. {opt.text} — **{score}** {unit}")
            else:
                opt_lines.append(f"{i+1}. {opt.text}")

        embed = discord.Embed(
            title=f"🔧 管理投票：{poll.title}",
            description=(
                f"**ID：** `{poll.poll_id}`\n"
                f"**模式：** {mode_name(poll.mode)}\n"
                f"**狀態：** {status_emoji(poll.status)}\n"
                f"**投票人數：** {poll.vote_count()}\n"
                f"**選項數：** {poll.option_count()}\n\n"
                f"**\u8eab\u4efd\u7d44\u9650\u5236\uff1a** " + (", ".join("<@&{}>".format(rid) for rid in poll.allowed_roles) if poll.allowed_roles else "\u6240\u6709\u4eba") + "\n"
                f"**選項清單：**\n" + "\n".join(opt_lines)
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="使用下方按鈕管理此投票")
        return embed

    def _guild_overview_embed(self) -> discord.Embed:
        polls = guild_polls.get(self.guild_id, {})
        if not polls:
            return discord.Embed(
                title="🔧 投票管理面板",
                description="目前沒有任何投票。\n使用 `/poll create` 建立新投票。",
                color=discord.Color.blurple(),
            )

        lines = []
        for pid, p in polls.items():
            lines.append(f"`{pid}`  {p.title} — {status_emoji(p.status)} · {mode_name(p.mode)} · {p.vote_count()} 票")

        embed = discord.Embed(
            title="🔧 投票管理面板",
            description=f"本伺服器共有 {len(polls)} 個投票：\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="從下拉選單選擇一個投票進行管理")
        return embed

    async def on_start(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束。", ephemeral=True)
            return
        if poll.option_count() < 2:
            await interaction.response.send_message("❌ 至少需要 2 個選項才能啟動投票。", ephemeral=True)
            return

        poll.status = "active"
        save_polls_to_disk()

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
        poll.message_id = msg.id

        self._refresh_select()
        await interaction.followup.edit_message(interaction.message.id, embed=self._poll_detail_embed(poll), view=self)

    async def on_end(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未啟動。", ephemeral=True)
            return

        poll.status = "ended"
        save_polls_to_disk()
        scores = poll.tally()
        total_votes = poll.vote_count()
        n = poll.option_count()

        if not scores or total_votes == 0:
            await interaction.response.send_message(f"📊 投票「{poll.title}」已結束，但沒有收到任何投票。")
        else:
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
            await interaction.response.send_message(embed=embed)

        self._refresh_select()
        await interaction.followup.edit_message(interaction.message.id, embed=self._poll_detail_embed(poll), view=self)

    async def on_view(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return

        # Build per-user vote detail (ephemeral followup)
        await interaction.response.defer(ephemeral=True)

        if not poll.votes:
            await interaction.followup.send("📭 目前還沒有人投票。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📋 投票明細：{poll.title}",
            color=discord.Color.blurple(),
        )

        lines = []
        for user_id, vote_data in poll.votes.items():
            user = interaction.guild.get_member(user_id)
            name = user.display_name if user else f"用戶 {user_id}"
            if poll.mode == "borda":
                # vote_data = [opt_idx, opt_idx, ...]
                ranked = []
                for rank_pos, opt_idx in enumerate(vote_data, 1):
                    opt_text = poll.options[opt_idx].text if opt_idx < len(poll.options) else f"#{opt_idx}"
                    ranked.append(f"{rank_pos}. {opt_text}")
                lines.append(f"**{name}**\n" + "  ".join(ranked))
            else:
                # vote_data = single opt_idx
                opt_text = poll.options[vote_data].text if vote_data < len(poll.options) else f"#{vote_data}"
                lines.append(f"**{name}**：{opt_text}")

        # Discord embed description limit = 4096 chars; chunk into fields if needed
        chunk = []
        chunk_len = 0
        field_count = 0
        for line in lines:
            if chunk_len + len(line) + 2 > 1000 or len(chunk) >= 10:
                embed.add_field(
                    name=f"投票者 ({field_count * 10 + 1}–{field_count * 10 + len(chunk)})" if field_count > 0 or len(lines) > 10 else "投票者",
                    value="\n".join(chunk),
                    inline=False,
                )
                chunk = []
                chunk_len = 0
                field_count += 1
            chunk.append(line)
            chunk_len += len(line) + 2
        if chunk:
            embed.add_field(
                name=f"投票者 ({field_count * 10 + 1}–{field_count * 10 + len(chunk)})" if field_count > 0 else "投票者",
                value="\n".join(chunk),
                inline=False,
            )

        embed.set_footer(text=f"共 {len(poll.votes)} 人投票 | 僅你可見")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_roles(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        role_view = RoleSelectView(self, poll)
        embed = discord.Embed(
            title=f"🔐 身份組限制：{poll.title}",
            description=(
                f"投票 ID：`{poll.poll_id}`\n"
                f"從下方選擇允許投票的身份組（可多選）。\n"
                f"不選任何身份組 = 所有人皆可投票。"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=role_view)

    async def on_delete(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(self.guild_id, self.selected_poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到投票。", ephemeral=True)
            return
        del guild_polls[self.guild_id][self.selected_poll_id]
        save_polls_to_disk()
        self.selected_poll_id = None
        self._refresh_select()
        embed = self._guild_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)


# ──────────────────────────────────────────────
# Slash Command Group
# ──────────────────────────────────────────────

class PollGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="poll", description="投票系統（波達計數法 + 一般投票）")

    @app_commands.command(name="create", description="建立新投票（管理員限定）")
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
        save_polls_to_disk()

        await interaction.response.send_message(
            f"📝 投票「**{title}**」已建立！\n"
            f"**ID：** `{poll_id}`\n"
            f"**模式：** {mode_name(poll_mode)}\n\n"
            f"使用 `/poll add <poll_id> <option>` 新增選項，或用 `/poll manage` 開啟管理面板。"
        )

    @app_commands.command(name="add", description="新增選項到指定投票（管理員限定）")
    @app_commands.describe(poll_id="投票 ID", option="選項內容")
    async def add(self, interaction: discord.Interaction, poll_id: str, option: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        poll = get_poll(interaction.guild.id, poll_id)
        if not poll:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束，無法新增選項。", ephemeral=True)
            return
        if len(poll.options) >= 25:
            await interaction.response.send_message("❌ Discord 上限 25 個選項。", ephemeral=True)
            return
        poll.add_option(option)
        save_polls_to_disk()
        await interaction.response.send_message(
            f"✅ 已新增選項 **{option}**（目前共 {poll.option_count()} 個選項）\n"
            f"投票 ID：`{poll_id}`\n"
            f"繼續用 `/poll add` 新增，或用 `/poll start {poll_id}` 啟動投票。"
        )

    @app_commands.command(name="list", description="查看投票清單或指定投票的選項")
    @app_commands.describe(poll_id="投票 ID（不填則列出所有投票）")
    async def list(self, interaction: discord.Interaction, poll_id: Optional[str] = None):
        polls = guild_polls.get(interaction.guild.id, {})

        if poll_id:
            poll = polls.get(poll_id)
            if not poll:
                await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
                return
            if not poll.options:
                await interaction.response.send_message(f"📭 投票「{poll.title}」目前沒有選項。", ephemeral=True)
                return
            lines = [f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options)]
            embed = discord.Embed(
                title=f"📊 {poll.title}",
                description=(
                    f"ID：`{poll.poll_id}`\n"
                    f"模式：{mode_name(poll.mode)}\n"
                    f"狀態：{status_emoji(poll.status)}\n\n"
                    + "\n".join(lines)
                ),
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed)
        else:
            if not polls:
                await interaction.response.send_message("❌ 目前沒有任何投票。", ephemeral=True)
                return
            lines = []
            for pid, p in polls.items():
                lines.append(f"`{pid}`  {p.title} — {status_emoji(p.status)} · {mode_name(p.mode)} · {p.vote_count()} 票 · {p.option_count()} 選項")
            embed = discord.Embed(
                title="📋 所有投票",
                description=f"共 {len(polls)} 個投票：\n\n" + "\n".join(lines),
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="start", description="啟動指定投票（管理員限定）")
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
        save_polls_to_disk()

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
        poll.message_id = msg.id

    @app_commands.command(name="end", description="結束指定投票並顯示結果（管理員限定）")
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
        save_polls_to_disk()
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
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delete", description="刪除指定投票（管理員限定）")
    @app_commands.describe(poll_id="投票 ID")
    async def delete(self, interaction: discord.Interaction, poll_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        polls = guild_polls.get(interaction.guild.id, {})
        if poll_id not in polls:
            await interaction.response.send_message(f"❌ 找不到 ID 為 `{poll_id}` 的投票。", ephemeral=True)
            return
        title = polls[poll_id].title
        del polls[poll_id]
        await interaction.response.send_message(f"🗑️ 投票「**{title}**」（`{poll_id}`）已刪除。")

    @app_commands.command(name="vote", description="投票（一般成員，依投票模式自動判斷）")
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
            )

    @app_commands.command(name="test", description="投票系統測試（管理員限定）")
    async def test(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        poll_id = "test-" + str(interaction.guild.id)
        polls = guild_polls.setdefault(interaction.guild.id, {})

        # 如果已有測試投票，先刪除
        if poll_id in polls:
            del polls[poll_id]

        # 建立測試投票（波達計數法，3 個選項）
        poll = Poll(
            poll_id=poll_id,
            title="投票系統測試",
            mode="borda",
        )
        poll.options = [
            PollOption(text="選項 A"),
            PollOption(text="選項 B"),
            PollOption(text="選項 C"),
        ]
        poll.status = "active"
        save_polls_to_disk()  # 直接啟動，方便立即測試
        polls[poll_id] = poll

        embed = discord.Embed(
            title="🧪 投票系統測試",
            description=(
                f"已建立測試投票並自動啟動。\n\n"
                f"**投票 ID：** `{poll_id}`\n"
                f"**模式：** 波達計數法\n"
                f"**選項：**\n"
                f"1. 選項 A\n"
                f"2. 選項 B\n"
                f"3. 選項 C\n\n"
                f"👉 請使用 `/poll vote {poll_id}` 開始投票測試\n"
                f"👉 管理員可使用 `/poll end {poll_id}` 結束並查看結果\n"
                f"👉 測試完畢可用 `/poll delete {poll_id}` 刪除"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="manage", description="開啟投票管理面板（管理員限定）")
    async def manage(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此面板僅限管理員使用。", ephemeral=True)
            return
        view = ManagePanelView(interaction.guild.id)
        embed = view._guild_overview_embed()
        await interaction.response.send_message(embed=embed, view=view)


# ──────────────────────────────────────────────
# 系統診斷指令群組
# ──────────────────────────────────────────────

