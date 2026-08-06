# ═════════════════════════════════════════════════════════════════
# Module: 150_emergency_fallback (最壞情況備援演算法)
# When ALL AI APIs are down (primary + backup Gemini), administrative
# functions must not freeze. This module provides deterministic,
# rule-based "algorithm" fallbacks for proposal analysis, membership
# application review, and ballot judgment.
#
# Design philosophy: "最糟糕的演算法" — deliberately conservative.
# When AI is available, it does semantic understanding. When AI is
# gone, we fall back to pattern matching that errs on the side of:
#   - Proposals: accept + label them, let humans decide
#   - Applications: accept fields that look non-empty, flag borderline
#   - Ballots: only count regex-perfect ballots, reject ambiguous ones
#
# This is NOT meant to be smart. It's meant to keep the org running
# when the lights go out.
# ═════════════════════════════════════════════════════════════════

import re as _re
import json as _json

# ──────────────────────────────────────────────────────────────
# Proposal Analysis — Deterministic Fallback
# ──────────────────────────────────────────────────────────────

# Keyword → proposal type mapping, ordered by specificity (most specific first)
_PROPOSAL_TYPE_KEYWORDS = [
    ("升格案",       ["升格", "晉升", "提升地位", "觀察員升", "會員國升", "理事國升"]),
    ("罷免案",       ["罷免", "彈劾", "不信任投票", "解職", "免除職務"]),
    ("選舉案",       ["選舉", "改選", "補選", "投票選出", "競選"]),
    ("任命案",       ["任命", "提名", "指派", "授職", "聘任"]),
    ("預算提案",     ["預算", "撥款", "撥付", "經費", "追加預算", "特別預算"]),
    ("法律提案",     ["法律", "法案", "條例", "規程", "修正案", "立法", "修法", "增訂", "廢止"]),
    ("政策提案",     ["政策", "方針", "方案", "計畫", "辦法"]),
    ("外交提案",     ["建交", "斷交", "外交", "承認", "條約", "協定", "備忘錄"]),
    ("行政提案",     ["行政", "公告", "宣布", "聲明", "決議"]),
]

# Severity indicators that make a proposal "high priority"
_HIGH_PRIORITY_KEYWORDS = ["緊急", "即時", "馬上", "立刻", "限期", "逾期"]
_CONTROVERSIAL_KEYWORDS = ["爭議", "反對", "抗議", "異議", "衝突", "違憲", "違規"]


def emergency_proposal_analysis(content: str, channel_name: str = "") -> dict:
    """Deterministic proposal analysis when all AI is down.
    
    Returns the same shape as _analyze_proposal / _heuristic_proposal_analysis:
    {"type": str, "summary": str}
    
    But with extra emergency metadata:
    {"type": str, "summary": str, "_emergency": True, "priority": "high"|"normal", "flags": list}
    """
    text = content.lower()
    
    # Type detection — first match wins (ordered by specificity)
    ptype = "一般提案"
    for label, keywords in _PROPOSAL_TYPE_KEYWORDS:
        if any(kw in text for kw in keywords):
            ptype = label
            break
    
    # Priority detection
    priority = "normal"
    flags = []
    if any(kw in text for kw in _HIGH_PRIORITY_KEYWORDS):
        priority = "high"
        flags.append("緊急標記")
    if any(kw in text for kw in _CONTROVERSIAL_KEYWORDS):
        flags.append("具爭議性")
    
    # Word count estimation
    char_count = len(content.replace("\n", "").replace(" ", ""))
    if char_count < 50:
        flags.append("內容過短")
    elif char_count > 1500:
        flags.append("內容極長")
    
    # Generate summary — extract first meaningful sentence
    summary = _extract_summary_deterministic(content)
    
    # Check for common structural issues
    if not _re.search(r'[一二三四五六七八九十\d]+[、.．]', content):
        flags.append("缺少條列結構")
    if "理由" not in content and "說明" not in content and "目的" not in content:
        flags.append("缺少理由/說明段落")
    
    return {
        "type": ptype,
        "summary": summary,
        "_emergency": True,
        "priority": priority,
        "flags": flags,
        "char_count": char_count,
    }


def _extract_summary_deterministic(content: str) -> str:
    """Extract a one-line summary without AI.
    Strategy: find the first non-template line that looks like a title/statement."""
    lines = content.strip().split("\n")
    
    # Skip template/boilerplate lines
    skip_patterns = [
        r'^[一二三四五六七八九十\d]+[、.．]',  # numbered sections
        r'^#',       # markdown headers
        r'^---',     # dividers
        r'^\s*$',    # blank lines
    ]
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(_re.match(p, stripped) for p in skip_patterns):
            continue
        # Found first content line — use it
        summary = stripped.replace("**", "").replace("__", "").strip()
        if len(summary) > 80:
            summary = summary[:77] + "..."
        return summary
    
    # Fallback: first 50 chars
    summary = content[:50].replace("\n", " ").strip()
    if len(content) > 50:
        summary += "..."
    return summary


# ──────────────────────────────────────────────────────────────
# Membership Application Review — Deterministic Fallback
# ──────────────────────────────────────────────────────────────

# Minimum content thresholds for essay fields
_ESSAY_MIN_CHARS = 10  # at least 10 meaningful characters
_ESSAY_MIN_WORDS = 3   # or at least 3 "words" (CJK chunks or space-separated tokens)

# Red flags that suggest a joke/troll application
_APPLICATION_RED_FLAGS = [
    "測試", "test", "asd", "qqq", "12345678", "哈哈", "笑死",
    "好玩", "無聊", "亂填", "隨便", "不知道", "沒有想法",
    " trolling", "troll", "fake", "joke", "lmao", "lol",
    "174", "93", "87", "783",  # common Taiwan internet slang numbers
]

# Positive indicators that suggest genuine effort
_APPLICATION_POSITIVE_INDICATORS = [
    "希望", "計畫", "目標", "理想", "發展", "建設",
    "文化", "歷史", "經濟", "外交", "制度", "憲法",
    "參與", "貢獻", "交流", "合作", "推廣", "理念",
]


def emergency_application_review(content: str) -> dict:
    """Deterministic membership application review when all AI is down.
    
    Checks all fields, scores essay quality, detects red flags.
    Returns:
    {
        "vision": bool,       # essay field 1 filled
        "profile": bool,     # essay field 2 filled  
        "all_pass": bool,    # all required fields present
        "missing_fields": list,
        "red_flags": list,
        "quality_score": int,    # 0-100, deterministic estimate
        "recommendation": str,   # "accept" | "review_manually" | "reject"
        "quality_notes": str,
        "_emergency": True,
    }
    """
    # Check simple fields (reuse the existing logic)
    try:
        simple_missing = _check_simple_fields(content)
    except Exception:
        # If _check_simple_fields isn't available (module load order), do it inline
        simple_missing = []
        for zh, en in _APPLICATION_SIMPLE_FIELDS:
            if zh not in content and en.lower() not in content.lower():
                simple_missing.append(zh)
                continue
            found = False
            for line in content.split("\n"):
                if zh in line or en.lower() in line.lower():
                    for sep in ["：", ":"]:
                        if sep in line:
                            after = line.split(sep, 1)[1].strip()
                            if after:
                                found = True
                            break
                    break
            if not found:
                simple_missing.append(zh)
    
    # Check essay fields with enhanced heuristics
    vision_ok, vision_score, vision_notes = _emergency_essay_check(
        content, "申請目的與願景", "Desired goals and vision"
    )
    profile_ok, profile_score, profile_notes = _emergency_essay_check(
        content, "國家簡介", "Country Profile"
    )
    
    # Flag image check (can't do vision AI, so just check existence)
    has_image_tag = "國旗" in content or "Flag" in content
    # The actual image check is done by the caller (message.attachments)
    # Here we just note it as "needs manual verification"
    flag_ok = has_image_tag  # tentative — caller should verify image exists
    
    missing_fields = list(simple_missing)
    if not vision_ok:
        missing_fields.append("申請目的與願景（空白或過短）")
    if not profile_ok:
        missing_fields.append("國家簡介（空白或過短）")
    if not flag_ok:
        missing_fields.append("國旗（無法AI驗證，需人工確認）")
    
    # Red flag detection
    text_lower = content.lower()
    red_flags = []
    for flag in _APPLICATION_RED_FLAGS:
        if flag.lower() in text_lower:
            red_flags.append(flag)
    
    # Positive indicators
    positive_count = sum(1 for ind in _APPLICATION_POSITIVE_INDICATORS if ind in content)
    
    # Quality scoring (deterministic)
    quality_score = 0
    if vision_ok:
        quality_score += 20 + min(vision_score, 15)  # up to 35
    if profile_ok:
        quality_score += 20 + min(profile_score, 15)  # up to 35
    if not simple_missing:
        quality_score += 15  # all simple fields present
    if positive_count >= 3:
        quality_score += 10  # shows genuine thought
    if positive_count >= 6:
        quality_score += 5   # bonus for depth
    if not red_flags:
        quality_score += 10  # clean of obvious troll markers
    quality_score = min(quality_score, 100)
    
    # Deduct for red flags
    if red_flags:
        quality_score = max(0, quality_score - len(red_flags) * 10)
    
    # Recommendation
    if not missing_fields and quality_score >= 50 and len(red_flags) <= 1:
        recommendation = "accept"
    elif not missing_fields and quality_score >= 30:
        recommendation = "review_manually"
    elif missing_fields and quality_score < 20:
        recommendation = "reject"
    else:
        recommendation = "review_manually"
    
    # Build notes
    notes_parts = []
    if vision_notes:
        notes_parts.append(f"願景：{vision_notes}")
    if profile_notes:
        notes_parts.append(f"簡介：{profile_notes}")
    if red_flags:
        notes_parts.append(f"可疑標記：{', '.join(red_flags[:5])}")
    if positive_count >= 3:
        notes_parts.append(f"正面指標：{positive_count} 個")
    quality_notes = "；".join(notes_parts) if notes_parts else "無特殊標記"
    
    return {
        "vision": vision_ok,
        "profile": profile_ok,
        "all_pass": len(missing_fields) == 0,
        "missing_fields": missing_fields,
        "red_flags": red_flags,
        "quality_score": quality_score,
        "recommendation": recommendation,
        "quality_notes": quality_notes,
        "_emergency": True,
    }


def _emergency_essay_check(content: str, zh: str, en: str) -> tuple:
    """Enhanced essay field check without AI.
    Returns (is_filled: bool, quality_score: int 0-15, notes: str)
    
    Goes beyond the basic _essay_fallback_check by also scoring
    content length, structure, and detecting copy-paste templates.
    """
    lines = content.split("\n")
    block = []
    capturing = False
    
    for line in lines:
        if zh in line or en.lower() in line.lower():
            capturing = True
            continue
        if capturing:
            # Stop at next section
            if _re.match(r'^\s*[一二三四五六七八九十0-9]+[、.．]', line):
                break
            if any(z in line for z, _e in
                   _APPLICATION_SIMPLE_FIELDS + _APPLICATION_ESSAY_FIELDS
                   if z != zh):
                break
            block.append(line)
    
    block_text = "\n".join(block)
    # Strip template noise
    block_text = _re.sub(r'[（(]\s*\d+\s*(字|words?)\s*[）)]', '', block_text,
                        flags=_re.IGNORECASE)
    # Strip the bilingual template repeat (English line that mirrors the label)
    block_text = _re.sub(r'(?i)' + _re.escape(en), '', block_text)
    block_text = block_text.strip()
    
    if len(block_text) < 5:
        return (False, 0, "未填寫或空白")
    
    # Score the content
    char_count = len(block_text.replace(" ", "").replace("\n", ""))
    word_count = len([w for w in block_text.replace("\n", " ").split() if w])
    
    # Length-based score (0-8)
    length_score = min(8, char_count // 15)
    
    # Structure bonus (0-4): has sentences with punctuation
    sentences = len(_re.findall(r'[。！？；，、]', block_text))
    structure_score = min(4, sentences)
    
    # Template detection penalty: if content is just the English template
    if block_text.lower().strip() == en.lower():
        return (False, 0, "僅含範本文字")
    
    # Copy-paste detection: if the exact same text appears in multiple fields
    # (simplified check: count how many times block_text appears in content)
    if content.count(block_text[:20]) > 1 and len(block_text) > 20:
        return (True, 2, "疑似複製貼上")
    
    quality_score = length_score + structure_score
    notes = f"{char_count}字，{word_count}詞"
    
    if char_count < 15:
        notes = "極短，僅勉強通過"
        return (True, max(1, quality_score), notes)
    elif char_count >= 50:
        notes = "內容充實"
        quality_score = min(15, quality_score + 2)
    
    return (True, quality_score, notes)


# ──────────────────────────────────────────────────────────────
# Ballot Judgment — Deterministic Fallback (Enhanced Regex)
# ──────────────────────────────────────────────────────────────

def emergency_ballot_judge(content: str, legend: dict, n_candidates: int) -> dict:
    """Enhanced regex-only ballot judgment when AI is unavailable.
    
    The existing regex (_NUMBERED_VOTE_RE) already handles most cases.
    This function adds extra fuzziness for common format errors that
    the strict regex misses, without AI:
    - Full-width numbers/letters (１２３ → 123, ａｂｃ → abc)
    - Missing separator between code and name (1e → 1.e)
    - Trailing/leading whitespace
    - Mixed case (a → A)
    
    Returns same shape as _ai_judge_ballot:
    {"is_vote": bool, "ranking": list, "complete": bool, "reason": str, "_emergency": True}
    """
    if not content.strip():
        return {"is_vote": False, "ranking": [], "complete": False,
                "reason": "空白訊息", "_emergency": True}
    
    # Full-width → half-width conversion
    def _to_half_width(s: str) -> str:
        result = []
        for ch in s:
            code = ord(ch)
            # Full-width ASCII (0xFF01-0xFF5E) → normal (0x21-0x7E)
            if 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFF00 + 0x20))
            # Full-width digits (０-９: 0xFF10-0xFF19)
            elif 0xFF10 <= code <= 0xFF19:
                result.append(chr(code - 0xFF10 + ord('0')))
            # Full-width uppercase letters
            elif 0xFF21 <= code <= 0xFF3A:
                result.append(chr(code - 0xFF21 + ord('A')))
            # Full-width lowercase letters  
            elif 0xFF41 <= code <= 0xFF5A:
                result.append(chr(code - 0xFF41 + ord('a')))
            else:
                result.append(ch)
        return "".join(result)
    
    normalized = _to_half_width(content).strip()
    
    # Try the existing regex pattern first (if available)
    found_codes = []
    
    # Pattern: number/letter followed by separator and optional content
    # Match: 1. 1, 1、 1： 1: 1 (with space)
    # Also match codes directly stuck to names: 1a, 1e, 2b
    ballot_pattern = _re.compile(
        r'(?:^|\n)\s*(\d+|[a-zA-Z])\s*[.．、：:）)]\s*',
        _re.MULTILINE
    )
    
    # Also try a looser pattern: code directly followed by known candidate name
    for code, name in legend.items():
        code_upper = code.upper().strip()
        # Check if this code appears in the message with a separator
        for pattern in [
            rf'(?:^|\n)\s*{code_upper}\s*[.．、：:）)]',  # standard
            rf'(?:^|\n)\s*{code_upper}\s+(?=[\w])',       # space after code
            rf'(?:^|\n)\s*{code_upper}(?=[A-Za-z\u4e00-\u9fff])',  # code stuck to name
        ]:
            if _re.search(pattern, normalized, _re.MULTILINE | _re.IGNORECASE):
                if code_upper not in found_codes:
                    found_codes.append(code_upper)
                break
    
    # If we didn't find enough with legend-aware patterns, try generic extraction
    if len(found_codes) < n_candidates:
        all_matches = ballot_pattern.findall(normalized)
        for m in all_matches:
            code_up = m.upper().strip()
            if code_up in legend and code_up not in found_codes:
                found_codes.append(code_up)
    
    # Determine ordering by position in the text
    code_positions = []
    for code in found_codes:
        # Find first occurrence position
        pos = normalized.upper().find(code.upper())
        if pos >= 0:
            code_positions.append((pos, code))
    code_positions.sort()
    
    ranking = [code for _, code in code_positions]
    
    is_complete = len(ranking) == n_candidates
    
    # Determine if this looks like a ballot at all
    if not ranking:
        # No candidate codes found at all
        # Check if it looks like it's trying to be a ballot
        looks_like_ballot = _looks_like_ballot_pattern(normalized, legend)
        if looks_like_ballot:
            return {
                "is_vote": True, "ranking": [], "complete": False,
                "reason": "格式無法解析但疑似選票（AI 離線，需人工確認）",
                "_emergency": True,
            }
        return {
            "is_vote": False, "ranking": [], "complete": False,
            "reason": "未偵測到候選人代碼",
            "_emergency": True,
        }
    
    return {
        "is_vote": True,
        "ranking": ranking,
        "complete": is_complete,
        "reason": f"正則解析{len(ranking)}/{n_candidates}位候選人"
                  + ("（完整）" if is_complete else "（不完整，AI離線無法智能補全）"),
        "_emergency": True,
    }


def _looks_like_ballot_pattern(text: str, legend: dict) -> bool:
    """Check if text looks like it might be a ballot even though we
    couldn't extract any codes with our regex."""
    # Contains numbers that could be rankings
    has_numbers = bool(_re.search(r'\d+.*\d+.*\d+', text, _re.DOTALL))
    # Contains candidate names
    has_names = any(name in text for name in legend.values())
    # Contains ranking indicators
    has_ranking = any(kw in text for kw in ["第", "名", "排序", "排名", "偏好"])
    # Contains bullet points or numbered list
    has_list = bool(_re.search(r'(?:^|\n)\s*[\d\w]+[.．、：:）)]', text, _re.MULTILINE))
    
    # At least 2 indicators
    indicators = [has_numbers, has_names, has_ranking, has_list]
    return sum(indicators) >= 2


# ──────────────────────────────────────────────────────────────
# Global Emergency State
# ──────────────────────────────────────────────────────────────

_emergency_mode_active = False
_emergency_mode_since = None  # timestamp
_emergency_consecutive_failures = 0
_EMERGENCY_THRESHOLD = 3  # after 3 consecutive total API failures, enter emergency mode


def _record_api_outcome(success: bool):
    """Track consecutive total failures. Called after every call_chat_api
    that used both primary and fallback and both failed."""
    global _emergency_consecutive_failures, _emergency_mode_active, _emergency_mode_since
    
    if not success:
        _emergency_consecutive_failures += 1
        if _emergency_consecutive_failures >= _EMERGENCY_THRESHOLD and not _emergency_mode_active:
            _emergency_mode_active = True
            _emergency_mode_since = _time.time()
            print(f"🚨 進入緊急備援模式：連續 {_emergency_consecutive_failures} 次 API 完全失敗（主+備援皆掛）")
    else:
        if _emergency_mode_active:
            print(f"✅ 緊急備援模式解除：API 恢復正常")
        _emergency_consecutive_failures = 0
        _emergency_mode_active = False
        _emergency_mode_since = None


def is_emergency_mode() -> bool:
    """Check if we're in emergency mode (all APIs down)."""
    return _emergency_mode_active


def get_emergency_status() -> dict:
    """Get current emergency mode status for /chat status display."""
    return {
        "active": _emergency_mode_active,
        "since": _emergency_mode_since,
        "consecutive_failures": _emergency_consecutive_failures,
        "threshold": _EMERGENCY_THRESHOLD,
    }


# ──────────────────────────────────────────────────────────────
# Integration Helpers — drop-in replacements for callers
# ──────────────────────────────────────────────────────────────

async def safe_analyze_proposal(content: str, channel_name: str = "") -> dict:
    """Try AI first. If all AI fails, use emergency algorithm.
    This wraps _analyze_proposal and adds the emergency fallback layer."""
    try:
        result = await _analyze_proposal(content, channel_name)
        if result and not result.get("_emergency"):
            _record_api_outcome(True)
            return result
        # AI returned emergency result (shouldn't happen, but handle it)
        _record_api_outcome(False)
        return emergency_proposal_analysis(content, channel_name)
    except Exception as e:
        print(f"🚨 提案 AI 分析完全失敗，啟用緊急備援：{e}")
        _record_api_outcome(False)
        return emergency_proposal_analysis(content, channel_name)


async def safe_verify_application_essays(content: str) -> dict:
    """Try AI first. If all AI fails, use emergency algorithm."""
    try:
        result = await _verify_application_essays(content)
        if result and isinstance(result, dict) and "vision" in result:
            # AI succeeded (or its own fallback succeeded)
            _record_api_outcome(True)
            return result
        _record_api_outcome(False)
        # Fall through to emergency
    except Exception as e:
        print(f"🚨 入盟審核 AI 完全失敗，啟用緊急備援：{e}")
        _record_api_outcome(False)
    
    # Emergency algorithm
    emergency = emergency_application_review(content)
    return {
        "vision": emergency["vision"],
        "profile": emergency["profile"],
        "_emergency": True,
        "_emergency_data": emergency,
    }


async def safe_judge_ballot(content: str, legend: dict, n_candidates: int) -> dict:
    """Try AI first. If all AI fails, use emergency enhanced regex."""
    try:
        result = await _ai_judge_ballot(content, legend, n_candidates)
        if result and result.get("reason", "").startswith("AI"):
            # AI succeeded
            _record_api_outcome(True)
            return result
        # Check if the "fallback" reason means AI was never available
        if result and "AI 判讀失敗或未設定 AI" in result.get("reason", ""):
            _record_api_outcome(False)
        else:
            _record_api_outcome(True)
            return result
    except Exception as e:
        print(f"🚨 選票判讀 AI 完全失敗，啟用緊急備援：{e}")
        _record_api_outcome(False)
    
    return emergency_ballot_judge(content, legend, n_candidates)


# ──────────────────────────────────────────────────────────────
# Discord Status Embed for Emergency Mode
# ──────────────────────────────────────────────────────────────

def build_emergency_status_embed() -> "discord.Embed":
    """Build a Discord embed showing emergency mode status."""
    status = get_emergency_status()
    
    if status["active"]:
        import datetime as _dt
        since_str = ""
        if status["since"]:
            since_dt = _dt.datetime.fromtimestamp(status["since"], GMT8)
            since_str = since_dt.strftime("%H:%M:%S")
        
        embed = discord.Embed(
            title="🚨 緊急備援模式啟用中",
            description=(
                "所有 AI API（主 API + 備援 Gemini）目前離線。\n"
                "行政功能已切換至演算法備援模式。\n\n"
                f"連續失敗次數：{status['consecutive_failures']}\n"
                f"啟用時間：{since_str}\n"
                f"觸發閾值：{status['threshold']} 次連續失敗\n\n"
                "⚠️ 此模式下：\n"
                "• 提案分析：關鍵字規則分類（可能不精確）\n"
                "• 入盟審核：結構化評分（保守判斷）\n"
                "• 選票判讀：嚴格正則解析（無法智能補全）\n"
                "建議管理員人工覆核所有自動判定的結果。"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
    else:
        embed = discord.Embed(
            title="✅ AI 正常運行",
            description=f"近期失敗次數：{status['consecutive_failures']}（閾值 {status['threshold']}）",
            color=discord.Color.green(),
        )
    
    embed.set_footer(text="ICEA 緊急備援系統")
    return embed


print("🛡️ 緊急備援模組已載入 (emergency_fallback)")
