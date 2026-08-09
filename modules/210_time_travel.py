# ═══════════════════════════════════════════════════════════════════
# Module: 210_time_travel (時間穿越)
# 用戶輸入要穿越多久，bot 立即回覆「穿越中」，等時間到了再發完成訊息。
# 純趣味功能，不涉及經濟系統或其他模組。
# ═══════════════════════════════════════════════════════════════════

try:
    ICEA_GUILD_ID
except NameError:
    try:
        from discord_borda_poll import ICEA_GUILD_ID
    except ImportError:
        ICEA_GUILD_ID = "1425065927027720286"

import asyncio
import discord
from discord import app_commands

# ── 限制 ──
TIME_TRAVEL_MAX_SECONDS = 3600   # 最多穿越 1 小時
TIME_TRAVEL_MIN_SECONDS = 1       # 最少 1 秒

# ── 進行中的穿越記錄（重啟後清空，不持久化）──
_active_travels: dict[str, dict] = {}  # {user_id: {"channel_id": int, "end_time": float, "duration": int}}


def _format_duration(seconds: int) -> str:
    """把秒數格式化成人類可讀的時長。"""
    if seconds < 60:
        return f"{seconds} 秒"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins} 分鐘" + (f" {secs} 秒" if secs else "")
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours} 小時" + (f" {mins} 分鐘" if mins else "")


class TimeTravelGroup(app_commands.Group):
    """時間穿越指令群組。"""

    def __init__(self):
        super().__init__(name="timetravel", description="⏳ 時間穿越")

    @app_commands.command(name="go", description="開始時間穿越 — 輸入要穿越的時間長度")
    @app_commands.describe(
        minutes="要穿越幾分鐘（1-60）",
        seconds="額外要穿越幾秒（0-59，選填）",
    )
    async def go(self, interaction: discord.Interaction, minutes: int = 0, seconds: int = 0):
        uid = str(interaction.user.id)
        total_seconds = minutes * 60 + seconds

        if total_seconds < TIME_TRAVEL_MIN_SECONDS:
            await interaction.response.send_message(
                "❌ 穿越時間至少要 1 秒鐘。你穿越了 0 秒，等於沒穿越。",
                ephemeral=True,
            )
            return

        if total_seconds > TIME_TRAVEL_MAX_SECONDS:
            await interaction.response.send_message(
                f"❌ 穿越時間上限是 1 小時（{TIME_TRAVEL_MAX_SECONDS} 秒）。"
                f"你要求穿越 {_format_duration(total_seconds)}，太久了。",
                ephemeral=True,
            )
            return

        if uid in _active_travels:
            await interaction.response.send_message(
                "❌ 你已經在穿越中了！等目前的穿越結束後再試。",
                ephemeral=True,
            )
            return

        duration_str = _format_duration(total_seconds)
        user_name = interaction.user.display_name
        channel = interaction.channel

        # 立即回覆：穿越開始
        embed = discord.Embed(
            title="⏳ 時間穿越啟動",
            color=0x9b59b6,
            description=(
                f"🌙 **{user_name}** 正在穿越時空...\n\n"
                f"📍 穿越目標：**{duration_str}**\n"
                f"⏱️ 預計穿越耗時：**{total_seconds} 秒**\n"
                f"🔮 請稍後，穿越完成後將在此頻道公告。"
            ),
        )
        embed.set_footer(text="時間穿越 · 在等待的時光裡，未來正在成形")
        await interaction.response.send_message(embed=embed)

        # 記錄進行中的穿越
        import time as _time
        _active_travels[uid] = {
            "channel_id": channel.id if channel else None,
            "end_time": _time.time() + total_seconds,
            "duration": total_seconds,
            "username": user_name,
        }

        # 排程完成訊息
        asyncio.ensure_future(_travel_complete(uid, channel, total_seconds, user_name))

    @app_commands.command(name="status", description="查看自己目前的穿越狀態")
    async def status(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        if uid not in _active_travels:
            await interaction.response.send_message(
                "📭 你目前沒有在穿越中。",
                ephemeral=True,
            )
            return

        import time as _time
        travel = _active_travels[uid]
        remaining = max(0, int(travel["end_time"] - _time.time()))
        await interaction.response.send_message(
            f"⏳ 你正在穿越中！\n"
            f"總穿越時長：{_format_duration(travel['duration'])}\n"
            f"剩餘時間：{_format_duration(remaining)}",
            ephemeral=True,
        )

    @app_commands.command(name="cancel", description="取消目前的穿越")
    async def cancel(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        if uid not in _active_travels:
            await interaction.response.send_message(
                "📭 你目前沒有在穿越中，無法取消。",
                ephemeral=True,
            )
            return

        travel = _active_travels.pop(uid)
        await interaction.response.send_message(
            f"🛑 **{interaction.user.display_name}** 的穿越已被取消。\n"
            f"原定穿越時長：{_format_duration(travel['duration'])}\n"
            f"你從時空隧道中被拉了回來。",
        )


async def _travel_complete(uid: str, channel, duration: int, username: str):
    """等待指定秒數後，在原頻道發送穿越完成訊息。"""
    try:
        await asyncio.sleep(duration)

        # 確認穿越沒有被取消
        if uid not in _active_travels:
            return  # 被取消了

        _active_travels.pop(uid, None)

        if channel is None:
            return

        embed = discord.Embed(
            title="⏳ 時間穿越完成！",
            color=0x2ecc71,
            description=(
                f"🎉 **{username}** 成功穿越了 **{_format_duration(duration)}** 的時光！\n\n"
                f"你回到了現在。在這段時間裡，未來已經成為了過去。\n"
                f"歡迎回來，時空旅人。"
            ),
        )
        embed.set_footer(text="時間穿越 · 未來就是現在")

        await channel.send(embed=embed)

    except asyncio.CancelledError:
        _active_travels.pop(uid, None)
    except Exception as e:
        print(f"⚠️ 時間穿越完成訊息發送失敗：{e}")
        _active_travels.pop(uid, None)


# ── 註冊 ──
_travel_group = TimeTravelGroup()
bot.tree.add_command(_travel_group)

print("⏳ 時間穿越模組已載入")
