#!/usr/bin/env python3
"""Patch modules/080_proposal_application.py:
1. Make ProposalReviewView / ApplicationFlagUploadView / ApplicationReviewView
   persistent (static custom_id + extract entry id from message embed) so
   buttons survive bot restarts.
2. Register these views globally via bot.add_view() at module load time.
3. Store notify/ack message+channel ids on entries so we can locate old panels.
4. Add handle_bot_ready() that scans pending proposals/applications on startup
   and resends fresh, working panels (disabling the old dead ones).
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
# 1. Proposal notify send — capture message id / channel id
# ─────────────────────────────────────────────────────────────────────────
old = '''    view = ProposalReviewView(proposal_id)
    try:
        await sec_ch.send(embed=embed, view=view)
        print(f"✅ 提案通知已發送至秘書處 #{sec_ch.name}")
    except Exception as e:
        print(f"❌ 提案通知發送失敗：{e}")'''
new = '''    view = ProposalReviewView(proposal_id)
    try:
        sent_msg = await sec_ch.send(embed=embed, view=view)
        entry["notify_message_id"] = str(sent_msg.id)
        entry["notify_channel_id"] = str(sec_ch.id)
        save_proposals()
        print(f"✅ 提案通知已發送至秘書處 #{sec_ch.name}")
    except Exception as e:
        print(f"❌ 提案通知發送失敗：{e}")'''
do_replace(old, new, "proposal notify send captures message id")


# ─────────────────────────────────────────────────────────────────────────
# 2. ProposalReviewView — persistent custom_id + extract id from embed
# ─────────────────────────────────────────────────────────────────────────
old = '''class ProposalReviewView(discord.ui.View):
    """受理/駁回 buttons attached to proposal notifications."""

    def __init__(self, proposal_id: str):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id

    @discord.ui.button(label="受理", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        _role_id = proposal_settings.get("review_role_id")
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        await _handle_proposal_decision(interaction, self.proposal_id, "accepted", "")

    @discord.ui.button(label="駁回", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        _role_id = proposal_settings.get("review_role_id")
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        modal = ProposalRejectModal(self.proposal_id)
        await interaction.response.send_modal(modal)'''
new = '''class ProposalReviewView(discord.ui.View):
    """受理/駁回 buttons attached to proposal notifications.

    Persistent view: buttons use static custom_id so they survive bot
    restarts (registered once via bot.add_view() at module load time).
    The proposal_id is looked up from the message's embed field ("提案 ID"),
    NOT from self.proposal_id — that only works for freshly-created
    instances in the same process. This makes ALL such panels forever
    clickable regardless of when/which process sent them.
    """

    def __init__(self, proposal_id: str = None):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id

    @staticmethod
    def _extract_proposal_id(interaction: discord.Interaction):
        try:
            embeds = interaction.message.embeds
            if embeds:
                for field in embeds[0].fields:
                    if field.name == "提案 ID":
                        return field.value.strip()
        except Exception:
            pass
        return None

    @discord.ui.button(label="受理", style=discord.ButtonStyle.success, emoji="✅", custom_id="icea_proposal_accept")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        proposal_id = self._extract_proposal_id(interaction) or self.proposal_id
        _role_id = proposal_settings.get("review_role_id")
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        if not proposal_id:
            await interaction.response.send_message("❌ 無法辨識此提案 ID，請聯絡管理員。", ephemeral=True)
            return
        await _handle_proposal_decision(interaction, proposal_id, "accepted", "")

    @discord.ui.button(label="駁回", style=discord.ButtonStyle.danger, emoji="❌", custom_id="icea_proposal_reject")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        proposal_id = self._extract_proposal_id(interaction) or self.proposal_id
        _role_id = proposal_settings.get("review_role_id")
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        if not proposal_id:
            await interaction.response.send_message("❌ 無法辨識此提案 ID，請聯絡管理員。", ephemeral=True)
            return
        modal = ProposalRejectModal(proposal_id)
        await interaction.response.send_modal(modal)


# 註冊為持久化 view（一次即可，讓所有提案通知面板的按鈕永遠可用）
bot.add_view(ProposalReviewView())'''
do_replace(old, new, "ProposalReviewView persistent")


# ─────────────────────────────────────────────────────────────────────────
# 3. Application ack embed (missing fields) — add footer with app_id,
#    capture ack_channel_id alongside ack_message_id
# ─────────────────────────────────────────────────────────────────────────
old = '''        ack_embed = discord.Embed(
            title="⚠️ 入盟申請待補齊",
            description=ack_desc,
            color=discord.Color.orange(),
        )
        if image_url:
            ack_embed.set_thumbnail(url=image_url)

        view = ApplicationFlagUploadView(entry["id"]) if "國旗" in str(missing_fields) else None
        try:
            if existing_entry and existing_entry.get("ack_message_id"):
                # 編輯已有的 ack 訊息
                try:
                    orig_ack_ch = message.channel if isinstance(message.channel, discord.Thread) else channel
                    ack_msg = await orig_ack_ch.fetch_message(int(existing_entry["ack_message_id"]))
                    await ack_msg.edit(embed=ack_embed, view=view)
                except Exception:
                    await message.reply(embed=ack_embed, view=view, mention_author=False)
            else:
                sent = await message.reply(embed=ack_embed, view=view, mention_author=False)
                entry["ack_message_id"] = str(sent.id) if hasattr(sent, 'id') else ""
                save_applications()
        except Exception as e:
            print(f"⚠️ 入盟申請 ack 訊息發送失敗：{e}")
        return'''
new = '''        ack_embed = discord.Embed(
            title="⚠️ 入盟申請待補齊",
            description=ack_desc,
            color=discord.Color.orange(),
        )
        if image_url:
            ack_embed.set_thumbnail(url=image_url)
        ack_embed.set_footer(text=f"申請 ID：{entry['id']}")

        view = ApplicationFlagUploadView(entry["id"]) if "國旗" in str(missing_fields) else None
        orig_ack_ch = message.channel if isinstance(message.channel, discord.Thread) else channel
        try:
            if existing_entry and existing_entry.get("ack_message_id"):
                # 編輯已有的 ack 訊息
                try:
                    ack_msg = await orig_ack_ch.fetch_message(int(existing_entry["ack_message_id"]))
                    await ack_msg.edit(embed=ack_embed, view=view)
                except Exception:
                    sent = await message.reply(embed=ack_embed, view=view, mention_author=False)
                    entry["ack_message_id"] = str(sent.id) if hasattr(sent, 'id') else ""
                    entry["ack_channel_id"] = str(orig_ack_ch.id)
                    save_applications()
            else:
                sent = await message.reply(embed=ack_embed, view=view, mention_author=False)
                entry["ack_message_id"] = str(sent.id) if hasattr(sent, 'id') else ""
                entry["ack_channel_id"] = str(orig_ack_ch.id)
                save_applications()
        except Exception as e:
            print(f"⚠️ 入盟申請 ack 訊息發送失敗：{e}")
        return'''
do_replace(old, new, "ack embed footer + ack_channel_id capture")


# ─────────────────────────────────────────────────────────────────────────
# 4. Application notify send (Phase 2, from _process_new_application)
#    — capture notify_message_id / notify_channel_id
# ─────────────────────────────────────────────────────────────────────────
old = '''    try:
        await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(entry["id"]))
        print(f"✅ 入盟申請通知已發送至{reviewer_label} #{notify_ch.name}")
    except Exception as e:
        print(f"❌ 入盟申請通知發送失敗：{e}")'''
new = '''    try:
        sent_notify = await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(entry["id"]))
        entry["notify_message_id"] = str(sent_notify.id)
        entry["notify_channel_id"] = str(notify_ch.id)
        save_applications()
        print(f"✅ 入盟申請通知已發送至{reviewer_label} #{notify_ch.name}")
    except Exception as e:
        print(f"❌ 入盟申請通知發送失敗：{e}")'''
do_replace(old, new, "application notify send (phase 2) captures message id")


# ─────────────────────────────────────────────────────────────────────────
# 5. Application notify send (from _handle_flag_upload, after flag completes)
#    — capture notify_message_id / notify_channel_id
# ─────────────────────────────────────────────────────────────────────────
old = '''        try:
            await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(entry["id"]))
            reviewer_label = "理事國" if entry.get("system_type") == "council" else "秘書處"
            print(f"✅ 入盟申請通知已發送至{reviewer_label} #{notify_ch.name}")
        except Exception as e:
            print(f"❌ 通知發送失敗：{e}")'''
new = '''        try:
            sent_notify = await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(entry["id"]))
            entry["notify_message_id"] = str(sent_notify.id)
            entry["notify_channel_id"] = str(notify_ch.id)
            save_applications()
            reviewer_label = "理事國" if entry.get("system_type") == "council" else "秘書處"
            print(f"✅ 入盟申請通知已發送至{reviewer_label} #{notify_ch.name}")
        except Exception as e:
            print(f"❌ 通知發送失敗：{e}")'''
do_replace(old, new, "application notify send (flag upload path) captures message id")


# ─────────────────────────────────────────────────────────────────────────
# 6. ApplicationFlagUploadView — persistent custom_id + extract app_id
# ─────────────────────────────────────────────────────────────────────────
old = '''class ApplicationFlagUploadView(discord.ui.View):
    """View with a '補上國旗' button attached to the orange ⚠️ embed."""

    def __init__(self, app_id: str):
        super().__init__(timeout=None)
        self.app_id = app_id

    @discord.ui.button(label="補上國旗圖片", style=discord.ButtonStyle.primary, emoji="🚩")
    async def upload_flag_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = None
        for a in _applications.get("entries", []):
            if a.get("id") == self.app_id:
                entry = a
                break
        if not entry:
            await interaction.response.send_message("❌ 找不到此申請記錄。", ephemeral=True)
            return
        if entry.get("status") in ("accepted", "rejected"):
            await interaction.response.send_message("⚠️ 此申請已審核完畢。", ephemeral=True)
            return
        if entry.get("secretariat_notified"):
            await interaction.response.send_message("✅ 此申請已通過初檢，國旗無需再補。", ephemeral=True)
            return

        _pending_flag_uploads[self.app_id] = {
            "user_id": str(interaction.user.id),
            "expires": _time.time() + 300,
            "channel_id": entry.get("channel_id"),
            "thread_id": entry.get("thread_id"),
        }
        reviewer_label = "理事國" if entry.get("system_type") == "council" else "秘書處"
        await interaction.response.send_message(
            "🚩 請在這個頻道/貼文中**傳送一張國旗圖片**（直接附加圖片發送即可）。\\n"
            f"系統會自動接收，通過後自動送交{reviewer_label}審核。\\n"
            "（5 分鐘內有效）",
            ephemeral=True,
        )'''
new = '''class ApplicationFlagUploadView(discord.ui.View):
    """View with a '補上國旗' button attached to the orange ⚠️ embed.

    Persistent view: static custom_id, app_id read from the embed footer
    ("申請 ID：xxx") so it keeps working after bot restarts, regardless of
    which process originally sent the panel.
    """

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

    @discord.ui.button(label="補上國旗圖片", style=discord.ButtonStyle.primary, emoji="🚩", custom_id="icea_flag_upload_btn")
    async def upload_flag_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        app_id = self._extract_app_id(interaction) or self.app_id
        if not app_id:
            await interaction.response.send_message("❌ 無法辨識此申請 ID，請聯絡管理員。", ephemeral=True)
            return
        entry = None
        for a in _applications.get("entries", []):
            if a.get("id") == app_id:
                entry = a
                break
        if not entry:
            await interaction.response.send_message("❌ 找不到此申請記錄。", ephemeral=True)
            return
        if entry.get("status") in ("accepted", "rejected"):
            await interaction.response.send_message("⚠️ 此申請已審核完畢。", ephemeral=True)
            return
        if entry.get("secretariat_notified"):
            await interaction.response.send_message("✅ 此申請已通過初檢，國旗無需再補。", ephemeral=True)
            return

        _pending_flag_uploads[app_id] = {
            "user_id": str(interaction.user.id),
            "expires": _time.time() + 300,
            "channel_id": entry.get("channel_id"),
            "thread_id": entry.get("thread_id"),
        }
        reviewer_label = "理事國" if entry.get("system_type") == "council" else "秘書處"
        await interaction.response.send_message(
            "🚩 請在這個頻道/貼文中**傳送一張國旗圖片**（直接附加圖片發送即可）。\\n"
            f"系統會自動接收，通過後自動送交{reviewer_label}審核。\\n"
            "（5 分鐘內有效）",
            ephemeral=True,
        )


# 註冊為持久化 view
bot.add_view(ApplicationFlagUploadView())'''
do_replace(old, new, "ApplicationFlagUploadView persistent")


# ─────────────────────────────────────────────────────────────────────────
# 7. ApplicationReviewView — persistent custom_id + extract app_id
# ─────────────────────────────────────────────────────────────────────────
old = '''class ApplicationReviewView(discord.ui.View):
    """審核通過/退回 buttons for application notifications."""

    def __init__(self, app_id: str):
        super().__init__(timeout=None)
        self.app_id = app_id

    @discord.ui.button(label="審核通過", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        _role_id = _get_application_review_role_id(self.app_id)
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        await _handle_application_decision(interaction, self.app_id, "accepted", "")

    @discord.ui.button(label="退回", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        _role_id = _get_application_review_role_id(self.app_id)
        if not _check_review_permission(interaction, _role_id):
            _msg = "❌ 此操作僅限指定身分組。" if _role_id else "❌ 此操作僅限管理員。"
            await interaction.response.send_message(_msg, ephemeral=True)
            return
        modal = ApplicationRejectModal(self.app_id)
        await interaction.response.send_modal(modal)'''
new = '''class ApplicationReviewView(discord.ui.View):
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
do_replace(old, new, "ApplicationReviewView persistent")

with open("modules/080_proposal_application.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Part 1 (persistent views) done.")
