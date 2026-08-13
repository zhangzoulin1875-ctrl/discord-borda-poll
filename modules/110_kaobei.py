# 靠北微國版（Kaobei Micronation）— 匿名投稿到指定論壇頻道
# 用戶透過 /post 指令投稿，bot 以匿名身分在論壇頻道建立貼文。
# 支援文字 + 圖片附件，投稿者身分完全不公開。
#
# ─── Shared globals injected by the main file ──────────────────────────────
# bot, tree, save_json, load_json, is_owner, now_str, OWNER_ID, TZ_TAIPEI,
# DATA_DIR, discord, app_commands, asyncio, github_push_json, _bot_ready_hooks

import io
import time
import json

# ═════════════════════════════════════════════════════════════════
# 設定 & 持久化
# ═════════════════════════════════════════════════════════════════

kaobei_settings = {
    "enabled": False,
    "forum_channel_id": None,
    "next_post_number": 1,
    "cooldown_seconds": 300,
}

_kaobei_cooldowns = {}


def load_kaobei_settings():
    global kaobei_settings
    loaded = load_json("kaobei_settings.json", {})
    if loaded:
        for key in kaobei_settings:
            if key in loaded:
                kaobei_settings[key] = loaded[key]
    print(f"📰 靠北微國版設定已載入：{'啟用' if kaobei_settings.get('enabled') else '停用'}")


def save_kaobei_settings():
    save_json("kaobei_settings.json", kaobei_settings)


async def _persist_kaobei_settings_now():
    path = DATA_DIR / "kaobei_settings.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kaobei_settings, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    try:
        await github_push_json("kaobei_settings.json", kaobei_settings)
    except Exception as e:
        print(f"⚠️ 靠北設定 GitHub 同步失敗：{e}")


# ═════════════════════════════════════════════════════════════════
# 頂層 /post 指令（匿名投稿）
# ═════════════════════════════════════════════════════════════════

@app_commands.command(name="post", description="📰 匿名投稿到靠北微國版")
@app_commands.describe(
    title="貼文標題（最多 100 字）",
    content="貼文內容（最多 4000 字）",
    image="附帶圖片（選填）",
)
async def post_command(
    interaction: discord.Interaction,
    title: str,
    content: str,
    image: discord.Attachment = None,
):
    if not kaobei_settings.get("enabled"):
        await interaction.response.send_message("❌ 靠北微國版目前未啟用。", ephemeral=True)
        return

    forum_id = kaobei_settings.get("forum_channel_id")
    if not forum_id:
        await interaction.response.send_message("❌ 尚未設定論壇頻道，請聯絡管理員。", ephemeral=True)
        return

    # ── 冷卻檢查 ──
    user_id = str(interaction.user.id)
    now = time.time()
    cooldown = kaobei_settings.get("cooldown_seconds", 300)
    last = _kaobei_cooldowns.get(user_id, 0)
    remaining = cooldown - (now - last)
    if remaining > 0:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        await interaction.response.send_message(
            f"⏳ 投稿冷卻中，請於 {mins} 分 {secs} 秒後再試。",
            ephemeral=True,
        )
        return

    # ── 驗證 ──
    title = title.strip()[:100]
    content = content.strip()[:4000]
    if not title or not content:
        await interaction.response.send_message("❌ 標題與內容皆為必填。", ephemeral=True)
        return

    if image and not (image.content_type or "").startswith("image/"):
        await interaction.response.send_message("❌ 附件必須為圖片格式。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # ── 尋找論壇頻道 ──
    forum_channel = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(forum_id))
        if ch and isinstance(ch, discord.ForumChannel):
            forum_channel = ch
            break

    if not forum_channel:
        await interaction.followup.send(
            "❌ 找不到論壇頻道，請聯絡管理員重新設定。",
            ephemeral=True,
        )
        return

    # ── 下載圖片（如有）──
    files = []
    if image:
        try:
            image_data = await image.read()
            files.append(
                discord.File(
                    io.BytesIO(image_data),
                    filename=image.filename or "image.png",
                    description="匿名投稿圖片",
                )
            )
        except Exception as e:
            print(f"⚠️ 靠北圖片下載失敗：{e}")

    # ── 建立論壇貼文 ──
    post_number = kaobei_settings.get("next_post_number", 1)
    thread_name = f"#{post_number} {title}"[:100]

    try:
        result = await forum_channel.create_thread(
            name=thread_name,
            content=content,
            files=files if files else None,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ 建立貼文失敗：機器人缺少「管理頻道」或「發送訊息」權限。",
            ephemeral=True,
        )
        return
    except Exception as e:
        print(f"⚠️ 靠北論壇貼文建立失敗：{e}")
        await interaction.followup.send(f"❌ 建立貼文失敗：{e}", ephemeral=True)
        return

    thread = result.thread if hasattr(result, "thread") else result

    kaobei_settings["next_post_number"] = post_number + 1
    await _persist_kaobei_settings_now()

    _kaobei_cooldowns[user_id] = now

    await interaction.followup.send(
        f"✅ 匿名投稿成功！\n貼文已建立：{thread.mention}",
        ephemeral=True,
    )
    print(f"📰 靠北微國版 #{post_number} 已由用戶 {interaction.user.id} 匿名發布")


# ═════════════════════════════════════════════════════════════════
# 管理指令群組 /kaobei
# ═════════════════════════════════════════════════════════════════

class KaobeiGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="kaobei", description="📰 靠北微國版管理")

    @app_commands.command(name="setup", description="設定靠北微國版論壇頻道（僅擁有者）")
    @app_commands.describe(channel="目標論壇頻道")
    async def setup(self, interaction: discord.Interaction, channel: discord.ForumChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return

        kaobei_settings["forum_channel_id"] = str(channel.id)
        kaobei_settings["enabled"] = True
        await _persist_kaobei_settings_now()
        await interaction.response.send_message(
            f"✅ 靠北微國版已設定至 {channel.mention} 並自動啟用。",
            ephemeral=True,
        )

    @app_commands.command(name="toggle", description="啟用/停用靠北微國版（僅擁有者）")
    async def toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return

        kaobei_settings["enabled"] = not kaobei_settings.get("enabled", False)
        await _persist_kaobei_settings_now()
        status = "啟用" if kaobei_settings["enabled"] else "停用"
        await interaction.response.send_message(
            f"✅ 靠北微國版已{status}。",
            ephemeral=True,
        )

    @app_commands.command(name="cooldown", description="設定投稿冷卻時間（僅擁有者）")
    @app_commands.describe(seconds="冷卻秒數（0 = 不限制，預設 300）")
    async def cooldown(self, interaction: discord.Interaction, seconds: int):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return

        seconds = max(0, min(86400, seconds))
        kaobei_settings["cooldown_seconds"] = seconds
        await _persist_kaobei_settings_now()
        if seconds == 0:
            await interaction.response.send_message("✅ 投稿冷卻已關閉。", ephemeral=True)
        else:
            mins = seconds // 60
            secs = seconds % 60
            await interaction.response.send_message(
                f"✅ 投稿冷卻已設為 {mins} 分 {secs} 秒。",
                ephemeral=True,
            )

    @app_commands.command(name="status", description="查看靠北微國版狀態")
    async def status(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📰 靠北微國版狀態", color=discord.Color.blurple())
        embed.add_field(
            name="狀態",
            value="🟢 啟用" if kaobei_settings.get("enabled") else "🔴 停用",
            inline=False,
        )
        forum_id = kaobei_settings.get("forum_channel_id")
        embed.add_field(
            name="論壇頻道",
            value=f"<#{forum_id}>" if forum_id else "未設定",
            inline=False,
        )
        embed.add_field(
            name="已發布貼文數",
            value=str(kaobei_settings.get("next_post_number", 1) - 1),
            inline=True,
        )
        cd = kaobei_settings.get("cooldown_seconds", 300)
        embed.add_field(
            name="冷卻時間",
            value="不限制" if cd == 0 else f"{cd} 秒",
            inline=True,
        )
        embed.set_footer(text="ICEA 靠北微國版")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ═════════════════════════════════════════════════════════════════
# 啟動時載入
# ═════════════════════════════════════════════════════════════════

load_kaobei_settings()

KaobeiGroup_instance = KaobeiGroup()
PostCommand_instance = post_command
