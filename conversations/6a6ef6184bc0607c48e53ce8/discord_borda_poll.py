"""
Borda Count 投票功能 — Discord Bot (discord.py)

使用方式：
  pip install discord.py

  將你的 bot token 設為環境變數 DISCORD_TOKEN，或直接寫在 main() 裡。

指令一覽（皆為 slash command，管理員限定 create/add/start/end）：
  /poll create <title>            建立新投票（進入「準備中」狀態，可加選項）
  /poll add <option>               新增選項到目前準備中的投票
  /poll list                       查看目前投票的選項清單
  /poll start                      啟動投票（開放使用者投票）
  /poll end                        結束投票並顯示波達計數法結果
  /poll rank                       （使用者）排序偏好並投票

波達計數法：n 個選項中，第 1 名得 n-1 分，第 2 名得 n-2 分，…，最後一名得 0 分。
每位投票者對所有選項排序，總分最高者為勝。
"""

import discord
from discord import app_commands
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import asyncio
import json
import os
import uuid


# ──────────────────────────────────────────────
# 資料結構
# ──────────────────────────────────────────────

@dataclass
class PollOption:
    text: str

@dataclass
class Poll:
    title: str
    options: List[PollOption] = field(default_factory=list)
    status: str = "drafting"  # "drafting" | "active" | "ended"
    votes: Dict[int, List[int]] = field(default_factory=dict)  # user_id -> 排序後的 option indices
    message_id: Optional[int] = None  # 啟動投票時發出的訊息 ID
    created_by: int = 0

    def option_count(self) -> int:
        return len(self.options)

    def add_option(self, text: str):
        self.options.append(PollOption(text=text))

    # 波達計數法計分
    def tally(self) -> Dict[str, int]:
        """回傳 {選項文字: 總分}，分數越高越好。"""
        n = self.option_count()
        scores: Dict[str, int] = {opt.text: 0 for opt in self.options}
        for ranking in self.votes.values():  # ranking = [option_idx 排序, 第0個是最高偏好]
            for rank_pos, opt_idx in enumerate(ranking):
                if 0 <= opt_idx < n:
                    borda_points = n - 1 - rank_pos  # 第1名得 n-1 分
                    scores[self.options[opt_idx].text] += borda_points
        return scores


# 全域狀態（簡單起見用記憶體；正式環境可換成 JSON / DB）
# 每個 guild 最多一個活躍投票
guild_polls: Dict[int, Poll] = {}


def is_admin(interaction: discord.Interaction) -> bool:
    """檢查使用者是否有管理員權限。"""
    if interaction.user.guild_permissions.administrator:
        return True
    # 也接受「管理伺服器」權限
    if interaction.user.guild_permissions.manage_guild:
        return True
    return False


# ──────────────────────────────────────────────
# Bot 定義
# ──────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    bot.tree.add_command(PollGroup())
    await bot.tree.sync()
    print(f"✅ Bot 上線：{bot.user}（已同步 slash commands）")


# ──────────────────────────────────────────────
# 投票下拉選單 View（使用者排序偏好）
# ──────────────────────────────────────────────

class RankVoteView(discord.ui.View):
    """讓使用者用下拉選單逐一排序所有選項的偏好順序。"""

    def __init__(self, poll: Poll):
        super().__init__(timeout=None)
        self.poll = poll
        self.user_ranking: Dict[int, List[int]] = {}  # user_id -> 排序清單

        options_for_select = [
            discord.SelectOption(
                label=f"{i+1}. {opt.text[:90]}",  # Discord label 上限 100 字
                value=str(i),
                description=f"選項 {i+1}",
            )
            for i, opt in enumerate(poll.options)
        ]

        # 第一個下拉選單：選擇第 1 偏好
        select = discord.ui.Select(
            placeholder="選擇你的第 1 偏好 👑",
            min_values=1,
            max_values=1,
            options=options_for_select,
            custom_id="borda_rank",
        )
        select.callback = self.on_rank_select
        self.add_item(select)
        self._remaining = list(range(len(poll.options)))  # 尚未選的 option indices
        self._current_rank: List[int] = []

    async def on_rank_select(self, interaction: discord.Interaction):
        """每次選一個選項，逐步建立完整排序。"""
        if self.poll.status != "active":
            await interaction.response.send_message(
                "❌ 投票尚未開放或已結束。", ephemeral=True
            )
            return

        selected_val = interaction.data["values"][0]
        opt_idx = int(selected_val)

        if opt_idx in self._current_rank:
            await interaction.response.send_message(
                "⚠️ 你已經排過這個選項了。", ephemeral=True
            )
            return

        self._current_rank.append(opt_idx)
        rank_num = len(self._current_rank)

        n = self.poll.option_count()

        # 已排完所有選項 → 提交投票
        if rank_num >= n:
            self.poll.votes[interaction.user.id] = list(self._current_rank)

            # 顯示確認
            ranking_text = "\n".join(
                f"{i+1}. {self.poll.options[idx].text}"
                for i, idx in enumerate(self._current_rank)
            )
            await interaction.response.edit_message(
                content=f"✅ **投票完成！** 你的排序：\n{ranking_text}\n\n謝謝投票，結果將在管理員結束投票後公布。",
                view=None,
            )
            return

        # 還有選項要排 → 更新下拉選單，移除已選的
        remaining_options = [
            discord.SelectOption(
                label=f"{i+1}. {self.poll.options[i].text[:90]}",
                value=str(i),
                description=f"選項 {i+1}",
            )
            for i in range(n)
            if i not in self._current_rank
        ]
        self.children[0].options = remaining_options
        self.children[0].placeholder = f"選擇你的第 {rank_num + 1} 偏好"

        # 顯示目前進度
        progress = "\n".join(
            f"{i+1}. {self.poll.options[idx].text}"
            for i, idx in enumerate(self._current_rank)
        )
        await interaction.response.edit_message(
            content=f"📊 **{self.poll.title}** — 排序你的偏好\n\n目前已排：\n{progress}\n\n請選擇第 **{rank_num + 1}** 偏好：",
            view=self,
        )


# ──────────────────────────────────────────────
# Slash Command Group
# ──────────────────────────────────────────────

class PollGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="poll", description="波達計數法投票系統")

    # ── create ───────────────────────────────
    @app_commands.command(name="create", description="建立新投票（管理員限定）")
    @app_commands.describe(title="投票標題")
    async def create(self, interaction: discord.Interaction, title: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        existing = guild_polls.get(guild_id)
        if existing and existing.status == "active":
            await interaction.response.send_message(
                "❌ 目前已有進行中的投票，請先結束再建立新的。", ephemeral=True
            )
            return

        poll = Poll(title=title, created_by=interaction.user.id)
        guild_polls[guild_id] = poll
        await interaction.response.send_message(
            f"📝 投票「**{title}**」已建立！\n"
            f"使用 `/poll add <option>` 新增選項，`/poll start` 啟動投票。"
        )

    # ── add ───────────────────────────────────
    @app_commands.command(name="add", description="新增選項到目前準備中的投票（管理員限定）")
    @app_commands.describe(option="選項內容")
    async def add(self, interaction: discord.Interaction, option: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        poll = guild_polls.get(interaction.guild.id)
        if not poll:
            await interaction.response.send_message("❌ 目前沒有投票，請先用 `/poll create` 建立。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message(
                "❌ 投票已啟動或已結束，無法新增選項。", ephemeral=True
            )
            return
        if len(poll.options) >= 25:
            await interaction.response.send_message(
                "❌ Discord 下拉選單上限 25 個選項。", ephemeral=True
            )
            return

        poll.add_option(option)
        count = poll.option_count()
        await interaction.response.send_message(
            f"✅ 已新增選項 **{option}**（目前共 {count} 個選項）\n"
            f"繼續用 `/poll add` 新增，或用 `/poll start` 啟動投票。"
        )

    # ── list ──────────────────────────────────
    @app_commands.command(name="list", description="查看目前投票的選項清單")
    async def list(self, interaction: discord.Interaction):
        poll = guild_polls.get(interaction.guild.id)
        if not poll:
            await interaction.response.send_message("❌ 目前沒有投票。", ephemeral=True)
            return
        if not poll.options:
            await interaction.response.send_message("📭 投票「{}」目前沒有選項。".format(poll.title), ephemeral=True)
            return

        status_emoji = {"drafting": "📝 準備中", "active": "🗳️ 進行中", "ended": "✅ 已結束"}
        lines = [f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options)]
        embed = discord.Embed(
            title=f"📊 {poll.title}",
            description=f"狀態：{status_emoji.get(poll.status, poll.status)}\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    # ── start ────────────────────────────────
    @app_commands.command(name="start", description="啟動投票，開放使用者投票（管理員限定）")
    async def start(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        poll = guild_polls.get(interaction.guild.id)
        if not poll:
            await interaction.response.send_message("❌ 目前沒有投票，請先用 `/poll create` 建立。", ephemeral=True)
            return
        if poll.status != "drafting":
            await interaction.response.send_message("❌ 投票已啟動或已結束。", ephemeral=True)
            return
        if poll.option_count() < 2:
            await interaction.response.send_message("❌ 至少需要 2 個選項才能啟動投票。", ephemeral=True)
            return

        poll.status = "active"

        # 發送投票訊息（含下拉選單）
        embed = discord.Embed(
            title=f"🗳️ 投票開始：{poll.title}",
            description=(
                f"共 {poll.option_count()} 個選項\n"
                f"點擊下方下拉選單，依偏好排序所有選項（第 1 名最偏好）。\n\n"
                "📋 **選項：**\n"
                + "\n".join(f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options))
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="波達計數法投票 · 排序所有選項即可投票")

        view = RankVoteView(poll)
        await interaction.response.send_message(embed=embed, view=view)
        # 記下訊息 ID 以供結束時參考
        msg = await interaction.original_response()
        poll.message_id = msg.id

    # ── end ──────────────────────────────────
    @app_commands.command(name="end", description="結束投票並顯示波達計數法結果（管理員限定）")
    async def end(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        poll = guild_polls.get(interaction.guild.id)
        if not poll:
            await interaction.response.send_message("❌ 目前沒有投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未啟動。", ephemeral=True)
            return

        poll.status = "ended"

        # 計算波達計數法結果
        scores = poll.tally()
        total_votes = len(poll.votes)

        if not scores or total_votes == 0:
            await interaction.response.send_message(
                f"📊 投票「{poll.title}」已結束，但沒有收到任何投票。"
            )
            return

        # 排序：分數高到低
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        n = poll.option_count()

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for rank_pos, (opt_text, score) in enumerate(ranked):
            medal = medals[rank_pos] if rank_pos < 3 else f"`{rank_pos+1}`"
            lines.append(f"{medal}  **{opt_text}** — {score} 分")

        embed = discord.Embed(
            title=f"📊 投票結果：{poll.title}",
            description=(
                f"🗳️ 共 {total_votes} 人投票 · {n} 個選項\n"
                f"計分方式：波達計數法（第 1 名得 {n-1} 分，最後一名得 0 分）\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="投票已結束")

        await interaction.response.send_message(embed=embed)

    # ── rank ──────────────────────────────────
    @app_commands.command(name="rank", description="排序偏好並投票（一般成員使用）")
    async def rank(self, interaction: discord.Interaction):
        poll = guild_polls.get(interaction.guild.id)
        if not poll:
            await interaction.response.send_message("❌ 目前沒有投票。", ephemeral=True)
            return
        if poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
            return
        if interaction.user.id in poll.votes:
            await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
            return

        view = RankVoteView(poll)
        await interaction.response.send_message(
            content=f"📊 **{poll.title}** — 排序你的偏好\n\n請選擇第 **1** 偏好：",
            view=view,
            ephemeral=True,
        )


# ──────────────────────────────────────────────
# 啟動
# ──────────────────────────────────────────────

def main():
    token = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
    if token == "YOUR_BOT_TOKEN_HERE":
        print("⚠️  請設定環境變數 DISCORD_TOKEN")
        return
    bot.run(token)


if __name__ == "__main__":
    main()
