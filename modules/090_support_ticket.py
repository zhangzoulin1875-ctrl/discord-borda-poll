# ═════════════════════════════════════════════════════════════════
# 客服單系統（Support Ticket System）
# ═════════════════════════════════════════════════════════════════
# 固定頻道放一個持久面板（📩 開啟客服單按鈕），點擊後跳出 Modal 讓使用者
# 填寫「問題主旨／詢問內容／備註（可選）」，送出後自動建立一個僅
# 【開單者 + 管理員／客服身分組】可見的專屬頻道。
#
# 面板本身若因訊息被刪除、機器人重啟等原因失效，handle_bot_ready 會自動
# 偵測並補發一份全新可用的面板（沿用審核面板既有的重發慣例）。
#
# 安全規則（延續既有教訓）：面板按鈕是公開頻道訊息上的按鈕，開的 Modal
# 提交時絕對不能對原面板呼叫 edit_message()，一律用 ephemeral
# send_message/followup 回應。客服頻道內「結束客服單」按鈕則是編輯它
# 自己所附著的那則訊息（同一則訊息），這是安全、慣用的用法。

import re
import time
import json

# ─── 設定 ──────────────────────────────────────────────────────────────────
ticket_settings = {
    "enabled": False,
    "panel_channel_id": None,
    "panel_message_id": None,
    "category_id": None,       # 客服頻道要建立在哪個分類下（選填）
    "support_role_id": None,   # 客服/管理身分組（選填，沒設就只有 is_admin 能看到）
    "next_ticket_number": 1,
}

_tickets = {"entries": []}
_ticket_startup_refresh_done = False


def load_ticket_settings():
    global ticket_settings
    loaded = load_json("ticket_settings.json", {})
    if loaded:
        for key in ticket_settings:
            if key in loaded:
                ticket_settings[key] = loaded[key]
    print(f"🎫 客服系統設定已載入：{'啟用' if ticket_settings.get('enabled') else '停用'}")


def save_ticket_settings():
    save_json("ticket_settings.json", ticket_settings)


def load_tickets():
    global _tickets
    loaded = load_json("tickets.json", {"entries": []})
    _tickets = loaded if isinstance(loaded, dict) else {"entries": loaded if isinstance(loaded, list) else []}
    print(f"🎫 客服單記錄已載入：{len(_tickets.get('entries', []))} 筆")


def save_tickets():
    save_json("tickets.json", _tickets)


async def _persist_tickets_now():
    """立即寫入本地檔案並等待 GitHub 推送完成。

    客服單建立/關閉/刪除這幾個關鍵時刻專用——一般 save_json() 只是把 GitHub
    推送丟到背景（fire-and-forget），如果 Render 剛好在這幾秒內重啟（例如
    我們自己 push 新程式碼觸發重新部署），背景推送可能還沒送到 GitHub 就被
    砍掉，新程序拉到的還是舊資料，之後開單者按「結束」就會出現「找不到此
    客服單記錄」。这裡改成寫本地檔之後直接 await 推送完成，確保回覆使用者
    「已建立/已結束」的當下，GitHub 上的資料已經是最新的。
    """
    path = DATA_DIR / "tickets.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_tickets, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    try:
        await github_push_json("tickets.json", _tickets)
    except Exception as e:
        print(f"⚠️ 客服單資料 GitHub 同步失敗：{e}")


def _find_ticket_by_channel(channel_id):
    for t in _tickets.get("entries", []):
        if str(t.get("channel_id")) == str(channel_id):
            return t
    return None


def _reconstruct_entry_from_channel(channel):
    """救援機制：當客服單記錄意外遺失（例如 GitHub 推送被重啟打斷）時，
    管理員關閉客服單不該被硬擋死——從頻道名稱/topic/權限覆寫反推出一筆
    最小可用的記錄，讓關閉流程可以繼續走完。"""
    number = 0
    m = re.match(r"^(?:closed-)?ticket-(\d+)-", channel.name or "")
    if m:
        number = int(m.group(1))

    subject = channel.name or "（記錄遺失，已重建）"
    topic = channel.topic or ""
    m2 = re.search(r"主旨：(.+)$", topic)
    if m2:
        subject = m2.group(1).strip()

    opener_id = None
    opener_name = "未知使用者"
    try:
        bot_id = bot.user.id if bot.user else None
        for target, overwrite in getattr(channel, "overwrites", {}).items():
            if not isinstance(target, discord.Member):
                continue
            if bot_id and target.id == bot_id:
                continue
            if hasattr(target, "guild_permissions") and target.guild_permissions.administrator:
                continue
            if overwrite.send_messages is True:
                opener_id = str(target.id)
                opener_name = target.display_name
                break
    except Exception as e:
        print(f"⚠️ 重建客服單記錄時解析權限失敗：{e}")

    entry = {
        "id": f"tk_recovered_{channel.id}",
        "number": number,
        "guild_id": str(channel.guild.id) if getattr(channel, "guild", None) else "",
        "channel_id": str(channel.id),
        "opener_id": opener_id or "0",
        "opener_name": opener_name,
        "subject": subject,
        "content": "（原始記錄遺失，已從頻道資訊自動重建）",
        "note": "",
        "status": "open",
        "created_at": now_str(),
        "closed_at": None,
        "closed_by": None,
        "recovered": True,
    }
    _tickets["entries"].append(entry)
    return entry


def _find_open_ticket_by_user(user_id):
    """檢查某使用者是否已有未關閉的客服單，回傳該客服單 entry 或 None。"""
    uid = str(user_id)
    for t in _tickets.get("entries", []):
        if str(t.get("opener_id")) == uid and t.get("status") == "open":
            return t
    return None


def _slugify_subject(text, max_len=40):
    text = (text or "").strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\-]", "", text)
    text = text.strip("-") or "客服單"
    return text[:max_len]


async def _get_member_safe(guild, user_id):
    m = guild.get_member(user_id)
    if m:
        return m
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════
# Embed 建構
# ═════════════════════════════════════════════════════════════════

def _build_ticket_panel_embed():
    embed = discord.Embed(
        title="🎫 客服中心",
        description=(
            "有問題想詢問秘書處嗎？\n"
            "點擊下方「📩 開啟客服單」按鈕，填寫問題主旨與詢問內容後，\n"
            "會自動為你建立一個專屬頻道，**僅你本人與管理員／客服人員可見**。"
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="ICEA 客服系統")
    return embed


def _build_ticket_embed(entry):
    embed = discord.Embed(
        title=f"🎫 客服單 #{entry['number']}",
        description=f"**問題主旨**\n{entry['subject']}",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="📝 詢問內容", value=(entry.get("content") or "（無）")[:1024], inline=False)
    if entry.get("note"):
        embed.add_field(name="🗒️ 備註", value=entry["note"][:1024], inline=False)
    embed.add_field(
        name="ℹ️ 資訊",
        value=f"開單者：<@{entry['opener_id']}>\n建立時間：{entry['created_at']}\n狀態：🟢 進行中",
        inline=False,
    )
    embed.set_footer(text=f"客服單 ID：{entry['id']}")
    return embed


def _build_ticket_closed_embed(entry):
    embed = discord.Embed(
        title=f"🎫 客服單 #{entry['number']}（已結束）",
        description=f"**問題主旨**\n{entry['subject']}",
        color=discord.Color.dark_gray(),
    )
    embed.add_field(name="📝 詢問內容", value=(entry.get("content") or "（無）")[:1024], inline=False)
    if entry.get("note"):
        embed.add_field(name="🗒️ 備註", value=entry["note"][:1024], inline=False)
    embed.add_field(
        name="ℹ️ 資訊",
        value=(
            f"開單者：<@{entry['opener_id']}>\n建立時間：{entry['created_at']}\n"
            f"結束時間：{entry.get('closed_at', '?')}\n"
            f"結束者：{entry.get('closed_by', '?')}\n狀態：🔴 已結束"
        ),
        inline=False,
    )
    embed.set_footer(text=f"客服單 ID：{entry['id']}")
    return embed


# ═════════════════════════════════════════════════════════════════
# 建立客服頻道
# ═════════════════════════════════════════════════════════════════

async def _create_ticket_channel(guild: discord.Guild, opener, subject: str, content: str, note: str):
    """建立一個僅開單者與管理員/客服身分組可見的專屬頻道，回傳 (channel, ticket_entry)。"""
    number = ticket_settings.get("next_ticket_number", 1)
    slug = _slugify_subject(subject)
    channel_name = f"ticket-{number:04d}-{slug}"[:100]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        opener: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, attach_files=True
        ),
    }

    if bot.user:
        bot_member = await _get_member_safe(guild, bot.user.id)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, manage_permissions=True
            )

    support_role_id = ticket_settings.get("support_role_id")
    if support_role_id:
        role = guild.get_role(int(support_role_id))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    # 機器人擁有者保底可見（避免擁有者在此伺服器沒有 Administrator 權限時看不到）
    owner_member = await _get_member_safe(guild, OWNER_ID)
    if owner_member:
        overwrites[owner_member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )

    category = None
    cat_id = ticket_settings.get("category_id")
    if cat_id:
        cat = guild.get_channel(int(cat_id))
        if isinstance(cat, discord.CategoryChannel):
            category = cat

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"客服單 #{number}｜開單者：{getattr(opener, 'display_name', opener)}｜主旨：{subject}"[:1024],
        reason=f"客服單 #{number} by {opener}",
    )

    ticket_settings["next_ticket_number"] = number + 1
    save_ticket_settings()

    entry = {
        "id": f"tk{int(time.time())}{number}",
        "number": number,
        "guild_id": str(guild.id),
        "channel_id": str(channel.id),
        "opener_id": str(opener.id),
        "opener_name": getattr(opener, "display_name", str(opener)),
        "subject": subject,
        "content": content,
        "note": note or "",
        "status": "open",
        "created_at": now_str(),
        "closed_at": None,
        "closed_by": None,
    }
    _tickets["entries"].append(entry)
    await _persist_tickets_now()

    return channel, entry


# ═════════════════════════════════════════════════════════════════
# 持久化 View：客服頻道內的「結束客服單」/「刪除頻道」
# ═════════════════════════════════════════════════════════════════

class TicketClosedView(discord.ui.View):
    """已結束客服單的面板：僅管理員可永久刪除頻道（需二次確認）。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🗑️ 永久刪除此頻道", style=discord.ButtonStyle.danger, custom_id="icea_ticket_delete_btn")
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此操作僅限管理員使用。", ephemeral=True)
            return

        confirm_view = discord.ui.View(timeout=30)

        async def confirm_delete(confirm_ia: discord.Interaction):
            if not is_admin(confirm_ia):
                await confirm_ia.response.send_message("❌ 此操作僅限管理員使用。", ephemeral=True)
                return
            entry = _find_ticket_by_channel(confirm_ia.channel.id)
            if entry:
                entry["deleted_at"] = now_str()
                await _persist_tickets_now()
            await confirm_ia.response.send_message("🗑️ 頻道即將刪除…", ephemeral=True)
            try:
                await confirm_ia.channel.delete(reason=f"客服單刪除 by {confirm_ia.user}")
            except Exception as e:
                print(f"⚠️ 刪除客服頻道失敗：{e}")

        async def cancel_delete(cancel_ia: discord.Interaction):
            await cancel_ia.response.send_message("已取消刪除。", ephemeral=True)

        yes_btn = discord.ui.Button(label="✅ 確認刪除", style=discord.ButtonStyle.danger)
        no_btn = discord.ui.Button(label="取消", style=discord.ButtonStyle.secondary)
        yes_btn.callback = confirm_delete
        no_btn.callback = cancel_delete
        confirm_view.add_item(yes_btn)
        confirm_view.add_item(no_btn)

        await interaction.response.send_message(
            "⚠️ 確定要永久刪除此客服頻道嗎？此動作無法復原。", view=confirm_view, ephemeral=True
        )


class TicketCloseView(discord.ui.View):
    """客服頻道內的持久面板：開單者或管理員可結束客服單。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 結束客服單", style=discord.ButtonStyle.danger, custom_id="icea_ticket_close_btn")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = _find_ticket_by_channel(interaction.channel.id)
        if not entry:
            if not is_admin(interaction):
                await interaction.response.send_message(
                    "❌ 找不到此客服單記錄（可能是機器人重啟時資料還沒同步完成），請聯絡管理員手動處理。",
                    ephemeral=True,
                )
                return
            # 管理員：記錄遺失也不硬擋，從頻道資訊重建一筆最小記錄後繼續關閉流程
            entry = _reconstruct_entry_from_channel(interaction.channel)
            await _persist_tickets_now()
            print(f"⚠️ 客服單記錄遺失，已由管理員 {interaction.user} 強制重建並關閉：頻道 {interaction.channel.id}")
        if entry.get("status") == "closed":
            await interaction.response.send_message("⚠️ 此客服單已經結束。", ephemeral=True)
            return

        is_opener = str(interaction.user.id) == str(entry.get("opener_id"))
        if not is_opener and not is_admin(interaction):
            await interaction.response.send_message("❌ 只有開單者或管理員可以結束客服單。", ephemeral=True)
            return

        entry["status"] = "closed"
        entry["closed_at"] = now_str()
        entry["closed_by"] = interaction.user.display_name
        await _persist_tickets_now()

        # 開單者移除發言權限，保留可讀以留存紀錄
        try:
            opener_member = await _get_member_safe(interaction.guild, int(entry["opener_id"]))
            if opener_member:
                await interaction.channel.set_permissions(
                    opener_member, view_channel=True, send_messages=False, read_message_history=True
                )
        except Exception as e:
            print(f"⚠️ 客服單關閉時調整權限失敗：{e}")

        # 頻道改名標示已結束
        try:
            if not interaction.channel.name.startswith("closed-"):
                await interaction.channel.edit(name=f"closed-{interaction.channel.name}"[:100])
        except Exception as e:
            print(f"⚠️ 客服單關閉時改名失敗：{e}")

        # 這是編輯「按鈕自己所附著的那則訊息」（客服頻道內的面板本身），
        # 不是公開主面板，符合安全的 edit_message 用法。
        await interaction.response.edit_message(embed=_build_ticket_closed_embed(entry), view=TicketClosedView())
        try:
            await interaction.channel.send(
                f"🔒 此客服單已由 {interaction.user.mention} 結束。管理員可用上方按鈕永久刪除此頻道。"
            )
        except Exception:
            pass


bot.add_view(TicketCloseView())
bot.add_view(TicketClosedView())


# ═════════════════════════════════════════════════════════════════
# 持久化 View：固定頻道的開單面板
# ═════════════════════════════════════════════════════════════════

class TicketPanelView(discord.ui.View):
    """固定頻道的客服單開單面板（持久化）。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 開啟客服單", style=discord.ButtonStyle.primary, custom_id="icea_ticket_open_btn")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not ticket_settings.get("enabled"):
            await interaction.response.send_message("❌ 客服系統目前未啟用，請聯絡管理員。", ephemeral=True)
            return

        # 一人一單限制：直接在點按鈕時就檢查，避免填完整個 Modal 才被擋
        existing_open = _find_open_ticket_by_user(interaction.user.id)
        if existing_open:
            ch_id = existing_open.get("channel_id", "")
            ch_mention = f"<#{ch_id}>" if ch_id else "（頻道已刪除）"
            await interaction.response.send_message(
                f"⚠️ 你已有一張進行中的客服單：{ch_mention}\n"
                f"請先結束該客服單後再開新單。\n"
                f"客服單 #{existing_open.get('number')} — {existing_open.get('subject', '')}",
                ephemeral=True,
            )
            return

        modal = discord.ui.Modal(title="📩 開啟客服單")
        modal.add_item(discord.ui.TextInput(
            label="問題主旨",
            placeholder="簡短描述你的問題",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
            custom_id="ticket_subject",
        ))
        modal.add_item(discord.ui.TextInput(
            label="詢問內容",
            placeholder="請詳細描述你的問題或需求",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            custom_id="ticket_content",
        ))
        modal.add_item(discord.ui.TextInput(
            label="備註（可選）",
            placeholder="其他補充資訊",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
            custom_id="ticket_note",
        ))

        async def on_modal_submit(modal_ia: discord.Interaction):
            values = {}
            for row in modal_ia.data.get("components", []):
                for comp in row.get("components", []):
                    values[comp.get("custom_id")] = comp.get("value", "")
            subject = values.get("ticket_subject", "").strip()
            content = values.get("ticket_content", "").strip()
            note = values.get("ticket_note", "").strip()

            if not subject or not content:
                # 公開面板按鈕開的 Modal → 提交必須用 ephemeral send_message，絕對不能 edit_message 原面板
                await modal_ia.response.send_message("❌ 問題主旨與詢問內容為必填。", ephemeral=True)
                return

            await modal_ia.response.defer(ephemeral=True, thinking=True)

            # 一人一單限制：檢查是否已有未關閉的客服單
            existing_open = _find_open_ticket_by_user(modal_ia.user.id)
            if existing_open:
                ch_id = existing_open.get("channel_id", "")
                ch_mention = f"<#{ch_id}>" if ch_id else "（頻道已刪除）"
                await modal_ia.followup.send(
                    f"⚠️ 你已有一張進行中的客服單：{ch_mention}\n"
                    f"請先結束該客服單後再開新單。\n"
                    f"客服單 #{existing_open.get('number')} — {existing_open.get('subject', '')}",
                    ephemeral=True,
                )
                return

            try:
                channel, entry = await _create_ticket_channel(
                    modal_ia.guild, modal_ia.user, subject, content, note
                )
            except discord.Forbidden:
                await modal_ia.followup.send("❌ 建立頻道失敗：機器人缺少「管理頻道」權限，請聯絡管理員。", ephemeral=True)
                return
            except Exception as e:
                print(f"⚠️ 建立客服頻道失敗：{e}")
                await modal_ia.followup.send(f"❌ 建立客服單時發生錯誤：{e}", ephemeral=True)
                return

            try:
                await channel.send(
                    content=f"{modal_ia.user.mention} 你的客服單已建立！請在此頻道等候管理員回覆。",
                    embed=_build_ticket_embed(entry),
                    view=TicketCloseView(),
                )
            except Exception as e:
                print(f"⚠️ 傳送客服單初始訊息失敗：{e}")

            await modal_ia.followup.send(f"✅ 客服單已建立：{channel.mention}", ephemeral=True)

        modal.on_submit = on_modal_submit
        await interaction.response.send_modal(modal)


bot.add_view(TicketPanelView())


# ═════════════════════════════════════════════════════════════════
# 指令：/ticket setup / disable / list
# ═════════════════════════════════════════════════════════════════

class TicketGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ticket", description="客服單系統設定與管理")

    @app_commands.command(name="setup", description="設定客服單固定面板頻道（管理員專用）")
    @app_commands.describe(
        channel="要放置客服單開單面板的頻道",
        support_role="客服/管理身分組（選填，該身分組成員可看到所有客服頻道）",
        category="客服頻道要建立在哪個分類下（選填）",
    )
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel,
                     support_role: discord.Role = None, category: discord.CategoryChannel = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        ticket_settings["enabled"] = True
        ticket_settings["panel_channel_id"] = str(channel.id)
        if support_role:
            ticket_settings["support_role_id"] = str(support_role.id)
        if category:
            ticket_settings["category_id"] = str(category.id)

        # 刪除舊面板（如果有）
        old_msg_id = ticket_settings.get("panel_message_id")
        if old_msg_id:
            try:
                old_msg = await channel.fetch_message(int(old_msg_id))
                await old_msg.delete()
            except Exception:
                pass

        try:
            new_msg = await channel.send(embed=_build_ticket_panel_embed(), view=TicketPanelView())
            ticket_settings["panel_message_id"] = str(new_msg.id)
        except Exception as e:
            await interaction.followup.send(f"❌ 發送面板失敗：{e}", ephemeral=True)
            return

        save_ticket_settings()
        msg = f"✅ 客服系統已設定完成！面板已發送至 {channel.mention}"
        if support_role:
            msg += f"\n客服身分組：{support_role.mention}"
        if category:
            msg += f"\n客服頻道分類：{category.name}"
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="disable", description="停用客服單開單功能（不影響現有客服頻道）（管理員專用）")
    async def disable(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        ticket_settings["enabled"] = False
        save_ticket_settings()
        await interaction.response.send_message("✅ 已停用客服單開單功能（現有頻道不受影響）。", ephemeral=True)

    @app_commands.command(name="list", description="查看客服單記錄（管理員專用）")
    @app_commands.describe(status="篩選狀態")
    @app_commands.choices(status=[
        app_commands.Choice(name="進行中", value="open"),
        app_commands.Choice(name="已結束", value="closed"),
        app_commands.Choice(name="全部", value="all"),
    ])
    async def list_tickets(self, interaction: discord.Interaction, status: app_commands.Choice[str] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        entries = _tickets.get("entries", [])
        filter_status = status.value if status else "all"
        if filter_status != "all":
            entries = [e for e in entries if e.get("status") == filter_status]
        if not entries:
            await interaction.response.send_message("📋 沒有符合條件的客服單記錄。", ephemeral=True)
            return
        recent = sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)[:15]
        lines = []
        for e in recent:
            emoji = "🟢" if e.get("status") == "open" else "🔴"
            lines.append(
                f"{emoji} `#{e.get('number')}` **{e.get('subject', '')[:40]}**\n"
                f"　開單者：{e.get('opener_name', '?')} | <#{e.get('channel_id')}>\n"
                f"　建立：{e.get('created_at', '?')}"
            )
        await interaction.response.send_message(
            f"🎫 **客服單記錄（{len(recent)}/{len(entries)} 筆）**\n\n" + "\n\n".join(lines),
            ephemeral=True,
        )


# ═════════════════════════════════════════════════════════════════
# 啟動後掛鉤：偵測面板是否失效，自動補發
# ═════════════════════════════════════════════════════════════════

async def handle_ticket_bot_ready():
    """在 on_ready 時檢查客服單面板是否還存在，失效（被刪除/找不到）就自動重發一份新的。"""
    global _ticket_startup_refresh_done
    if _ticket_startup_refresh_done:
        return
    _ticket_startup_refresh_done = True

    await asyncio.sleep(6)  # 等 bot 完全連線、guild/channel cache 建立完成

    if not ticket_settings.get("enabled") or not ticket_settings.get("panel_channel_id"):
        return

    try:
        channel = None
        for guild in bot.guilds:
            ch = guild.get_channel(int(ticket_settings["panel_channel_id"]))
            if ch:
                channel = ch
                break
        if not channel:
            print("⚠️ 客服單面板頻道已不存在，略過自動重發")
            return

        need_resend = True
        msg_id = ticket_settings.get("panel_message_id")
        if msg_id:
            try:
                await channel.fetch_message(int(msg_id))
                need_resend = False  # 訊息仍存在，持久化 View 已透過 bot.add_view() 恢復互動性
            except Exception:
                need_resend = True

        if need_resend:
            new_msg = await channel.send(embed=_build_ticket_panel_embed(), view=TicketPanelView())
            ticket_settings["panel_message_id"] = str(new_msg.id)
            save_ticket_settings()
            print(f"🔄 客服單面板已重發至 #{channel.name}")
        else:
            print("ℹ️ 客服單面板仍存在，跳過重發")
    except Exception as e:
        print(f"⚠️ 客服單面板重啟檢查失敗：{e}")


_bot_ready_hooks.append(handle_ticket_bot_ready)


# ─── 啟動時載入資料 ──────────────────────────────────────────────────────────
load_ticket_settings()
load_tickets()

# ─── 註冊指令群組（透過 exec 的全域命名空間被主程式看見）────────────────────
TicketGroup_instance = TicketGroup()
