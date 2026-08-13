# 靠北微國版（Kaobei Micronation）— 匿名投稿到指定論壇頻道
# /post new  — 匿名投稿（一般成員）
# /post manage — 管理面板（僅擁有者）
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


def _build_status_embed():
    embed = discord.Embed(title="📰 靠北微國版管理面板", color=discord.Color.blurple())
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
    return embed


# ═════════════════════════════════════════════════════════════════
# 管理面板 View
# ═════════════════════════════════════════════════════════════════

class _CooldownModal(discord.ui.Modal, title="⏱️ 設定投稿冷卻時間"):
    seconds_input = discord.ui.TextInput(
        label="冷卻秒數（0 = 不限制）",
        placeholder="例如 300 = 5 分鐘",
        default="300",
        max_length=6,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            seconds = max(0, min(86400, int(self.seconds_input.value)))
        except ValueError:
            await interaction.response.send_message("❌ 請輸入有效數字。", ephemeral=True)
            return
        kaobei_settings["cooldown_seconds"] = seconds
        await _persist_kaobei_settings_now()
        if seconds == 0:
            msg = "✅ 投稿冷卻已關閉。"
        else:
            msg = f"✅ 投稿冷卻已設為 {seconds // 60} 分 {seconds % 60} 秒。"
        await interaction.response.send_message(msg, ephemeral=True)


class KaobeiManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="啟用/停用", style=discord.ButtonStyle.primary, emoji="🔄")
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        kaobei_settings["enabled"] = not kaobei_settings.get("enabled", False)
        await _persist_kaobei_settings_now()
        status = "啟用" if kaobei_settings["enabled"] else "停用"
        await interaction.response.edit_message(embed=_build_status_embed(), view=self)
        await interaction.followup.send(f"✅ 靠北微國版已{status}。", ephemeral=True)

    @discord.ui.button(label="設定頻道", style=discord.ButtonStyle.secondary, emoji="📌")
    async def setup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        # 切換為頻道選擇器
        view = KaobeiChannelSelectView(self)
        await interaction.response.edit_message(
            content="請選擇論壇頻道：",
            embed=None,
            view=view,
        )

    @discord.ui.button(label="冷卻設定", style=discord.ButtonStyle.secondary, emoji="⏱️")
    async def cooldown_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        await interaction.response.send_modal(_CooldownModal())


class KaobeiChannelSelectView(discord.ui.View):
    def __init__(self, parent_view: KaobeiManageView):
        super().__init__(timeout=120)
        self.parent_view = parent_view

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.forum],
        placeholder="選擇論壇頻道...",
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者。", ephemeral=True)
            return
        channel = select.values[0]
        kaobei_settings["forum_channel_id"] = str(channel.id)
        kaobei_settings["enabled"] = True
        await _persist_kaobei_settings_now()
        await interaction.response.edit_message(
            content=None,
            embed=_build_status_embed(),
            view=self.parent_view,
        )
        await interaction.followup.send(
            f"✅ 靠北微國版已設定至 <#{channel.id}> 並自動啟用。",
            ephemeral=True,
        )

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=_build_status_embed(),
            view=self.parent_view,
        )


# ═════════════════════════════════════════════════════════════════
# /post 指令群組
# ═════════════════════════════════════════════════════════════════

class PostGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="post", description="📰 靠北微國版 — 匿名投稿")

    # ── /post new ─ 匿名投稿 ─────────────────────────────────────

    @app_commands.command(name="new", description="匿名投稿到靠北微國版")
    @app_commands.describe(
        title="貼文標題（最多 100 字）",
        content="貼文內容（最多 4000 字）",
        image="附帶圖片（選填）",
    )
    async def new(
        self,
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

    # ── /post manage ─ 管理面板 ───────────────────────────────────

    @app_commands.command(name="manage", description="靠北微國版管理面板（僅擁有者）")
    async def manage(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return
        view = KaobeiManageView()
        embed = _build_status_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ═════════════════════════════════════════════════════════════════
# 啟動時載入
# ═════════════════════════════════════════════════════════════════

load_kaobei_settings()

PostGroup_instance = PostGroup()
