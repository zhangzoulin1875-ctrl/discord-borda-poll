# ═════════════════════════════════════════════════════════════════
# Module: 90_tally (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

# ── FIX：支援「純空白分隔、完全沒有句點/符號」的格式，例如「1 a」「2 i」——
# 之前規則裡分隔符號 [.、)：:] 是必填的，只有空格不算數，導致這種完全合法、
# 常見的編號清單寫法（用空格代替句點）整張票 0 個代碼都抓不到。現在分隔符號
# 可以是「句點類符號（前後可有空白）」或「純粹一個以上的空白」兩者之一。
_NUMBERED_VOTE_RE = re.compile(r'(\d{1,2})(?:\s*[.、)：:]\s*|\s+)([A-Za-z]{1,6})(?![A-Za-z0-9])')


def _extract_vote_tokens(content: str, legend_keys: set, token_type: str) -> list:
    """從一則回覆訊息中，依 token_type 抓出屬於 legend 的候選代碼，並依票面上出現/標示的順序回傳。"""
    if token_type == "emoji":
        return _dedup_preserve_order([tok for tok in _extract_emoji_tokens(content) if tok in legend_keys])

    # text_code 模式：抓「數字.代碼」格式，依數字大小排序還原出投票人標示的偏好順序
    matches = _NUMBERED_VOTE_RE.findall(content)
    parsed = []
    for rank_str, code_str in matches:
        code_up = code_str.upper()
        if code_up in legend_keys:
            try:
                rank_num = int(rank_str)
            except ValueError:
                rank_num = 999
            parsed.append((rank_num, code_up))
    parsed.sort(key=lambda x: x[0])
    return _dedup_preserve_order([c for _, c in parsed])


async def _ai_detect_text_legend(op_text: str) -> dict:
    """最後手段：貼文既沒有 Emoji，也沒有符合「代碼+中文名稱」規律格式的候選人清單時，
    改用 AI 直接從原文判讀候選人代碼對照表與計票方式。"""
    fallback = {
        "mode": "single",
        "legend": {},
        "notes": "AI 無法使用，且找不到可辨識的候選人代碼格式，無法自動計票，請用 legend 參數手動指定。",
    }
    ps_ai = proposal_settings.get("ai_settings", {})
    settings = {
        "api_url": ps_ai.get("api_url") or chat_ai_settings.get("api_url", ""),
        "api_key": ps_ai.get("api_key") or chat_ai_settings.get("api_key", ""),
        "model": ps_ai.get("model") or chat_ai_settings.get("model", "gpt-4o-mini"),
        "system_prompt": "你是投票制度分析助手，負責判讀 Discord 論壇投票貼文的計票規則。",
    }
    if not settings["api_url"] or not settings["api_key"]:
        return fallback

    prompt = (
        "以下是一篇 Discord 論壇投票貼文的原文內容：\n\n"
        f"「{op_text[:2000]}」\n\n"
        "這篇貼文說明了一個投票，但投票時使用的並非 Emoji，而是文字/英文字母代碼"
        "（例如候選人清單「a大斯皇帝國」代表代碼 a 對應候選人「大斯皇帝國」，"
        "投票者會回覆「1.a 2.b 3.c」這種格式來投票）。\n\n"
        "請從文字中判斷：\n"
        "1. mode：single（每人選一個）或 ranked（依偏好排序多個，即波達計數法）。\n"
        "2. legend：把貼文中列出的每個代碼對應到候選人/選項名稱，"
        '格式為 {"代碼": "候選人名稱"}。'
        "如果貼文裡完全沒有清楚列出代碼對應表，legend 請回傳空物件 {}，不要亂猜。\n\n"
        "請直接回覆 JSON（不要加 markdown code block），格式：\n"
        '{"mode": "single", "legend": {"a": "候選人A", "b": "候選人B"}, "notes": "簡短說明"}\n'
        "只回覆 JSON，不要加其他文字。"
    )

    try:
        result = await call_ai_api(prompt, settings)
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json_module.loads(result)
        mode = parsed.get("mode", "single")
        if mode not in ("single", "ranked"):
            mode = "single"
        legend_raw = parsed.get("legend", {}) or {}
        legend = {}
        for k, v in legend_raw.items():
            k = str(k)
            if re.fullmatch(r'[A-Za-z0-9]{1,6}', k):
                k = k.upper()
            legend[k] = str(v)
        return {"mode": mode, "legend": legend, "notes": str(parsed.get("notes", ""))[:200]}
    except Exception as e:
        print(f"⚠️ 計票 AI 文字判讀失敗，改用保底規則：{e}")
        return fallback


async def _detect_thread_vote_scheme(op_text: str) -> dict:
    """自動判斷整個貼文的投票制度：
    1. 若原po文字含 Emoji → 走 Emoji 對照模式（沿用既有 AI 判讀）。
    2. 若沒有 Emoji，但有「代碼+候選人名稱」的清單格式（例如 a大斯皇帝國）→ 直接用規則解析，
       不需要 AI，最準確也最省 token（本組織實際選舉貼文最常見的格式）。
    3. 兩者都偵測不到時，才用 AI 直接從原文判讀（最後手段，格式太特殊時使用）。
    計票方式（單選/排序）優先看貼文有沒有「波達計數法」等明確關鍵字，沒有才交給 AI／規則猜測。
    回傳：{"token_type": "emoji"|"text_code", "legend": {...}, "mode": "single"|"ranked", "notes": str}
    """
    op_emoji_tokens = _dedup_preserve_order(_extract_emoji_tokens(op_text))
    keyword_mode = _detect_mode_from_text(op_text)

    if op_emoji_tokens:
        ai_result = await _ai_detect_vote_legend(op_text, op_emoji_tokens)
        return {
            "token_type": "emoji",
            "legend": ai_result["legend"],
            "mode": keyword_mode or ai_result["mode"],
            "notes": ai_result.get("notes", ""),
        }

    text_legend = _extract_text_code_legend(op_text)
    if len(text_legend) >= 2:
        mode = keyword_mode or ("ranked" if len(text_legend) > 2 else "single")
        notes = "貼文沒有使用 Emoji，已依候選人代碼清單（例如「a候選人名」）自動比對文字代碼進行計票。"
        if keyword_mode == "ranked":
            notes += "偵測到「波達計數法」關鍵字，確認為排序偏好投票。"
        return {"token_type": "text_code", "legend": text_legend, "mode": mode, "notes": notes}

    ai_result = await _ai_detect_text_legend(op_text)
    return {
        "token_type": "text_code",
        "legend": ai_result["legend"],
        "mode": keyword_mode or ai_result["mode"],
        "notes": ai_result.get("notes", ""),
    }


def _compute_tally(ballots: dict, legend: dict, mode: str) -> dict:
    """ballots: voter_key -> ordered list of distinct legend tokens they cast（Emoji 或文字代碼皆可）.
    legend: token -> candidate label.
    回傳每個候選人 label 的分數/票數。

    ── FIX：波達計分公式對照人工計票結果修正 ──
    用使用者提供的 15 筆真實選票 + 主席公佈的人工計票結果反推驗證後發現：
    這裡原本用的是「學術教科書版」波達計數法，n 位候選人，第1名拿 n-1 分、
    最後一名拿 0 分（例如 12 人：第1名11分...第12名0分）。但這個組織實際
    人工計票用的是更常見的「n位候選人、第1名拿 n 分、最後一名拿 1 分」
    （例如 12 人：第1名12分...第12名1分）——用這個公式重算，12 個候選人中
    有 8 個跟主席公佈的分數逐分不差地吻合，其餘 4 個也只差 3~4 分（完全
    符合人工用手加總 15 張票 x 12 個名次時偶爾算錯個幾分的合理誤差範圍）。
    這才是這幾輪一直對不起來的真正原因——不是抓票抓錯，是計分公式本身
    跟這個組織實際採用的波達計數法版本不一樣。"""
    n = len(legend)
    scores = {label: 0 for label in legend.values()}
    for ordered_tokens in ballots.values():
        if mode == "ranked":
            for rank_pos, tok in enumerate(ordered_tokens):
                label = legend.get(tok)
                if label is not None and rank_pos < n:
                    scores[label] = scores.get(label, 0) + max(0, n - rank_pos)
        else:
            if ordered_tokens:
                label = legend.get(ordered_tokens[0])
                if label is not None:
                    scores[label] = scores.get(label, 0) + 1
    return scores


def _looks_like_possible_ballot(content: str, found: list, legend: dict) -> bool:
    """便宜的預篩：判斷這則訊息值不值得花 AI 額度去做逐訊息智能判讀。
    避免對純聊天/純圖片（完全沒有文字、或內容太短不像選票）也呼叫 AI，
    浪費資源；但只要有一點點「可能是選票」的訊號，就寧可花這次 AI 呼叫，
    也不要用格式規則直接錯殺一張完整的票。"""
    stripped = (content or "").strip()
    if not stripped:
        return False  # 純圖片/貼圖，完全沒有文字內容，AI 也判讀不出東西
    if len(stripped) < 4 and not found:
        return False  # 太短又完全沒抓到任何代碼，不像選票（例如「ok」、單個字）
    if found:
        return True  # regex 已經抓到至少幾個合法代碼，很可能是投票，值得讓 AI 判讀救回完整票
    if re.search(r'\d', stripped):
        return True  # 訊息裡有數字，可能是名次編號只是格式跟 regex 預期的不完全一樣
    for name in legend.values():
        if name and name in stripped:
            return True  # 提到候選人全名，很可能就是在投票
    return False


async def _ai_judge_ballot(content: str, legend: dict, n_candidates: int) -> dict:
    """AI 逐訊息智能判讀一則論壇回覆是否為完整有效的排序選票（波達計數法）。
    不只依賴格式規則（半形/全形數字字母、有沒有打句點、代碼跟候選人全名是否
    黏在一起等），而是真正理解語意去抓出投票者「從第1名到最後一名」的完整
    排序意圖——這樣才不會因為投票者漏打一個句點、用了全形字元，或把代碼跟
    國名寫在一起，就把一張完整有效的票錯殺成廢票或閒聊噪音。

    回傳 {"is_vote": bool, "ranking": [代碼,...], "complete": bool, "reason": str}
    這是行政功能（正式選舉計票），使用 fallback_mode="full"（主 API 故障時
    直接切換備援 API，不受聊天限速/每日配額限制），確保計票結果可靠。"""
    fallback = {"is_vote": False, "ranking": [], "complete": False, "reason": "AI 判讀失敗或未設定 AI，保留原判定"}
    if not chat_ai_settings.get("api_url") or not chat_ai_settings.get("api_key"):
        return fallback

    candidate_list = "\n".join(f"{code} = {name}" for code, name in legend.items())
    prompt = (
        f"這是一場排序偏好投票（波達計數法），共有 {n_candidates} 位候選人，代碼對照如下：\n"
        f"{candidate_list}\n\n"
        "請判讀以下這則論壇回覆訊息，抓出投票者「從第1名到最後一名」完整的候選人代碼排序。\n\n"
        "重要規則：\n"
        "1. 訊息格式可能不規則——半形或全形數字/字母、漏打句點或其他分隔符號、"
        "代碼跟候選人全名黏在一起寫（例如「1.e厂万共和國」或「1e厂万共和國」）、"
        "多餘的空白、暱稱、國名重複等。只要人類讀者能清楚看懂「第幾名選了誰」，"
        "就要判定為合法格式並正確抓出來，不要因為格式瑕疵就判定失敗。\n"
        f"2. 只有在「明顯真的沒有排完全部 {n_candidates} 位候選人」（少了幾位、"
        "代碼寫錯到完全對不到任何候選人、或訊息根本不是選票，只是閒聊/純圖片沒有文字）"
        "時，才視為不完整或不是選票。\n"
        "3. 絕對不要自己「補完」或「猜測」缺漏的名次——沒被明確提到的候選人就是沒排到，"
        "不要因為想湊滿而亂猜順序。\n\n"
        "只回覆 JSON（不要加 markdown code block、不要有其他文字），格式：\n"
        '{"is_vote": true/false, "ranking": ["代碼1","代碼2","...按名次順序排列到最後一名"], '
        '"complete": true/false, "reason": "簡短說明（尤其是判定不完整/非選票的原因）"}\n\n'
        f"訊息內容：\n\"\"\"\n{content[:1500]}\n\"\"\""
    )
    try:
        msg = await call_chat_api(
            [
                {"role": "system", "content": "你是嚴謹的選票判讀助手，只回覆 JSON，不要有其他文字。"},
                {"role": "user", "content": prompt},
            ],
            chat_ai_settings,
            max_tokens=500,
            timeout_total=40,
            timeout_read=30,
            is_background=True,
            fallback_mode="full", category="admin",  # 行政功能（正式選舉計票）— 主 API 故障直接切備援，不受聊天限速影響
        )
        raw = (msg.get("content") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json_module.loads(raw)
        ranking_raw = parsed.get("ranking", []) or []
        ranking = []
        seen = set()
        for code in ranking_raw:
            code_up = str(code).upper().strip()
            if code_up in legend and code_up not in seen:
                seen.add(code_up)
                ranking.append(code_up)
        is_complete = bool(parsed.get("complete", False)) and len(ranking) == n_candidates
        return {
            "is_vote": bool(parsed.get("is_vote", False)),
            "ranking": ranking,
            "complete": is_complete,
            "reason": str(parsed.get("reason", ""))[:200],
        }
    except Exception as e:
        print(f"⚠️ AI 選票判讀失敗：{e}")
        return fallback


async def _run_forum_tally(thread: discord.Thread, manual_legend_str: str = "", mode_override: str = "auto"):
    """核心計票流程。回傳一個 dict，包含所有計票結果與統計資訊，供指令與後續公佈使用。"""
    # 1. 取得原po內容
    starter = thread.starter_message
    if starter is None:
        try:
            starter = await asyncio.wait_for(thread.fetch_message(thread.id), timeout=8)
        except Exception:
            starter = None

    op_text = _gather_thread_text(starter)

    manual_legend = _parse_manual_legend(manual_legend_str)
    if manual_legend:
        # 手動指定：只信任手動給的對照表，並補上原po偵測到但未手動指定的 Emoji（用 Emoji 本身當名稱）
        op_emoji_tokens = _dedup_preserve_order(_extract_emoji_tokens(op_text))
        legend = dict(manual_legend)
        for tok in op_emoji_tokens:
            if tok not in legend:
                legend[tok] = tok
        token_type = _guess_token_type_from_legend(legend)
        detected_mode = _detect_mode_from_text(op_text) or "single"
        ai_notes = "使用手動指定的選項對照表。"
    else:
        scheme = await _detect_thread_vote_scheme(op_text)
        legend = scheme["legend"]
        token_type = scheme["token_type"]
        detected_mode = scheme["mode"] or "single"
        ai_notes = scheme.get("notes", "")

    final_mode = detected_mode if mode_override == "auto" else mode_override
    legend_keys = set(legend.keys())

    # 2. 掃描回覆訊息，蒐集每位使用者的最後一筆有效投票
    # ── 排序投票（波達計數法）的「完整性」規則 ──
    # 論壇雜訊很多：有投錯代碼的、只填了一部分選項就沒填完（變成廢票）的、
    # 直接發圖片投票（完全沒有可解析文字）的。人工計票時，沒有把所有候選人
    # 都排完序的票會直接視為廢票不計分——但先前 AI 自動計票只要抓到幾個合法
    # 代碼就照樣給部分分數，等於讓不完整/投錯的票混進正式計票，跟人工結果
    # 兜不起來。修正：ranked 模式下，一則回覆必須「剛好包含全部 n 個候選人
    # 代碼」才算有效票；只包含一部分（無論是因為投錯代碼、故意放棄、還是
    # 只填了 7/12 個）都算廢票，整張不計分，不給部分分數。
    n_candidates = len(legend_keys)
    ballots = {}          # author_id -> ordered token list
    voter_labels = {}      # author_id -> (display_name, raw_country_text)
    voter_last_time = {}   # author_id -> created_at（用於「取最後一筆」判斷)
    skipped_count = 0      # 完全沒有偵測到任何合法代碼（閒聊、純圖片等）
    disputed = []          # 有爭議的投票（單選卻填多個選項等）
    spoiled = []           # 排序投票但沒填完整/代碼有誤的廢票
    excluded_announcements = []  # 明確排除的「開票/計票結果公告」訊息
    ai_recovered = []      # 格式有瑕疵、靠 AI 逐訊息智能判讀救回來的完整票（供複核透明度）

    # 主席/秘書處在投票結束後，通常會在同一個貼文串裡回覆公佈人工計票結果
    # （例如列出每個候選人的總分/總票數，最後宣布誰當選）。這種訊息長得很像
    # 選票（也會提到每個候選人），必須明確排除，絕對不能被誤當成一張選票
    # 去計分或誤判為廢票灌水統計。用關鍵字直接抓出來排除，不只是靠格式不match
    # 這種被動保護。
    _ANNOUNCEMENT_KEYWORDS = ("當選", "開票結果", "計票結果", "投票結果", "人工計票", "公佈結果")

    async for msg in thread.history(limit=None, oldest_first=True):
        if starter is not None and msg.id == starter.id:
            continue
        if msg.author.bot:
            continue
        content = msg.content or ""
        if any(kw in content for kw in _ANNOUNCEMENT_KEYWORDS):
            excluded_announcements.append({
                "author": msg.author.display_name,
                "content": content[:80],
            })
            continue
        found = _extract_vote_tokens(content, legend_keys, token_type)

        # ── AI 逐訊息智能判讀（不只看格式）──
        # 純規則 regex 只認得固定格式（半形數字+固定分隔符號+半形字母代碼）。
        # 但真實投票訊息很雜：全形數字/字母、漏打句點、代碼跟候選人全名黏在
        # 一起寫等等，光靠格式規則永遠在追加新的例外情況、還是會錯殺一些
        # 語意上完全清楚、完整的票。所以 ranked 模式下，只要 regex 沒有剛好
        # 抓到全部 n 個代碼，且這則訊息看起來有點像選票（不是純聊天/純圖片），
        # 就交給 AI 逐則重新判讀語意，而不是直接認定為廢票或閒聊噪音。
        if (
            final_mode == "ranked"
            and n_candidates > 0
            and len(found) != n_candidates
            and _looks_like_possible_ballot(content, found, legend)
        ):
            ai_result = await safe_judge_ballot(content, legend, n_candidates)
            if ai_result["is_vote"] and ai_result["complete"]:
                found = ai_result["ranking"]
                ai_recovered.append({
                    "author": msg.author.display_name,
                    "content": content[:80],
                    "note": "規則比對格式抓不全，AI 逐訊息判讀後確認為完整排序票",
                })
            elif ai_result["is_vote"]:
                spoiled.append({
                    "author": msg.author.display_name,
                    "content": content[:80],
                    "reason": f"AI 判讀：{ai_result['reason'] or '排序不完整'}",
                })
                continue
            else:
                skipped_count += 1
                continue

        if not found:
            skipped_count += 1
            continue

        if final_mode == "single" and len(found) > 1:
            disputed.append({
                "author": msg.author.display_name,
                "content": content[:80],
                "reason": f"單選投票卻偵測到 {len(found)} 個選項",
            })
            continue

        if final_mode == "ranked" and n_candidates > 0 and len(found) < n_candidates:
            spoiled.append({
                "author": msg.author.display_name,
                "content": content[:80],
                "reason": f"只排了 {len(found)}/{n_candidates} 個選項，未完整排序視為廢票",
            })
            continue

        aid = msg.author.id
        prev_time = voter_last_time.get(aid)
        if prev_time is not None and msg.created_at <= prev_time:
            continue  # 已有更新的投票，忽略這筆較舊的（理論上 oldest_first 不會發生，保險起見）

        ballots[aid] = found
        first_line = next((ln.strip() for ln in content.split("\n") if ln.strip()), content.strip())
        voter_labels[aid] = (msg.author.display_name, first_line[:60] or msg.author.display_name)
        voter_last_time[aid] = msg.created_at

    scores = _compute_tally(ballots, legend, final_mode)

    return {
        "op_text": op_text,
        "legend": legend,
        "mode": final_mode,
        "token_type": token_type,
        "ai_notes": ai_notes,
        "scores": scores,
        "ballots": ballots,
        "voter_labels": voter_labels,
        "valid_vote_count": len(ballots),
        "skipped_count": skipped_count,
        "disputed": disputed,
        "spoiled": spoiled,
        "excluded_announcements": excluded_announcements,
        "ai_recovered": ai_recovered,
        "thread_id": thread.id,
        "thread_name": thread.name,
    }


def _build_tally_embed(result: dict) -> discord.Embed:

    mode = result["mode"]
    mode_label = "🔢 排序偏好（波達計數法）" if mode == "ranked" else "☑️ 單選"
    unit = "分" if mode == "ranked" else "票"

    scores = result["scores"]
    ranked_scores = sorted(scores.items(), key=lambda x: -x[1])

    embed = discord.Embed(
        title=f"🗳️ AI 自動計票結果 — {result['thread_name']}",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    token_type_label = "文字/字母代碼（無 Emoji）" if result.get("token_type") == "text_code" else "Emoji"
    embed.add_field(name="偵測到的計票方式", value=f"{mode_label}\n選項代碼類型：{token_type_label}", inline=False)

    legend_lines = [f"{tok} → {label}" for tok, label in result["legend"].items()]
    if legend_lines:
        embed.add_field(name="選項對照", value="\n".join(legend_lines)[:1024], inline=False)

    if ranked_scores:
        medals = ["🥇", "🥈", "🥉"]
        result_lines = []
        for i, (label, score) in enumerate(ranked_scores):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            result_lines.append(f"{prefix} {label} — {score} {unit}")
        embed.add_field(name="計票結果", value="\n".join(result_lines)[:1024], inline=False)
    else:
        embed.add_field(name="計票結果", value="（沒有偵測到任何選項）", inline=False)

    spoiled = result.get("spoiled", [])
    excluded_ann = result.get("excluded_announcements", [])
    ai_recovered = result.get("ai_recovered", [])
    stats_lines = [
        f"✅ 有效投票：{result['valid_vote_count']} 筆",
        f"🚫 已過濾閒聊/圖片/無效訊息：{result['skipped_count']} 筆",
    ]
    if ai_recovered:
        stats_lines.append(f"🤖 AI 判讀救回的票（格式有瑕疵但排序完整）：{len(ai_recovered)} 筆")
    if spoiled:
        stats_lines.append(f"🗑️ 廢票（未完整排序/代碼有誤）：{len(spoiled)} 筆")
    if excluded_ann:
        stats_lines.append(f"📢 已排除的計票結果公告訊息：{len(excluded_ann)} 筆（確認未被誤計為選票）")
    if result["disputed"]:
        stats_lines.append(f"⚠️ 有爭議訊息：{len(result['disputed'])} 筆（需人工複核）")
    embed.add_field(name="統計", value="\n".join(stats_lines), inline=False)

    if ai_recovered:
        recovered_lines = [f"• {d['author']}：{d['note']}" for d in ai_recovered[:8]]
        if len(ai_recovered) > 8:
            recovered_lines.append(f"…等共 {len(ai_recovered)} 筆")
        embed.add_field(name="🤖 AI 判讀救回明細（已計入正式票數）", value="\n".join(recovered_lines)[:1024], inline=False)

    if spoiled:
        spoiled_lines = [
            f"• {d['author']}：{d['reason']}（「{d['content']}」）" for d in spoiled[:8]
        ]
        if len(spoiled) > 8:
            spoiled_lines.append(f"…等共 {len(spoiled)} 筆")
        embed.add_field(name="🗑️ 廢票明細（已排除，不計分）", value="\n".join(spoiled_lines)[:1024], inline=False)

    if result["disputed"]:
        dispute_lines = [
            f"• {d['author']}：{d['reason']}（「{d['content']}」）" for d in result["disputed"][:8]
        ]
        embed.add_field(name="⚠️ 爭議投票明細", value="\n".join(dispute_lines)[:1024], inline=False)

    if result.get("ai_notes"):
        embed.add_field(name="AI 判讀備註", value=result["ai_notes"][:300], inline=False)

    embed.set_footer(text="AI 自動計票 | 如發現異常請秘書處人工複核")
    return embed


# ── Slash Command Group ──

class TallyGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="tally", description="AI 自動計票（論壇貼文投票）")

    @app_commands.command(name="count", description="AI 自動判斷投票格式並計票（管理員限定）")
    @app_commands.describe(
        thread="要計票的論壇貼文（留空則使用目前所在的貼文）",
        legend="手動指定選項對照，格式：代碼1=候選人1,代碼2=候選人2（代碼可為 Emoji 或文字/字母代碼，留空則自動判斷）",
        mode="計票方式（留空則由 AI 自動判斷）",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="自動判斷", value="auto"),
        app_commands.Choice(name="單選（計數）", value="single"),
        app_commands.Choice(name="排序偏好（波達計數法）", value="ranked"),
    ])
    async def count(
        self,
        interaction: discord.Interaction,
        thread: discord.Thread = None,
        legend: str = "",
        mode: str = "auto",
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        target_thread = thread or (interaction.channel if isinstance(interaction.channel, discord.Thread) else None)
        if target_thread is None:
            await interaction.response.send_message(
                "❌ 請在投票貼文（論壇貼文）裡直接執行本指令，或用 thread 參數指定要計票的貼文。",
                ephemeral=True,
            )
            return

        # ── FIX：計票（尤其現在會逐訊息呼叫 AI 智能判讀格式有瑕疵的票）
        # 需要處理一段時間，之前的流程是「私人可見的 thinking → 私人預覽結果 →
        # 秘書處還要手動點一次確認按鈕才真正公佈」，等於讓使用者對著沒人看得到
        # 的私人畫面多等一輪。改成：thinking 狀態本身就是公開的（讓大家知道
        # 機器人在算，而不是懷疑指令沒反應），算完直接把結果公佈到原投票貼文，
        # 不再需要額外的手動確認按鈕。
        await interaction.response.defer(thinking=True, ephemeral=False)

        try:
            result = await _run_forum_tally(target_thread, manual_legend_str=legend, mode_override=mode)
        except Exception as e:
            await interaction.followup.send(f"❌ 計票失敗：{e}", ephemeral=True)
            return

        if not result["legend"]:
            await interaction.followup.send(
                "ℹ️ 沒有在這篇貼文裡偵測到任何選項 Emoji，無法計票。"
                "請確認貼文中有寫明投票用的 Emoji，或改用 legend 參數手動指定。",
                ephemeral=True,
            )
            return

        embed = _build_tally_embed(result)
        try:
            await target_thread.send(embed=embed)
            await interaction.followup.send("✅ 計票完成，結果已直接公佈於原投票貼文。", ephemeral=True)
        except Exception as e:
            # 公佈到原貼文失敗（例如貼文被刪、權限不足）—— 至少讓執行者看到結果，不要讓計票結果消失
            print(f"⚠️ 計票結果公佈至原貼文失敗：{e}")
            await interaction.followup.send(
                content=f"⚠️ 無法自動公佈到原貼文（{e}），計票結果如下：",
                embed=embed,
            )
        print(f"🗳️ AI 計票完成：{target_thread.name}｜模式={result['mode']}｜有效票數={result['valid_vote_count']}")



# ════════════════════════════════════════════════════════════════════
# AI 海龜湯 (Sea Turtle Soup) 小遊戲
# ════════════════════════════════════════════════════════════════════

# ── 全域狀態 ──
