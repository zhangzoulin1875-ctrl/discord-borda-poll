#!/usr/bin/env python3
"""Replace the sprawling `/system` command group (13 subcommands) with a
single unified `/system_manager` owner-only interactive panel using
discord.ui.ChannelSelect / RoleSelect components.
"""

with open("modules/080_proposal_application.py", "r", encoding="utf-8") as f:
    content = f.read()

with open("/tmp/old_systemgroup_block.txt", "r", encoding="utf-8") as f:
    old_block = f.read()

assert old_block in content, "old SystemGroup block not found verbatim"
assert content.count(old_block) == 1, f"not unique: {content.count(old_block)}"

new_block = '''# ═════════════════════════════════════════════════════════════════
# 系統管理面板（/system_manager）— 機器人擁有者專用互動面板
# 取代原本一大串 /system xxx 零散指令，全部收進一個可點擊導覽的面板。
# ═════════════════════════════════════════════════════════════════

def _mention_list(ids, kind="channel"):
    if not ids:
        return "❌ 未設定"
    tag = "#" if kind == "channel" else "@&"
    return "、".join(f"<{tag}{i}>" for i in ids)


def _mention_one(cid, kind="channel"):
    if not cid:
        return "❌ 未設定"
    tag = "#" if kind == "channel" else "@&"
    return f"<{tag}{cid}>"


def _proposal_status_lines():
    enabled = proposal_settings.get("enabled", False)
    return [
        f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用'}",
        f"提案區頻道：{_mention_list(proposal_settings.get('proposal_channels', []))}",
        f"秘書處通知頻道：{_mention_one(proposal_settings.get('secretariat_channel'))}",
        f"審核身分組：{_mention_one(proposal_settings.get('review_role_id'), 'role') if proposal_settings.get('review_role_id') else '管理員限定（未設定）'}",
        f"已收錄提案：{len(_proposals.get('entries', []))} 筆",
    ]


def _application_status_lines():
    enabled = application_settings.get("enabled", False)
    sec_role = application_settings.get("secretariat_review_role_id")
    council_role = application_settings.get("council_review_role_id")
    return [
        f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用'}",
        f"秘書處申請區頻道：{_mention_list(application_settings.get('application_channels', []))}",
        f"理事國申請區頻道：{_mention_list(application_settings.get('council_channels', []))}",
        f"秘書處通知頻道：{_mention_one(application_settings.get('secretariat_channel'))}",
        f"理事國通知頻道：{_mention_one(application_settings.get('council_channel'))}",
        f"秘書處審核身分組：{_mention_one(sec_role, 'role') if sec_role else '管理員限定（未設定）'}",
        f"理事國審核身分組：{_mention_one(council_role, 'role') if council_role else '管理員限定（未設定）'}",
        f"已收錄申請：{len(_applications.get('entries', []))} 筆",
    ]


def _format_proposal_list_text(filter_status="all"):
    entries = _proposals.get("entries", [])
    if filter_status != "all":
        entries = [e for e in entries if e.get("status") == filter_status]
    if not entries:
        return "📋 沒有符合條件的提案記錄。"
    recent = sorted(entries, key=lambda e: e.get("_ts", 0), reverse=True)[:10]
    lines = []
    for e in recent:
        emoji = {"pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(e.get("status", ""), "?")
        line = (
            f"{emoji} **{e.get('proposal_type', '?')}** | {e.get('proposer_name', '?')} | {e.get('date', '?')}\\n"
            f"　摘要：{e.get('summary', '')[:60]} | ID: `{e.get('id', '')}`"
        )
        lines.append(line)
    text = f"📋 **提案記錄（{len(recent)}/{len(entries)} 筆）**\\n\\n" + "\\n\\n".join(lines)
    return text[:1900] + ("\\n…（僅顯示部分）" if len(text) > 1900 else "")


def _format_application_list_text(filter_status="all"):
    entries = _applications.get("entries", [])
    if filter_status != "all":
        entries = [e for e in entries if e.get("status") == filter_status]
    if not entries:
        return "📝 沒有符合條件的入盟申請記錄。"
    recent = sorted(entries, key=lambda e: e.get("_ts", 0), reverse=True)[:10]
    lines = []
    for e in recent:
        emoji = {"pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(e.get("status", ""), "?")
        sys_label = "理事國" if e.get("system_type") == "council" else "秘書處"
        line = (
            f"{emoji} **{e.get('applicant_nation') or e.get('applicant_name', '?')}** | {sys_label} | {e.get('date', '?')}\\n"
            f"　狀態：{e.get('status', '?')} | ID: `{e.get('id', '')}`"
        )
        if e.get("reject_reason"):
            line += f"\\n　退回原因：{e['reject_reason'][:60]}"
        lines.append(line)
    text = f"📝 **入盟申請記錄（{len(recent)}/{len(entries)} 筆）**\\n\\n" + "\\n\\n".join(lines)
    return text[:1900] + ("\\n…（僅顯示部分）" if len(text) > 1900 else "")


async def _owner_guard(interaction: discord.Interaction) -> bool:
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 此面板僅限機器人擁有者使用。", ephemeral=True)
        return False
    return True


# ── 主選單 ──

def _build_system_manager_embed():
    embed = discord.Embed(
        title="⚙️ 系統管理面板",
        description="機器人擁有者專用。點下方按鈕進入對應設定分類。",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="📋 提案系統", value="\\n".join(_proposal_status_lines()), inline=False)
    embed.add_field(name="📝 入盟申請系統", value="\\n".join(_application_status_lines()), inline=False)
    embed.set_footer(text="ICEA official ・ 系統管理面板")
    return embed


def _system_manager_main_view():
    view = discord.ui.View(timeout=300)

    proposal_btn = discord.ui.Button(label="📋 提案系統設定", style=discord.ButtonStyle.primary, row=0)

    async def proposal_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await interaction.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

    proposal_btn.callback = proposal_cb
    view.add_item(proposal_btn)

    application_btn = discord.ui.Button(label="📝 入盟申請系統設定", style=discord.ButtonStyle.primary, row=0)

    async def application_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await interaction.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

    application_btn.callback = application_cb
    view.add_item(application_btn)

    refresh_btn = discord.ui.Button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=0)

    async def refresh_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await interaction.response.edit_message(embed=_build_system_manager_embed(), view=_system_manager_main_view())

    refresh_btn.callback = refresh_cb
    view.add_item(refresh_btn)

    return view


async def _back_to_main_menu(interaction: discord.Interaction):
    await interaction.response.edit_message(embed=_build_system_manager_embed(), view=_system_manager_main_view())


# ── 通用選取器（ChannelSelect / RoleSelect + 返回按鈕）──

def _build_channel_picker_view(*, get_current, on_save, channel_types, multi, back_to):
    view = discord.ui.View(timeout=300)
    current_ids = get_current()
    defaults = [discord.Object(id=int(cid)) for cid in current_ids] if current_ids else []

    select = discord.ui.ChannelSelect(
        channel_types=channel_types,
        min_values=0,
        max_values=25 if multi else 1,
        placeholder="選擇頻道…（全部取消＝清空設定）",
        default_values=defaults,
        row=0,
    )

    async def select_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        ids = [c.id for c in select.values]
        await on_save(interaction, ids)

    select.callback = select_cb
    view.add_item(select)

    back_btn = discord.ui.Button(label="⬅️ 返回", style=discord.ButtonStyle.secondary, row=1)

    async def back_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await back_to(interaction)

    back_btn.callback = back_cb
    view.add_item(back_btn)
    return view


def _build_role_picker_view(*, get_current, on_save, back_to):
    view = discord.ui.View(timeout=300)
    current_id = get_current()
    defaults = [discord.Object(id=int(current_id))] if current_id else []

    select = discord.ui.RoleSelect(
        min_values=0,
        max_values=1,
        placeholder="選擇審核身分組…（全部取消＝清除，改為管理員限定）",
        default_values=defaults,
        row=0,
    )

    async def select_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        role_id = str(select.values[0].id) if select.values else None
        await on_save(interaction, role_id)

    select.callback = select_cb
    view.add_item(select)

    back_btn = discord.ui.Button(label="⬅️ 返回", style=discord.ButtonStyle.secondary, row=1)

    async def back_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await back_to(interaction)

    back_btn.callback = back_cb
    view.add_item(back_btn)
    return view


# ── 提案系統子面板 ──

def _build_proposal_settings_embed():
    embed = discord.Embed(
        title="📋 提案系統設定",
        description="\\n".join(_proposal_status_lines()),
        color=discord.Color.gold(),
    )
    return embed


def _build_proposal_settings_view():
    view = discord.ui.View(timeout=300)

    enabled = proposal_settings.get("enabled", False)
    toggle_btn = discord.ui.Button(
        label="🔴 關閉系統" if enabled else "🟢 開啟系統",
        style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
        row=0,
    )

    async def toggle_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        proposal_settings["enabled"] = not proposal_settings.get("enabled", False)
        save_proposal_settings()
        await interaction.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

    toggle_btn.callback = toggle_cb
    view.add_item(toggle_btn)

    list_btn = discord.ui.Button(label="📜 查看提案記錄", style=discord.ButtonStyle.secondary, row=0)

    async def list_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await interaction.response.send_message(_format_proposal_list_text("all"), ephemeral=True)

    list_btn.callback = list_cb
    view.add_item(list_btn)

    back_btn = discord.ui.Button(label="⬅️ 返回主選單", style=discord.ButtonStyle.secondary, row=0)

    async def back_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await _back_to_main_menu(interaction)

    back_btn.callback = back_cb
    view.add_item(back_btn)

    ch_btn = discord.ui.Button(label="📌 提案區頻道", style=discord.ButtonStyle.primary, row=1)

    async def ch_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, ids):
            proposal_settings["proposal_channels"] = ids
            save_proposal_settings()
            await ia.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

        picker = _build_channel_picker_view(
            get_current=lambda: proposal_settings.get("proposal_channels", []),
            on_save=_save,
            channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
            multi=True,
            back_to=_back,
        )
        embed = discord.Embed(
            title="📌 設定提案區頻道",
            description="選擇要監控的提案區頻道（可多選文字/論壇頻道；重新選取即取代目前設定，全部取消＝清空）。",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    ch_btn.callback = ch_cb
    view.add_item(ch_btn)

    sec_btn = discord.ui.Button(label="📨 秘書處通知頻道", style=discord.ButtonStyle.primary, row=1)

    async def sec_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, ids):
            proposal_settings["secretariat_channel"] = ids[0] if ids else None
            save_proposal_settings()
            await ia.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

        current = proposal_settings.get("secretariat_channel")
        picker = _build_channel_picker_view(
            get_current=lambda: [current] if current else [],
            on_save=_save,
            channel_types=[discord.ChannelType.text],
            multi=False,
            back_to=_back,
        )
        embed = discord.Embed(
            title="📨 設定提案秘書處通知頻道",
            description="選擇一個文字頻道，接收提案受理/駁回通知。",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    sec_btn.callback = sec_cb
    view.add_item(sec_btn)

    role_btn = discord.ui.Button(label="🎭 審核身分組", style=discord.ButtonStyle.primary, row=1)

    async def role_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, role_id):
            proposal_settings["review_role_id"] = role_id
            save_proposal_settings()
            await ia.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_proposal_settings_embed(), view=_build_proposal_settings_view())

        picker = _build_role_picker_view(
            get_current=lambda: proposal_settings.get("review_role_id"),
            on_save=_save,
            back_to=_back,
        )
        embed = discord.Embed(
            title="🎭 設定提案審核身分組",
            description="選擇有權審核提案的身分組（全部取消＝清除，改為管理員限定）。",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    role_btn.callback = role_cb
    view.add_item(role_btn)

    return view


# ── 入盟申請系統子面板 ──

def _build_application_settings_embed():
    embed = discord.Embed(
        title="📝 入盟申請系統設定",
        description="\\n".join(_application_status_lines()),
        color=discord.Color.dark_gold(),
    )
    return embed


def _build_application_settings_view():
    view = discord.ui.View(timeout=300)

    enabled = application_settings.get("enabled", False)
    toggle_btn = discord.ui.Button(
        label="🔴 關閉系統" if enabled else "🟢 開啟系統",
        style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
        row=0,
    )

    async def toggle_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        application_settings["enabled"] = not application_settings.get("enabled", False)
        save_application_settings()
        await interaction.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

    toggle_btn.callback = toggle_cb
    view.add_item(toggle_btn)

    list_btn = discord.ui.Button(label="📜 查看申請記錄", style=discord.ButtonStyle.secondary, row=0)

    async def list_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await interaction.response.send_message(_format_application_list_text("all"), ephemeral=True)

    list_btn.callback = list_cb
    view.add_item(list_btn)

    back_btn = discord.ui.Button(label="⬅️ 返回主選單", style=discord.ButtonStyle.secondary, row=0)

    async def back_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return
        await _back_to_main_menu(interaction)

    back_btn.callback = back_cb
    view.add_item(back_btn)

    sec_ch_btn = discord.ui.Button(label="📌 秘書處申請區頻道", style=discord.ButtonStyle.primary, row=1)

    async def sec_ch_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, ids):
            application_settings["application_channels"] = ids
            save_application_settings()
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        picker = _build_channel_picker_view(
            get_current=lambda: application_settings.get("application_channels", []),
            on_save=_save,
            channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
            multi=True,
            back_to=_back,
        )
        embed = discord.Embed(
            title="📌 設定秘書處入盟申請區頻道",
            description="選擇要監控的入盟申請區頻道（可多選文字/論壇頻道；重新選取即取代目前設定，全部取消＝清空）。",
            color=discord.Color.dark_gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    sec_ch_btn.callback = sec_ch_cb
    view.add_item(sec_ch_btn)

    council_ch_btn = discord.ui.Button(label="📌 理事國申請區頻道", style=discord.ButtonStyle.primary, row=1)

    async def council_ch_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, ids):
            application_settings["council_channels"] = ids
            save_application_settings()
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        picker = _build_channel_picker_view(
            get_current=lambda: application_settings.get("council_channels", []),
            on_save=_save,
            channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
            multi=True,
            back_to=_back,
        )
        embed = discord.Embed(
            title="📌 設定理事國入盟申請區頻道",
            description="選擇要監控的理事國入盟申請區頻道（可多選文字/論壇頻道；重新選取即取代目前設定，全部取消＝清空）。",
            color=discord.Color.dark_gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    council_ch_btn.callback = council_ch_cb
    view.add_item(council_ch_btn)

    sec_notify_btn = discord.ui.Button(label="📨 秘書處通知頻道", style=discord.ButtonStyle.primary, row=2)

    async def sec_notify_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, ids):
            application_settings["secretariat_channel"] = ids[0] if ids else None
            save_application_settings()
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        current = application_settings.get("secretariat_channel")
        picker = _build_channel_picker_view(
            get_current=lambda: [current] if current else [],
            on_save=_save,
            channel_types=[discord.ChannelType.text],
            multi=False,
            back_to=_back,
        )
        embed = discord.Embed(
            title="📨 設定秘書處審核通知頻道",
            description="選擇一個文字頻道，接收秘書處入盟申請審核通知。",
            color=discord.Color.dark_gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    sec_notify_btn.callback = sec_notify_cb
    view.add_item(sec_notify_btn)

    council_notify_btn = discord.ui.Button(label="📨 理事國通知頻道", style=discord.ButtonStyle.primary, row=2)

    async def council_notify_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, ids):
            application_settings["council_channel"] = ids[0] if ids else None
            save_application_settings()
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        current = application_settings.get("council_channel")
        picker = _build_channel_picker_view(
            get_current=lambda: [current] if current else [],
            on_save=_save,
            channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
            multi=False,
            back_to=_back,
        )
        embed = discord.Embed(
            title="📨 設定理事國審核通知頻道",
            description="選擇一個頻道，接收理事國入盟申請審核通知。",
            color=discord.Color.dark_gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    council_notify_btn.callback = council_notify_cb
    view.add_item(council_notify_btn)

    sec_role_btn = discord.ui.Button(label="🎭 秘書處審核身分組", style=discord.ButtonStyle.primary, row=3)

    async def sec_role_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, role_id):
            application_settings["secretariat_review_role_id"] = role_id
            save_application_settings()
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        picker = _build_role_picker_view(
            get_current=lambda: application_settings.get("secretariat_review_role_id"),
            on_save=_save,
            back_to=_back,
        )
        embed = discord.Embed(
            title="🎭 設定秘書處審核身分組",
            description="選擇有權審核秘書處入盟申請的身分組（全部取消＝清除，改為管理員限定）。",
            color=discord.Color.dark_gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    sec_role_btn.callback = sec_role_cb
    view.add_item(sec_role_btn)

    council_role_btn = discord.ui.Button(label="🎭 理事國審核身分組", style=discord.ButtonStyle.primary, row=3)

    async def council_role_cb(interaction: discord.Interaction):
        if not await _owner_guard(interaction):
            return

        async def _save(ia, role_id):
            application_settings["council_review_role_id"] = role_id
            save_application_settings()
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        async def _back(ia):
            await ia.response.edit_message(embed=_build_application_settings_embed(), view=_build_application_settings_view())

        picker = _build_role_picker_view(
            get_current=lambda: application_settings.get("council_review_role_id"),
            on_save=_save,
            back_to=_back,
        )
        embed = discord.Embed(
            title="🎭 設定理事國審核身分組",
            description="選擇有權審核理事國入盟申請的身分組（全部取消＝清除，改為管理員限定）。",
            color=discord.Color.dark_gold(),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

    council_role_btn.callback = council_role_cb
    view.add_item(council_role_btn)

    return view


@tree.command(name="system_manager", description="⚙️ 系統管理面板（機器人擁有者專用）")
async def system_manager_command(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=_build_system_manager_embed(),
        view=_system_manager_main_view(),
        ephemeral=True,
    )


'''

content = content.replace(old_block, new_block)

# Remove the SystemGroup_instance registration line (no longer needed —
# system_manager is registered directly via @tree.command above).
old_reg = "ProposalGroup_instance = ProposalGroup()\nSystemGroup_instance = SystemGroup()"
assert old_reg in content
assert content.count(old_reg) == 1
content = content.replace(old_reg, "ProposalGroup_instance = ProposalGroup()")

with open("modules/080_proposal_application.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Patched successfully.")
