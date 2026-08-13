# ═════════════════════════════════════════════════════════════════
# Module: 080_proposal_application
# 提案審查 + 入盟申請自動回覆（無 AI 版本）
# 完全不依賴 AI，確保穩定運行。
# ═════════════════════════════════════════════════════════════════

import os
import re as _re
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Union

import discord
from discord import app_commands

# ─── Shared globals injected by the main file ──────────────────────────────
# bot, tree, save_json, load_json, is_owner, now_str, OWNER_ID, TZ_TAIPEI, DATA_DIR
# discord, app_commands are also available

GMT8 = TZ_TAIPEI

ICEA_GUILD_ID = 1425065927027720286
BOT_OWNER_ID = OWNER_ID

def _save_json_file(path, data):
    save_json(os.path.basename(path), data)

# ─── is_admin: owner or Discord admin perms ─────────────────────────────────
def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.id == BOT_OWNER_ID:
        return True
    if hasattr(interaction.user, 'guild_permissions') and interaction.user.guild_permissions.administrator:
        return True
    return False

# ─── Data files ──────────────────────────────────────────────────────────────
PROPOSAL_SETTINGS_FILE = os.path.join(str(DATA_DIR), "proposal_settings.json")
PROPOSALS_FILE = os.path.join(str(DATA_DIR), "proposals.json")
APPLICATION_SETTINGS_FILE = os.path.join(str(DATA_DIR), "application_settings.json")
APPLICATIONS_FILE = os.path.join(str(DATA_DIR), "applications.json")

# ─── Proposal settings ──────────────────────────────────────────────────────
proposal_settings = {
    "enabled": False,
    "proposal_channels": [],       # list of channel IDs to monitor for proposals
    "secretariat_channel": None,   # channel ID where admin gets notifications
    "review_role_id": None,        # role ID required to review proposals (None = is_admin)
}

_proposals = {"entries": []}


def load_proposal_settings():
    global proposal_settings
    loaded = load_json("proposal_settings.json", {})
    if loaded:
        for key in proposal_settings:
            if key in loaded:
                proposal_settings[key] = loaded[key]
        print(f"📋 提案系統設定已載入：{'啟用' if proposal_settings.get('enabled') else '停用'}，監控 {len(proposal_settings.get('proposal_channels', []))} 個頻道")


def save_proposal_settings():
    _save_json_file(PROPOSAL_SETTINGS_FILE, proposal_settings)


def load_proposals():
    global _proposals
    loaded = load_json("proposals.json", {"entries": []})
    _proposals = loaded if isinstance(loaded, dict) else {"entries": loaded if isinstance(loaded, list) else []}
    print(f"📋 提案記錄已載入：{len(_proposals.get('entries', []))} 筆")


def save_proposals():
    _save_json_file(PROPOSALS_FILE, _proposals)


# ─── Application settings ────────────────────────────────────────────────────
application_settings = {
    "enabled": False,
    "application_channels": [],       # 秘書處入盟申請區 channels to monitor
    "secretariat_channel": None,     # 秘書處 notification target
    "council_channels": [],           # 理事國入盟申請區 channels to monitor (separate)
    "council_channel": None,          # 理事國 notification target
    "secretariat_review_role_id": None,
    "council_review_role_id": None,
    "nation_admin_whitelist": [],
}

_applications = {"entries": []}


def load_application_settings():
    global application_settings
    loaded = load_json("application_settings.json", {})
    if loaded:
        for key in application_settings:
            if key in loaded:
                application_settings[key] = loaded[key]


def save_application_settings():
    _save_json_file(APPLICATION_SETTINGS_FILE, application_settings)


def load_applications():
    global _applications
    loaded = load_json("applications.json", {"entries": []})
    _applications = loaded if isinstance(loaded, dict) else {"entries": loaded if isinstance(loaded, list) else []}
    print(f"✅ 載入入盟申請記錄：{len(_applications.get('entries', []))} 筆")


def save_applications():
    _save_json_file(APPLICATIONS_FILE, _applications)


# ═════════════════════════════════════════════════════════════════
# 提案分析（純關鍵字啟發式，無 AI）
# ═════════════════════════════════════════════════════════════════

def _heuristic_proposal_analysis(content: str, channel_name: str) -> dict:
    """關鍵字啟發式分析提案種類 + 摘要。"""
    text = content.lower()
    if "升格" in text:
        ptype = "升格案"
    elif "選舉" in text:
        ptype = "選舉案"
    elif "罷免" in text:
        ptype = "罷免案"
    elif "任命" in text or "提名" in text:
        ptype = "任命案"
    elif "預算" in text or "撥款" in text:
        ptype = "預算提案"
    elif "法律" in text or "法案" in text or "修正" in text:
        ptype = "法律提案"
    elif "政策" in text:
        ptype = "政策提案"
    else:
        ptype = "一般提案"
    summary = content[:50].replace("\n", " ").strip()
    if len(content) > 50:
        summary += "..."
    return {"type": ptype, "summary": summary}


# ═════════════════════════════════════════════════════════════════
# 提案處理流程
# ═════════════════════════════════════════════════════════════════

async def _process_new_proposal(message: discord.Message, channel):
    """偵測新提案 → 分析 → 通知秘書處。"""
    if not proposal_settings.get("enabled"):
        return
    proposal_channels = proposal_settings.get("proposal_channels", [])
    if channel.id not in proposal_channels:
        return

    msg_id = str(message.id)
    existing = [p for p in _proposals.get("entries", []) if p.get("message_id") == msg_id]
    if existing:
        return

    print(f"📋 偵測到新提案：#{channel.name} by {message.author.display_name}")

    analysis = _heuristic_proposal_analysis(message.content, channel.name)

    now = _time.time()
    proposal_id = str(int(now * 1000))
    entry = {
        "id": proposal_id,
        "date": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M"),
        "_ts": now,
        "guild_id": message.guild.id if message.guild else 0,
        "proposer_id": str(message.author.id),
        "proposer_name": message.author.display_name,
        "channel_id": channel.id,
        "channel_name": channel.name,
        "thread_id": (
            str(message.channel.id) if isinstance(message.channel, discord.Thread) and message.channel.id != channel.id
            else (str(message.id) if hasattr(message, 'thread') and message.thread else None)
        ),
        "message_id": msg_id,
        "message_url": str(message.jump_url) if hasattr(message, 'jump_url') else "",
        "raw_content": message.content[:2000],
        "proposal_type": analysis["type"],
        "summary": analysis["summary"],
        "status": "pending",
        "reviewed_by": "",
        "review_date": "",
        "reject_reason": "",
    }
    _proposals.setdefault("entries", []).append(entry)
    if len(_proposals["entries"]) > 500:
        _proposals["entries"] = _proposals["entries"][-500:]
    save_proposals()

    # 立即在原提案處回覆確認訊息
    try:
        ack_embed = discord.Embed(
            description=(
                f"✅ 已收到提案，判定為「**{analysis['type']}**」\n"
                f"摘要：{analysis['summary']}\n\n"
                f"提案已送交秘書處審核，請耐心等候。"
            ),
            color=discord.Color.blue(),
        )
        await message.reply(embed=ack_embed, mention_author=False)
    except Exception as e:
        print(f"⚠️ 提案確認訊息發送失敗（不影響審核流程）：{e}")

    # 發送通知到秘書處頻道
    sec_ch_id = proposal_settings.get("secretariat_channel")
    if not sec_ch_id:
        print("⚠️ 提案系統：未設定秘書處頻道，無法發送通知")
        return

    sec_ch = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(sec_ch_id))
        if ch:
            sec_ch = ch
            break

    if not sec_ch:
        print(f"⚠️ 提案系統：找不到秘書處頻道 {sec_ch_id}")
        return

    embed = discord.Embed(
        title=f"📋 新提案通知：{analysis['type']}",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="提案人", value=message.author.display_name, inline=True)
    embed.add_field(name="提案頻道", value=f"#{channel.name}", inline=True)
    embed.add_field(name="提案時間", value=entry["date"], inline=True)
    embed.add_field(name="摘要", value=analysis["summary"][:1024], inline=False)
    embed.add_field(
        name="原文連結",
        value=message.jump_url if hasattr(message, 'jump_url') else "(無)",
        inline=False,
    )
    embed.add_field(name="提案 ID", value=proposal_id, inline=False)
    embed.set_footer(text="請管理員點擊下方按鈕受理或駁回此提案")

    view = ProposalReviewView(proposal_id)
    try:
        sent_msg = await sec_ch.send(embed=embed, view=view)
        entry["notify_message_id"] = str(sent_msg.id)
        entry["notify_channel_id"] = str(sec_ch.id)
        save_proposals()
        print(f"✅ 提案通知已發送至秘書處 #{sec_ch.name}")
    except Exception as e:
        print(f"❌ 提案通知發送失敗：{e}")


# ─── 提案審核 UI ────────────────────────────────────────────────────────────

class ProposalRejectModal(discord.ui.Modal, title="駁回提案原因"):
    reason_input = discord.ui.TextInput(
        label="請說明駁回原因",
        style=discord.TextStyle.paragraph,
        placeholder="例：提案格式不符/內容不完整/不符合規定...",
        required=True,
        max_length=300,
    )

    def __init__(self, proposal_id: str):
        super().__init__(timeout=300)
        self.proposal_id = proposal_id

    async def on_submit(self, interaction: discord.Interaction):
        await _handle_proposal_decision(interaction, self.proposal_id, "rejected", self.reason_input.value.strip())

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"⚠️ 駁回 Modal 錯誤：{error}")
        try:
            await interaction.response.send_message("⚠️ 提交駁回原因時發生錯誤。", ephemeral=True)
        except Exception:
            pass


class ProposalReviewView(discord.ui.View):
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
bot.add_view(ProposalReviewView())


async def _handle_proposal_decision(interaction: discord.Interaction, proposal_id: str,
                                      decision: str, reject_reason: str):
    """Process accept/reject and notify the original proposer."""
    entry = None
    for p in _proposals.get("entries", []):
        if p.get("id") == proposal_id:
            entry = p
            break

    if not entry:
        try:
            await interaction.response.send_message("❌ 找不到此提案記錄（可能已被清除）。", ephemeral=True)
        except Exception:
            pass
        return

    if entry["status"] != "pending":
        try:
            await interaction.response.send_message(f"⚠️ 此提案已被{'受理' if entry['status']=='accepted' else '駁回'}過了。", ephemeral=True)
        except Exception:
            pass
        return

    entry["status"] = decision
    entry["reviewed_by"] = interaction.user.display_name
    entry["review_date"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M")
    entry["reject_reason"] = reject_reason
    save_proposals()

    status_emoji = "✅" if decision == "accepted" else "❌"
    status_text = "已受理" if decision == "accepted" else "已駁回"
    embed = interaction.message.embeds[0] if interaction.message.embeds else None
    if embed:
        embed.color = discord.Color.green() if decision == "accepted" else discord.Color.red()
        embed.add_field(
            name=f"{status_emoji} 審核結果",
            value=f"{status_text} by {interaction.user.display_name} ({entry['review_date']})"
                  + (f"\n原因：{reject_reason}" if reject_reason else ""),
            inline=False,
        )
        embed.set_footer(text=f"提案已{status_text}")
        try:
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception:
            pass
    else:
        try:
            await interaction.response.send_message(f"{status_emoji} 提案已{status_text}。", ephemeral=True)
        except Exception:
            pass

    # 通知原提案人
    orig_ch_id = entry.get("channel_id")
    guild_id = entry.get("guild_id", 0)
    thread_id = entry.get("thread_id")
    orig_ch = None
    target_thread = None
    for guild in bot.guilds:
        if guild.id == guild_id:
            orig_ch = guild.get_channel(int(orig_ch_id)) if orig_ch_id else None
            if thread_id:
                try:
                    target_thread = guild.get_thread(int(thread_id))
                except Exception:
                    pass
                if not target_thread and orig_ch:
                    try:
                        target_thread = await orig_ch.fetch_thread(int(thread_id))
                    except Exception:
                        pass
            break

    if not orig_ch and not target_thread:
        print(f"⚠️ 找不到原始提案頻道 {orig_ch_id}，無法通知提案人")
        return

    proposer_mention = f"<@{entry.get('proposer_id')}>"
    if decision == "accepted":
        notify_embed = discord.Embed(
            title="✅ 提案已受理",
            description=(
                f"{proposer_mention} 你的提案已被管理員受理！\n\n"
                f"**提案種類：** {entry.get('proposal_type', '?')}\n"
                f"**摘要：** {entry.get('summary', '')}\n"
                f"**審核人：** {interaction.user.display_name}\n"
                f"**審核時間：** {entry['review_date']}"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
    else:
        notify_embed = discord.Embed(
            title="❌ 提案已駁回",
            description=(
                f"{proposer_mention} 你的提案已被駁回。\n\n"
                f"**提案種類：** {entry.get('proposal_type', '?')}\n"
                f"**摘要：** {entry.get('summary', '')}\n"
                f"**駁回原因：** {reject_reason or '未提供'}\n"
                f"**審核人：** {interaction.user.display_name}\n"
                f"**審核時間：** {entry['review_date']}"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )

    try:
        if target_thread:
            await target_thread.send(embed=notify_embed)
            print(f"✅ 提案結果已發送至論壇貼文 #{target_thread.name}")
            return
        msg_id = entry.get("message_id")
        if msg_id and hasattr(orig_ch, 'fetch_message'):
            try:
                orig_msg = await orig_ch.fetch_message(int(msg_id))
                await orig_msg.reply(embed=notify_embed, mention_author=True)
                print(f"✅ 提案結果已回覆至 #{orig_ch.name}")
                return
            except Exception as e:
                print(f"⚠️ fetch_message 失敗 ({e})，改用頻道發送")
        if hasattr(orig_ch, 'send'):
            await orig_ch.send(embed=notify_embed)
            print(f"✅ 提案結果已發送至 #{orig_ch.name}")
    except Exception as e:
        print(f"❌ 通知提案人失敗：{e}")


# ═════════════════════════════════════════════════════════════════
# 入盟申請 — 欄位檢查（純文字啟發式，無 AI）
# ═════════════════════════════════════════════════════════════════

_APPLICATION_SIMPLE_FIELDS = [
    ("申請國家名稱", "Name of Applicant"),
    ("國家成立日期", "Date of Establishment"),
    ("聯絡代表姓名", "Name of Representative"),
    ("聯絡方式", "Contact Information"),
    ("國家代碼", "National Code"),
    ("伺服器連結", "Server Link"),
]

_APPLICATION_ESSAY_FIELDS = [
    ("申請目的與願景", "Desired goals and vision"),
    ("國家簡介", "Country Profile"),
]


def _check_simple_fields(content: str) -> list:
    """Check which SIMPLE fields are missing or empty.
    Returns a list of missing/empty field Chinese labels."""
    missing = []
    for zh, en in _APPLICATION_SIMPLE_FIELDS:
        if zh not in content and en.lower() not in content.lower():
            missing.append(zh)
            continue
        found_content = False
        for line in content.split("\n"):
            if zh in line or en.lower() in line.lower():
                for sep in ["：", ":"]:
                    if sep in line:
                        after = line.split(sep, 1)[1].strip()
                        if after:
                            found_content = True
                        break
                break
        if not found_content:
            missing.append(zh)
    return missing


def _essay_fallback_check(content: str, zh: str, en: str) -> bool:
    """Heuristic check for essay fields: grab the text block between
    this label's line and the next label/blank divider, strip template
    noise, and see if meaningful text remains."""
    lines = content.split("\n")
    block = []
    capturing = False
    for line in lines:
        if zh in line or en.lower() in line.lower():
            capturing = True
            continue
        if capturing:
            if _re.match(r'^\s*[一二三四五六七八九十0-9]+[、.．]', line):
                break
            if any(z in line for z, _e in _APPLICATION_SIMPLE_FIELDS + _APPLICATION_ESSAY_FIELDS if z != zh):
                break
            block.append(line)
    block_text = "\n".join(block)
    block_text = _re.sub(r'[（(]\s*\d+\s*(字|words?)\s*[）)]', '', block_text, flags=_re.IGNORECASE)
    block_text = _re.sub(en, '', block_text, flags=_re.IGNORECASE)
    block_text = block_text.strip()
    return len(block_text) >= 5


# ─── 國旗驗證（無 AI：只要有圖片就接受）──────────────────────────────────────

def _verify_flag_image(image_url: str) -> bool:
    """無 AI 版本：只要有圖片就接受。
    舊版用視覺 AI 判斷是否為旗幟，現在直接放行。
    之後如果要恢復 AI 驗證，可以在這裡加回來。"""
    return True


# ═════════════════════════════════════════════════════════════════
# 入盟申請處理流程
# ═════════════════════════════════════════════════════════════════

async def _process_new_application(message: discord.Message, channel, is_edit: bool = False, system_type: str = "secretariat"):
    """Auto-reply to a membership application.

    Two-phase flow:
    1. First check — if fields are missing, reply with orange ⚠️ and tell the
       applicant to EDIT their original post. Do NOT notify reviewer yet.
    2. On edit re-check — when all fields pass (including flag image), reply
       with blue ✅ and THEN notify the reviewer (秘書處 or 理事國).
    """
    if not application_settings.get("enabled"):
        return
    if system_type == "council":
        monitored = application_settings.get("council_channels", [])
    else:
        monitored = application_settings.get("application_channels", [])
    if channel.id not in monitored:
        return

    msg_id = str(message.id)
    thread_id_str = str(message.channel.id) if isinstance(message.channel, discord.Thread) else None

    existing_entry = None
    for a in _applications.get("entries", []):
        if a.get("message_id") == msg_id:
            existing_entry = a
            break

    if existing_entry and existing_entry.get("secretariat_notified") and not is_edit:
        return
    if existing_entry and existing_entry.get("status") in ("accepted", "rejected"):
        return

    # 防止已審核完的 thread 裡的新訊息被重新檢查
    if not existing_entry and thread_id_str:
        for a in _applications.get("entries", []):
            if a.get("thread_id") == thread_id_str and a.get("status") in ("accepted", "rejected"):
                return

    print(f"📝 偵測到入盟申請{'（編輯）' if is_edit else ''}：#{getattr(channel, 'name', '?')} by {message.author.display_name}")

    # ── Sticky per-field pass tracking ──
    field_status = dict(existing_entry.get("field_status", {})) if existing_entry else {}

    # 1) Simple line-based fields
    simple_missing = set(_check_simple_fields(message.content))
    for zh, _en in _APPLICATION_SIMPLE_FIELDS:
        field_status[zh] = field_status.get(zh, False) or (zh not in simple_missing)

    # 2) Essay fields — 純文字啟發式判斷（無 AI）
    need_vision_check = not field_status.get("申請目的與願景", False)
    need_profile_check = not field_status.get("國家簡介", False)
    if need_vision_check:
        field_status["申請目的與願景"] = _essay_fallback_check(message.content, "申請目的與願景", "Desired goals and vision")
    if need_profile_check:
        field_status["國家簡介"] = _essay_fallback_check(message.content, "國家簡介", "Country Profile")

    # 3) Flag image — 有圖片就接受（無 AI 驗證）
    has_image = bool(message.attachments)
    image_url = str(message.attachments[0].url) if has_image else ""
    if not has_image:
        for emb in getattr(message, "embeds", []) or []:
            if getattr(emb, "type", None) == "image" and emb.image and emb.image.url:
                image_url = str(emb.image.url)
                has_image = True
                break

    already_flag_ok = field_status.get("國旗", False) or bool(existing_entry and existing_entry.get("flag_valid"))
    flag_reason = ""
    if already_flag_ok:
        flag_ok = True
        flag_image_url = (existing_entry.get("flag_image_url") if existing_entry else "") or image_url
    elif has_image:
        flag_ok = _verify_flag_image(image_url)  # 無 AI 版本：永遠 True
        flag_reason = "" if flag_ok else "invalid"
        flag_image_url = image_url if flag_ok else ""
    else:
        flag_ok = False
        flag_reason = "no_image"
        flag_image_url = ""
    field_status["國旗"] = flag_ok
    image_url = flag_image_url or image_url

    # ── Build missing_fields display list ──
    missing_fields = []
    for zh, _en in _APPLICATION_SIMPLE_FIELDS + _APPLICATION_ESSAY_FIELDS:
        if not field_status.get(zh, False):
            missing_fields.append(f"{zh}（空白）")
    if not field_status.get("國旗", False):
        if flag_reason == "no_image":
            missing_fields.append("國旗（缺少圖片）")
        else:
            missing_fields.append("國旗（判定非旗幟）")

    all_pass = len(missing_fields) == 0
    flag_valid = field_status.get("國旗", False)

    # Extract applicant nation name
    applicant_name = ""
    for line in message.content.split("\n"):
        if "申請國家名稱" in line or "Name of Applicant" in line:
            parts = line.split("：")
            if len(parts) > 1:
                applicant_name = parts[1].strip()[:50]
            break

    now = _time.time()

    if existing_entry:
        entry = existing_entry
        entry["raw_content"] = message.content[:2000]
        entry["missing_fields"] = missing_fields
        entry["field_status"] = field_status
        entry["flag_status"] = "ok" if flag_valid else (flag_reason or "missing")
        entry["flag_valid"] = flag_valid
        if flag_valid and flag_image_url:
            entry["flag_image_url"] = flag_image_url
        entry["last_checked"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M")
        entry["applicant_nation"] = applicant_name or entry.get("applicant_nation", "")
    else:
        app_id = str(int(now * 1000))
        entry = {
            "id": app_id,
            "date": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M"),
            "_ts": now,
            "guild_id": message.guild.id if message.guild else 0,
            "applicant_id": str(message.author.id),
            "applicant_name": message.author.display_name,
            "applicant_nation": applicant_name,
            "channel_id": channel.id,
            "channel_name": getattr(channel, 'name', ''),
            "thread_id": (
                str(message.channel.id) if isinstance(message.channel, discord.Thread) and message.channel.id != channel.id
                else (str(message.id) if hasattr(message, 'thread') and message.thread else None)
            ),
            "message_id": msg_id,
            "message_url": str(message.jump_url) if hasattr(message, 'jump_url') else "",
            "raw_content": message.content[:2000],
            "missing_fields": missing_fields,
            "field_status": field_status,
            "flag_status": "ok" if flag_valid else (flag_reason or "missing"),
            "flag_valid": flag_valid,
            "flag_image_url": flag_image_url if flag_valid else "",
            "system_type": system_type,
            "status": "pending",
            "secretariat_notified": False,
            "reviewed_by": "",
            "review_date": "",
            "reject_reason": "",
            "last_checked": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M"),
        }
        _applications.setdefault("entries", []).append(entry)
        if len(_applications["entries"]) > 500:
            _applications["entries"] = _applications["entries"][-500:]
    save_applications()

    # ── 判定 reviewer label ──
    if system_type == "council":
        notify_ch_id = application_settings.get("council_channel")
        notify_title = "📝 新入盟申請（理事國審核）"
        notify_footer = "請理事國點擊下方按鈕審核通過或退回此申請"
        notify_color = discord.Color.dark_gold()
        reviewer_label = "理事國"
    else:
        notify_ch_id = application_settings.get("secretariat_channel")
        notify_title = "📝 新入盟申請"
        notify_footer = "請管理員點擊下方按鈕審核通過或退回此申請"
        notify_color = discord.Color.gold()
        reviewer_label = "秘書處"

    # ── Phase 1: Fields missing → orange ⚠️, do NOT notify reviewer yet ──
    if not all_pass:
        fields_text = "\n".join(f"❌ {f}" for f in missing_fields)
        ack_desc = (
            f"📝 已收到入盟申請，但以下欄位仍需補齊：\n\n"
            f"{fields_text}\n\n"
            f"請**編輯原貼文**補齊上述欄位，系統會自動重新檢查。\n"
            f"如果缺少國旗圖片，可以點擊下方按鈕單獨補上。"
        )
        ack_embed = discord.Embed(
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
        return

    # ── Phase 2: All fields pass → blue ✅, notify reviewer ──
    if existing_entry and existing_entry.get("secretariat_notified"):
        # Already notified, just update the ack
        try:
            ack_embed = discord.Embed(
                title="✅ 入盟申請已通過初檢",
                description=(
                    f"所有必填欄位齊全！\n"
                    f"已送交{reviewer_label}審核，請耐心等候結果。"
                ),
                color=discord.Color.green(),
            )
            if image_url:
                ack_embed.set_thumbnail(url=image_url)
            if existing_entry.get("ack_message_id"):
                try:
                    orig_ack_ch = message.channel if isinstance(message.channel, discord.Thread) else channel
                    ack_msg = await orig_ack_ch.fetch_message(int(existing_entry["ack_message_id"]))
                    await ack_msg.edit(embed=ack_embed, view=None)
                except Exception:
                    pass
        except Exception:
            pass
        return

    # 更新 ack 為綠色 ✅
    try:
        done_embed = discord.Embed(
            title="✅ 入盟申請已通過初檢",
            description=(
                f"所有必填欄位齊全！\n"
                f"已送交{reviewer_label}審核，請耐心等候結果。"
            ),
            color=discord.Color.green(),
        )
        if image_url:
            done_embed.set_thumbnail(url=image_url)
        if existing_entry and existing_entry.get("ack_message_id"):
            try:
                orig_ack_ch = message.channel if isinstance(message.channel, discord.Thread) else channel
                ack_msg = await orig_ack_ch.fetch_message(int(existing_entry["ack_message_id"]))
                await ack_msg.edit(embed=done_embed, view=None)
            except Exception:
                await message.reply(embed=done_embed, mention_author=False)
        else:
            await message.reply(embed=done_embed, mention_author=False)
    except Exception as e:
        print(f"⚠️ 入盟申請通過 ack 訊息發送失敗：{e}")

    entry["secretariat_notified"] = True
    save_applications()

    # 發送通知到審核頻道
    if not notify_ch_id:
        print(f"⚠️ 入盟申請系統：未設定{reviewer_label}通知頻道")
        return

    notify_ch = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(notify_ch_id))
        if ch:
            notify_ch = ch
            break

    if not notify_ch:
        print(f"⚠️ 找不到{reviewer_label}通知頻道 {notify_ch_id}")
        return

    notify_embed = discord.Embed(
        title=notify_title,
        color=notify_color,
        timestamp=discord.utils.utcnow(),
    )
    notify_embed.add_field(name="申請人", value=entry.get("applicant_name", "?"), inline=True)
    notify_embed.add_field(name="申請頻道", value=f"#{entry.get('channel_name', '?')}", inline=True)
    notify_embed.add_field(name="申請時間", value=entry.get("date", "?"), inline=True)
    if entry.get("applicant_nation"):
        notify_embed.add_field(name="申請國家", value=entry["applicant_nation"], inline=True)
    notify_embed.add_field(name="欄位檢查", value="✅ 全部必填欄位齊全（含國旗圖片）", inline=False)
    if image_url:
        notify_embed.set_thumbnail(url=image_url)
    notify_embed.add_field(name="原文連結", value=entry.get("message_url", "(無)"), inline=False)
    notify_embed.add_field(name="申請 ID", value=entry["id"], inline=False)
    notify_embed.set_footer(text=notify_footer)

    try:
        sent_notify = await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(entry["id"]))
        entry["notify_message_id"] = str(sent_notify.id)
        entry["notify_channel_id"] = str(notify_ch.id)
        save_applications()
        print(f"✅ 入盟申請通知已發送至{reviewer_label} #{notify_ch.name}")
    except Exception as e:
        print(f"❌ 入盟申請通知發送失敗：{e}")


# ─── 國旗上傳按鈕 ──────────────────────────────────────────────────────────

_pending_flag_uploads = {}


class ApplicationFlagUploadView(discord.ui.View):
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
            "🚩 請在這個頻道/貼文中**傳送一張國旗圖片**（直接附加圖片發送即可）。\n"
            f"系統會自動接收，通過後自動送交{reviewer_label}審核。\n"
            "（5 分鐘內有效）",
            ephemeral=True,
        )


# 註冊為持久化 view
bot.add_view(ApplicationFlagUploadView())


# ─── 入盟申請審核 UI ──────────────────────────────────────────────────────

class ApplicationRejectModal(discord.ui.Modal, title="退回入盟申請原因"):
    reason_input = discord.ui.TextInput(
        label="請說明退回原因",
        style=discord.TextStyle.paragraph,
        placeholder="例：欄位不完整/資料有誤/不符合入盟標準...",
        required=True,
        max_length=300,
    )

    def __init__(self, app_id: str):
        super().__init__(timeout=300)
        self.app_id = app_id

    async def on_submit(self, interaction: discord.Interaction):
        await _handle_application_decision(interaction, self.app_id, "rejected", self.reason_input.value.strip())

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"⚠️ 入盟申請駁回 Modal 錯誤：{error}")
        try:
            await interaction.response.send_message("⚠️ 提交退回原因時發生錯誤。", ephemeral=True)
        except Exception:
            pass


def _check_review_permission(interaction: discord.Interaction, role_id, fallback_admin_check=True) -> bool:
    """檢查使用者是否有權限按審核按鈕。"""
    if role_id:
        user_role_ids = {r.id for r in interaction.user.roles} if interaction.user.roles else set()
        if int(role_id) in user_role_ids:
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        return False
    if fallback_admin_check:
        return is_admin(interaction)
    return False


def _get_application_review_role_id(app_id: str):
    """依申請的 system_type 取得對應的審核身分組 ID。"""
    entry = None
    for a in _applications.get("entries", []):
        if a.get("id") == app_id:
            entry = a
            break
    if entry and entry.get("system_type") == "council":
        return application_settings.get("council_review_role_id")
    return application_settings.get("secretariat_review_role_id")


class ApplicationReviewView(discord.ui.View):
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
bot.add_view(ApplicationReviewView())


async def _handle_application_decision(interaction: discord.Interaction, app_id: str,
                                         decision: str, reject_reason: str):
    """Process accept/reject of a membership application and notify the applicant."""
    entry = None
    for a in _applications.get("entries", []):
        if a.get("id") == app_id:
            entry = a
            break

    if not entry:
        try:
            await interaction.response.send_message("❌ 找不到此申請記錄（可能已被清除）。", ephemeral=True)
        except Exception:
            pass
        return

    if entry["status"] != "pending":
        try:
            await interaction.response.send_message(
                f"⚠️ 此申請已被{'審核通過' if entry['status']=='accepted' else '退回'}過了。",
                ephemeral=True
            )
        except Exception:
            pass
        return

    entry["status"] = decision
    entry["reviewed_by"] = interaction.user.display_name
    entry["review_date"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M")
    entry["reject_reason"] = reject_reason
    save_applications()

    status_emoji = "✅" if decision == "accepted" else "❌"
    status_text = "審核通過" if decision == "accepted" else "已退回"
    embed = interaction.message.embeds[0] if interaction.message.embeds else None
    if embed:
        embed.color = discord.Color.green() if decision == "accepted" else discord.Color.red()
        embed.add_field(
            name=f"{status_emoji} 審核結果",
            value=f"{status_text} by {interaction.user.display_name} ({entry['review_date']})"
                  + (f"\n原因：{reject_reason}" if reject_reason else ""),
            inline=False,
        )
        embed.set_footer(text=f"申請已{status_text}")
        try:
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception:
            pass
    else:
        try:
            await interaction.response.send_message(f"{status_emoji} 申請已{status_text}。", ephemeral=True)
        except Exception:
            pass

    # 通知申請人
    orig_ch_id = entry.get("channel_id")
    guild_id = entry.get("guild_id", 0)
    thread_id = entry.get("thread_id")
    orig_ch = None
    target_thread = None
    for guild in bot.guilds:
        if guild.id == guild_id:
            orig_ch = guild.get_channel(int(orig_ch_id)) if orig_ch_id else None
            if thread_id:
                try:
                    target_thread = guild.get_thread(int(thread_id))
                except Exception:
                    pass
                if not target_thread and orig_ch:
                    try:
                        target_thread = await orig_ch.fetch_thread(int(thread_id))
                    except Exception:
                        pass
            break

    if not orig_ch and not target_thread:
        print(f"⚠️ 找不到原始申請頻道 {orig_ch_id}，無法通知申請人")
        return

    applicant_mention = f"<@{entry.get('applicant_id')}>"
    nation_name = entry.get("applicant_nation", "")
    if decision == "accepted":
        notify_embed = discord.Embed(
            title="🎉 入盟申請審核通過",
            description=(
                f"{applicant_mention} 你的入盟申請已通過審核！\n\n"
                + (f"**申請國家：** {nation_name}\n" if nation_name else "")
                + f"**審核人：** {interaction.user.display_name}\n"
                f"**審核時間：** {entry['review_date']}\n\n"
                f"歡迎正式加入 ICEA 國際總會！請留意後續的入盟程序與會員國權利義務說明。"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
    else:
        notify_embed = discord.Embed(
            title="❌ 入盟申請未通過",
            description=(
                f"{applicant_mention} 你的入盟申請未通過審核。\n\n"
                + (f"**申請國家：** {nation_name}\n" if nation_name else "")
                + f"**退回原因：** {reject_reason or '未提供'}\n"
                + f"**審核人：** {interaction.user.display_name}\n"
                + f"**審核時間：** {entry['review_date']}\n\n"
                + f"請根據退回原因修正後重新提交申請。"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )

    try:
        if target_thread:
            await target_thread.send(embed=notify_embed)
            print(f"✅ 入盟申請結果已發送至論壇貼文 #{target_thread.name}")
            return
        msg_id = entry.get("message_id")
        if msg_id and hasattr(orig_ch, 'fetch_message'):
            try:
                orig_msg = await orig_ch.fetch_message(int(msg_id))
                await orig_msg.reply(embed=notify_embed, mention_author=True)
                print(f"✅ 入盟申請結果已回覆至 #{orig_ch.name}")
                return
            except Exception as e:
                print(f"⚠️ fetch_message 失敗 ({e})，改用頻道發送")
        if hasattr(orig_ch, 'send'):
            await orig_ch.send(embed=notify_embed)
            print(f"✅ 入盟申請結果已發送至 #{orig_ch.name}")
    except Exception as e:
        print(f"❌ 通知申請人失敗：{e}")


# ═════════════════════════════════════════════════════════════════
# 指令群組
# ═════════════════════════════════════════════════════════════════

class ProposalGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="proposal", description="提案相關工具")

    @app_commands.command(name="list", description="查看提案記錄")
    @app_commands.describe(status="篩選狀態：pending=待審, accepted=已受理, rejected=已駁回, all=全部")
    @app_commands.choices(status=[
        app_commands.Choice(name="待審", value="pending"),
        app_commands.Choice(name="已受理", value="accepted"),
        app_commands.Choice(name="已駁回", value="rejected"),
        app_commands.Choice(name="全部", value="all"),
    ])
    async def proposal_list(self, interaction: discord.Interaction,
                            status: app_commands.Choice[str] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        entries = _proposals.get("entries", [])
        filter_status = status.value if status else "all"
        if filter_status != "all":
            entries = [e for e in entries if e.get("status") == filter_status]
        if not entries:
            await interaction.followup.send("📋 沒有符合條件的提案記錄。", ephemeral=True)
            return
        recent = sorted(entries, key=lambda e: e.get("_ts", 0), reverse=True)[:15]
        lines = []
        for e in recent:
            emoji = {"pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(e.get("status", ""), "?")
            line = (
                f"{emoji} **{e.get('proposal_type', '?')}** | {e.get('proposer_name', '?')} | {e.get('date', '?')}\n"
                f"  摘要：{e.get('summary', '')[:80]}\n"
                f"  狀態：{e.get('status', '?')} | ID: `{e.get('id', '')}`"
            )
            if e.get("reject_reason"):
                line += f"\n  駁回原因：{e['reject_reason'][:80]}"
            lines.append(line)
        await interaction.followup.send(
            f"📋 **提案記錄（{len(recent)}/{len(entries)} 筆）**\n\n" + "\n\n".join(lines),
            ephemeral=True,
        )


class SystemGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="system", description="系統管理指令")

    # ── 提案系統指令 ──

    @app_commands.command(name="proposal_toggle", description="開啟/關閉提案區自動受理系統")
    async def proposal_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        proposal_settings["enabled"] = not proposal_settings.get("enabled", False)
        save_proposal_settings()
        status = "啟用" if proposal_settings["enabled"] else "停用"
        await interaction.response.send_message(f"📋 提案系統已{status}。", ephemeral=True)

    @app_commands.command(name="proposal_channel", description="新增/移除提案區頻道")
    @app_commands.describe(action="add=新增頻道, remove=移除頻道, list=列出所有頻道", channel="要新增/移除的頻道（支援文字頻道與論壇頻道）")
    @app_commands.choices(action=[
        app_commands.Choice(name="新增", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="列表", value="list"),
    ])
    async def proposal_channel(self, interaction: discord.Interaction,
                               action: app_commands.Choice[str],
                               channel: Union[discord.TextChannel, discord.ForumChannel] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        act = action.value
        if act == "list":
            channels = proposal_settings.get("proposal_channels", [])
            if not channels:
                await interaction.response.send_message("📋 目前沒有設定任何提案區頻道。", ephemeral=True)
                return
            lines = [f"• <#{cid}> (`{cid}`)" for cid in channels]
            await interaction.response.send_message(f"📋 **提案區頻道列表（{len(channels)} 個）**\n" + "\n".join(lines), ephemeral=True)
            return
        if not channel:
            await interaction.response.send_message("❌ 請指定一個頻道。", ephemeral=True)
            return
        channels = proposal_settings.get("proposal_channels", [])
        if act == "add":
            if channel.id not in channels:
                channels.append(channel.id)
                proposal_settings["proposal_channels"] = channels
                save_proposal_settings()
                await interaction.response.send_message(f"✅ 已新增 #{channel.name} 為提案區頻道。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 已經是提案區頻道。", ephemeral=True)
        elif act == "remove":
            if channel.id in channels:
                channels.remove(channel.id)
                proposal_settings["proposal_channels"] = channels
                save_proposal_settings()
                await interaction.response.send_message(f"✅ 已移除 #{channel.name} 的提案區頻道設定。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 不在提案區頻道列表中。", ephemeral=True)

    @app_commands.command(name="proposal_secretariat", description="設定秘書處通知頻道")
    @app_commands.describe(channel="秘書處頻道（系統會在此發送提案通知供管理員受理/駁回）")
    async def proposal_secretariat(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        proposal_settings["secretariat_channel"] = channel.id
        save_proposal_settings()
        await interaction.response.send_message(f"✅ 秘書處通知頻道已設為 #{channel.name}。", ephemeral=True)

    @app_commands.command(name="proposal_status", description="查看提案系統目前設定狀態")
    async def proposal_status(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        enabled = proposal_settings.get("enabled", False)
        channels = proposal_settings.get("proposal_channels", [])
        sec_id = proposal_settings.get("secretariat_channel")

        lines = ["📋 **提案系統狀態**", ""]
        lines.append(f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用（用 /system proposal_toggle 開啟）'}")
        if channels:
            ch_list = "\n".join(f"  • <#{cid}> (`{cid}`)" for cid in channels)
            lines.append(f"提案區頻道（{len(channels)} 個）：\n{ch_list}")
        else:
            lines.append("提案區頻道：❌ 尚未設定任何頻道")
        if sec_id:
            lines.append(f"秘書處通知頻道：<#{sec_id}> (`{sec_id}`)")
        else:
            lines.append("秘書處通知頻道：❌ 尚未設定（用 /system proposal_secretariat 設定）")
        lines.append("")
        lines.append(f"已收錄提案總數：{len(_proposals.get('entries', []))} 筆")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="proposal_list", description="查看提案記錄")
    @app_commands.describe(status="篩選狀態：pending=待審, accepted=已受理, rejected=已駁回, all=全部")
    @app_commands.choices(status=[
        app_commands.Choice(name="待審", value="pending"),
        app_commands.Choice(name="已受理", value="accepted"),
        app_commands.Choice(name="已駁回", value="rejected"),
        app_commands.Choice(name="全部", value="all"),
    ])
    async def proposal_list(self, interaction: discord.Interaction,
                            status: app_commands.Choice[str] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        entries = _proposals.get("entries", [])
        filter_status = status.value if status else "all"
        if filter_status != "all":
            entries = [e for e in entries if e.get("status") == filter_status]
        if not entries:
            await interaction.followup.send("📋 沒有符合條件的提案記錄。", ephemeral=True)
            return
        recent = sorted(entries, key=lambda e: e.get("_ts", 0), reverse=True)[:15]
        lines = []
        for e in recent:
            emoji = {"pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(e.get("status", ""), "?")
            line = (
                f"{emoji} **{e.get('proposal_type', '?')}** | {e.get('proposer_name', '?')} | {e.get('date', '?')}\n"
                f"  摘要：{e.get('summary', '')[:80]}\n"
                f"  狀態：{e.get('status', '?')} | ID: `{e.get('id', '')}`"
            )
            if e.get("reject_reason"):
                line += f"\n  駁回原因：{e['reject_reason'][:80]}"
            lines.append(line)
        await interaction.followup.send(
            f"📋 **提案記錄（{len(recent)}/{len(entries)} 筆）**\n\n" + "\n\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(name="proposal_review_role", description="設定提案審核身分組（留空=管理員限定）")
    @app_commands.describe(role="審核提案所需的身分組")
    async def proposal_review_role(self, interaction: discord.Interaction, role: discord.Role = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        proposal_settings["review_role_id"] = str(role.id) if role else None
        save_proposal_settings()
        if role:
            await interaction.response.send_message(f"✅ 提案審核身分組已設為 {role.mention}。", ephemeral=True)
        else:
            await interaction.response.send_message("✅ 提案審核身分組已清除（改為管理員限定）。", ephemeral=True)

    # ── 入盟申請系統指令 ──

    @app_commands.command(name="application_toggle", description="開啟/關閉入盟申請自動回覆系統")
    async def application_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        application_settings["enabled"] = not application_settings.get("enabled", False)
        save_application_settings()
        status = "啟用" if application_settings["enabled"] else "停用"
        await interaction.response.send_message(f"📝 入盟申請系統已{status}。", ephemeral=True)

    @app_commands.command(name="application_channel", description="新增/移除入盟申請區頻道")
    @app_commands.describe(action="add=新增頻道, remove=移除頻道, list=列出所有頻道", channel="要新增/移除的頻道（支援文字頻道與論壇頻道）")
    @app_commands.choices(action=[
        app_commands.Choice(name="新增", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="列表", value="list"),
    ])
    async def application_channel(self, interaction: discord.Interaction,
                                 action: app_commands.Choice[str],
                                 channel: Union[discord.TextChannel, discord.ForumChannel] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        act = action.value
        if act == "list":
            channels = application_settings.get("application_channels", [])
            if not channels:
                await interaction.response.send_message("📝 目前沒有設定任何入盟申請區頻道。", ephemeral=True)
                return
            lines = [f"• <#{cid}> (`{cid}`)" for cid in channels]
            await interaction.response.send_message(f"📝 **入盟申請區頻道列表（{len(channels)} 個）**\n" + "\n".join(lines), ephemeral=True)
            return
        if not channel:
            await interaction.response.send_message("❌ 請指定一個頻道。", ephemeral=True)
            return
        channels = application_settings.get("application_channels", [])
        if act == "add":
            if channel.id not in channels:
                channels.append(channel.id)
                application_settings["application_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 已新增 #{channel.name} 為入盟申請區頻道。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 已經是入盟申請區頻道。", ephemeral=True)
        elif act == "remove":
            if channel.id in channels:
                channels.remove(channel.id)
                application_settings["application_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 已移除 #{channel.name} 的入盟申請區頻道設定。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 不在入盟申請區頻道列表中。", ephemeral=True)

    @app_commands.command(name="application_council_channel", description="新增/移除理事國入盟申請區頻道")
    @app_commands.describe(action="add=新增頻道, remove=移除頻道, list=列出已設定的頻道")
    @app_commands.choices(action=[
        app_commands.Choice(name="新增", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="列表", value="list"),
    ])
    async def application_council_channel(self, interaction: discord.Interaction,
                                            action: app_commands.Choice[str],
                                            channel: Union[discord.TextChannel, discord.ForumChannel] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        act = action.value
        if act == "list":
            channels = application_settings.get("council_channels", [])
            if not channels:
                await interaction.response.send_message("📝 目前沒有設定任何理事國入盟申請區頻道。", ephemeral=True)
                return
            lines = [f"• <#{cid}> (`{cid}`)" for cid in channels]
            await interaction.response.send_message(f"📝 **理事國入盟申請區頻道列表（{len(channels)} 個）**\n" + "\n".join(lines), ephemeral=True)
            return
        if not channel:
            await interaction.response.send_message("❌ 請指定一個頻道。", ephemeral=True)
            return
        channels = application_settings.get("council_channels", [])
        if act == "add":
            if channel.id not in channels:
                channels.append(channel.id)
                application_settings["council_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 已新增 #{channel.name} 為理事國入盟申請區頻道。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 已經是理事國入盟申請區頻道。", ephemeral=True)
        elif act == "remove":
            if channel.id in channels:
                channels.remove(channel.id)
                application_settings["council_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 已移除 #{channel.name} 的理事國入盟申請區頻道設定。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 不在理事國入盟申請區頻道列表中。", ephemeral=True)

    @app_commands.command(name="application_secretariat", description="設定入盟申請秘書處通知頻道")
    @app_commands.describe(channel="秘書處頻道（系統會在此發送申請通知供管理員審核）")
    async def application_secretariat(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        application_settings["secretariat_channel"] = channel.id
        save_application_settings()
        await interaction.response.send_message(f"✅ 入盟申請秘書處通知頻道已設為 #{channel.name}。", ephemeral=True)

    @app_commands.command(name="application_council", description="設定理事國審核通知頻道")
    @app_commands.describe(channel="理事國頻道（系統會在此發送申請通知供理事國審核）")
    async def application_council(self, interaction: discord.Interaction,
                                  channel: Union[discord.TextChannel, discord.ForumChannel]):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        application_settings["council_channel"] = channel.id
        save_application_settings()
        await interaction.response.send_message(f"✅ 理事國審核通知頻道已設為 #{channel.name}。", ephemeral=True)

    @app_commands.command(name="application_status", description="查看入盟申請系統目前設定狀態")
    async def application_status(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        enabled = application_settings.get("enabled", False)
        channels = application_settings.get("application_channels", [])
        sec_id = application_settings.get("secretariat_channel")

        lines = ["📝 **入盟申請系統狀態**", ""]
        lines.append(f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用（用 /system application_toggle 開啟）'}")
        if channels:
            ch_list = "\n".join(f"  • <#{cid}> (`{cid}`)" for cid in channels)
            lines.append(f"秘書處入盟申請區頻道（{len(channels)} 個）：\n{ch_list}")
        else:
            lines.append("秘書處入盟申請區頻道：❌ 尚未設定（用 /system application_channel add 設定）")
        council_chs = application_settings.get("council_channels", [])
        if council_chs:
            ch_list2 = "\n".join(f"  • <#{cid}> (`{cid}`)" for cid in council_chs)
            lines.append(f"理事國入盟申請區頻道（{len(council_chs)} 個）：\n{ch_list2}")
        else:
            lines.append("理事國入盟申請區頻道：❌ 尚未設定（用 /system application_council_channel add 設定）")
        if sec_id:
            lines.append(f"秘書處通知頻道：<#{sec_id}> (`{sec_id}`)")
        else:
            lines.append("秘書處通知頻道：❌ 尚未設定（用 /system application_secretariat 設定）")
        council_id = application_settings.get("council_channel")
        if council_id:
            lines.append(f"理事國審核頻道：<#{council_id}> (`{council_id}`)")
        else:
            lines.append("理事國審核頻道：❌ 尚未設定（用 /system application_council 設定）")
        lines.append("")
        lines.append(f"已收錄申請總數：{len(_applications.get('entries', []))} 筆")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="application_review_role", description="設定入盟申請審核身分組")
    @app_commands.describe(
        system="secretariat=秘書處審核, council=理事國審核",
        role="審核所需的身分組（不指定=清除，改為管理員限定）",
    )
    @app_commands.choices(system=[
        app_commands.Choice(name="秘書處", value="secretariat"),
        app_commands.Choice(name="理事國", value="council"),
    ])
    async def application_review_role(self, interaction: discord.Interaction,
                                      system: app_commands.Choice[str],
                                      role: discord.Role = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        key = "secretariat_review_role_id" if system.value == "secretariat" else "council_review_role_id"
        application_settings[key] = str(role.id) if role else None
        save_application_settings()
        if role:
            await interaction.response.send_message(f"✅ {system.name}審核身分組已設為 {role.mention}。", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ {system.name}審核身分組已清除（改為管理員限定）。", ephemeral=True)


# ═════════════════════════════════════════════════════════════════
# 事件掛載（on_message / on_message_edit / on_thread_create）
# ═════════════════════════════════════════════════════════════════

# 原始 on_message 和 on_thread_create 在主程式裡，模組透過
# 註冊 callback 的方式讓主程式呼叫。這裡把需要監聽的邏輯包成函式，
# 主程式的 on_message / on_thread_create / on_message_edit 呼叫它們。

async def handle_proposal_message(message: discord.Message):
    """在 on_message 中呼叫：偵測提案區新訊息。"""
    if not proposal_settings.get("enabled"):
        return
    if not message.guild or str(message.guild.id) != str(ICEA_GUILD_ID):
        return
    if message.author.bot:
        return
    proposal_channels = proposal_settings.get("proposal_channels", [])
    ch_id = message.channel.id
    parent_id = getattr(message.channel, 'parent_id', None)
    target_ch = message.channel
    if ch_id in proposal_channels:
        pass
    elif parent_id and parent_id in proposal_channels:
        # 論壇貼文：只處理 starter message
        if isinstance(message.channel, discord.Thread) and message.id != message.channel.id:
            return
        target_ch = message.channel.parent
    else:
        return
    try:
        await _process_new_proposal(message, target_ch)
    except Exception as e:
        print(f"⚠️ 提案處理錯誤：{e}")



async def _handle_flag_upload(message: discord.Message, app_id: str, image_url: str):
    """處理國旗圖片上傳：更新申請記錄，如果所有欄位齊全就通知審核方。"""
    entry = None
    for a in _applications.get("entries", []):
        if a.get("id") == app_id:
            entry = a
            break
    if not entry:
        print(f"⚠️ 國旗上傳：找不到申請記錄 {app_id}")
        return
    if entry.get("status") in ("accepted", "rejected"):
        print(f"⚠️ 國旗上傳：申請 {app_id} 已審核完畢")
        return

    flag_valid = _verify_flag_image(image_url)  # 無 AI 版本：永遠 True
    if not flag_valid:
        return

    # 更新申請記錄
    entry["flag_status"] = "ok"
    entry["flag_valid"] = True
    entry["flag_image_url"] = image_url
    entry.setdefault("field_status", {})["國旗"] = True
    entry["missing_fields"] = [f for f in entry.get("missing_fields", []) if "國旗" not in f]
    save_applications()

    remaining_missing = entry.get("missing_fields", [])
    if not remaining_missing:
        # ── 所有欄位齊全，通知審核方 ──
        try:
            reviewer_name = "理事國" if entry.get("system_type") == "council" else "秘書處"
            done_embed = discord.Embed(
                title="✅ 國旗已收到",
                description=f"國旗圖片已驗證，所有欄位齊全！正在送交{reviewer_name}審核...",
                color=discord.Color.green(),
            )
            await message.reply(embed=done_embed, mention_author=False)
        except Exception:
            pass

        entry["secretariat_notified"] = True
        save_applications()

        notify_target_id = application_settings.get("council_channel") if entry.get("system_type") == "council" else application_settings.get("secretariat_channel")
        notify_title = "📝 新入盟申請（理事國審核）" if entry.get("system_type") == "council" else "📝 新入盟申請"
        notify_footer = "請理事國點擊下方按鈕審核通過或退回此申請" if entry.get("system_type") == "council" else "請管理員點擊下方按鈕審核通過或退回此申請"
        notify_color = discord.Color.dark_gold() if entry.get("system_type") == "council" else discord.Color.gold()

        if not notify_target_id:
            print(f"⚠️ 入盟申請系統：未設定{'理事國' if entry.get('system_type') == 'council' else '秘書處'}通知頻道")
            return

        notify_ch = None
        for guild in bot.guilds:
            ch = guild.get_channel(int(notify_target_id))
            if ch:
                notify_ch = ch
                break

        if not notify_ch:
            print(f"⚠️ 找不到通知頻道 {notify_target_id}")
            return

        notify_embed = discord.Embed(
            title=notify_title,
            color=notify_color,
            timestamp=discord.utils.utcnow(),
        )
        notify_embed.add_field(name="申請人", value=entry.get("applicant_name", "?"), inline=True)
        notify_embed.add_field(name="申請頻道", value=f"#{entry.get('channel_name', '?')}", inline=True)
        notify_embed.add_field(name="申請時間", value=entry.get("date", "?"), inline=True)
        if entry.get("applicant_nation"):
            notify_embed.add_field(name="申請國家", value=entry["applicant_nation"], inline=True)
        notify_embed.add_field(name="欄位檢查", value="✅ 全部必填欄位齊全（含國旗圖片）", inline=False)
        if image_url:
            notify_embed.set_thumbnail(url=image_url)
        notify_embed.add_field(name="原文連結", value=entry.get("message_url", "(無)"), inline=False)
        notify_embed.add_field(name="申請 ID", value=entry["id"], inline=False)
        notify_embed.set_footer(text=notify_footer)

        try:
            sent_notify = await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(entry["id"]))
            entry["notify_message_id"] = str(sent_notify.id)
            entry["notify_channel_id"] = str(notify_ch.id)
            save_applications()
            reviewer_label = "理事國" if entry.get("system_type") == "council" else "秘書處"
            print(f"✅ 入盟申請通知已發送至{reviewer_label} #{notify_ch.name}")
        except Exception as e:
            print(f"❌ 通知發送失敗：{e}")
    else:
        # ── 仍有缺漏欄位 ──
        try:
            still_missing_embed = discord.Embed(
                title="⚠️ 國旗已收到，但仍有缺漏",
                description=(
                    f"國旗圖片已收到！\n\n"
                    f"但以下欄位仍需補齊：\n"
                    + "\n".join(f"❌ {f}" for f in remaining_missing)
                    + "\n\n請編輯原貼文補齊上述欄位。"
                ),
                color=discord.Color.orange(),
            )
            await message.reply(embed=still_missing_embed, mention_author=False)
        except Exception:
            pass


async def handle_application_message(message: discord.Message):
    """在 on_message 中呼叫：偵測入盟申請區新訊息 + 國旗上傳。"""
    if not application_settings.get("enabled"):
        return
    if not message.guild or str(message.guild.id) != str(str(ICEA_GUILD_ID)):
        return
    if message.author.bot:
        return

    sec_channels = application_settings.get("application_channels", [])
    council_channels = application_settings.get("council_channels", [])
    ch_id = message.channel.id
    parent_id = getattr(message.channel, 'parent_id', None)

    # ── 先處理國旗上傳（在任何「非首則訊息」early return 之前）──
    # forum thread 裡的回覆訊息（含國旗圖片）會被當成「非首則訊息」跳過，
    # 必須在 channel 偵測邏輯之前先攔截 pending flag uploads。
    is_in_monitored_channel = (
        ch_id in sec_channels or ch_id in council_channels
        or (parent_id and (parent_id in sec_channels or parent_id in council_channels))
    )
    if is_in_monitored_channel and message.attachments and _pending_flag_uploads:
        _now = _time.time()
        expired_keys = [k for k, v in _pending_flag_uploads.items() if v.get("expires", 0) < _now]
        for k in expired_keys:
            _pending_flag_uploads.pop(k, None)
        for app_id, info in list(_pending_flag_uploads.items()):
            if info.get("user_id") != str(message.author.id):
                continue
            entry_ch = info.get("channel_id")
            entry_thread = info.get("thread_id")
            # 比對：同一頻道 或 同一 forum thread
            if (str(entry_ch) == str(ch_id)
                    or (entry_thread and str(entry_thread) == str(ch_id))
                    or (parent_id and str(entry_ch) == str(parent_id))):
                image_url = str(message.attachments[0].url)
                print(f"🚩 收到國旗圖片上傳：app {app_id} by {message.author.display_name}")
                _pending_flag_uploads.pop(app_id, None)
                await _handle_flag_upload(message, app_id, image_url)
                return  # 圖片已消化，不繼續處理為新申請

    # ── Channel 偵測（區分新申請 vs 回覆）──
    system_type = None
    target_ch = message.channel
    if ch_id in sec_channels:
        system_type = "secretariat"
    elif ch_id in council_channels:
        system_type = "council"
    elif parent_id and parent_id in sec_channels:
        if isinstance(message.channel, discord.Thread) and message.id != message.channel.id:
            return  # 非首則訊息（不是新申請）
        system_type = "secretariat"
        target_ch = message.channel.parent
    elif parent_id and parent_id in council_channels:
        if isinstance(message.channel, discord.Thread) and message.id != message.channel.id:
            return  # 非首則訊息（不是新申請）
        system_type = "council"
        target_ch = message.channel.parent
    else:
        return

    # 處理國旗上傳（文字頻道路徑：直接在申請頻道發圖片）
    if message.attachments and _pending_flag_uploads:
        _now = _time.time()
        expired_keys = [k for k, v in _pending_flag_uploads.items() if v.get("expires", 0) < _now]
        for k in expired_keys:
            _pending_flag_uploads.pop(k, None)
        for app_id, info in list(_pending_flag_uploads.items()):
            if info.get("user_id") != str(message.author.id):
                continue
            entry_ch = info.get("channel_id")
            entry_thread = info.get("thread_id")
            if str(entry_ch) == str(ch_id) or (entry_thread and str(entry_thread) == str(message.channel.id)):
                image_url = str(message.attachments[0].url)
                print(f"🚩 收到國旗圖片上傳：app {app_id} by {message.author.display_name}")
                _pending_flag_uploads.pop(app_id, None)
                await _handle_flag_upload(message, app_id, image_url)
                return  # 圖片已消化

    try:
        await _process_new_application(message, target_ch, system_type=system_type)
    except Exception as e:
        print(f"⚠️ 入盟申請處理錯誤：{e}")


async def handle_application_edit(before: discord.Message, after: discord.Message):
    """在 on_message_edit 中呼叫：偵測入盟申請編輯。"""
    if after.author.bot:
        return
    if not application_settings.get("enabled") or not after.guild:
        return
    sec_channels = application_settings.get("application_channels", [])
    council_channels = application_settings.get("council_channels", [])
    ch_id = after.channel.id
    parent_id = getattr(after.channel, 'parent_id', None)

    system_type = None
    if ch_id in sec_channels or (parent_id and parent_id in sec_channels):
        system_type = "secretariat"
    elif ch_id in council_channels or (parent_id and parent_id in council_channels):
        system_type = "council"
    if not system_type:
        return

    msg_id = str(after.id)
    existing = [a for a in _applications.get("entries", []) if a.get("message_id") == msg_id]
    if existing:
        entry = existing[0]
        if entry.get("status") in ("accepted", "rejected"):
            return

    print(f"📝 偵測到入盟申請編輯：msg {msg_id} by {after.author.display_name}")

    try:
        ch = after.channel
        if isinstance(ch, discord.Thread) and parent_id and (parent_id in sec_channels or parent_id in council_channels):
            ch = ch.parent
        await _process_new_application(after, ch, is_edit=True, system_type=system_type)
    except Exception as e:
        print(f"⚠️ 入盟申請編輯處理錯誤：{e}")


async def handle_thread_create(thread: discord.Thread):
    """在 on_thread_create 中呼叫：偵測論壇新貼文。"""
    parent_id = thread.parent_id if hasattr(thread, 'parent_id') else None

    # 提案區
    if proposal_settings.get("enabled") and parent_id and parent_id in proposal_settings.get("proposal_channels", []):
        try:
            await asyncio.sleep(2)
            starter = await thread.fetch_message(thread.id) if hasattr(thread, 'id') else None
            if starter and not starter.author.bot:
                await _process_new_proposal(starter, thread.parent)
                print(f"📋 論壇貼文提案已處理：#{thread.name}")
        except Exception as e:
            print(f"⚠️ 論壇貼文提案處理失敗：{e}")

    # 入盟申請區（秘書處）
    if application_settings.get("enabled"):
        sec_channels = application_settings.get("application_channels", [])
        council_channels = application_settings.get("council_channels", [])
        if parent_id and parent_id in sec_channels:
            try:
                await asyncio.sleep(2)
                starter = await thread.fetch_message(thread.id) if hasattr(thread, 'id') else None
                if starter and not starter.author.bot:
                    await _process_new_application(starter, thread.parent, system_type="secretariat")
                    print(f"📝 論壇貼文入盟申請已處理（秘書處）：#{thread.name}")
            except Exception as e:
                print(f"⚠️ 論壇貼文入盟申請處理失敗：{e}")
        elif parent_id and parent_id in council_channels:
            try:
                await asyncio.sleep(2)
                starter = await thread.fetch_message(thread.id) if hasattr(thread, 'id') else None
                if starter and not starter.author.bot:
                    await _process_new_application(starter, thread.parent, system_type="council")
                    print(f"📝 論壇貼文入盟申請已處理（理事國）：#{thread.name}")
            except Exception as e:
                print(f"⚠️ 論壇貼文入盟申請處理失敗（理事國）：{e}")



# ═════════════════════════════════════════════════════════════════
# 重啟後自動偵測未結案的提案/申請 → 重發全新可用面板
# （舊面板因 custom_id 未持久化/process 重啟而失效，此處主動補救）
# ═════════════════════════════════════════════════════════════════

_startup_panel_refresh_done = False


async def _resend_proposal_panel(entry: dict):
    """重發一則提案審核面板（受理/駁回按鈕），並標記舊面板已失效。"""
    proposal_id = entry["id"]
    notify_ch_id = entry.get("notify_channel_id") or proposal_settings.get("secretariat_channel")
    if not notify_ch_id:
        return
    notify_ch = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(notify_ch_id))
        if ch:
            notify_ch = ch
            break
    if not notify_ch:
        return

    old_msg_id = entry.get("notify_message_id")
    if old_msg_id:
        try:
            old_msg = await notify_ch.fetch_message(int(old_msg_id))
            if old_msg.components:
                old_embed = old_msg.embeds[0] if old_msg.embeds else None
                if old_embed:
                    old_embed.set_footer(text="⚠️ 此面板因機器人重啟失效，請使用下方新面板")
                    await old_msg.edit(embed=old_embed, view=None)
                else:
                    await old_msg.edit(view=None)
        except Exception:
            pass  # 訊息可能已刪除/找不到，忽略

    embed = discord.Embed(
        title=f"📋 新提案通知：{entry.get('proposal_type', '一般提案')}（重啟後重發）",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="提案人", value=entry.get("proposer_name", "?"), inline=True)
    embed.add_field(name="提案頻道", value=f"#{entry.get('channel_name', '?')}", inline=True)
    embed.add_field(name="提案時間", value=entry.get("date", "?"), inline=True)
    embed.add_field(name="摘要", value=entry.get("summary", "")[:1024], inline=False)
    embed.add_field(name="原文連結", value=entry.get("message_url", "(無)"), inline=False)
    embed.add_field(name="提案 ID", value=proposal_id, inline=False)
    embed.set_footer(text="請管理員點擊下方按鈕受理或駁回此提案")

    try:
        sent = await notify_ch.send(embed=embed, view=ProposalReviewView(proposal_id))
        entry["notify_message_id"] = str(sent.id)
        entry["notify_channel_id"] = str(notify_ch.id)
        save_proposals()
        print(f"🔄 已重發提案審核面板（{proposal_id}）")
    except Exception as e:
        print(f"⚠️ 重發提案面板失敗（{proposal_id}）：{e}")


async def _resend_application_notify_panel(entry: dict):
    """重發一則入盟申請審核面板（審核通過/退回按鈕），並標記舊面板已失效。"""
    app_id = entry["id"]
    system_type = entry.get("system_type")
    if system_type == "council":
        notify_ch_id = entry.get("notify_channel_id") or application_settings.get("council_channel")
        notify_title = "📝 新入盟申請（理事國審核・重啟後重發）"
        notify_footer = "請理事國點擊下方按鈕審核通過或退回此申請"
        notify_color = discord.Color.dark_gold()
        reviewer_label = "理事國"
    else:
        notify_ch_id = entry.get("notify_channel_id") or application_settings.get("secretariat_channel")
        notify_title = "📝 新入盟申請（重啟後重發）"
        notify_footer = "請管理員點擊下方按鈕審核通過或退回此申請"
        notify_color = discord.Color.gold()
        reviewer_label = "秘書處"

    if not notify_ch_id:
        return
    notify_ch = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(notify_ch_id))
        if ch:
            notify_ch = ch
            break
    if not notify_ch:
        return

    old_msg_id = entry.get("notify_message_id")
    if old_msg_id:
        try:
            old_msg = await notify_ch.fetch_message(int(old_msg_id))
            if old_msg.components:
                old_embed = old_msg.embeds[0] if old_msg.embeds else None
                if old_embed:
                    old_embed.set_footer(text="⚠️ 此面板因機器人重啟失效，請使用下方新面板")
                    await old_msg.edit(embed=old_embed, view=None)
                else:
                    await old_msg.edit(view=None)
        except Exception:
            pass

    notify_embed = discord.Embed(
        title=notify_title,
        color=notify_color,
        timestamp=discord.utils.utcnow(),
    )
    notify_embed.add_field(name="申請人", value=entry.get("applicant_name", "?"), inline=True)
    notify_embed.add_field(name="申請頻道", value=f"#{entry.get('channel_name', '?')}", inline=True)
    notify_embed.add_field(name="申請時間", value=entry.get("date", "?"), inline=True)
    if entry.get("applicant_nation"):
        notify_embed.add_field(name="申請國家", value=entry["applicant_nation"], inline=True)
    notify_embed.add_field(name="欄位檢查", value="✅ 全部必填欄位齊全（含國旗圖片）", inline=False)
    if entry.get("flag_image_url"):
        notify_embed.set_thumbnail(url=entry["flag_image_url"])
    notify_embed.add_field(name="原文連結", value=entry.get("message_url", "(無)"), inline=False)
    notify_embed.add_field(name="申請 ID", value=entry["id"], inline=False)
    notify_embed.set_footer(text=notify_footer)

    try:
        sent = await notify_ch.send(embed=notify_embed, view=ApplicationReviewView(app_id))
        entry["notify_message_id"] = str(sent.id)
        entry["notify_channel_id"] = str(notify_ch.id)
        save_applications()
        print(f"🔄 已重發入盟審核面板至{reviewer_label} #{notify_ch.name}（{app_id}）")
    except Exception as e:
        print(f"⚠️ 重發入盟審核面板失敗（{app_id}）：{e}")


async def _resend_application_ack_panel(entry: dict):
    """重發一則入盟申請「待補齊欄位/國旗」面板，並標記舊面板已失效。"""
    app_id = entry["id"]
    ack_ch_id = entry.get("ack_channel_id") or entry.get("thread_id") or entry.get("channel_id")
    if not ack_ch_id:
        return
    ack_ch = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(ack_ch_id))
        if not ch:
            try:
                ch = await guild.fetch_channel(int(ack_ch_id))
            except Exception:
                ch = None
        if ch:
            ack_ch = ch
            break
    if not ack_ch:
        return

    old_msg_id = entry.get("ack_message_id")
    if old_msg_id:
        try:
            old_msg = await ack_ch.fetch_message(int(old_msg_id))
            if old_msg.components:
                old_embed = old_msg.embeds[0] if old_msg.embeds else None
                if old_embed:
                    old_embed.set_footer(text="⚠️ 此面板因機器人重啟失效，請使用下方新面板")
                    await old_msg.edit(embed=old_embed, view=None)
                else:
                    await old_msg.edit(view=None)
        except Exception:
            pass

    missing_fields = entry.get("missing_fields", [])
    fields_text = "\n".join(f"❌ {f}" for f in missing_fields)
    ack_desc = (
        f"📝 已收到入盟申請，但以下欄位仍需補齊：\n\n"
        f"{fields_text}\n\n"
        f"請**編輯原貼文**補齊上述欄位，系統會自動重新檢查。\n"
        f"如果缺少國旗圖片，可以點擊下方按鈕單獨補上。\n\n"
        f"（此面板為機器人重啟後重發）"
    )
    ack_embed = discord.Embed(
        title="⚠️ 入盟申請待補齊",
        description=ack_desc,
        color=discord.Color.orange(),
    )
    if entry.get("flag_image_url"):
        ack_embed.set_thumbnail(url=entry["flag_image_url"])
    ack_embed.set_footer(text=f"申請 ID：{app_id}")

    view = ApplicationFlagUploadView(app_id) if "國旗" in str(missing_fields) else None
    mention = f"<@{entry.get('applicant_id')}>" if entry.get("applicant_id") else None
    try:
        sent = await ack_ch.send(content=mention, embed=ack_embed, view=view)
        entry["ack_message_id"] = str(sent.id)
        entry["ack_channel_id"] = str(ack_ch.id)
        save_applications()
        print(f"🔄 已重發入盟補件面板（{app_id}）")
    except Exception as e:
        print(f"⚠️ 重發入盟補件面板失敗（{app_id}）：{e}")


async def handle_bot_ready():
    """在 on_ready 中呼叫（僅執行一次）：偵測未結案的提案/申請，
    重發一份全新、按鈕仍可用的審核面板。舊面板因機器人重啟（custom_id
    未持久化跨 process）而失效，會被編輯加註警語並移除按鈕，避免混淆。
    """
    global _startup_panel_refresh_done
    if _startup_panel_refresh_done:
        return
    _startup_panel_refresh_done = True

    await asyncio.sleep(5)  # 等 bot 完全連線、guild/channel cache 建立完成

    refreshed_proposals = 0
    refreshed_apps_notify = 0
    refreshed_apps_ack = 0

    for entry in list(_proposals.get("entries", [])):
        if entry.get("status") != "pending":
            continue
        try:
            await _resend_proposal_panel(entry)
            refreshed_proposals += 1
        except Exception as e:
            print(f"⚠️ 重發提案面板時發生例外（{entry.get('id')}）：{e}")

    for entry in list(_applications.get("entries", [])):
        if entry.get("status") != "pending":
            continue
        if entry.get("secretariat_notified"):
            try:
                await _resend_application_notify_panel(entry)
                refreshed_apps_notify += 1
            except Exception as e:
                print(f"⚠️ 重發入盟審核面板時發生例外（{entry.get('id')}）：{e}")
        elif entry.get("missing_fields"):
            try:
                await _resend_application_ack_panel(entry)
                refreshed_apps_ack += 1
            except Exception as e:
                print(f"⚠️ 重發入盟補件面板時發生例外（{entry.get('id')}）：{e}")

    if refreshed_proposals or refreshed_apps_notify or refreshed_apps_ack:
        print(f"🔄 重啟面板重發完成：提案 {refreshed_proposals} 筆、入盟審核 {refreshed_apps_notify} 筆、入盟補件 {refreshed_apps_ack} 筆")
    else:
        print("ℹ️ 沒有待審提案/申請需要重發面板")


# ─── 啟動時載入資料 ──────────────────────────────────────────────────────────
load_proposal_settings()
load_proposals()
load_application_settings()
load_applications()

# ─── 註冊指令群組 ─────────────────────────────────────────────────────────────
# 這些會透過 exec 的全域命名空間被主程式看見
ProposalGroup_instance = ProposalGroup()
SystemGroup_instance = SystemGroup()
