# 秘書長曠工計時器（Secretary Absence Timer）
# /secretary manage — 管理面板（僅擁有者）
# 追蹤指定秘書長的離線時長，上線時在指定頻道發布曠工紀錄。
#
# ─── Shared globals injected by the main file ──────────────────────────────
# bot, tree, save_json, load_json, is_owner, now_str, OWNER_ID, TZ_TAIPEI,
# DATA_DIR, discord, app_commands, asyncio, github_push_json, _bot_ready_hooks

import json
import time
from datetime import datetime

# ═════════════════════════════════════════════════════════════════
# 錯誤攔截裝飾器
# ═════════════════════════════════════════════════════════════════

def _safe_callback(func):
    async def wrapper(*args, **kwargs):
        interaction = None
        for a in list(args) + list(kwargs.values()):
            if isinstance(a, discord.Interaction):
                interaction = a
                break
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            import traceback
            print(f"⚠️ 秘書長計時器發生未預期錯誤（{getattr(func, '__name__', '?')}）：{e}")
            traceback.print_exc()
            if interaction is not None:
                try:
                    err_msg = f"❌ 操作失敗，請重試一次或聯絡管理員。\n錯誤：`{e}`"
                    if interaction.response.is_done():
                        await interaction.followup.send(err_msg, ephemeral=True)
                    else:
                        await interaction.response.send_message(err_msg, ephemeral=True)
                except Exception:
                    pass
    return wrapper


# ═════════════════════════════════════════════════════════════════
# 設定 & 持久化
# ═════════════════════════════════════════════════════════════════

secretary_timer_settings = {
    "enabled": False,
    "log_channel_id": None,
    "secretary_id": None,
    "offline_since": None,  # epoch float (time.time())
}


def load_secretary_timer_settings():
    global secretary_timer_settings
    loaded = load_json("secretary_timer_settings.json", {})
    if loaded:
        for key in secretary_timer_settings:
            if key in loaded:
                secretary_timer_settings[key] = loaded[key]
    status = "啟用" if secretary_timer_settings.get("enabled") else "停用"
    sec_id = secretary_timer_settings.get("secretary_id")
    print(f"⏱️ 秘書長計時器設定已載入：{status}（秘書長 ID: {sec_id or '未指定'}）")


async def _persist_settings():
    path = DATA_DIR / "secretary_timer_settings.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(secretary_timer_settings, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    try:
        await github_push_json("secretary_timer_settings.json", secretary_timer_settings)
    except Exception as e:
        print(f"⚠️ 秘書長計時器 GitHub 同步失敗：{e}")


def _format_duration(seconds):
    """把秒數格式化為 'X天 Y時 Z分 W秒'。"""
    if seconds < 0:
        seconds = 0
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    parts = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 時")
    if mins:
        parts.append(f"{mins} 分")
    parts.append(f"{secs} 秒")
    return " ".join(parts)


def _get_secretary_member():
    """從伺服器取得秘書長 Member 物件。"""
    sec_id = secretary_timer_settings.get("secretary_id")
    if not sec_id:
        return None
    for guild in bot.guilds:
        m = guild.get_member(int(sec_id))
        if m:
            return m
    return None


def _build_status_embed():
    embed = discord.Embed(title="⏱️ 秘書長曠工計時器", color=discord.Color.blurple())
    embed.add_field(
        name="狀態",
        value="🟢 啟用" if secretary_timer_settings.get("enabled") else "🔴 停用",
        inline=False,
    )
    # 秘書長
    sec_id = secretary_timer_settings.get("secretary_id")
    if sec_id:
        member = _get_secretary_member()
        if member:
            sec_display = f"{member.display_name}\n{member.mention}\n`{member.id}`"
        else:
            sec_display = f"`{sec_id}`（找不到成員）"
    else:
        sec_display = "未指定"
    embed.add_field(name="追蹤對象", value=sec_display, inline=False)
    # Log 頻道
    log_id = secretary_timer_settings.get("log_channel_id")
    embed.add_field(
        name="紀錄頻道",
        value=f"<#{log_id}>" if log_id else "未設定",
        inline=False,
    )
    # 即時狀態
    if secretary_timer_settings.get("enabled") and sec_id:
        member = _get_secretary_member()
        if member:
            if member.status == discord.Status.offline:
                since = secretary_timer_settings.get("offline_since")
                if since:
                    duration = time.time() - since
                    embed.add_field(
                        name="目前狀態",
                        value=f"🔴 離線中（已離線 {_format_duration(duration)}）",
                        inline=False,
                    )
                else:
                    embed.add_field(name="目前狀態", value="🔴 離線中", inline=False)
            else:
                embed.add_field(name="目前狀態", value=f"🟢 上線中（{str(member.status)}）", inline=False)
        else:
            embed.add_field(name="目前狀態", value="⚠️ 找不到成員", inline=False)
    embed.set_footer(text="ICEA 秘書長曠工計時器")
    return embed


# ═════════════════════════════════════════════════════════════════
# 管理面板
# ═════════════════════════════════════════════════════════════════

class SecretaryUserSelectView(discord.ui.View):
    """選擇秘書長的 UserSelect。"""
    def __init__(self, parent_view):
        super().__init__(timeout=120)
        self.parent_view = parent_view

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="選擇要追蹤的秘書長...",
        min_values=1,
        max_values=1,
    )
    @_safe_callback
    async def user_select(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        user = select.values[0]
        secretary_timer_settings["secretary_id"] = str(user.id)
        # 初始化離線狀態
        member = None
        for guild in bot.guilds:
            m = guild.get_member(user.id)
            if m:
                member = m
                break
        if member and member.status == discord.Status.offline:
            secretary_timer_settings["offline_since"] = time.time()
        else:
            secretary_timer_settings["offline_since"] = None
        await _persist_settings()
        await interaction.response.edit_message(
            content=None,
            embed=_build_status_embed(),
            view=self.parent_view,
        )
        await interaction.followup.send(
            f"✅ 已設定追蹤對象：{user.mention}",
            ephemeral=True,
        )

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary)
    @_safe_callback
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=_build_status_embed(),
            view=self.parent_view,
        )


class SecretaryChannelSelectView(discord.ui.View):
    """選擇紀錄頻道的 ChannelSelect。"""
    def __init__(self, parent_view):
        super().__init__(timeout=120)
        self.parent_view = parent_view

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="選擇紀錄頻道...",
    )
    @_safe_callback
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        channel = select.values[0]
        secretary_timer_settings["log_channel_id"] = str(channel.id)
        await _persist_settings()
        await interaction.response.edit_message(
            content=None,
            embed=_build_status_embed(),
            view=self.parent_view,
        )
        await interaction.followup.send(
            f"✅ 紀錄頻道已設定至 <#{channel.id}>。",
            ephemeral=True,
        )

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary)
    @_safe_callback
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=_build_status_embed(),
            view=self.parent_view,
        )


class SecretaryManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="啟用/停用", style=discord.ButtonStyle.primary, emoji="🔄")
    @_safe_callback
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        secretary_timer_settings["enabled"] = not secretary_timer_settings.get("enabled", False)
        # 啟用時檢查目前狀態
        if secretary_timer_settings["enabled"]:
            member = _get_secretary_member()
            if member and member.status == discord.Status.offline:
                secretary_timer_settings["offline_since"] = time.time()
            else:
                secretary_timer_settings["offline_since"] = None
        await _persist_settings()
        status = "啟用" if secretary_timer_settings["enabled"] else "停用"
        await interaction.response.edit_message(embed=_build_status_embed(), view=self)
        await interaction.followup.send(f"✅ 秘書長計時器已{status}。", ephemeral=True)

    @discord.ui.button(label="紀錄頻道", style=discord.ButtonStyle.secondary, emoji="📋")
    @_safe_callback
    async def channel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        view = SecretaryChannelSelectView(self)
        await interaction.response.edit_message(
            content="請選擇紀錄頻道：",
            embed=None,
            view=view,
        )

    @discord.ui.button(label="指定秘書長", style=discord.ButtonStyle.secondary, emoji="👤")
    @_safe_callback
    async def secretary_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        view = SecretaryUserSelectView(self)
        await interaction.response.edit_message(
            content="請選擇要追蹤的秘書長：",
            embed=None,
            view=view,
        )


# ═════════════════════════════════════════════════════════════════
# /secretary 指令群組
# ═════════════════════════════════════════════════════════════════

class SecretaryGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="secretary", description="⏱️ 秘書長曠工計時器")

    @app_commands.command(name="manage", description="秘書長曠工計時器管理面板（僅擁有者）")
    async def manage(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return
        view = SecretaryManageView()
        embed = _build_status_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ═════════════════════════════════════════════════════════════════
# on_member_update 事件處理
# ═════════════════════════════════════════════════════════════════

async def handle_member_update(before: discord.Member, after: discord.Member):
    """追蹤秘書長上線/離線狀態變化。"""
    if not secretary_timer_settings.get("enabled"):
        return
    sec_id = secretary_timer_settings.get("secretary_id")
    if not sec_id or str(after.id) != sec_id:
        return

    before_offline = before.status == discord.Status.offline
    after_offline = after.status == discord.Status.offline

    # 狀態沒變就不處理
    if before_offline == after_offline:
        return

    if after_offline and not before_offline:
        # ── 秘書長離線 ──
        secretary_timer_settings["offline_since"] = time.time()
        await _persist_settings()
        print(f"⏱️ 秘書長 {after.display_name} ({after.id}) 已離線於 {now_str()}")

    elif not after_offline and before_offline:
        # ── 秘書長上線 ──
        since = secretary_timer_settings.get("offline_since")
        secretary_timer_settings["offline_since"] = None
        await _persist_settings()

        if not since:
            # 沒有離線紀錄（可能 bot 重啟前就離線了），只記上線事件
            print(f"⏱️ 秘書長 {after.display_name} 上線，但無離線紀錄可計時")
            return

        duration = time.time() - since

        # 發送曠工紀錄到 log 頻道
        log_id = secretary_timer_settings.get("log_channel_id")
        if not log_id:
            print("⚠️ 秘書長計時器未設定 log 頻道")
            return

        log_channel = None
        for guild in bot.guilds:
            ch = guild.get_channel(int(log_id))
            if ch:
                log_channel = ch
                break

        if not log_channel:
            print(f"⚠️ 秘書長計時器 log 頻道 {log_id} 找不到")
            return

        embed = discord.Embed(
            title="⏱️ 秘書長曠工紀錄",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="秘書長",
            value=f"{after.mention}\n`{after.display_name}` (`{after.id}`)",
            inline=False,
        )
        embed.add_field(
            name="離線時長",
            value=_format_duration(duration),
            inline=False,
        )
        embed.add_field(
            name="離線時間",
            value=now_str(),
            inline=True,
        )
        embed.add_field(
            name="上線時間",
            value=now_str(),
            inline=True,
        )
        embed.set_footer(text="ICEA 秘書長曠工計時器")

        try:
            await log_channel.send(embed=embed)
            print(f"⏱️ 秘書長 {after.display_name} 上線，離線 {_format_duration(duration)}，已發送曠工紀錄")
        except Exception as e:
            print(f"⚠️ 秘書長曠工紀錄發送失敗：{e}")


# ═════════════════════════════════════════════════════════════════
# 啟動時載入
# ═════════════════════════════════════════════════════════════════

load_secretary_timer_settings()

SecretaryGroup_instance = SecretaryGroup()
