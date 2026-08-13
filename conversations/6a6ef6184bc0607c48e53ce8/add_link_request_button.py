#!/usr/bin/env python3
"""Add '要求傳送國家伺服器連結' admin-only button to the application review panel.

- New 3rd button on ApplicationReviewView (row=1, below accept/reject), admin-only.
- Survives accept/reject: after a decision, the panel keeps a standalone
  ApplicationLinkRequestView (same button) instead of losing all buttons.
- Clicking it sends a message to the ORIGINAL application post/thread with a
  '填寫連結' button. Applicant clicks that -> opens a Modal -> submits privately
  (ephemeral) -> link is forwarded ONLY to the secretariat/council channel,
  never posted publicly in the applicant's thread.
- Modal opened directly from a PUBLIC message's button, so on_submit uses
  interaction.response.send_message(ephemeral=True) — NEVER edit_message()
  (see project lesson: editing would edit the public origin message itself).
"""

with open("modules/080_proposal_application.py", "r", encoding="utf-8") as f:
    content = f.read()


def do_replace(old, new, label):
    global content
    assert old in content, f"MISSING BLOCK: {label}"
    assert content.count(old) == 1, f"NOT UNIQUE: {label} (found {content.count(old)} times)"
    content = content.replace(old, new)
    print(f"OK: {label}")


# ─────────────────────────────────────────────────────────────────────────
# 1. Add 3rd button to ApplicationReviewView + new views/modal + helpers,
#    replacing the whole class block through its bot.add_view() registration.
# ─────────────────────────────────────────────────────────────────────────
old = '''class ApplicationReviewView(discord.ui.View):
    """審核通過/退回 buttons for application notifications.

    Persistent view: static custom_id, app_id read from the embed field
    ("申請 ID") so it keeps working after bot restarts, regardless of which
    process originally sent the panel.
    """

    def __init__(self, app_id: str = None):
        super().__init__(timeout=None)
        self.app_id = app_id

    @staticmethod
    def _extract_app_id(interaction: discord.Interaction):
        try:
            embeds = interaction.message.embeds
            if embeds:
                for field in embeds[0].fields:
                    if field.name == "申請 ID":
                        return field.value.strip()
        except Exception:
            pass
        return None

    @discord.ui.button(label="審核通過", style=discord.ButtonStyle.success, emoji="✅", custom_id="icea_app_review_accept")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        if not app_id:
            await interaction.response.send_message("❌ 無法辨識此申請 ID，請聯絡管理員。", ephemeral=True)
            return
        _role_id = _get_application_review_role_id(app_id)
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        await _handle_application_decision(interaction, app_id, "accepted", "")

    @discord.ui.button(label="退回", style=discord.ButtonStyle.danger, emoji="❌", custom_id="icea_app_review_reject")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        if not app_id:
            await interaction.response.send_message("❌ 無法辨識此申請 ID，請聯絡管理員。", ephemeral=True)
            return
        _role_id = _get_application_review_role_id(app_id)
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        modal = ApplicationRejectModal(app_id)
        await interaction.response.send_modal(modal)


# 註冊為持久化 view
bot.add_view(ApplicationReviewView())'''

new = '''class ApplicationReviewView(discord.ui.View):
    """審核通過/退回 + 要求連結 buttons for application notifications.

    Persistent view: static custom_id, app_id read from the embed field
    ("申請 ID") so it keeps working after bot restarts, regardless of which
    process originally sent the panel.
    """

    def __init__(self, app_id: str = None):
        super().__init__(timeout=None)
        self.app_id = app_id

    @staticmethod
    def _extract_app_id(interaction: discord.Interaction):
        try:
            embeds = interaction.message.embeds
            if embeds:
                for field in embeds[0].fields:
                    if field.name == "申請 ID":
                        return field.value.strip()
        except Exception:
            pass
        return None

    @discord.ui.button(label="審核通過", style=discord.ButtonStyle.success, emoji="✅", custom_id="icea_app_review_accept", row=0)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        if not app_id:
            await interaction.response.send_message("❌ 無法辨識此申請 ID，請聯絡管理員。", ephemeral=True)
            return
        _role_id = _get_application_review_role_id(app_id)
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        await _handle_application_decision(interaction, app_id, "accepted", "")

    @discord.ui.button(label="退回", style=discord.ButtonStyle.danger, emoji="❌", custom_id="icea_app_review_reject", row=0)
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        if not app_id:
            await interaction.response.send_message("❌ 無法辨識此申請 ID，請聯絡管理員。", ephemeral=True)
            return
        _role_id = _get_application_review_role_id(app_id)
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        modal = ApplicationRejectModal(app_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="要求傳送國家伺服器連結", style=discord.ButtonStyle.secondary, emoji="🔗", custom_id="icea_app_request_link", row=1)
    async def request_link_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        await _handle_request_link_button(interaction, app_id)


# 註冊為持久化 view
bot.add_view(ApplicationReviewView())


# ─── 要求傳送國家伺服器連結（管理員專用，審核通過/退回後依然保留）───────────

def _find_application(app_id: str):
    for a in _applications.get("entries", []):
        if a.get("id") == app_id:
            return a
    return None


async def _resolve_application_origin_channel(entry: dict):
    """依申請記錄找到原申請頻道/貼文（thread 優先，其次一般頻道）。"""
    thread_id = entry.get("thread_id")
    channel_id = entry.get("channel_id")
    guild_id = entry.get("guild_id", 0)
    for guild in bot.guilds:
        if guild_id and guild.id != guild_id:
            continue
        if thread_id:
            target_thread = None
            try:
                target_thread = guild.get_thread(int(thread_id))
            except Exception:
                pass
            if not target_thread:
                try:
                    orig_ch = guild.get_channel(int(channel_id)) if channel_id else None
                    if orig_ch:
                        target_thread = await orig_ch.fetch_thread(int(thread_id))
                except Exception:
                    pass
            if target_thread:
                return target_thread
        if channel_id:
            ch = guild.get_channel(int(channel_id))
            if ch:
                return ch
    return None


async def _handle_request_link_button(interaction: discord.Interaction, app_id: str):
    """管理員按下「要求傳送國家伺服器連結」：發送一則附按鈕的訊息到原申請貼文，
    申請人點擊後填寫連結，內容私密送交秘書處/理事國，不公開顯示。"""
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 此操作僅限管理員使用。", ephemeral=True)
        return
    if not app_id:
        await interaction.response.send_message("❌ 無法辨識此申請 ID，請聯絡管理員。", ephemeral=True)
        return

    entry = _find_application(app_id)
    if not entry:
        await interaction.response.send_message("❌ 找不到此申請記錄（可能已被清除）。", ephemeral=True)
        return

    target_ch = await _resolve_application_origin_channel(entry)
    if not target_ch:
        await interaction.response.send_message("❌ 找不到原申請頻道/貼文，可能已被刪除。", ephemeral=True)
        return

    applicant_id = entry.get("applicant_id")
    mention = f"<@{applicant_id}>" if applicant_id else None
    link_embed = discord.Embed(
        title="🔗 請提供國家伺服器連結",
        description=(
            "秘書處需要您的國家 Discord 伺服器邀請連結以完成後續審核程序。\\n\\n"
            "請點擊下方按鈕填寫連結——**內容只會私密送交秘書處/理事國，不會公開顯示在此頻道／貼文中**。\\n"
            "（如果不方便公開邀請連結，這正是為此設計的私密提交方式）"
        ),
        color=discord.Color.blue(),
    )
    link_embed.set_footer(text=f"申請 ID：{app_id}")

    try:
        await target_ch.send(content=mention, embed=link_embed, view=ApplicationLinkFillView(app_id))
        await interaction.response.send_message(f"✅ 已發送連結索取訊息至 {target_ch.mention}。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 發送失敗：{e}", ephemeral=True)


class ApplicationLinkRequestView(discord.ui.View):
    """審核通過/退回後，取代 ApplicationReviewView 的獨立面板——
    只保留「要求傳送國家伺服器連結」按鈕，讓管理員審核後仍能索取連結。"""

    def __init__(self, app_id: str = None):
        super().__init__(timeout=None)
        self.app_id = app_id

    @staticmethod
    def _extract_app_id(interaction: discord.Interaction):
        try:
            embeds = interaction.message.embeds
            if embeds:
                for field in embeds[0].fields:
                    if field.name == "申請 ID":
                        return field.value.strip()
        except Exception:
            pass
        return None

    @discord.ui.button(label="要求傳送國家伺服器連結", style=discord.ButtonStyle.secondary, emoji="🔗", custom_id="icea_app_request_link_post_decision")
    async def request_link_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        await _handle_request_link_button(interaction, app_id)


bot.add_view(ApplicationLinkRequestView())


class ApplicationLinkFillModal(discord.ui.Modal, title="提供國家伺服器連結"):
    link_input = discord.ui.TextInput(
        label="Discord 伺服器邀請連結",
        style=discord.TextStyle.short,
        placeholder="例：https://discord.gg/xxxxxxx",
        required=True,
        max_length=200,
    )

    def __init__(self, app_id: str):
        super().__init__(timeout=300)
        self.app_id = app_id

    async def on_submit(self, interaction: discord.Interaction):
        # 注意：這個 Modal 是從「公開貼文的按鈕」直接開啟的（無中間 ephemeral 訊息），
        # on_submit 絕對不能用 edit_message()（會編輯到公開的原始訊息本體），
        # 只能用 send_message(ephemeral=True) 回應。
        link = self.link_input.value.strip()
        entry = _find_application(self.app_id)
        applicant_display = entry.get("applicant_name", "?") if entry else "?"

        notify_ch_id = None
        if entry:
            notify_ch_id = entry.get("notify_channel_id")
            if not notify_ch_id:
                if entry.get("system_type") == "council":
                    notify_ch_id = application_settings.get("council_channel")
                else:
                    notify_ch_id = application_settings.get("secretariat_channel")

        sent_ok = False
        if notify_ch_id:
            for guild in bot.guilds:
                ch = guild.get_channel(int(notify_ch_id))
                if ch:
                    try:
                        result_embed = discord.Embed(
                            title="🔗 已收到國家伺服器連結（私密提交）",
                            color=discord.Color.green(),
                            timestamp=discord.utils.utcnow(),
                        )
                        result_embed.add_field(name="申請人", value=applicant_display, inline=True)
                        result_embed.add_field(name="提交者", value=interaction.user.mention, inline=True)
                        result_embed.add_field(name="伺服器連結", value=link, inline=False)
                        result_embed.add_field(name="申請 ID", value=self.app_id, inline=False)
                        result_embed.set_footer(text="此訊息僅發送至本頻道，未公開於申請貼文")
                        await ch.send(embed=result_embed)
                        sent_ok = True
                    except Exception as e:
                        print(f"⚠️ 連結私密傳送失敗：{e}")
                    break

        if sent_ok:
            await interaction.response.send_message("✅ 已將連結私密送交秘書處/理事國，感謝配合！", ephemeral=True)
        else:
            print(f"⚠️ 找不到通知頻道，無法轉送連結（app_id={self.app_id}）：{link}")
            await interaction.response.send_message(
                "⚠️ 連結已收到，但系統找不到秘書處通知頻道，請直接私訊管理員提供連結。",
                ephemeral=True,
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"⚠️ 連結填寫 Modal 錯誤：{error}")
        try:
            await interaction.response.send_message("⚠️ 提交時發生錯誤，請再試一次。", ephemeral=True)
        except Exception:
            pass


class ApplicationLinkFillView(discord.ui.View):
    """發送到原申請貼文的公開按鈕：申請人點擊後彈出 Modal 私密填寫連結。"""

    def __init__(self, app_id: str = None):
        super().__init__(timeout=None)
        self.app_id = app_id

    @staticmethod
    def _extract_app_id(interaction: discord.Interaction):
        try:
            embeds = interaction.message.embeds
            if embeds and embeds[0].footer and embeds[0].footer.text:
                footer_text = embeds[0].footer.text
                if "申請 ID" in footer_text:
                    return footer_text.split("：", 1)[-1].split(":", 1)[-1].strip()
        except Exception:
            pass
        return None

    @discord.ui.button(label="填寫連結", style=discord.ButtonStyle.primary, emoji="🔗", custom_id="icea_app_link_fill_btn")
    async def fill_link_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        if not app_id:
            await interaction.response.send_message("❌ 無法辨識此申請 ID，請聯絡管理員。", ephemeral=True)
            return
        await interaction.response.send_modal(ApplicationLinkFillModal(app_id))


bot.add_view(ApplicationLinkFillView())'''

do_replace(old, new, "ApplicationReviewView + link request feature")


# ─────────────────────────────────────────────────────────────────────────
# 2. After a decision, keep the link-request button instead of view=None
# ─────────────────────────────────────────────────────────────────────────
old = '''        embed.set_footer(text=f"申請已{status_text}")
        try:
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception:
            pass'''
new = '''        embed.set_footer(text=f"申請已{status_text}")
        try:
            await interaction.response.edit_message(embed=embed, view=ApplicationLinkRequestView(app_id))
        except Exception:
            pass'''
do_replace(old, new, "keep link-request button after decision")

with open("modules/080_proposal_application.py", "w", encoding="utf-8") as f:
    f.write(content)

print("All patches applied.")
