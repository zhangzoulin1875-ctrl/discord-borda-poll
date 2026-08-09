# ═════════════════════════════════════════════════════════════════
# Module: 110_nations (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

class NationGroup(app_commands.Group):
    """微國家相關指令群組"""

    @app_commands.command(name="name_rate", description="評價微國家國號（1-10分 + AI評論 + 修改建議）")
    @app_commands.describe(
        nation_name="要評價的國號名稱",
        nation_info="（選填）國情簡介：這個微國家的背景、理念、文化等",
        gov_info="（選填）政體簡介：政府體制、政治結構、運作方式等",
    )
    async def nation_name_rate(
        self,
        interaction: discord.Interaction,
        nation_name: str,
        nation_info: str = "",
        gov_info: str = "",
    ):
        await interaction.response.defer()  # public, not ephemeral

        nation_name = nation_name.strip()
        if not nation_name or len(nation_name) > 100:
            await interaction.followup.send("❌ 國號名稱無效（請輸入 1-100 字）。")
            return

        nation_info = nation_info.strip()[:500]
        gov_info = gov_info.strip()[:500]

        # Use the briefing AI settings (more reliable than the chat AI settings)
        result = await _rate_nation_name(nation_name, ai_settings, nation_info, gov_info)

        if "error" in result:
            if _is_api_unavailable(result["error"]):
                await interaction.followup.send(_get_entertainment_unavailable_msg())
            else:
                await interaction.followup.send(f"❌ 評價失敗：{result['error']}")
            return

        score = result["score"]
        comment = result["comment"]
        suggestions = result["suggestions"]

        # Color based on score: red < 4, orange 4-6, yellow 6-8, green > 8
        if score >= 8:
            color = discord.Color.from_rgb(76, 175, 80)   # green
        elif score >= 6:
            color = discord.Color.from_rgb(255, 193, 7)    # amber
        elif score >= 4:
            color = discord.Color.from_rgb(255, 152, 0)    # orange
        else:
            color = discord.Color.from_rgb(244, 67, 54)    # red

        # Score bar (10 blocks)
        filled = int(round(score))
        bar = "█" * filled + "░" * (10 - filled)

        embed = discord.Embed(
            title=f"🏷️ 國號評價：{nation_name}",
            color=color,
        )
        embed.add_field(
            name=f"📊 評分　{score:.1f} / 10.0",
            value=f"`{bar}`",
            inline=False,
        )
        embed.add_field(
            name="📝 AI 評論",
            value=comment[:1024] if comment else "（無評論）",
            inline=False,
        )
        embed.add_field(
            name="💡 修改建議",
            value=suggestions[:1024] if suggestions else "（無建議）",
            inline=False,
        )
        embed.set_footer(text=f"由 {interaction.user.display_name} 發起評價")
        embed.timestamp = interaction.created_at

        await interaction.followup.send(embed=embed)


# ──────────────────────────────────────────────
# AI 整理事項（訊息右鍵選單 App）
# ──────────────────────────────────────────────

async def _organize_agenda_items(raw_text: str, ai_settings: dict) -> dict:
    """Call the AI to organize/refine a raw list of agenda items (e.g. a
    secretary-general's 待辦事項 message) into a clean, categorized,
    actionable checklist. Returns {"organized": str, "error": str?}.
    Strictly preserves the original items — the AI groups/clarifies wording,
    it does not invent new tasks."""
    prompt = (
        f"以下是微國家組織（ICEA）秘書處的一則待辦/事項訊息原文，請幫忙整理成清楚、"
        f"可執行的事項清單。\n\n"
        f"─── 原始訊息 ───\n{raw_text}\n─── 原始訊息結束 ───\n\n"
        f"請依照以下規則整理：\n"
        f"1. 找出訊息中每一項獨立的待辦事項，不可遺漏、不可增加原文沒有的新事項。\n"
        f"2. 依性質將事項分組歸類（例如：組織內部事務、對外協調、制度建設、人事/會籍等），"
        f"組別名稱請依實際內容自行擬定，不要硬套。\n"
        f"3. 每項事項用一行簡潔清楚地重新表述（可以讓語意更明確，但不能改變原意），"
        f"前面加上 `☐ `。\n"
        f"4. 如果某項事項描述模糊、看不出具體該怎麼做，在該行後面用「（建議：...）」的"
        f"格式簡短補充一個讓它更可執行的具體化建議。\n"
        f"5. 最後加一段「💡 整體建議」，簡短點出目前事項清單有沒有優先順序建議、"
        f"是否有事項看起來互相關聯可以合併處理、或有沒有遺漏常見的秘書處工作面向。\n"
        f"6. 使用繁體中文，語氣專業、簡潔。使用 Discord Markdown（**粗體**用於分組標題）。\n"
        f"7. 直接輸出整理後的內容，不要加開場白或「以下是整理結果」這類贅語。"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "你是微國家組織（micronation）秘書處的行政幕僚 AI，擅長把零散的待辦事項"
                "整理成清楚、可執行、有條理的清單。你尊重原文的每一項內容，絕不擅自增減"
                "事項，只負責讓表達更清楚、分類更合理。用繁體中文回答。"
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        result = await call_chat_api(
            messages,
            {"api_url": ai_settings["api_url"], "api_key": ai_settings["api_key"], "model": ai_settings.get("model", "gpt-4o-mini"), "model_fallback_chain": ai_settings.get("model_fallback_chain", "")},
            max_tokens=1500, fallback_mode="full", category="admin",
        )
        text = result.get("content", "") if isinstance(result, dict) else ""
        if not text:
            return {"error": "AI 回應為空"}
        return {"organized": text.strip()}
    except asyncio.TimeoutError:
        return {"error": "AI 回應逾時，請稍後再試一次"}
    except Exception as e:
        print(f"⚠️ AI 整理事項呼叫失敗：{e}")
        return {"error": "AI 暫時沒有給出有效回覆，可能是模型當下比較忙，稍後再試一次應該就能過"}


@app_commands.context_menu(name="AI整理事項")
async def ai_organize_agenda(interaction: discord.Interaction, message: discord.Message):
    """右鍵訊息 → 應用程式 → AI整理事項。抓取該訊息的文字內容，交給 AI
    分析整理成分類清楚、可執行的事項清單，公開回覆在頻道中方便大家對照。"""
    # 限管理員或機器人擁有者使用（跟其他管理性質指令一致）——避免任何人
    # 對任意訊息亂點造成 AI 呼叫量暴增。
    if not is_owner(interaction):
        if not (interaction.guild and interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ 此功能僅限管理員使用。", ephemeral=True)
            return

    content = (message.content or "").strip()
    if not content and message.embeds:
        parts = []
        for e in message.embeds:
            if e.title:
                parts.append(e.title)
            if e.description:
                parts.append(e.description)
            for f in e.fields:
                parts.append(f"{f.name}：{f.value}")
        content = "\n".join(parts).strip()

    if not content:
        await interaction.response.send_message("❌ 這則訊息沒有可分析的文字內容。", ephemeral=True)
        return

    content = content[:3000]  # 避免超長訊息把 prompt 撐爆

    await interaction.response.defer()  # 公開回覆，讓其他人也能看到整理結果

    result = await _organize_agenda_items(content, ai_settings)
    if "error" in result:
        await interaction.followup.send(f"❌ 整理失敗：{result['error']}", ephemeral=True)
        return

    organized = result["organized"]
    embed = discord.Embed(
        title="📋 AI 整理事項",
        description=organized[:4096],
        color=discord.Color.blurple(),
    )
    embed.add_field(name="原始訊息", value=f"[點此查看]({message.jump_url}) · 作者：{message.author.display_name}", inline=False)
    embed.set_footer(text=f"由 {interaction.user.display_name} 發起整理")
    embed.timestamp = interaction.created_at

    await interaction.followup.send(embed=embed)
    # 若整理內容過長被截斷，額外用一則訊息補完剩餘部分
    if len(organized) > 4096:
        remainder = organized[4096:]
        for i in range(0, len(remainder), 1900):
            await interaction.followup.send(remainder[i:i + 1900])


# ──────────────────────────────────────────────
# 用戶分析系統
# ──────────────────────────────────────────────

async def _fetch_user_messages(guild, user_id: int, limit: int = 100, overall_timeout: float = 40.0) -> list:
    """Fetch a user's recent messages across all text channels and forum
    threads in the guild — CONCURRENTLY, with a hard overall time budget.

    The original version awaited every channel and every archived forum
    thread ONE AT A TIME, sequentially. On a server with a few hundred
    channels/threads (this one has ~234 channels) that easily took minutes
    per call — long enough that the command appeared to just hang forever,
    and the AI API was never even reached (no request ever left the bot).
    This version scans all sources in parallel (bounded concurrency) and
    gives up on individual slow sources instead of blocking on them, so the
    whole scan always finishes within `overall_timeout` seconds and returns
    whatever was collected — best-effort, but it always returns.

    Returns a list of {"channel": str, "content": str, "date": str} dicts.
    Skips bot messages, empty messages, and test/log channels (same
    exclusions as the search indexer)."""
    _skip_keywords = {"測試", "test", "log", "紀錄", "ai-log", "bot-log"}
    _t0 = _time.time()

    async def _scan_one(source, label: str) -> list:
        """Scan a single channel/thread's history for this user's messages,
        with its own short timeout so one slow/huge channel can't eat the
        whole budget on its own."""
        out = []
        try:
            async def _do_scan():
                async for msg in source.history(limit=limit, oldest_first=False):
                    if msg.author.id != user_id:
                        continue
                    if not msg.content or msg.content.strip().startswith("/"):
                        continue
                    out.append({
                        "channel": label,
                        "content": msg.content.strip()[:300],
                        "date": (msg.created_at + timedelta(hours=8)).strftime("%Y-%m-%d"),
                    })
            await asyncio.wait_for(_do_scan(), timeout=10)
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            pass
        except Exception as e:
            print(f"⚠️ 用戶訊息掃描「{label}」失敗：{e}")
        return out

    # Build the list of sources to scan: all text channels + currently open
    # forum threads (cheap — already cached client-side, no extra API call)
    # + a CAPPED number of archived forum threads per forum. Archived
    # threads are capped low on purpose: a busy proposal forum can
    # accumulate hundreds of them over time, and for a personality snapshot
    # recent activity matters far more than every old archived thread —
    # scanning them all was the actual root cause of the hang.
    sources = []
    for ch in guild.text_channels:
        if any(sk in ch.name.lower() for sk in _skip_keywords):
            continue
        sources.append((ch, f"#{ch.name}"))
    for ch in guild.forums:
        for thread in ch.threads:  # open threads, already in cache
            sources.append((thread, f"📋 {ch.name} > {thread.name}"))
        try:
            async def _list_archived():
                found = []
                async for thread in ch.archived_threads(limit=20):
                    found.append(thread)
                return found
            archived = await asyncio.wait_for(_list_archived(), timeout=6)
            for thread in archived:
                sources.append((thread, f"📋 {ch.name} > {thread.name}"))
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            pass
        except Exception as e:
            print("⚠️ 靜默例外:", e)

    print(f"📊 用戶訊息掃描：{len(sources)} 個頻道/討論串，開始並行抓取（總預算 {overall_timeout:.0f}s）...")

    # Scan all sources CONCURRENTLY with bounded parallelism, then hard-cap
    # the total wait — whatever hasn't finished by then gets cancelled and
    # we proceed with whatever we've got instead of hanging indefinitely.
    sem = asyncio.Semaphore(12)

    async def _bounded_scan(source, label):
        async with sem:
            return await _scan_one(source, label)

    tasks = [asyncio.create_task(_bounded_scan(s, lbl)) for s, lbl in sources]
    done, pending = await asyncio.wait(tasks, timeout=overall_timeout) if tasks else (set(), set())

    for t in pending:
        t.cancel()

    messages_collected = []
    for t in done:
        try:
            messages_collected.extend(t.result())
        except Exception as e:
            print("⚠️ 靜默例外:", e)

    elapsed = _time.time() - _t0
    _timeout_note = f"（{len(pending)} 個來源逾時被取消）" if pending else ""
    print(f"📊 用戶訊息掃描完成：{len(messages_collected)} 則訊息，{len(done)}/{len(sources)} 來源完成，"
          f"耗時 {elapsed:.1f}s{_timeout_note}")

    # Sort by date descending, cap at 200 total
    messages_collected.sort(key=lambda m: m["date"], reverse=True)
    return messages_collected[:200]


async def _analyze_user(user_name: str, messages: list, ai_settings: dict) -> dict:
    """Call the AI to analyze a user based on their message history.
    Returns {"analysis": str, "mbti": str, "one_liner": str, "error": str?}."""
    if not messages:
        return {"error": "沒有找到該用戶的訊息紀錄"}

    # Build a compact transcript for the AI
    transcript_parts = []
    for m in messages[:150]:  # cap at 150 messages to keep prompt manageable
        transcript_parts.append(f"[{m['date']}][{m['channel']}] {m['content']}")
    transcript = "\n".join(transcript_parts)

    # Truncate to ~8000 chars to avoid blowing the token budget on free APIs
    if len(transcript) > 8000:
        transcript = transcript[:8000] + "\n...（已截斷）"

    prompt = (
        f"你是微國家社群的心理分析專家。以下是「{user_name}」在 Discord 伺服器中的歷史訊息紀錄。"
        f"請根據這些訊息分析這個人的性格特徵和行為模式。\n\n"
        f"─── 訊息紀錄 ───\n"
        f"{transcript}\n\n"
        f"請嚴格按以下格式回覆（不要加其他多餘內容）：\n"
        f"分析：（200-400字的中文分析，描述該用戶的發言風格、關注話題、"
        f"互動方式、情緒傾向、社群角色等）\n"
        f"MBTI：（16型人格中的哪一型，附一句簡短理由，格式如「INTJ — 因為...」）\n"
        f"一句話：（用一句話送給這個人，可以是鼓勵、吐槽或觀察，語氣自然不造作）\n"
        f"⚠️ 以上分析僅基於有限的 Discord 訊息，僅供娛樂參考，不代表專業心理評估。"
    )

    messages_payload = [
        {
            "role": "system",
            "content": (
                "你是一位擅長從文字行為分析性格的專家，用繁體中文回答。"
                "你的分析應該客觀但不冷冰冰，有觀察力但不過度解讀。"
                "MBTI 判斷要基於訊息中展現出的實際溝通風格和思考方式，不要勉強套型。"
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        result = await call_chat_api(
            messages_payload,
            {
                "api_url": ai_settings.get("api_url", ""),
                "api_key": ai_settings.get("api_key", ""),
                "model": ai_settings.get("model", "gpt-4o-mini"),
                "model_fallback_chain": ai_settings.get("model_fallback_chain", ""),
            },
            max_tokens=1500,
            fallback_mode="full",
            category="admin",
        )
        text = result.get("content", "") if isinstance(result, dict) else ""
        if not text:
            return {"error": "AI 回應為空"}

        import re as _re

        # Parse analysis
        analysis_match = _re.search(r'分析[：:]\s*(.+?)(?=MBTI[：:]|$)', text, _re.DOTALL)
        analysis = analysis_match.group(1).strip() if analysis_match else ""

        # Parse MBTI
        mbti_match = _re.search(r'MBTI[：:]\s*(.+?)(?=一句話[：:]|$)', text, _re.DOTALL)
        mbti = mbti_match.group(1).strip() if mbti_match else ""

        # Parse one-liner
        oneliner_match = _re.search(r'一句話[：:]\s*(.+?)(?=⚠️|$)', text, _re.DOTALL)
        one_liner = oneliner_match.group(1).strip() if oneliner_match else ""

        if not analysis:
            analysis = text[:500]

        return {"analysis": analysis, "mbti": mbti, "one_liner": one_liner}
    except asyncio.TimeoutError:
        return {"error": "AI 回應逾時，請稍後再試一次"}
    except Exception as e:
        print(f"⚠️ 用戶分析 AI 呼叫失敗：{e}")
        if _is_api_unavailable(str(e)):
            return {"error": _get_entertainment_unavailable_msg()}
        return {"error": "AI 暫時沒有給出有效回覆，稍後再試一次應該就能過"}


class AnalyzeGroup(app_commands.Group):
    """用戶分析指令群組"""

    def __init__(self):
        super().__init__(name="analyze", description="用戶分析系統")

    @app_commands.command(name="user", description="分析指定用戶的發言風格、MBTI 人格分析與一句話（機器人擁有者限定）")
    @app_commands.describe(user="要分析的用戶")
    async def analyze_user(self, interaction: discord.Interaction, user: discord.Member):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        await interaction.response.defer()

        user_name = user.display_name
        user_id = user.id

        progress_msg = await interaction.followup.send(f"🔍 正在抓取「{user_name}」的歷史訊息，請稍候...")

        # Fetch user messages (bounded to ~40s max — always returns, never hangs)
        messages = await _fetch_user_messages(interaction.guild, user_id, limit=100)

        if not messages:
            await progress_msg.edit(content=f"❌ 沒有找到「{user_name}」的訊息紀錄。")
            return

        print(f"📊 用戶分析：抓到 {len(messages)} 則訊息，呼叫 AI 分析中...")
        try:
            await progress_msg.edit(content=f"🧠 已抓到 {len(messages)} 則訊息，AI 分析中，請稍候...")
        except (discord.NotFound, discord.HTTPException):
            pass

        # Use the briefing AI settings (more reliable)
        result = await _analyze_user(user_name, messages, ai_settings)

        if "error" in result:
            if _is_api_unavailable(result.get("error", "")):
                await interaction.followup.send(_get_entertainment_unavailable_msg())
            else:
                await interaction.followup.send(f"❌ 分析失敗：{result['error']}")
            return

        analysis = result["analysis"]
        mbti = result["mbti"]
        one_liner = result["one_liner"]

        embed = discord.Embed(
            title=f"🔍 用戶分析：{user_name}",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="📝 行為分析",
            value=analysis[:1024] if analysis else "（無分析）",
            inline=False,
        )
        embed.add_field(
            name="🧠 MBTI 人格分析",
            value=mbti[:1024] if mbti else "（無法判斷）",
            inline=False,
        )
        embed.add_field(
            name="💬 一句話",
            value=f"「{one_liner}」" if one_liner else "（無）",
            inline=False,
        )
        embed.add_field(
            name="⚠️ 免責聲明",
            value="以上分析僅基於 Discord 訊息紀錄，由 AI 生成，僅供娛樂參考，不代表專業心理評估。",
            inline=False,
        )
        embed.set_footer(text=f"分析 {len(messages)} 則訊息 | 由 {interaction.user.display_name} 發起")
        embed.timestamp = interaction.created_at

        await interaction.followup.send(embed=embed)


# ──────────────────────────────────────────────
# 會員國註冊系統
# ──────────────────────────────────────────────

MEMBER_NATIONS_FILE = os.path.join(DATA_DIR, "member_nations.json")

# Each entry:
#   {id, guild_id, name_zh, name_en, iso_code, representatives: [user_id, ...],
#    registered_by, registered_date, status: "active"|"inactive", notes}
_member_nations = {"entries": []}


def save_member_nations():
    os.makedirs(os.path.dirname(MEMBER_NATIONS_FILE), exist_ok=True)
    _save_json_file(MEMBER_NATIONS_FILE, _member_nations)


def load_member_nations():
    global _member_nations
    try:
        if os.path.exists(MEMBER_NATIONS_FILE):
            with open(MEMBER_NATIONS_FILE, "r", encoding="utf-8") as f:
                _member_nations = json_module.load(f)
            if "entries" not in _member_nations:
                _member_nations = {"entries": _member_nations if isinstance(_member_nations, list) else []}
            print(f"✅ 載入會員國資料：{len(_member_nations['entries'])} 筆")
    except Exception as e:
        print(f"⚠️ 載入會員國資料失敗：{e}")


# ── Discord slash command group ──

class MemberNationGroup(app_commands.Group):
    """會員國註冊與管理指令群組"""

    # 四個類別
    CATEGORIES = {
        "成員國": "member",
        "理事國": "council",
        "觀察國": "observer",
        "已除籍": "removed",
    }
    # 反向映射（英文 -> 中文）
    CATEGORY_LABELS = {
        "member": "成員國",
        "council": "理事國",
        "observer": "觀察國",
        "removed": "已除籍",
    }
    CATEGORY_EMOJI = {
        "member": "🟢",
        "council": "🔵",
        "observer": "🟡",
        "removed": "⚫",
    }
    CATEGORY_COLOR = {
        "member": discord.Color.green(),
        "council": discord.Color.blue(),
        "observer": discord.Color.gold(),
        "removed": discord.Color.dark_gray(),
    }

    def __init__(self):
        super().__init__(name="nation", description="會員國註冊與管理")

    @app_commands.command(name="register", description="註冊會員國")
    @app_commands.describe(
        name_zh="國名（中文）",
        name_en="國名（英文）",
        iso_code="ISO-3166 國家代碼（如 TW、JP、US，2-3碼）",
        category="會員國類別：成員國 / 理事國 / 觀察國",
        rep1="第一位派駐代表（@用戶）",
        rep2="第二位派駐代表（選填）",
        rep3="第三位派駐代表（選填）",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="成員國", value="member"),
        app_commands.Choice(name="理事國", value="council"),
        app_commands.Choice(name="觀察國", value="observer"),
    ])
    async def register(
        self,
        interaction: discord.Interaction,
        name_zh: str,
        name_en: str,
        iso_code: str,
        category: app_commands.Choice[str] = None,
        rep1: discord.Member = None,
        rep2: discord.Member = None,
        rep3: discord.Member = None,
    ):
        # Registration is restricted to Administrator permission (or bot owner) —
        # manage_guild alone is no longer sufficient.
        # Permission: bot owner, Discord admin, or nation_admin_whitelist
        if not is_owner(interaction):
            uid_str = str(interaction.user.id)
            wl = application_settings.get("nation_admin_whitelist", [])
            if not interaction.user.guild_permissions.administrator and uid_str not in [str(w) for w in wl]:
                await interaction.response.send_message("❌ 此指令僅限管理員或白名單使用者使用。", ephemeral=True)
                return

        name_zh = name_zh.strip()
        name_en = name_en.strip()
        iso_code = iso_code.strip().upper()

        if not name_zh or not name_en or not iso_code:
            await interaction.response.send_message("❌ 國名（中英）和 ISO 代碼皆為必填。", ephemeral=True)
            return
        if len(iso_code) < 2 or len(iso_code) > 3:
            await interaction.response.send_message("❌ ISO 代碼應為 2-3 碼英文字母（如 TW、USA）。", ephemeral=True)
            return

        cat_value = category.value if category else "member"

        # Collect representatives (deduplicate, max 3)
        reps_input = [rep1, rep2, rep3]
        seen_ids = set()
        rep_ids = []
        rep_names = []
        for r in reps_input:
            if r and r.id not in seen_ids:
                seen_ids.add(r.id)
                rep_ids.append(r.id)
                rep_names.append(r.display_name)

        # Check for duplicate ISO code in same guild (excluding 已除籍)
        guild_id = interaction.guild_id
        existing = [
            e for e in _member_nations["entries"]
            if int(e.get("guild_id", 0)) == guild_id
            and e.get("iso_code", "").upper() == iso_code
            and e.get("category") != "removed"
        ]
        if existing:
            await interaction.response.send_message(
                f"❌ ISO 代碼 `{iso_code}` 已被註冊：{existing[0]['name_zh']}（{existing[0]['name_en']}）",
                ephemeral=True,
            )
            return

        import uuid as _uuid
        entry = {
            "id": str(_uuid.uuid4()),
            "guild_id": guild_id,
            "name_zh": name_zh,
            "name_en": name_en,
            "iso_code": iso_code,
            "category": cat_value,
            "representatives": rep_ids,
            "representative_names": rep_names,  # for display, updated on load
            "registered_by": interaction.user.id,
            "registered_by_name": interaction.user.display_name,
            "registered_date": (interaction.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M"),
            "notes": "",
        }

        _member_nations["entries"].append(entry)
        save_member_nations()

        cat_label = self.CATEGORY_LABELS.get(cat_value, "成員國")
        cat_emoji = self.CATEGORY_EMOJI.get(cat_value, "🟢")
        cat_color = self.CATEGORY_COLOR.get(cat_value, discord.Color.green())

        # Build confirmation embed
        embed = discord.Embed(
            title=f"{cat_emoji} 會員國註冊成功",
            color=cat_color,
        )
        embed.add_field(name="國名", value=f"{name_zh}（{name_en}）", inline=False)
        embed.add_field(name="ISO 代碼", value=f"`{iso_code}`", inline=True)
        embed.add_field(name="類別", value=f"{cat_emoji} {cat_label}", inline=True)
        rep_mentions = " ".join(f"<@{rid}>" for rid in rep_ids) if rep_ids else "未指定"
        embed.add_field(name="派駐代表", value=rep_mentions, inline=False)
        embed.set_footer(text=f"由 {interaction.user.display_name} 註冊")
        embed.timestamp = interaction.created_at

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="list", description="列出所有會員國（可依類別篩選）")
    @app_commands.describe(category="可選擇只看某個類別")
    @app_commands.choices(category=[
        app_commands.Choice(name="成員國", value="member"),
        app_commands.Choice(name="理事國", value="council"),
        app_commands.Choice(name="觀察國", value="observer"),
        app_commands.Choice(name="已除籍", value="removed"),
    ])
    async def list_nations(self, interaction: discord.Interaction, category: app_commands.Choice[str] = None):
        guild_id = interaction.guildId if hasattr(interaction, 'guildId') else interaction.guild_id
        entries = [e for e in _member_nations["entries"] if e.get("guild_id") == guild_id]

        if category:
            cat_val = category.value
            entries = [e for e in entries if e.get("category", "member") == cat_val]
            filter_label = f"（{self.CATEGORY_LABELS.get(cat_val, cat_val)}）"
        else:
            filter_label = ""

        if not entries:
            await interaction.response.send_message(f"📋 目前沒有符合條件的會員國{filter_label}。")
            return

        # Sort by category order: member, council, observer, removed
        cat_order = {"member": 0, "council": 1, "observer": 2, "removed": 3}
        entries.sort(key=lambda e: cat_order.get(e.get("category", "member"), 99))

        embed = discord.Embed(
            title=f"🌍 會員國一覽{filter_label}",
            color=discord.Color.blue(),
        )
        for e in entries:
            cat = e.get("category", "member")
            cat_emoji = self.CATEGORY_EMOJI.get(cat, "🟢")
            cat_label = self.CATEGORY_LABELS.get(cat, "成員國")
            reps = " ".join(f"<@{rid}>" for rid in e.get("representatives", []))
            embed.add_field(
                name=f"{cat_emoji} {e['name_zh']}（{e['name_en']}）",
                value=f"類別：{cat_emoji} {cat_label}\nISO：`{e['iso_code']}`\n代表：{reps or '未指定'}\n註冊日期：{e.get('registered_date', '未知')}",
                inline=False,
            )

        # Summary counts
        counts = {}
        for e in entries:
            c = e.get("category", "member")
            counts[c] = counts.get(c, 0) + 1
        summary_parts = [f"{self.CATEGORY_EMOJI.get(c,'')} {self.CATEGORY_LABELS.get(c,c)} {n}" for c, n in counts.items()]
        embed.set_footer(text=f"共 {len(entries)} 個會員國｜{'  '.join(summary_parts)}")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="info", description="查詢指定會員國的詳細資訊")
    @app_commands.describe(iso_code="ISO-3166 國家代碼（如 TW、JP）")
    async def nation_info(self, interaction: discord.Interaction, iso_code: str):
        iso_code = iso_code.strip().upper()
        guild_id = interaction.guildId if hasattr(interaction, 'guildId') else interaction.guild_id
        entry = next(
            (e for e in _member_nations["entries"]
             if e.get("guild_id") == guild_id
             and e.get("iso_code", "").upper() == iso_code),
            None,
        )

        if not entry:
            await interaction.response.send_message(f"❌ 找不到 ISO 代碼為 `{iso_code}` 的會員國。", ephemeral=True)
            return

        cat = entry.get("category", "member")
        cat_emoji = self.CATEGORY_EMOJI.get(cat, "🟢")
        cat_label = self.CATEGORY_LABELS.get(cat, "成員國")
        cat_color = self.CATEGORY_COLOR.get(cat, discord.Color.green())

        embed = discord.Embed(
            title=f"{cat_emoji} {entry['name_zh']}（{entry['name_en']}）",
            color=cat_color,
        )
        embed.add_field(name="ISO 代碼", value=f"`{entry['iso_code']}`", inline=True)
        embed.add_field(name="類別", value=f"{cat_emoji} {cat_label}", inline=True)
        reps = " ".join(f"<@{rid}>" for rid in entry.get("representatives", []))
        embed.add_field(name="派駐代表", value=reps or "未指定", inline=False)
        embed.add_field(name="註冊者", value=f"{entry.get('registered_by_name', '未知')}", inline=True)
        embed.add_field(name="註冊日期", value=entry.get("registered_date", "未知"), inline=True)
        if entry.get("notes"):
            embed.add_field(name="備註", value=entry["notes"][:1024], inline=False)
        embed.timestamp = interaction.created_at

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="recategorize", description="變更會員國的類別")
    @app_commands.describe(
        iso_code="ISO-3166 國家代碼",
        category="新類別：成員國 / 理事國 / 觀察國 / 已除籍",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="成員國", value="member"),
        app_commands.Choice(name="理事國", value="council"),
        app_commands.Choice(name="觀察國", value="observer"),
        app_commands.Choice(name="已除籍", value="removed"),
    ])
    async def recategorize(self, interaction: discord.Interaction, iso_code: str, category: app_commands.Choice[str]):
        # Permission: bot owner, Discord admin, or nation_admin_whitelist
        if not is_owner(interaction):
            uid_str = str(interaction.user.id)
            wl = application_settings.get("nation_admin_whitelist", [])
            if not interaction.user.guild_permissions.administrator and uid_str not in [str(w) for w in wl]:
                await interaction.response.send_message("❌ 此指令僅限管理員或白名單使用者使用。", ephemeral=True)
                return

        iso_code = iso_code.strip().upper()
        guild_id = interaction.guildId if hasattr(interaction, 'guildId') else interaction.guild_id
        entry = next(
            (e for e in _member_nations["entries"]
             if e.get("guild_id") == guild_id
             and e.get("iso_code", "").upper() == iso_code),
            None,
        )

        if not entry:
            await interaction.response.send_message(f"❌ 找不到 ISO 代碼為 `{iso_code}` 的會員國。", ephemeral=True)
            return

        old_cat = entry.get("category", "member")
        new_cat = category.value
        entry["category"] = new_cat
        save_member_nations()

        old_label = self.CATEGORY_LABELS.get(old_cat, old_cat)
        new_label = self.CATEGORY_LABELS.get(new_cat, new_cat)
        old_emoji = self.CATEGORY_EMOJI.get(old_cat, "🟢")
        new_emoji = self.CATEGORY_EMOJI.get(new_cat, "🟢")

        embed = discord.Embed(
            title=f"🔄 類別變更成功",
            color=self.CATEGORY_COLOR.get(new_cat, discord.Color.green()),
        )
        embed.add_field(name="國名", value=f"{entry['name_zh']}（{entry['name_en']}）", inline=False)
        embed.add_field(name="ISO 代碼", value=f"`{entry['iso_code']}`", inline=True)
        embed.add_field(name="變更", value=f"{old_emoji} {old_label} → {new_emoji} {new_label}", inline=False)
        embed.set_footer(text=f"由 {interaction.user.display_name} 變更")
        embed.timestamp = interaction.created_at

        await interaction.response.send_message(embed=embed)


# ──────────────────────────────────────────────
# 永久知識庫（每日凌晨三點 AI 整理重點）
# ──────────────────────────────────────────────

KNOWLEDGE_BASE_FILE = os.path.join(DATA_DIR, "knowledge_base.json")
CORRECTIONS_FILE = os.path.join(DATA_DIR, "corrections.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")

PROPOSAL_SETTINGS_FILE = os.path.join(DATA_DIR, "proposal_settings.json")
PROPOSALS_FILE = os.path.join(DATA_DIR, "proposals.json")
SCHEDULE_SETTINGS_FILE = os.path.join(DATA_DIR, "schedule_settings.json")

# ──────────────────────────────────────────────
# 自動排程／會議通知系統
