# ═════════════════════════════════════════════════════════════════
# 070_poll_system.py — 投票系統（一般投票 + 波達計數）
# 模組化載入，透過 /poll 指令群組提供所有投票功能。
# 參考設計：Modal 建立投票 → 公開面板即時顯示 → 事後面板調整細節。
# 所有投票互動使用 ephemeral 訊息；公開面板按鈕開 Modal 時，
# 提交用 ephemeral send_message，絕不 edit_message 原面板。
# ═════════════════════════════════════════════════════════════════

import os
import json
import time
import uuid
import re
from pathlib import Path

# ── 從主程式命名空間取得共用工具 ──
# bot, tree, save_json, load_json, is_owner, now_str, OWNER_ID, TZ_TAIPEI, DATA_DIR
# discord, app_commands, asyncio, github_push_json, _bot_ready_hooks

# TZ_TAIPEI and DATA_DIR are available from the main globals


def is_admin(interaction: discord.Interaction) -> bool:
    """機器人擁有者 或 該伺服器管理員 皆視為管理員。"""
    if interaction.user.id == OWNER_ID:
        return True
    if hasattr(interaction.user, "guild_permissions") and interaction.user.guild_permissions.administrator:
        return True
    return False


def _has_role(member, role_id) -> bool:
    if not role_id:
        return True
    if not hasattr(member, "roles"):
        return False
    return any(str(r.id) == str(role_id) for r in member.roles)


# ── 資料檔案 ──
POLLS_FILE = DATA_DIR / "polls.json"

_polls = {"entries": []}


def load_polls():
    global _polls
    loaded = load_json("polls.json", {"entries": []})
    if isinstance(loaded, dict):
        _polls = loaded
    elif isinstance(loaded, list):
        _polls = {"entries": loaded}
    print(f"🗳️ 投票記錄已載入：{len(_polls.get('entries', []))} 筆")


def save_polls():
    path = POLLS_FILE
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_polls, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    # GitHub sync (async, non-blocking)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(github_push_json("polls.json", _polls))
    except Exception:
        pass


load_polls()


# ═════════════════════════════════════════════════════════════════
# 工具函式
# ═════════════════════════════════════════════════════════════════

def _new_poll_id():
    return uuid.uuid4().hex[:12]


def _gen_candidate_codes(n):
    """產生 A, B, C, ... Z, AA, AB, ... 格式的候選人代碼。"""
    codes = []
    for i in range(n):
        s = ""
        x = i
        while True:
            s = chr(ord("A") + (x % 26)) + s
            x = x // 26 - 1
            if x < 0:
                break
        codes.append(s)
    return codes


def _borda_scores(n_candidates):
    """返回 [n, n-1, ..., 2, 1] — 第1名得 n 分，最後1名得 1 分。"""
    return list(range(n_candidates, 0, -1))


def _parse_ballot(text, codes):
    """解析一則投票訊息，回傳 (ranked_codes, is_valid, reason)。
    支援格式：
      1.A 2.B 3.C
      1. A 2. B 3. C
      1A 2B 3C
      1 A 2 B 3 C  （純空白分隔）
      A>B>C
      A B C  （純空白，順序即排名）
    必須包含所有候選人代碼且不重複。
    """
    text = text.strip()
    if not text:
        return [], False, "空訊息"

    codes_upper = [c.upper() for c in codes]
    codes_set = set(codes_upper)

    # --- 嘗試格式1: 數字.代碼 或 數字. 代碼 或 數字代碼 或 數字 代碼 ---
    pattern = re.compile(
        r'(\d+)\s*\.?\s*(' + '|'.join(re.escape(c) for c in codes_upper) + ')',
        re.IGNORECASE
    )
    matches = pattern.findall(text)
    if matches:
        ranked = []
        seen = set()
        for rank_str, code in matches:
            code_u = code.upper()
            if code_u in seen:
                return [], False, f"代碼 {code_u} 重複出現"
            seen.add(code_u)
            ranked.append((int(rank_str), code_u))
        ranked.sort(key=lambda x: x[0])
        result_codes = [c for _, c in ranked]
        if set(result_codes) == codes_set and len(result_codes) == len(codes_set):
            return result_codes, True, "ok"
        elif len(result_codes) < len(codes_set):
            missing = codes_set - set(result_codes)
            return result_codes, False, f"缺少候選人：{', '.join(sorted(missing))}"
        else:
            extra = set(result_codes) - codes_set
            return result_codes, False, f"無效代碼：{', '.join(sorted(extra))}"

    # --- 嘗試格式2: A>B>C ---
    if '>' in text:
        parts = [p.strip().upper() for p in text.split('>')]
        if all(p in codes_set for p in parts) and len(parts) == len(codes_set) and len(set(parts)) == len(codes_set):
            return parts, True, "ok"
        if len(parts) != len(codes_set):
            return [], False, f"需排序全部 {len(codes_set)} 位候選人（目前 {len(parts)} 位）"
        invalid = [p for p in parts if p not in codes_set]
        if invalid:
            return [], False, f"無效代碼：{', '.join(invalid)}"

    # --- 嘗試格式3: 純空白分隔 A B C（順序即排名） ---
    parts = text.split()
    if len(parts) == len(codes_set):
        parts_u = [p.upper().rstrip('.,;，。；') for p in parts]
        if all(p in codes_set for p in parts_u) and len(set(parts_u)) == len(codes_set):
            return parts_u, True, "ok"

    return [], False, "無法辨識投票格式"


def _compute_borda_result(poll):
    """計算波達計數結果。回傳 dict: {candidate_code: score}。"""
    candidates = poll.get("candidates", [])
    n = len(candidates)
    if n == 0:
        return {}
    scores = {c["code"]: 0 for c in candidates}
    point_values = _borda_scores(n)

    for vote in poll.get("votes", []):
        ranking = vote.get("ranking", [])
        if len(ranking) != n:
            continue  # 廢票不計分
        for i, code in enumerate(ranking):
            if code in scores and i < len(point_values):
                scores[code] += point_values[i]
    return scores


def _compute_regular_result(poll):
    """計算一般投票結果。回傳 dict: {option_label: count}。"""
    counts = {}
    for opt in poll.get("options", []):
        counts[opt["label"]] = 0
    for vote in poll.get("votes", []):
        choice = vote.get("choice")
        if choice in counts:
            counts[choice] += 1
    return counts


def _format_borda_results(poll):
    """格式化波達計數結果為 (result_text, summary_text)。"""
    scores = _compute_borda_result(poll)
    candidates = poll.get("candidates", [])
    code_to_name = {c["code"]: c["name"] for c in candidates}

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    n = len(candidates)
    total_votes = sum(1 for v in poll.get("votes", []) if len(v.get("ranking", [])) == n)
    spoiled = len(poll.get("votes", [])) - total_votes

    lines = []
    medal = ["🥇", "🥈", "🥉"]
    max_score = ranked[0][1] if ranked else 0
    for idx, (code, score) in enumerate(ranked):
        prefix = medal[idx] if idx < 3 else f"`{idx+1}.`"
        name = code_to_name.get(code, code)
        bar_len = max(1, int(score / max(1, max_score) * 10)) if max_score > 0 else 0
        bar = "█" * bar_len
        lines.append(f"{prefix} **{name}** (`{code}`) — **{score}** 分\n　　{bar}")

    result_text = "\n".join(lines)
    summary = f"📊 有效票：{total_votes}　廢票：{spoiled}　總計：{total_votes + spoiled}"
    return result_text, summary


def _format_regular_results(poll):
    """格式化一般投票結果為 (result_text, summary_text)。"""
    counts = _compute_regular_result(poll)
    total = sum(counts.values())
    lines = []
    medal = ["🥇", "🥈", "🥉"]
    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    max_count = ranked[0][1] if ranked else 0
    for idx, (label, count) in enumerate(ranked):
        prefix = medal[idx] if idx < 3 else f"`{idx+1}.`"
        pct = f"{count/total*100:.1f}%" if total > 0 else "0%"
        bar_len = max(1, int(count / max(1, max_count) * 10)) if max_count > 0 else 0
        bar = "█" * bar_len
        lines.append(f"{prefix} **{label}** — {count} 票 ({pct})\n　　{bar}")
    return "\n".join(lines), f"📊 總投票數：{total}"


def _format_voter_breakdown(poll):
    """回傳逐一投票者的投票內容（每個人投了什麼），供結果面板顯示明細用。"""
    poll_type = poll.get("type", "regular")
    votes = poll.get("votes", [])
    if not votes:
        return []

    lines = []
    if poll_type == "borda":
        candidates = poll.get("candidates", [])
        n = len(candidates)
        code_to_name = {c.get("code"): c.get("name", "") for c in candidates}
        for v in votes:
            name = v.get("user_name", "未知")
            ranking = v.get("ranking", [])
            if n > 0 and len(ranking) == n:
                parts = []
                for code in ranking:
                    cname = code_to_name.get(code, code)
                    parts.append(code if not cname or cname == code else f"{code}（{cname}）")
                lines.append(f"• **{name}**：{' > '.join(parts)}")
            else:
                lines.append(f"• **{name}**：❌ 廢票（格式不完整）")
    else:
        for v in votes:
            name = v.get("user_name", "未知")
            choice = v.get("choice", "未知")
            lines.append(f"• **{name}**：{choice}")
    return lines


def _chunk_lines_for_fields(lines, max_len=1000):
    """把一串文字行切成多個 <=max_len 的區塊，供多個 embed field 分頁顯示。"""
    chunks = []
    current = ""
    for line in lines:
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _add_voter_breakdown_fields(embed, poll, max_fields=8):
    """在結果 embed 上附加「誰投了什麼」的明細欄位（自動分頁，避免超過單欄字數限制）。"""
    lines = _format_voter_breakdown(poll)
    if not lines:
        return
    chunks = _chunk_lines_for_fields(lines)
    total = len(chunks)
    if total > max_fields:
        # 太多人時，只顯示前 max_fields 頁，其餘用文字提示
        shown = chunks[:max_fields]
        remaining_people = sum(len(c.split("\n")) for c in chunks[max_fields:])
        shown[-1] += f"\n…以及其他 {remaining_people} 人（請用 /poll tally 查看完整明細）"
        chunks = shown
        total = len(chunks)
    for i, chunk in enumerate(chunks):
        field_name = "🧾 投票明細" if total == 1 else f"🧾 投票明細（{i+1}/{total}）"
        embed.add_field(name=field_name, value=chunk[:1024], inline=False)


# ═════════════════════════════════════════════════════════════════
# Embed 建構
# ═════════════════════════════════════════════════════════════════

def _build_poll_embed(poll):
    """建構投票面板 embed。"""
    poll_type = poll.get("type", "regular")
    status = poll.get("status", "open")
    title = poll.get("title", "投票")

    if poll_type == "borda":
        type_label = "🗳️ 波達計數投票"
    else:
        type_label = "📊 一般投票"

    status_emoji = {"open": "🟢 進行中", "closed": "🔴 已結束"}.get(status, "❓")
    color = discord.Color.blurple() if status == "open" else discord.Color.dark_gray()

    embed = discord.Embed(
        title=f"{type_label}：{title}",
        description=poll.get("description", "") or "",
        color=color,
    )

    # 候選人 / 選項
    if poll_type == "borda":
        candidates = poll.get("candidates", [])
        # 確保 code 已存入
        codes = _gen_candidate_codes(len(candidates))
        for i, c in enumerate(candidates):
            if not c.get("code"):
                c["code"] = codes[i]

        candidate_lines = []
        for c in candidates:
            candidate_lines.append(f"`{c['code']}` — **{c['name']}**")
        embed.add_field(
            name=f"📋 候選人（{len(candidates)} 位）",
            value="\n".join(candidate_lines) or "無",
            inline=False,
        )
        n = len(candidates)
        example = " ".join(f"{i+1}.{c['code']}" for i, c in enumerate(candidates))
        embed.add_field(
            name="📝 投票方式",
            value=(
                f"回覆排序，例如：\n`{example}`\n"
                f"必須包含全部 {n} 位候選人，缺一即為廢票。\n"
                f"也支援 `A>B>C` 或 `A B C` 格式。\n"
                f"點下方「🗳️ 投票」按鈕或直接回覆此訊息皆可。"
            ),
            inline=False,
        )
    else:
        options = poll.get("options", [])
        option_lines = []
        for i, opt in enumerate(options):
            emoji = opt.get("emoji") or f"{i+1}\uFE0F\u20E3"
            option_lines.append(f"{emoji} **{opt['label']}**")
        embed.add_field(
            name=f"📋 選項（{len(options)} 個）",
            value="\n".join(option_lines) or "無",
            inline=False,
        )

    # 即時票數
    votes = poll.get("votes", [])
    if poll_type == "borda":
        if votes:
            result_text, summary = _format_borda_results(poll)
            embed.add_field(name="📊 即時計票", value=result_text[:1024], inline=False)
            embed.add_field(name="📈 統計", value=summary, inline=False)
        else:
            embed.add_field(name="📊 即時計票", value="尚無人投票", inline=False)
    else:
        if votes:
            result_text, summary = _format_regular_results(poll)
            embed.add_field(name="📊 即時票數", value=result_text[:1024], inline=False)
            embed.add_field(name="📈 統計", value=summary, inline=False)
        else:
            embed.add_field(name="📊 即時票數", value="尚無人投票", inline=False)

    embed.add_field(
        name="ℹ️ 投票資訊",
        value=(
            f"狀態：{status_emoji}\n"
            f"建立者：{poll.get('author_name', '未知')}\n"
            f"已投票：{len(votes)} 人"
        ),
        inline=False,
    )

    if poll.get("restrict_role_id"):
        embed.add_field(name="🔒 限定身分組", value=f"<@&{poll['restrict_role_id']}>", inline=True)

    if poll.get("deadline"):
        embed.add_field(name="⏰ 截止時間", value=poll["deadline"], inline=True)

    embed.set_footer(text=f"ID: {poll.get('id', '')} ・ ICEA official")
    return embed


# ═════════════════════════════════════════════════════════════════
# 持久化 View（按鈕跨重啟仍可用）
# ═════════════════════════════════════════════════════════════════

class PollVoteView(discord.ui.View):
    """投票面板上的按鈕：投票 / 查看結果 / 結束投票（owner/建立者 only）。"""

    def __init__(self, poll_id: str):
        super().__init__(timeout=None)
        self.poll_id = poll_id

    @discord.ui.button(label="🗳️ 投票", style=discord.ButtonStyle.success, custom_id="poll_vote_btn", row=0)
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        poll = _find_poll(self.poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到此投票。", ephemeral=True)
            return
        if poll.get("status") != "open":
            await interaction.response.send_message("❌ 此投票已結束。", ephemeral=True)
            return

        poll_type = poll.get("type", "regular")
        user_id = str(interaction.user.id)

        restrict_role_id = poll.get("restrict_role_id")
        if restrict_role_id and not _has_role(interaction.user, restrict_role_id):
            await interaction.response.send_message(
                f"❌ 此投票僅限 <@&{restrict_role_id}> 身分組成員投票。", ephemeral=True
            )
            return

        existing = [v for v in poll.get("votes", []) if v.get("user_id") == user_id]

        if poll_type == "borda":
            candidates = poll.get("candidates", [])
            codes = [c.get("code", _gen_candidate_codes(len(candidates))[i]) for i, c in enumerate(candidates)]
            example = " ".join(f"{i+1}.{c}" for i, c in enumerate(codes))

            modal = discord.ui.Modal(title=f"投票：{poll['title'][:40]}")
            modal.add_item(discord.ui.TextInput(
                label="你的排序（完整排名）",
                placeholder=f"例如：{example}",
                required=True,
                max_length=500,
                custom_id="ballot_text",
            ))

            async def on_modal_submit(modal_ia: discord.Interaction):
                # 從 modal 取值
                text = ""
                for row in modal_ia.data["components"]:
                    for comp in row["components"]:
                        if comp["custom_id"] == "ballot_text":
                            text = comp.get("value", "")
                            break

                ranked, valid, reason = _parse_ballot(text, codes)
                if not valid:
                    err_msg = f"❌ 投票無效：{reason}\n\n請使用完整格式，例如：\n`{example}`"
                    # Modal 提交必須 response — 用 ephemeral send_message（不 edit 原面板）
                    await modal_ia.response.send_message(err_msg, ephemeral=True)
                    return

                if existing:
                    poll["votes"] = [v for v in poll.get("votes", []) if v.get("user_id") != user_id]

                poll["votes"].append({
                    "user_id": user_id,
                    "user_name": interaction.user.display_name,
                    "ranking": ranked,
                    "ts": int(time.time()),
                })
                save_polls()
                await _refresh_poll_message(poll)
                await modal_ia.response.send_message(
                    f"✅ 投票成功！你的排序：{' > '.join(ranked)}",
                    ephemeral=True,
                )

            modal.on_submit = on_modal_submit
            await interaction.response.send_modal(modal)

        else:
            # 一般投票：用 ephemeral Select
            options = poll.get("options", [])
            if not options:
                await interaction.response.send_message("❌ 此投票沒有選項。", ephemeral=True)
                return

            select = discord.ui.Select(
                placeholder="選擇你的選項…",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=opt["label"][:100],
                        value=opt["label"],
                        description=(opt.get("description") or "")[:100] or None,
                    )
                    for opt in options[:25]
                ],
            )

            async def on_select(select_ia: discord.Interaction):
                choice = select.values[0]
                if existing:
                    poll["votes"] = [v for v in poll.get("votes", []) if v.get("user_id") != user_id]
                poll["votes"].append({
                    "user_id": user_id,
                    "user_name": interaction.user.display_name,
                    "choice": choice,
                    "ts": int(time.time()),
                })
                save_polls()
                await _refresh_poll_message(poll)
                await select_ia.response.send_message(f"✅ 投票成功！你選了：**{choice}**", ephemeral=True)

            select.callback = on_select
            vote_view = discord.ui.View(timeout=120)
            vote_view.add_item(select)
            await interaction.response.send_message("請選擇你的選項：", view=vote_view, ephemeral=True)

    @discord.ui.button(label="📊 查看結果", style=discord.ButtonStyle.secondary, custom_id="poll_result_btn", row=0)
    async def result_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        poll = _find_poll(self.poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到此投票。", ephemeral=True)
            return

        poll_type = poll.get("type", "regular")
        if poll_type == "borda":
            result_text, summary = _format_borda_results(poll)
        else:
            result_text, summary = _format_regular_results(poll)

        embed = discord.Embed(
            title=f"📊 投票結果：{poll.get('title', '')}",
            color=discord.Color.green(),
        )
        embed.add_field(name="結果", value=result_text[:1024], inline=False)
        embed.add_field(name="統計", value=summary, inline=False)
        _add_voter_breakdown_fields(embed, poll)
        embed.set_footer(text=f"ID: {poll.get('id', '')}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔴 結束投票", style=discord.ButtonStyle.danger, custom_id="poll_close_btn", row=0)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        poll = _find_poll(self.poll_id)
        if not poll:
            await interaction.response.send_message("❌ 找不到此投票。", ephemeral=True)
            return

        if interaction.user.id != OWNER_ID and str(interaction.user.id) != poll.get("author_id", ""):
            await interaction.response.send_message("❌ 只有投票建立者或機器人擁有者可以結束投票。", ephemeral=True)
            return

        if poll.get("status") == "closed":
            await interaction.response.send_message("⚠️ 此投票已經結束。", ephemeral=True)
            return

        poll["status"] = "closed"
        poll["closed_at"] = now_str()
        save_polls()
        await _refresh_poll_message(poll)
        await interaction.response.send_message("✅ 投票已結束。", ephemeral=True)


def _find_poll(poll_id):
    for p in _polls.get("entries", []):
        if p.get("id") == poll_id:
            return p
    return None


async def _refresh_poll_message(poll, interaction=None):
    """更新投票面板訊息。嘗試 edit 原訊息；若失敗則跳過。"""
    channel_id = poll.get("channel_id")
    message_id = poll.get("message_id")
    if not channel_id or not message_id:
        return

    try:
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return
        message = await channel.fetch_message(int(message_id))
        if message:
            embed = _build_poll_embed(poll)
            view = PollVoteView(poll["id"]) if poll.get("status") == "open" else None
            await message.edit(embed=embed, view=view)
    except Exception as e:
        print(f"⚠️ 更新投票面板失敗（{poll.get('id')}）：{e}")


# ═════════════════════════════════════════════════════════════════
# 建立投票的 Modal
# ═════════════════════════════════════════════════════════════════

def _create_poll_modal(mode="regular"):
    """建構建立投票的 Modal。"""
    title_prefix = "波達計數" if mode == "borda" else "一般投票"
    modal = discord.ui.Modal(title=f"📋 建立{title_prefix}投票")

    modal.add_item(discord.ui.TextInput(
        label="投票標題",
        placeholder="例如：選出下任秘書長",
        required=True,
        max_length=200,
        custom_id="poll_title",
    ))

    modal.add_item(discord.ui.TextInput(
        label="說明（選填）",
        placeholder="投票目的、規則等",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
        custom_id="poll_desc",
    ))

    if mode == "borda":
        modal.add_item(discord.ui.TextInput(
            label="候選人名單（每行一位）",
            placeholder="張三\n李四\n王五\n趙六",
            required=True,
            max_length=1500,
            style=discord.TextStyle.paragraph,
            custom_id="poll_candidates",
        ))
    else:
        modal.add_item(discord.ui.TextInput(
            label="選項清單（每行一個）",
            placeholder="贊成\n反對\n棄權",
            required=True,
            max_length=1500,
            style=discord.TextStyle.paragraph,
            custom_id="poll_options",
        ))

    modal.add_item(discord.ui.TextInput(
        label="截止時間（選填）",
        placeholder="例如：2026-08-14 22:00（留空=無截止）",
        required=False,
        max_length=50,
        custom_id="poll_deadline",
    ))

    return modal


def _build_draft_preview_embed(poll_draft):
    """建立投票前的預覽面板：顯示目前設定，讓使用者選擇限定身分組後再發佈。"""
    mode = poll_draft.get("type", "regular")
    type_label = "🗳️ 波達計數投票" if mode == "borda" else "📊 一般投票"

    embed = discord.Embed(
        title=f"📝 建立預覽：{type_label}",
        description=poll_draft.get("description") or "（無說明）",
        color=discord.Color.orange(),
    )
    embed.add_field(name="標題", value=poll_draft.get("title", ""), inline=False)

    if mode == "borda":
        lines = [f"`{c['code']}` — {c['name']}" for c in poll_draft.get("candidates", [])]
        embed.add_field(name=f"候選人（{len(lines)} 位）", value="\n".join(lines) or "無", inline=False)
    else:
        lines = [f"• {o['label']}" for o in poll_draft.get("options", [])]
        embed.add_field(name=f"選項（{len(lines)} 個）", value="\n".join(lines) or "無", inline=False)

    if poll_draft.get("deadline"):
        embed.add_field(name="⏰ 截止時間", value=poll_draft["deadline"], inline=True)

    role_id = poll_draft.get("restrict_role_id")
    role_name = poll_draft.get("restrict_role_name")
    embed.add_field(
        name="🔒 限定投票身分組",
        value=(f"<@&{role_id}>（{role_name}）" if role_id else "未限制（所有人可投票）"),
        inline=True,
    )
    embed.set_footer(text="請確認以上內容，選擇限定身分組（可留空）後點「🚀 發佈投票」")
    return embed


def _build_poll_setup_view(poll_draft, mode):
    """草稿確認面板：下拉選單直接選限定身分組 + 發佈/取消按鈕。"""
    view = discord.ui.View(timeout=300)

    role_select = discord.ui.RoleSelect(
        placeholder="🔒 限定投票身分組（留空＝所有人可投票）",
        min_values=0,
        max_values=1,
        row=0,
    )

    async def on_role_select(select_interaction: discord.Interaction):
        if role_select.values:
            chosen = role_select.values[0]
            poll_draft["restrict_role_id"] = str(chosen.id)
            poll_draft["restrict_role_name"] = chosen.name
        else:
            poll_draft.pop("restrict_role_id", None)
            poll_draft.pop("restrict_role_name", None)
        await select_interaction.response.edit_message(embed=_build_draft_preview_embed(poll_draft), view=view)

    role_select.callback = on_role_select
    view.add_item(role_select)

    publish_btn = discord.ui.Button(label="🚀 發佈投票", style=discord.ButtonStyle.success, row=1)

    async def on_publish(button_interaction: discord.Interaction):
        await _finalize_and_post_poll(button_interaction, poll_draft, mode)

    publish_btn.callback = on_publish
    view.add_item(publish_btn)

    cancel_btn = discord.ui.Button(label="❌ 取消", style=discord.ButtonStyle.danger, row=1)

    async def on_cancel(button_interaction: discord.Interaction):
        await button_interaction.response.edit_message(content="❌ 已取消建立投票。", embed=None, view=None)

    cancel_btn.callback = on_cancel
    view.add_item(cancel_btn)

    return view


async def _finalize_and_post_poll(interaction: discord.Interaction, poll_draft: dict, mode: str):
    """使用者在草稿面板點「🚀 發佈投票」後：正式建立投票並發送公開面板。"""
    try:
        poll = dict(poll_draft)
        poll["id"] = _new_poll_id()
        poll["status"] = "open"
        poll["author_id"] = str(interaction.user.id)
        poll["author_name"] = interaction.user.display_name
        poll["channel_id"] = str(interaction.channel_id)
        poll["created_at"] = now_str()
        poll["votes"] = []

        embed = _build_poll_embed(poll)
        view = PollVoteView(poll["id"])
        poll_message = await interaction.channel.send(embed=embed, view=view)
        poll["message_id"] = str(poll_message.id)

        _polls.setdefault("entries", []).append(poll)
        save_polls()

        await interaction.response.edit_message(
            content=f"✅ 投票「{poll['title']}」已發佈！",
            embed=None,
            view=None,
        )
    except Exception as e:
        print(f"⚠️ 發佈投票失敗：{e}")
        try:
            await interaction.response.edit_message(content=f"❌ 發佈投票失敗：{e}", embed=None, view=None)
        except Exception:
            pass


async def _handle_poll_create(modal_interaction: discord.Interaction, mode: str):
    """Modal 提交後的處理邏輯：解析欄位、建立投票草稿，顯示身分組限制設定面板（尚未發佈）。"""
    # 從 Modal 取值
    data = {}
    for row in modal_interaction.data["components"]:
        for comp in row["components"]:
            data[comp["custom_id"]] = comp.get("value", "")

    title = data.get("poll_title", "").strip()
    desc = data.get("poll_desc", "").strip()
    deadline = data.get("poll_deadline", "").strip()

    if not title:
        await modal_interaction.response.send_message("❌ 標題不可為空。", ephemeral=True)
        return

    poll_draft = {
        "type": mode,
        "title": title,
        "description": desc,
        "deadline": deadline or None,
        "allow_revote": True,
    }

    if mode == "borda":
        raw_candidates = data.get("poll_candidates", "").strip()
        names = [line.strip() for line in raw_candidates.split("\n") if line.strip()]
        if len(names) < 2:
            await modal_interaction.response.send_message("❌ 波達計數至少需要 2 位候選人。", ephemeral=True)
            return
        if len(names) > 26:
            await modal_interaction.response.send_message("❌ 候選人不可超過 26 位。", ephemeral=True)
            return

        codes = _gen_candidate_codes(len(names))
        poll_draft["candidates"] = [
            {"code": codes[i], "name": name, "description": ""}
            for i, name in enumerate(names)
        ]
    else:
        raw_options = data.get("poll_options", "").strip()
        opt_labels = [line.strip() for line in raw_options.split("\n") if line.strip()]
        if len(opt_labels) < 2:
            await modal_interaction.response.send_message("❌ 一般投票至少需要 2 個選項。", ephemeral=True)
            return
        if len(opt_labels) > 25:
            await modal_interaction.response.send_message("❌ 選項不可超過 25 個。", ephemeral=True)
            return

        poll_draft["options"] = [
            {"label": label, "description": "", "emoji": None}
            for label in opt_labels
        ]

    # Modal 提交必須用 ephemeral send_message 回應（絕不 edit_message 原面板）——
    # 這裡直接送出草稿預覽面板，讓使用者用原生下拉選單選限定身分組後再發佈。
    embed = _build_draft_preview_embed(poll_draft)
    view = _build_poll_setup_view(poll_draft, mode)
    await modal_interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ═════════════════════════════════════════════════════════════════
# 投票管理面板（/poll manage）
# ═════════════════════════════════════════════════════════════════

def _build_manage_embed():
    embed = discord.Embed(
        title="🗳️ 投票管理面板",
        description="管理所有投票。點下方按鈕操作。",
        color=discord.Color.blurple(),
    )
    open_polls = [p for p in _polls.get("entries", []) if p.get("status") == "open"]
    closed_polls = [p for p in _polls.get("entries", []) if p.get("status") == "closed"]
    embed.add_field(
        name="📊 概況",
        value=f"進行中：{len(open_polls)}　已結束：{len(closed_polls)}　總計：{len(open_polls)+len(closed_polls)}",
        inline=False,
    )
    if open_polls:
        lines = []
        for p in open_polls[:10]:
            type_icon = "🗳️" if p.get("type") == "borda" else "📊"
            lines.append(f"{type_icon} **{p['title'][:40]}** — {len(p.get('votes', []))} 票 | ID: `{p['id']}`")
        embed.add_field(name="🟢 進行中的投票", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="ICEA official ・ 投票管理")
    return embed


def _build_manage_view():
    view = discord.ui.View(timeout=300)

    list_btn = discord.ui.Button(label="📋 列出所有投票", style=discord.ButtonStyle.primary, row=0)

    async def list_cb(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return
        entries = _polls.get("entries", [])
        if not entries:
            await interaction.response.send_message("📭 目前沒有任何投票。", ephemeral=True)
            return
        lines = []
        for p in entries[:20]:
            type_icon = "🗳️" if p.get("type") == "borda" else "📊"
            status_icon = "🟢" if p.get("status") == "open" else "🔴"
            lines.append(f"{type_icon} {status_icon} **{p['title'][:30]}** | {len(p.get('votes', []))} 票 | ID: `{p['id']}`")
        text = f"📋 **投票列表（{len(entries)} 筆）**\n\n" + "\n".join(lines)
        await interaction.response.send_message(text[:1900], ephemeral=True)

    list_btn.callback = list_cb
    view.add_item(list_btn)

    close_btn = discord.ui.Button(label="🔴 結束指定投票", style=discord.ButtonStyle.danger, row=0)

    async def close_cb(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return
        open_polls = [p for p in _polls.get("entries", []) if p.get("status") == "open"]
        if not open_polls:
            await interaction.response.send_message("📭 沒有進行中的投票。", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="選擇要結束的投票…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=p["title"][:100],
                    value=p["id"],
                    description=f"{len(p.get('votes', []))} 票 | {'波達計數' if p.get('type')=='borda' else '一般投票'}",
                )
                for p in open_polls[:25]
            ],
        )

        async def on_select(select_ia: discord.Interaction):
            poll_id = select.values[0]
            poll = _find_poll(poll_id)
            if not poll:
                await select_ia.response.send_message("❌ 找不到此投票。", ephemeral=True)
                return
            poll["status"] = "closed"
            poll["closed_at"] = now_str()
            save_polls()
            await _refresh_poll_message(poll)
            await select_ia.response.send_message(f"✅ 投票「{poll['title']}」已結束。", ephemeral=True)

        select.callback = on_select
        close_view = discord.ui.View(timeout=120)
        close_view.add_item(select)
        await interaction.response.send_message("選擇要結束的投票：", view=close_view, ephemeral=True)

    close_btn.callback = close_cb
    view.add_item(close_btn)

    delete_btn = discord.ui.Button(label="🗑️ 刪除投票", style=discord.ButtonStyle.danger, row=0)

    async def delete_cb(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return
        entries = _polls.get("entries", [])
        if not entries:
            await interaction.response.send_message("📭 沒有投票可刪除。", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="選擇要刪除的投票…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=p["title"][:100],
                    value=p["id"],
                    description=f"{'已結束' if p.get('status')=='closed' else '進行中'} | {len(p.get('votes', []))} 票",
                )
                for p in entries[:25]
            ],
        )

        async def on_select(select_ia: discord.Interaction):
            poll_id = select.values[0]
            poll = _find_poll(poll_id)
            if not poll:
                await select_ia.response.send_message("❌ 找不到此投票。", ephemeral=True)
                return
            _polls["entries"] = [p for p in _polls.get("entries", []) if p.get("id") != poll_id]
            save_polls()
            # 嘗試刪除頻道訊息
            try:
                ch_id = poll.get("channel_id")
                msg_id = poll.get("message_id")
                if ch_id and msg_id:
                    ch = bot.get_channel(int(ch_id))
                    if ch:
                        msg = await ch.fetch_message(int(msg_id))
                        if msg:
                            await msg.delete()
            except Exception:
                pass
            await select_ia.response.send_message(f"✅ 投票「{poll['title']}」已刪除。", ephemeral=True)

        select.callback = on_select
        del_view = discord.ui.View(timeout=120)
        del_view.add_item(select)
        await interaction.response.send_message("選擇要刪除的投票：", view=del_view, ephemeral=True)

    delete_btn.callback = delete_cb
    view.add_item(delete_btn)

    tally_btn = discord.ui.Button(label="📈 查看計票結果", style=discord.ButtonStyle.secondary, row=0)

    async def tally_cb(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限管理員使用。", ephemeral=True)
            return
        entries = _polls.get("entries", [])
        if not entries:
            await interaction.response.send_message("📭 沒有任何投票可查看。", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="選擇要查看結果的投票…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=p["title"][:100],
                    value=p["id"],
                    description=f"{'已結束' if p.get('status')=='closed' else '進行中'} | {len(p.get('votes', []))} 票 | {'波達計數' if p.get('type')=='borda' else '一般投票'}",
                )
                for p in entries[:25]
            ],
        )

        async def on_select(select_ia: discord.Interaction):
            poll_id = select.values[0]
            poll = _find_poll(poll_id)
            if not poll:
                await select_ia.response.send_message("❌ 找不到此投票。", ephemeral=True)
                return

            poll_type = poll.get("type", "regular")
            if poll_type == "borda":
                result_text, summary = _format_borda_results(poll)
            else:
                result_text, summary = _format_regular_results(poll)

            result_embed = discord.Embed(
                title=f"📊 計票結果：{poll.get('title', '')}",
                description=f"狀態：{'🟢 進行中' if poll.get('status')=='open' else '🔴 已結束'}",
                color=discord.Color.gold(),
            )
            result_embed.add_field(name="結果", value=result_text[:1024], inline=False)
            result_embed.add_field(name="統計", value=summary, inline=False)
            _add_voter_breakdown_fields(result_embed, poll, max_fields=15)
            result_embed.set_footer(text=f"ID: {poll_id}")

            await select_ia.response.send_message(embed=result_embed, ephemeral=True)

        select.callback = on_select
        tally_view = discord.ui.View(timeout=120)
        tally_view.add_item(select)
        await interaction.response.send_message("選擇要查看結果的投票：", view=tally_view, ephemeral=True)

    tally_btn.callback = tally_cb
    view.add_item(tally_btn)

    create_regular_btn = discord.ui.Button(label="📊 建立一般投票", style=discord.ButtonStyle.success, row=1)

    async def create_regular_cb(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限管理員使用。", ephemeral=True)
            return
        modal = _create_poll_modal(mode="regular")

        async def on_submit(modal_ia: discord.Interaction):
            await _handle_poll_create(modal_ia, "regular")

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    create_regular_btn.callback = create_regular_cb
    view.add_item(create_regular_btn)

    create_borda_btn = discord.ui.Button(label="🗳️ 建立波達計數投票", style=discord.ButtonStyle.success, row=1)

    async def create_borda_cb(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限管理員使用。", ephemeral=True)
            return
        modal = _create_poll_modal(mode="borda")

        async def on_submit(modal_ia: discord.Interaction):
            await _handle_poll_create(modal_ia, "borda")

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    create_borda_btn.callback = create_borda_cb
    view.add_item(create_borda_btn)

    refresh_btn = discord.ui.Button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=1)

    async def refresh_cb(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_build_manage_embed(), view=_build_manage_view())

    refresh_btn.callback = refresh_cb
    view.add_item(refresh_btn)

    return view


# ═════════════════════════════════════════════════════════════════
# 指令群組
# ═════════════════════════════════════════════════════════════════

class PollGroup(app_commands.Group):
    """投票系統指令群組。

    原本 create/borda/tally/close 分散成 4 個獨立指令，容易搞混、Discord 指令
    列表也太長。整合成單一 /poll manage 面板（僅管理員能用）——建立一般投票、
    建立波達計數投票、查看計票結果、結束投票、刪除投票全部收進面板按鈕/下拉
    選單操作，不用再記一堆指令跟手動輸入投票 ID。
    """

    def __init__(self):
        super().__init__(name="poll", description="投票系統")

    @app_commands.command(name="manage", description="投票管理面板：建立/查看/結束/刪除投票（管理員專用）")
    async def manage(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 僅限管理員使用。", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=_build_manage_embed(),
            view=_build_manage_view(),
            ephemeral=True,
        )


# ═════════════════════════════════════════════════════════════════
# 訊息監聽：偵測對投票訊息的回覆（波達計數可用回覆投票）
# ═════════════════════════════════════════════════════════════════

async def handle_poll_message(message: discord.Message):
    """在 on_message 中呼叫：偵測對投票面板的回覆訊息。"""
    if message.author.bot:
        return
    if not message.reference or not message.reference.message_id:
        return

    # 檢查是否回覆的是投票面板訊息
    for poll in _polls.get("entries", []):
        if poll.get("status") != "open":
            continue
        if str(message.reference.message_id) == str(poll.get("message_id", "")):
            if poll.get("type") != "borda":
                return  # 一般投票用按鈕，不處理回覆

            restrict_role_id = poll.get("restrict_role_id")
            if restrict_role_id and not _has_role(message.author, restrict_role_id):
                try:
                    await message.reply(f"❌ 此投票僅限 <@&{restrict_role_id}> 身分組成員投票。", delete_after=8)
                except Exception:
                    pass
                return

            candidates = poll.get("candidates", [])
            codes = [c.get("code", "") for c in candidates]
            text = message.content

            ranked, valid, reason = _parse_ballot(text, codes)
            if not valid:
                try:
                    example = " ".join(f"{i+1}.{c}" for i, c in enumerate(codes))
                    await message.reply(
                        f"⚠️ 投票格式無效：{reason}\n正確格式例如：`{example}`",
                        delete_after=10,
                    )
                except Exception:
                    pass
                return

            user_id = str(message.author.id)
            existing = [v for v in poll.get("votes", []) if v.get("user_id") == user_id]

            if existing:
                poll["votes"] = [v for v in poll.get("votes", []) if v.get("user_id") != user_id]

            poll["votes"].append({
                "user_id": user_id,
                "user_name": message.author.display_name,
                "ranking": ranked,
                "ts": int(time.time()),
            })
            save_polls()
            await _refresh_poll_message(poll)

            try:
                await message.add_reaction("✅")
            except Exception:
                pass


# ═════════════════════════════════════════════════════════════════
# 啟動後恢復
# ═════════════════════════════════════════════════════════════════

async def _poll_ready_hook():
    """on_ready 後：重新註冊所有進行中投票的 View，確保按鈕跨重啟可用。"""
    await asyncio.sleep(8)  # 等 bot 完全連線

    restored = 0
    for poll in _polls.get("entries", []):
        if poll.get("status") != "open":
            continue
        if not poll.get("message_id") or not poll.get("channel_id"):
            continue
        try:
            bot.add_view(PollVoteView(poll["id"]))
            await _refresh_poll_message(poll)
            restored += 1
        except Exception as e:
            print(f"⚠️ 恢復投票面板失敗（{poll.get('id')}）：{e}")

    if restored:
        print(f"🗳️ 已恢復 {restored} 個進行中的投票面板")


_bot_ready_hooks.append(_poll_ready_hook)


# ═════════════════════════════════════════════════════════════════
# 註冊到主程式
# ═════════════════════════════════════════════════════════════════

# 註冊 on_message hook
# handle_poll_message is defined below; main file picks it up from globals automatically

# 註冊指令群組
PollGroup_instance = PollGroup()
