# ═════════════════════════════════════════════════════════════════
# 100_task_tracker.py — 任務指派追蹤系統（秘書處專用）
# ═════════════════════════════════════════════════════════════════
# 指定頻道放一個持久面板，秘書處身分組成員可：
#   ➕ 新增任務（Modal）
#   📊 更新狀態（選任務 → 選新狀態）
#   📝 編輯任務（選任務 → Modal 改內容）
#   🗑️ 刪除任務（選任務 → 二次確認）
#   🔍 查看詳情（選任務 → embed）
#
# 安全規則（沿用客服單/投票系統的模式）：
#   - 公開面板按鈕開 Modal 時，提交一律用 ephemeral send_message
#   - 持久化 View 用固定 custom_id + bot.add_view() 註冊
#   - 關鍵寫入（新增/編輯/刪除）await GitHub 推送完成
#   - on_ready 自動偵測面板是否存在，被刪就補發
# ═════════════════════════════════════════════════════════════════

import json
import time
import uuid

# ── 從主程式命名空間取得共用工具 ──

task_settings = {
    "enabled": False,
    "panel_channel_id": None,
    "panel_message_id": None,
    "secretariat_role_id": None,  # 秘書處身分組 ID
    "next_task_number": 1,
}

_tasks = {"entries": []}


def load_task_settings():
    global task_settings
    loaded = load_json("task_settings.json", None)
    if isinstance(loaded, dict):
        task_settings.update(loaded)
    print(f"📋 任務追蹤設定已載入：{'啟用' if task_settings.get('enabled') else '停用'}")


def load_tasks():
    global _tasks
    loaded = load_json("tasks.json", {"entries": []})
    if isinstance(loaded, dict):
        _tasks = loaded
    elif isinstance(loaded, list):
        _tasks = {"entries": loaded}
    print(f"📋 任務記錄已載入：{len(_tasks.get('entries', []))} 筆")


load_task_settings()
load_tasks()


def save_task_settings():
    save_json("task_settings.json", task_settings)


def save_tasks():
    save_json("tasks.json", _tasks)


async def _persist_tasks_now():
    """立即寫入本地 + await GitHub 推送完成（關鍵操作專用）。"""
    path = DATA_DIR / "tasks.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_tasks, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    try:
        await github_push_json("tasks.json", _tasks)
    except Exception as e:
        print(f"⚠️ 任務資料 GitHub 同步失敗：{e}")


# ═════════════════════════════════════════════════════════════════
# 權限檢查
# ═════════════════════════════════════════════════════════════════

def _is_secretariat(interaction: discord.Interaction) -> bool:
    """機器人擁有者、伺服器管理員、或持有秘書處身分組的人。"""
    if interaction.user.id == OWNER_ID:
        return True
    if hasattr(interaction.user, "guild_permissions") and interaction.user.guild_permissions.administrator:
        return True
    role_id = task_settings.get("secretariat_role_id")
    if role_id:
        if hasattr(interaction.user, "roles"):
            for role in interaction.user.roles:
                if str(role.id) == str(role_id):
                    return True
    return False


# ═════════════════════════════════════════════════════════════════
# 任務狀態
# ═════════════════════════════════════════════════════════════════

STATUS_CONFIG = {
    "pending":     {"label": "待處理", "emoji": "⬜", "color": discord.Color.dark_gray()},
    "in_progress": {"label": "進行中", "emoji": "🔵", "color": discord.Color.blue()},
    "review":      {"label": "待審核", "emoji": "🟡", "color": discord.Color.gold()},
    "completed":   {"label": "已完成", "emoji": "✅", "color": discord.Color.green()},
    "blocked":     {"label": "卡住",   "emoji": "🔴", "color": discord.Color.red()},
}

STATUS_ORDER = ["pending", "in_progress", "review", "completed", "blocked"]


def _status_emoji(status):
    return STATUS_CONFIG.get(status, {}).get("emoji", "❓")


def _status_label(status):
    return STATUS_CONFIG.get(status, {}).get("label", "未知")


def _new_task_id():
    return f"TSK-{uuid.uuid4().hex[:8]}"


# ═════════════════════════════════════════════════════════════════
# 工具函式
# ═════════════════════════════════════════════════════════════════

def _find_task(task_id):
    for t in _tasks.get("entries", []):
        if t.get("id") == task_id:
            return t
    return None


def _active_tasks():
    return [t for t in _tasks.get("entries", []) if t.get("status") != "completed" and not t.get("deleted")]


def _all_tasks():
    return [t for t in _tasks.get("entries", []) if not t.get("deleted")]


# ═════════════════════════════════════════════════════════════════
# Embed 建構
# ═════════════════════════════════════════════════════════════════

def _build_panel_embed():
    """任務面板的 embed。"""
    all_t = _all_tasks()
    active = _active_tasks()

    # 按狀態統計
    status_counts = {s: 0 for s in STATUS_ORDER}
    for t in all_t:
        s = t.get("status", "pending")
        if s in status_counts:
            status_counts[s] += 1

    embed = discord.Embed(
        title="📋 秘書處任務追蹤面板",
        description="秘書處成員可使用下方按鈕新增、更新、編輯或刪除任務。",
        color=discord.Color.blurple(),
    )

    # 統計
    stat_line = "  ".join(
        f"{STATUS_CONFIG[s]['emoji']} {STATUS_CONFIG[s]['label']}：{status_counts[s]}"
        for s in STATUS_ORDER
    )
    embed.add_field(name="📊 任務概況", value=stat_line, inline=False)

    # 進行中的任務列表
    if active:
        lines = []
        for t in active[:15]:
            emoji = _status_emoji(t.get("status", "pending"))
            assignee = t.get("assignee", "未指派")
            title = t.get("title", "無標題")[:40]
            num = t.get("number", 0)
            deadline = t.get("deadline", "")
            dl_str = f" ｜ ⏰ {deadline}" if deadline else ""
            lines.append(f"{emoji} **#{num} {title}** → {assignee}{dl_str}")
        embed.add_field(name="📝 進行中任務", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="📝 進行中任務", value="🎉 目前沒有進行中的任務！", inline=False)

    embed.set_footer(text="ICEA official ・ 秘書處任務追蹤")
    return embed


def _build_task_detail_embed(task):
    """單一任務的詳細 embed。"""
    status = task.get("status", "pending")
    cfg = STATUS_CONFIG.get(status, STATUS_CONFIG["pending"])

    embed = discord.Embed(
        title=f"{' #' + str(task.get('number', ''))} {task.get('title', '無標題')}",
        description=task.get("description", "（無說明）"),
        color=cfg["color"],
    )
    embed.add_field(name="狀態", value=f"{cfg['emoji']} {cfg['label']}", inline=True)
    embed.add_field(name="負責人", value=task.get("assignee", "未指派"), inline=True)
    embed.add_field(name="截止日期", value=task.get("deadline") or "未設定", inline=True)
    embed.add_field(name="建立者", value=task.get("created_by", "未知"), inline=True)
    embed.add_field(name="建立時間", value=task.get("created_at", ""), inline=True)
    embed.add_field(name="最後更新", value=task.get("updated_at", ""), inline=True)

    # 進度日誌
    logs = task.get("progress_logs", [])
    if logs:
        log_lines = []
        for log in logs[-5:]:  # 最後5筆
            log_lines.append(f"• [{log.get('time', '')}] {log.get('text', '')}")
        embed.add_field(name="📝 進度日誌", value="\n".join(log_lines)[:1024], inline=False)

    embed.set_footer(text=f"ID: {task.get('id', '')} ・ ICEA official")
    return embed


# ═════════════════════════════════════════════════════════════════
# 面板 View（持久化）
# ═════════════════════════════════════════════════════════════════

class TaskPanelView(discord.ui.View):
    """任務面板上的按鈕：新增 / 更新狀態 / 編輯 / 刪除 / 查看詳情 / 重新整理。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="➕ 新增任務", style=discord.ButtonStyle.success, custom_id="icea_task_add_btn", row=0)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_secretariat(interaction):
            await interaction.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
            return

        modal = discord.ui.Modal(title="➕ 新增任務")
        modal.add_item(discord.ui.TextInput(
            label="任務標題",
            placeholder="例如：準備下月會議議程",
            required=True,
            max_length=200,
            custom_id="task_title",
        ))
        modal.add_item(discord.ui.TextInput(
            label="負責人",
            placeholder="例如：張三（可填姓名或職位）",
            required=True,
            max_length=100,
            custom_id="task_assignee",
        ))
        modal.add_item(discord.ui.TextInput(
            label="任務說明（選填）",
            placeholder="任務內容、注意事項等",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
            custom_id="task_desc",
        ))
        modal.add_item(discord.ui.TextInput(
            label="截止日期（選填）",
            placeholder="例如：2026-08-20",
            required=False,
            max_length=50,
            custom_id="task_deadline",
        ))

        async def on_submit(modal_ia: discord.Interaction):
            values = {}
            for row in modal_ia.data.get("components", []):
                for comp in row.get("components", []):
                    values[comp.get("custom_id")] = comp.get("value", "")

            title = values.get("task_title", "").strip()
            assignee = values.get("task_assignee", "").strip()
            desc = values.get("task_desc", "").strip()
            deadline = values.get("task_deadline", "").strip()

            if not title or not assignee:
                # 公開面板按鈕開的 Modal → 提交必須用 ephemeral send_message
                await modal_ia.response.send_message("❌ 任務標題與負責人為必填。", ephemeral=True)
                return

            number = task_settings.get("next_task_number", 1)
            entry = {
                "id": _new_task_id(),
                "number": number,
                "title": title,
                "assignee": assignee,
                "description": desc,
                "deadline": deadline or None,
                "status": "pending",
                "created_by": interaction.user.display_name,
                "created_by_id": str(interaction.user.id),
                "created_at": now_str(),
                "updated_at": now_str(),
                "progress_logs": [],
                "deleted": False,
            }
            _tasks.setdefault("entries", []).append(entry)
            task_settings["next_task_number"] = number + 1
            save_task_settings()
            await _persist_tasks_now()

            # 更新公開面板
            await _refresh_panel(interaction)

            await modal_ia.response.send_message(
                f"✅ 任務 **#{number} {title}** 已新增，指派給 {assignee}。",
                ephemeral=True,
            )

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📊 更新狀態", style=discord.ButtonStyle.primary, custom_id="icea_task_status_btn", row=0)
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_secretariat(interaction):
            await interaction.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
            return

        all_t = _all_tasks()
        if not all_t:
            await interaction.response.send_message("📭 目前沒有任何任務。", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="選擇要更新狀態的任務…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"#{t.get('number', 0)} {t.get('title', '')[:80]}",
                    value=t["id"],
                    description=f"{_status_emoji(t.get('status', 'pending'))} {_status_label(t.get('status', 'pending'))} → {t.get('assignee', '')}",
                )
                for t in all_t[:25]
            ],
        )

        async def on_select_task(select_ia: discord.Interaction):
            task = _find_task(select.values[0])
            if not task:
                await select_ia.response.send_message("❌ 找不到此任務。", ephemeral=True)
                return

            # 第二層：選新狀態
            status_select = discord.ui.Select(
                placeholder=f"目前：{_status_emoji(task['status'])} {_status_label(task['status'])} → 選擇新狀態…",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=STATUS_CONFIG[s]["label"],
                        value=s,
                        emoji=STATUS_CONFIG[s]["emoji"],
                    )
                    for s in STATUS_ORDER
                ],
            )

            async def on_select_status(status_ia: discord.Interaction):
                new_status = status_select.values[0]
                old_label = _status_label(task.get("status", "pending"))
                new_label = _status_label(new_status)
                task["status"] = new_status
                task["updated_at"] = now_str()
                task.setdefault("progress_logs", []).append({
                    "time": now_str(),
                    "text": f"狀態變更：{old_label} → {new_label}",
                    "by": status_ia.user.display_name,
                })
                await _persist_tasks_now()
                await _refresh_panel(status_ia)

                detail_embed = _build_task_detail_embed(task)
                await status_ia.response.send_message(
                    f"✅ 任務 **#{task.get('number', '')} {task.get('title', '')}** 狀態已更新為 {new_label}。",
                    embed=detail_embed,
                    ephemeral=True,
                )

            status_select.callback = on_select_status
            status_view = discord.ui.View(timeout=120)
            status_view.add_item(status_select)
            await select_ia.response.send_message(
                f"選擇任務 **#{task.get('number', '')} {task.get('title', '')}** 的新狀態：",
                view=status_view,
                ephemeral=True,
            )

        select.callback = on_select_task
        select_view = discord.ui.View(timeout=120)
        select_view.add_item(select)
        await interaction.response.send_message("選擇要更新狀態的任務：", view=select_view, ephemeral=True)

    @discord.ui.button(label="📝 編輯任務", style=discord.ButtonStyle.secondary, custom_id="icea_task_edit_btn", row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_secretariat(interaction):
            await interaction.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
            return

        all_t = _all_tasks()
        if not all_t:
            await interaction.response.send_message("📭 目前沒有任何任務。", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="選擇要編輯的任務…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"#{t.get('number', 0)} {t.get('title', '')[:80]}",
                    value=t["id"],
                    description=f"{_status_emoji(t.get('status', 'pending'))} → {t.get('assignee', '')}",
                )
                for t in all_t[:25]
            ],
        )

        async def on_select(select_ia: discord.Interaction):
            task = _find_task(select.values[0])
            if not task:
                await select_ia.response.send_message("❌ 找不到此任務。", ephemeral=True)
                return

            modal = discord.ui.Modal(title=f"📝 編輯任務 #{task.get('number', '')}")
            modal.add_item(discord.ui.TextInput(
                label="任務標題",
                default_value=task.get("title", ""),
                required=True,
                max_length=200,
                custom_id="edit_title",
            ))
            modal.add_item(discord.ui.TextInput(
                label="負責人",
                default_value=task.get("assignee", ""),
                required=True,
                max_length=100,
                custom_id="edit_assignee",
            ))
            modal.add_item(discord.ui.TextInput(
                label="任務說明",
                default_value=task.get("description", ""),
                required=False,
                max_length=500,
                style=discord.TextStyle.paragraph,
                custom_id="edit_desc",
            ))
            modal.add_item(discord.ui.TextInput(
                label="截止日期",
                default_value=task.get("deadline", "") or "",
                required=False,
                max_length=50,
                custom_id="edit_deadline",
            ))
            modal.add_item(discord.ui.TextInput(
                label="新增進度日誌（選填）",
                placeholder="例如：已完成初稿，待審核",
                required=False,
                max_length=300,
                style=discord.TextStyle.paragraph,
                custom_id="edit_log",
            ))

            async def on_edit_submit(edit_ia: discord.Interaction):
                values = {}
                for row in edit_ia.data.get("components", []):
                    for comp in row.get("components", []):
                        values[comp.get("custom_id")] = comp.get("value", "")

                task["title"] = values.get("edit_title", task.get("title", "")).strip()
                task["assignee"] = values.get("edit_assignee", task.get("assignee", "")).strip()
                task["description"] = values.get("edit_desc", "").strip()
                task["deadline"] = values.get("edit_deadline", "").strip() or None
                task["updated_at"] = now_str()

                log_text = values.get("edit_log", "").strip()
                if log_text:
                    task.setdefault("progress_logs", []).append({
                        "time": now_str(),
                        "text": log_text,
                        "by": edit_ia.user.display_name,
                    })

                await _persist_tasks_now()
                await _refresh_panel(edit_ia)

                await edit_ia.response.send_message(
                    f"✅ 任務 **#{task.get('number', '')} {task.get('title', '')}** 已更新。",
                    ephemeral=True,
                )

            modal.on_submit = on_edit_submit
            await select_ia.response.send_modal(modal)

        select.callback = on_select
        edit_view = discord.ui.View(timeout=120)
        edit_view.add_item(select)
        await interaction.response.send_message("選擇要編輯的任務：", view=edit_view, ephemeral=True)

    @discord.ui.button(label="🗑️ 刪除任務", style=discord.ButtonStyle.danger, custom_id="icea_task_delete_btn", row=0)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_secretariat(interaction):
            await interaction.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
            return

        all_t = _all_tasks()
        if not all_t:
            await interaction.response.send_message("📭 目前沒有任何任務。", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="選擇要刪除的任務…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"#{t.get('number', 0)} {t.get('title', '')[:80]}",
                    value=t["id"],
                    description=f"{_status_emoji(t.get('status', 'pending'))} → {t.get('assignee', '')}",
                )
                for t in all_t[:25]
            ],
        )

        async def on_select(select_ia: discord.Interaction):
            task = _find_task(select.values[0])
            if not task:
                await select_ia.response.send_message("❌ 找不到此任務。", ephemeral=True)
                return

            # 二次確認
            confirm_view = discord.ui.View(timeout=30)

            async def confirm_delete(confirm_ia: discord.Interaction):
                if not _is_secretariat(confirm_ia):
                    await confirm_ia.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
                    return
                task["deleted"] = True
                task["deleted_at"] = now_str()
                task["deleted_by"] = confirm_ia.user.display_name
                await _persist_tasks_now()
                await _refresh_panel(confirm_ia)
                await confirm_ia.response.send_message(
                    f"✅ 任務 **#{task.get('number', '')} {task.get('title', '')}** 已刪除。",
                    ephemeral=True,
                )

            async def cancel_delete(cancel_ia: discord.Interaction):
                await cancel_ia.response.send_message("已取消刪除。", ephemeral=True)

            yes_btn = discord.ui.Button(label="✅ 確認刪除", style=discord.ButtonStyle.danger)
            no_btn = discord.ui.Button(label="取消", style=discord.ButtonStyle.secondary)
            yes_btn.callback = confirm_delete
            no_btn.callback = cancel_delete
            confirm_view.add_item(yes_btn)
            confirm_view.add_item(no_btn)

            await select_ia.response.send_message(
                f"⚠️ 確定要刪除任務 **#{task.get('number', '')} {task.get('title', '')}** 嗎？\n"
                f"負責人：{task.get('assignee', '')}\n"
                f"狀態：{_status_emoji(task.get('status', 'pending'))} {_status_label(task.get('status', 'pending'))}\n"
                f"此動作無法復原。",
                view=confirm_view,
                ephemeral=True,
            )

        select.callback = on_select
        del_view = discord.ui.View(timeout=120)
        del_view.add_item(select)
        await interaction.response.send_message("選擇要刪除的任務：", view=del_view, ephemeral=True)

    @discord.ui.button(label="🔍 查看詳情", style=discord.ButtonStyle.secondary, custom_id="icea_task_detail_btn", row=1)
    async def detail_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_secretariat(interaction):
            await interaction.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
            return

        all_t = _all_tasks()
        if not all_t:
            await interaction.response.send_message("📭 目前沒有任何任務。", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="選擇要查看詳情的任務…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"#{t.get('number', 0)} {t.get('title', '')[:80]}",
                    value=t["id"],
                    description=f"{_status_emoji(t.get('status', 'pending'))} → {t.get('assignee', '')}",
                )
                for t in all_t[:25]
            ],
        )

        async def on_select(select_ia: discord.Interaction):
            task = _find_task(select.values[0])
            if not task:
                await select_ia.response.send_message("❌ 找不到此任務。", ephemeral=True)
                return
            embed = _build_task_detail_embed(task)
            await select_ia.response.send_message(embed=embed, ephemeral=True)

        select.callback = on_select
        detail_view = discord.ui.View(timeout=120)
        detail_view.add_item(select)
        await interaction.response.send_message("選擇要查看的任務：", view=detail_view, ephemeral=True)

    @discord.ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, custom_id="icea_task_refresh_btn", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_secretariat(interaction):
            await interaction.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_build_panel_embed(), view=TaskPanelView())


# ═════════════════════════════════════════════════════════════════
# 面板更新 & on_ready 恢復
# ═════════════════════════════════════════════════════════════════

async def _refresh_panel(interaction=None):
    """更新頻道中的面板訊息。"""
    channel_id = task_settings.get("panel_channel_id")
    message_id = task_settings.get("panel_message_id")
    if not channel_id or not message_id:
        return
    try:
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return
        message = await channel.fetch_message(int(message_id))
        if message:
            await message.edit(embed=_build_panel_embed(), view=TaskPanelView())
    except discord.NotFound:
        print("⚠️ 任務面板訊息不存在，將在 on_ready 時重新發送。")
    except Exception as e:
        print(f"⚠️ 更新任務面板失敗：{e}")


async def _task_ready_hook():
    """on_ready 後：重新註冊 View + 偵測面板是否存在。"""
    await asyncio.sleep(10)  # 等 bot 完全連線

    if not task_settings.get("enabled"):
        print("📋 任務追蹤系統未啟用，略過恢復。")
        return

    # 註冊持久化 View（讓跨重啟的按鈕仍可回應）
    bot.add_view(TaskPanelView())

    # 偵測面板是否存在
    channel_id = task_settings.get("panel_channel_id")
    message_id = task_settings.get("panel_message_id")
    if not channel_id or not message_id:
        print("📋 任務面板尚未設定。")
        return

    try:
        channel = bot.get_channel(int(channel_id))
        if not channel:
            print(f"⚠️ 找不到任務面板頻道 {channel_id}")
            return
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            message = None

        if message:
            # 面板還在 → 更新內容
            await message.edit(embed=_build_panel_embed(), view=TaskPanelView())
            print("✅ 任務面板已恢復並更新。")
        else:
            # 面板被刪了 → 重新發送
            new_msg = await channel.send(embed=_build_panel_embed(), view=TaskPanelView())
            task_settings["panel_message_id"] = str(new_msg.id)
            save_task_settings()
            print("✅ 任務面板已重新發送。")
    except Exception as e:
        print(f"⚠️ 恢復任務面板失敗：{e}")


_bot_ready_hooks.append(_task_ready_hook)


# ═════════════════════════════════════════════════════════════════
# 指令群組
# ═════════════════════════════════════════════════════════════════

class TaskGroup(app_commands.Group):
    """任務追蹤系統指令群組。"""

    def __init__(self):
        super().__init__(name="task", description="任務指派追蹤系統（秘書處專用）")

    @app_commands.command(name="setup", description="設定任務追蹤面板（管理員專用）")
    @app_commands.describe(
        channel="要放面板的頻道",
        role="秘書處身分組（擁有此身分組的人可操作面板）",
    )
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return

        task_settings["panel_channel_id"] = str(channel.id)
        task_settings["secretariat_role_id"] = str(role.id)
        task_settings["enabled"] = True

        # 發送面板
        embed = _build_panel_embed()
        view = TaskPanelView()
        panel_msg = await channel.send(embed=embed, view=view)

        task_settings["panel_message_id"] = str(panel_msg.id)
        save_task_settings()

        await interaction.response.send_message(
            f"✅ 任務追蹤面板已設定完成！\n"
            f"📍 頻道：{channel.mention}\n"
            f"👥 秘書處身分組：{role.mention}\n"
            f"面板已發送，所有秘書處成員可使用按鈕操作。",
            ephemeral=True,
        )

    @app_commands.command(name="list", description="列出所有任務")
    async def list_tasks(self, interaction: discord.Interaction):
        if not _is_secretariat(interaction):
            await interaction.response.send_message("❌ 僅限秘書處成員使用。", ephemeral=True)
            return

        all_t = _all_tasks()
        if not all_t:
            await interaction.response.send_message("📭 目前沒有任何任務。", ephemeral=True)
            return

        # 按狀態分組
        lines_by_status = {s: [] for s in STATUS_ORDER}
        for t in all_t:
            s = t.get("status", "pending")
            if s in lines_by_status:
                lines_by_status[s].append(
                    f"{STATUS_CONFIG[s]['emoji']} **#{t.get('number', 0)} {t.get('title', '')[:30]}** → {t.get('assignee', '')}"
                )

        embed = discord.Embed(title="📋 任務總覽", color=discord.Color.blurple())
        for s in STATUS_ORDER:
            if lines_by_status[s]:
                embed.add_field(
                    name=f"{STATUS_CONFIG[s]['emoji']} {STATUS_CONFIG[s]['label']}（{len(lines_by_status[s])}）",
                    value="\n".join(lines_by_status[s])[:1024],
                    inline=False,
                )
        embed.set_footer(text=f"總計 {len(all_t)} 項任務")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="disable", description="停用任務追蹤面板（管理員專用）")
    async def disable(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return
        task_settings["enabled"] = False
        save_task_settings()
        await interaction.response.send_message("✅ 任務追蹤面板已停用。現有任務記錄仍保留。", ephemeral=True)


TaskGroup_instance = TaskGroup()
