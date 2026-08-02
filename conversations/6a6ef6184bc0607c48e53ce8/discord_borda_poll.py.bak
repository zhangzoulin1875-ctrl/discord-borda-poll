"""
Borda Count 投票功能 — Discord Bot (discord.py)

部署到 Render 免費方案：
  - 內建 HTTP keep-alive server（port 預設 10000，Render 會自動指派 PORT）
  - 搭配 UptimeRobot 每 10 分鐘 ping 一次，防止 free tier spin-down

環境變數：
  DISCORD_BOT_TOKEN  - Discord bot token（必須）
  PORT               - HTTP server port（Render 自動注入，預設 10000）

指令一覽：
  /poll create <title>            建立新投票（管理員限定）
  /poll add <option>              新增選項到目前準備中的投票
  /poll list                      查看選項清單
  /poll start                     啟動投票
  /poll end                       結束投票並顯示波達計數法結果
  /poll rank                      排序偏好並投票（一般成員）

波達計數法：n 個選項中，第 1 名得 n-1 分，…，最後一名得 0 分。
"""

import discord
from discord import app_commands
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import asyncio
import os
from aiohttp import web


# ──────────────────────────────────────────────
# Keep-Alive HTTP Server
# ──────────────────────────────────────────────

async def keep_alive_server():
    """啟動一個輕量 HTTP server，讓 Render 認為服務活著。"""
    port = int(os.getenv("PORT", 10000))

    async def health(request):
        return web.Response(text="Bot is running ✅", status=200)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Keep-alive HTTP server started on port {port}")


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
    votes: Dict[int, List[int]] = field(default_factory=dict)
    message_id: Optional[int] = None
    created_by: int = 0

    def option_count(self) -> int:
        return len(self.options)

    def add_option(self, text: str):
        self.options.append(PollOption(text=text))

    def tally(self) -> Dict[str, int]:
        n = self.option_count()
        scores: Dict[str, int] = {opt.text: 0 for opt in self.options}
        for ranking in self.votes.values():
            for rank_pos, opt_idx in enumerate(ranking):
                if 0 <= opt_idx < n:
                    scores[self.options[opt_idx].text] += n - 1 - rank_pos
        return scores


guild_polls: Dict[int, Poll] = {}


def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return interaction.user.guild_permissions.manage_guild


# ──────────────────────────────────────────────
# Bot
# ──────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    bot.tree.add_command(PollGroup())
    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot 上線：{bot.user}（已同步 {len(synced)} 個 slash commands）")
    except Exception as e:
        print(f"❌ 同步指令失敗：{e}")


@bot.event
async def setup_hook():
    """在 bot 啟動前跑 keep-alive server。"""
    await keep_alive_server()


# ──────────────────────────────────────────────
# 投票下拉選單 View
# ──────────────────────────────────────────────

class RankVoteView(discord.ui.View):
    def __init__(self, poll: Poll):
        super().__init__(timeout=None)
        self.poll = poll
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
            custom_id="borda_rank",
        )
        select.callback = self.on_rank_select
        self.add_item(select)

    async def on_rank_select(self, interaction: discord.Interaction):
        if self.poll.status != "active":
            await interaction.response.send_message("❌ 投票尚未開放或已結束。", ephemeral=True)
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
            ranking_text = "\n".join(
                f"{i+1}. {self.poll.options[idx].text}"
                for i, idx in enumerate(self._current_rank)
            )
            await interaction.response.edit_message(
                content=f"✅ **投票完成！** 你的排序：\n{ranking_text}\n\n謝謝投票，結果將在管理員結束投票後公布。",
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
# Slash Command Group
# ──────────────────────────────────────────────

class PollGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="poll", description="波達計數法投票系統")

    @app_commands.command(name="create", description="建立新投票（管理員限定）")
    @app_commands.describe(title="投票標題")
    async def create(self, interaction: discord.Interaction, title: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        guild_id = interaction.guild.id
        existing = guild_polls.get(guild_id)
        if existing and existing.status == "active":
            await interaction.response.send_message("❌ 目前已有進行中的投票，請先結束再建立新的。", ephemeral=True)
            return
        poll = Poll(title=title, created_by=interaction.user.id)
        guild_polls[guild_id] = poll
        await interaction.response.send_message(
            f"📝 投票「**{title}**」已建立！\n"
            f"使用 `/poll add <option>` 新增選項，`/poll start` 啟動投票。"
        )

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
            await interaction.response.send_message("❌ 投票已啟動或已結束，無法新增選項。", ephemeral=True)
            return
        if len(poll.options) >= 25:
            await interaction.response.send_message("❌ Discord 下拉選單上限 25 個選項。", ephemeral=True)
            return
        poll.add_option(option)
        await interaction.response.send_message(
            f"✅ 已新增選項 **{option}**（目前共 {poll.option_count()} 個選項）\n"
            f"繼續用 `/poll add` 新增，或用 `/poll start` 啟動投票。"
        )

    @app_commands.command(name="list", description="查看目前投票的選項清單")
    async def list(self, interaction: discord.Interaction):
        poll = guild_polls.get(interaction.guild.id)
        if not poll:
            await interaction.response.send_message("❌ 目前沒有投票。", ephemeral=True)
            return
        if not poll.options:
            await interaction.response.send_message(f"📭 投票「{poll.title}」目前沒有選項。", ephemeral=True)
            return
        status_emoji = {"drafting": "📝 準備中", "active": "🗳️ 進行中", "ended": "✅ 已結束"}
        lines = [f"{i+1}. {opt.text}" for i, opt in enumerate(poll.options)]
        embed = discord.Embed(
            title=f"📊 {poll.title}",
            description=f"狀態：{status_emoji.get(poll.status, poll.status)}\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

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
        msg = await interaction.original_response()
        poll.message_id = msg.id

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
        scores = poll.tally()
        total_votes = len(poll.votes)
        if not scores or total_votes == 0:
            await interaction.response.send_message(f"📊 投票「{poll.title}」已結束，但沒有收到任何投票。")
            return
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
            view=view, ephemeral=True,
        )


# ──────────────────────────────────────────────
# Render 部署設定檔
# ──────────────────────────────────────────────

RENDER_YAML = """\
services:
  - type: web
    name: discord-borda-poll-bot
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: python discord_borda_poll.py
    envVars:
      - key: DISCORD_BOT_TOKEN
        sync: false
      - key: PYTHON_VERSION
        value: "3.11.0"
"""

# ──────────────────────────────────────────────
# 啟動
# ──────────────────────────────────────────────

def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("⚠️  請設定環境變數 DISCORD_BOT_TOKEN")
        return
    bot.run(token)


if __name__ == "__main__":
    main()
