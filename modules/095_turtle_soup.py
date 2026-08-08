# ═════════════════════════════════════════════════════════════════
# Module: 95_turtle_soup (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

_turtle_soup_game_id = 0  # 每局遞增，用於防止舊面板影響新遊戲

_turtle_soup_state = {
    "active": False,        # 是否有遊戲正在進行
    "surface": "",          # 湯面（故事題目，公開）
    "truth": "",            # 湯底（完整真相，絕對保密）
    "difficulty": "medium", # 本次難度
    "max_questions": 20,   # 最大提問次數
    "questions_used": 0,    # 已用提問次數
    "qa_history": [],       # [{"q": "他死了嗎？", "a": "是", "asked_by": "張三"}, ...]
    "extra_time_used": False,  # 是否已用過加時 (+5)
    "hint_panel_active": False,  # 提示按鈕面板是否正在等待玩家選擇
    "game_msg_id": None,    # 遊戲進行中的主訊息 ID
    "channel_id": None,     # 當前遊戲所在頻道 ID
    "processing": False,    # AI 是否正在處理提問（鎖定用）
    "queue": [],            # 排隊中的提問 [{"user_id", "user_name", "question", "interaction"}]
    "started_at": 0,        # 遊戲開始時間
    "starter_user_id": None,  # 發起遊戲的用戶
    "hints_given": 0,       # 已「接受」過幾次提示（僅供統計，不影響等級判定）
    "game_id": 0,           # 本局遊戲 ID（與 _turtle_soup_game_id 同步）
}

_turtle_soup_invite_msg_id = None  # 當前邀請面板的訊息 ID

def _save_turtle_soup():
    """持久化海龜湯設定（不含遊戲進行中的臨時狀態）。"""
    try:
        ts_settings = {
            "enabled": chat_ai_settings.get("turtle_soup_enabled", False),
            "channel_id": chat_ai_settings.get("turtle_soup_channel_id"),
            "difficulty": chat_ai_settings.get("turtle_soup_difficulty", "medium"),
        }
        _save_json_file(TURTLE_SOUP_FILE, ts_settings)
    except Exception as e:
        print(f"⚠️ Turtle soup save failed: {e}")

def _load_turtle_soup():
    """從磁碟載入海龜湯設定。"""
    try:
        if os.path.exists(TURTLE_SOUP_FILE):
            with open(TURTLE_SOUP_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            chat_ai_settings["turtle_soup_enabled"] = data.get("enabled", False)
            chat_ai_settings["turtle_soup_channel_id"] = data.get("channel_id")
            chat_ai_settings["turtle_soup_difficulty"] = data.get("difficulty", "medium")
    except Exception as e:
        print(f"⚠️ Turtle soup load failed: {e}")

# ── AI 湯底生成 ──
TURTLE_SOUP_GEN_PROMPT = """你是一個海龜湯（情境猜謎）出題大師。請創作一個高品質、獨特的海龜湯題目。

【核心要求】
1. **完全原創**：不要照搬經典海龜湯題目（如海龜湯自殺、盲人復明、照片詭計等）。每次出題都要有全新的故事場景和人物。
2. **邏輯嚴密**：湯底必須能合理解釋湯面中的每一個線索，不能有邏輯漏洞。玩家透過是/否問題逐步推理後，應該能夠自然得出真相，而不是靠瞎猜。
3. **因果清晰**：故事要有明確的因果關係鏈。A 發生 → 導致 B → 導致 C → 湯面呈現的現象。每一步都說得通。
4. **湯面不劇透**：湯面只描述表面現象（通常是反常、詭異或矛盾的行為/事件），絕不能透露背後原因。
5. **湯底不牽強**：真相不能是超自然力量、巧合、或「就是這樣沒有為什麼」。必須有合理的人性動機、物理規律或社會邏輯。
6. **難度控制**：
   - easy：真相比較直接，2-3個關鍵問題就能鎖定方向
   - medium：需要5-8個問題排除多種可能性後才能鎖定
   - hard：真相涉及多重因果或非直覺的轉折，需要10+個問題深入挖掘

【主題多樣性】
避免重複使用相同的主題元素。從以下領域中隨機選擇一個來創作：
- 職場/工作場所的異常行為
- 日常生活中的反常習慣
- 人際關係中的隱藏真相
- 旅行/交通中的詭異事件
- 飲食/餐飲場景的怪異舉動
- 居家生活的不尋常現象
- 學校/教育場景的奇怪事件
- 娛樂/休閒活動中的異常
- 醫療/健康相關的誤解
- 節日/儀式中的反常行為
- 金錢/交易中的詭異場景
- 時間/季節相關的怪事

【自檢清單】出題前自我檢查：
- 湯面是否只描述現象、不透露原因？
- 湯底是否合邏輯、能解釋所有線索？
- 是否跟常見經典海龜湯太相似？
- 玩家能否透過合理的是/否推理逐步接近答案？

請嚴格按照以下 JSON 格式回覆（不要有任何其他文字、不要 markdown code block）：
{{
  "surface": "湯面：20-80字的懸疑故事。只描述表面現象，不透露真相。結尾留下懸念。",
  "truth": "湯底：100-300字的完整真相。包含：人物背景、動機、事件因果鏈。要能合理解釋湯面中的所有線索。",
  "difficulty": "{difficulty}",
  "key_questions": <這個湯底需要多少個關鍵問題才能推理出真相（整數）>
}}

說明：
- "key_questions" 是你評估這個湯底需要多少個關鍵的「是/否」問題才能讓玩家推理出真相的數量。
- easy 湯底通常 key_questions 為 3-6
- medium 湯底通常 key_questions 為 6-10
- hard 湯底通常 key_questions 為 10-15
- 提問次數上限會由系統自動計算為 key_questions × 2 + 10，不需要你設定。
"""

async def _generate_turtle_soup(difficulty: str) -> tuple:
    """呼叫 AI 生成海龜湯題目，回傳 (data_dict_or_None, error_reason)。
    error_reason: "circuit_open" | "timeout_or_parse" | None（成功時）。

    修正史：
    - v1：timeout_total 從 30s 放寬到 50s + 提高 max_tokens（大輸出常被切斷）。
    - v2（本次）：舊版失敗後只是「重試同一個模型」——如果失敗原因是
      該模型本身對這種長 JSON 輸出不穩（而非單純網路抖動），重試同一
      模型大概率還是失敗，這正是使用者回報「已自動重試一次仍失敗」的
      根因。現在改成每次重試「換下一個池降級鏈的模型」，真正利用
      dashboard 設定的降級鏈，而不是原地重複同一個請求。"""
    prompt = TURTLE_SOUP_GEN_PROMPT.format(difficulty=difficulty)

    _ts_url, _ts_key, _ts_model = _resolve_role_endpoint("turtle_soup", chat_ai_settings)
    _candidates = [(
        _ts_url or chat_ai_settings["api_url"],
        _ts_key or chat_ai_settings["api_key"],
        _ts_model or chat_ai_settings["model"],
    )]
    for _c_url, _c_key, _c_model in _resolve_chain("main", chat_ai_settings):
        if (_c_url, _c_key, _c_model) not in _candidates:
            _candidates.append((_c_url, _c_key, _c_model))
    _candidates = _candidates[:3]  # 主模型 + 最多 2 個降級鏈備選，避免拖太久

    messages = [{"role": "user", "content": prompt}]
    _last_reason = "timeout_or_parse"

    for attempt, (_c_url, _c_key, _c_model) in enumerate(_candidates):
        settings = {
            "api_url": _c_url,
            "api_key": _c_key,
            "model": _c_model,
        }
        # 只有第一次嘗試（主模型）才啟用備援 API 直切；換到降級鏈備選模型時
        # 不再需要，因為本身就已經是在換模型了
        if attempt == 0 and chat_ai_settings.get("fallback_enabled") and not _ai_circuit_breaker["tripped"]:
            settings["fallback_enabled"] = True
        text = ""
        try:
            result = await call_chat_api(
                messages, settings,
                max_tokens=1200,
                timeout_total=50,
                timeout_read=40,
                is_background=True,
                fallback_mode="full" if attempt == 0 else "disabled",
                fallback_user_id="turtle_soup",
                category="entertainment",
            )
            if result.get("circuit_open"):
                print(f"⚠️ Turtle soup generation blocked: circuit breaker open")
                return None, "circuit_open"
            text = result.get("content", "").strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            if not text:
                print(f"⚠️ Turtle soup generation attempt {attempt+1}/{len(_candidates)} ({_c_model}): empty content, error={result.get('error')}")
                _last_reason = "timeout_or_parse"
                continue
            data = json_module.loads(text)
            if "surface" in data and "truth" in data:
                key_q = int(data.get("key_questions", 6))
                key_q = max(3, min(key_q, 15))
                max_q = key_q * 2 + 10
                return {
                    "surface": data["surface"],
                    "truth": data["truth"],
                    "difficulty": data.get("difficulty", difficulty),
                    "max_questions": max_q,
                    "key_questions": key_q,
                }, None
            print(f"⚠️ Turtle soup generation attempt {attempt+1}/{len(_candidates)} ({_c_model}): missing surface/truth in parsed JSON: {text[:300]}")
        except Exception as e:
            print(f"⚠️ Turtle soup generation attempt {attempt+1} failed: {e} | raw_text={text[:300]}")
        if attempt == 0:
            await asyncio.sleep(2)
    return None, "timeout_or_parse"

# ── AI 回答判定 ──
TURTLE_SOUP_JUDGE_PROMPT = """你是一個海龜湯遊戲的主持人（法官）。你手上有這局遊戲的湯底（真相）。

【湯底（真相）】
{truth}

【目前提問歷史】
{qa_history}

【規則】
1. 玩家的問題只能是「是/否」問題。如果玩家問了非是/否問題（例如「他買了什麼？」「為什麼？」「他是誰？」），你必須回答「無關」。
2. 如果問題包含多重假設（例如「他是不是買了刀然後去殺人？」），也回答「無關」。
3. 根據湯底判斷問題的答案是「是」還是「不是」。
4. 如果問題的答案雖然是「是」，但與破解湯底的關鍵無關，回答「是但也無關」。
5. 如果玩家的問題直接猜中了湯底的核心真相（不要求一字不差，語意接近即可），回答「答對了！恭喜破案！」。
6. 你只能回答以下五種之一，不能有任何其他文字：
   - 是
   - 不是
   - 是但也無關
   - 無關
   - 答對了！恭喜破案！

【防劇透】你絕對不能透露湯底內容，不能給出超出這五種回答的任何資訊。"""

async def _judge_turtle_soup_question(question: str, truth: str, qa_history: list) -> str:
    """呼叫 AI 判定玩家提問，回傳五種狀態之一。"""
    history_text = "\n".join(
        f"Q: {qa['q']}\nA: {qa['a']}"
        for qa in qa_history[-15:]  # 只送最近15條，省 token
    ) or "（尚無歷史）"

    prompt = TURTLE_SOUP_JUDGE_PROMPT.format(truth=truth, qa_history=history_text)

    _j_url, _j_key, _j_model = _resolve_role_endpoint("admin", chat_ai_settings)
    settings = {
        "api_url": _j_url or chat_ai_settings["api_url"],
        "api_key": _j_key or chat_ai_settings["api_key"],
        "model": _j_model or chat_ai_settings["model"],
        "model_fallback_chain": chat_ai_settings.get("model_fallback_chain", ""),
    }
    if chat_ai_settings.get("fallback_enabled") and not _ai_circuit_breaker["tripped"]:
        settings["fallback_api_url"] = chat_ai_settings.get("fallback_api_url", "")
        settings["fallback_api_key"] = chat_ai_settings.get("fallback_api_key", "")
        settings["fallback_model"] = chat_ai_settings.get("fallback_model", "")

    messages = [{"role": "user", "content": prompt + f"\n\n【玩家提問】\n{question}"}]
    try:
        result = await call_chat_api(
            messages, settings,
            max_tokens=50,
            timeout_total=20,
            timeout_read=15,
            is_background=True,
            fallback_mode="full",
            fallback_user_id="turtle_soup",
            category="entertainment",
        )
        answer = result.get("content", "").strip()
        # 只允許五種回答
        valid_answers = ["答對了！恭喜破案！", "是但也無關", "無關", "不是", "是"]
        for va in valid_answers:
            if va in answer:
                return va
        # 如果 AI 回了別的東西，預設為「無關」
        return "無關"
    except Exception as e:
        print(f"⚠️ Turtle soup judge failed: {e}")
        return "無關"

# ── AI 防卡關線索 ──
# 重要：每次呼叫只把「這一個等級」的指示給 AI，絕對不要把四個等級全部列出來
# 讓 AI 自己選——弱模型很容易選錯或直接把最詳細那條整段複製輸出，等於劇透。
_TURTLE_SOUP_HINT_LEVEL_INSTRUCTIONS = {
    1: "給一句非常含蓄的暗示，15字以內。只點出一個模糊的大方向或情緒（例如「跟他的工作有關」「跟一段時間有關」）。絕對不能提到任何具體物品、人物身分、動作細節。",
    2: "給一句中等程度的暗示，20-35字。可以指出一個玩家可能還沒想到的情境角度（例如「他這麼做其實有個目的」），但絕對不能提到職業、身分、具體物品名稱或動機。",
    3: "給一句較明顯的暗示，30-50字。可以透露情境中『一個』關鍵元素（例如他的職業或身分二選一），但絕對不能同時透露動機和結果，也絕對不能說出他為什麼這麼做或這件事為什麼結束/改變。",
    4: "給一句最明顯、最後一次的暗示，40-60字。可以組合情境中兩個關鍵元素一起講（例如身分+一個行為模式），但『為什麼』或『最後發生了什麼轉折』這個核心答案本身，絕對絕對不能講出來——玩家聽完仍必須自己推理出那個關鍵原因才算破案，不能讓提示直接等於答案。",
}

TURTLE_SOUP_HINT_PROMPT = """你是一個海龜湯遊戲的主持人，要給玩家一句提示，幫助他們卡關時往正確方向推理。

【湯底（真相，只給你自己參考，絕對不能整句或大段透露給玩家）】
{truth}

【目前提問歷史】
{qa_history}

【這次提示要求】
{level_instruction}

【絕對規則（不管上面要求什麼等級都要遵守）】
1. 玩家看完這句提示，絕對不能等於已經知道完整真相——核心的「為什麼」或最後轉折，必須留給玩家自己推理出來。
2. 不能出現湯底原文的完整句子或近乎逐字的內容。
3. 輸出裡絕對不能出現「等級」「提示」「線索」「模糊」「中等」「明顯」「直白」這些字眼，也不能有任何編號、標籤、前綴、引號。
4. 只能輸出這一句暗示語本身，不要有任何說明、開場白或格式符號。

現在請直接輸出這一句暗示語："""


def _sanitize_turtle_soup_hint(hint: str) -> str:
    """防禦性清理：移除 AI 可能誤植的等級標籤/前綴文字。"""
    import re as _re
    hint = hint.strip().strip('「」"\'')
    # 移除開頭類似「等級X」「等級X+」「提示：」「線索：」等標籤前綴
    hint = _re.sub(r'^(等級\s*\d*\+?\s*[（(][^）)]*[）)]\s*[:：]?\s*)+', '', hint)
    hint = _re.sub(r'^(提示|線索|暗示)\s*[:：]\s*', '', hint)
    return hint.strip() or hint


def _turtle_soup_hint_level() -> int:
    """依「已用/總提問次數」比例決定提示等級：1=模糊 ~ 4=直白。
    完全基於進度，不受玩家接受/拒絕提示的次數影響。"""
    used = _turtle_soup_state["questions_used"]
    total = max(_turtle_soup_state["max_questions"], 1)
    ratio = used / total
    if ratio <= 0.35:
        return 1
    elif ratio <= 0.6:
        return 2
    elif ratio <= 0.85:
        return 3
    return 4


async def _generate_turtle_soup_hint(truth: str, qa_history: list, level: int = 1) -> str:
    """生成防卡關線索。level 越高提示越明顯（但永遠保留核心答案不講）。"""
    level = max(1, min(int(level), 4))
    history_text = "\n".join(
        f"Q: {qa['q']}\nA: {qa['a']}"
        for qa in qa_history[-15:]
    ) or "（尚無歷史）"

    level_instruction = _TURTLE_SOUP_HINT_LEVEL_INSTRUCTIONS[level]
    prompt = TURTLE_SOUP_HINT_PROMPT.format(
        truth=truth, qa_history=history_text, level_instruction=level_instruction,
    )

    _ts_url, _ts_key, _ts_model = _resolve_role_endpoint("turtle_soup", chat_ai_settings)
    settings = {
        "api_url": _ts_url or chat_ai_settings["api_url"],
        "api_key": _ts_key or chat_ai_settings["api_key"],
        "model": _ts_model or chat_ai_settings.get("turtle_soup_model") or chat_ai_settings["model"],
        "model_fallback_chain": chat_ai_settings.get("model_fallback_chain", ""),
    }
    if chat_ai_settings.get("fallback_enabled") and not _ai_circuit_breaker["tripped"]:
        settings["fallback_api_url"] = chat_ai_settings.get("fallback_api_url", "")
        settings["fallback_api_key"] = chat_ai_settings.get("fallback_api_key", "")
        settings["fallback_model"] = chat_ai_settings.get("fallback_model", "")

    messages = [{"role": "user", "content": prompt}]
    try:
        result = await call_chat_api(
            messages, settings,
            max_tokens=100,
            timeout_total=15,
            timeout_read=12,
            is_background=True,
            fallback_mode="full",
            fallback_user_id="turtle_soup",
            category="entertainment",
        )
        hint = result.get("content", "").strip()
        hint = _sanitize_turtle_soup_hint(hint) if hint else hint
        return hint or "試著從時間線的角度想一想？"
    except Exception as e:
        print(f"⚠️ Turtle soup hint failed: {e}")
        return "試著從時間線的角度想一想？"

# ── Discord UI: 難度投票面板 ──
class TurtleSoupDifficultyVoteView(discord.ui.View):
    """60秒難度投票面板。每人一票，時間到多數決。"""
    def __init__(self):
        super().__init__(timeout=60)
        self._votes = {}  # {user_id: "easy"|"medium"|"hard"}
        self._result = None

    def get_result(self) -> str:
        """回傳勝出的難度。平手時取較高難度。"""
        counts = {"easy": 0, "medium": 0, "hard": 0}
        for v in self._votes.values():
            if v in counts:
                counts[v] += 1
        # 多數決；平手時取較高難度
        if counts["hard"] >= counts["medium"] and counts["hard"] >= counts["easy"]:
            self._result = "hard"
        elif counts["medium"] >= counts["easy"]:
            self._result = "medium"
        else:
            self._result = "easy"
        return self._result

    def get_summary(self) -> str:
        counts = {"easy": 0, "medium": 0, "hard": 0}
        for v in self._votes.values():
            if v in counts:
                counts[v] += 1
        total = sum(counts.values())
        return (
            f"參與人數：{total}｜"
            f"簡單 {counts['easy']} 票 / 中等 {counts['medium']} 票 / 困難 {counts['hard']} 票"
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="🟢 簡單（15題）", style=discord.ButtonStyle.success, custom_id="turtle_soup_diff_easy")
    async def vote_easy(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._votes[interaction.user.id] = "easy"
        await interaction.response.send_message(f"✅ {interaction.user.display_name} 已投票：簡單", ephemeral=True)

    @discord.ui.button(label="🟡 中等（20題）", style=discord.ButtonStyle.primary, custom_id="turtle_soup_diff_medium")
    async def vote_medium(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._votes[interaction.user.id] = "medium"
        await interaction.response.send_message(f"✅ {interaction.user.display_name} 已投票：中等", ephemeral=True)

    @discord.ui.button(label="🔴 困難（25題）", style=discord.ButtonStyle.danger, custom_id="turtle_soup_diff_hard")
    async def vote_hard(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._votes[interaction.user.id] = "hard"
        await interaction.response.send_message(f"✅ {interaction.user.display_name} 已投票：困難", ephemeral=True)


# ── Discord UI: 提示投票面板 ──
class TurtleSoupHintVoteView(discord.ui.View):
    """10秒提示投票面板。每人一票，時間到多數決。"""
    def __init__(self, level: int = 1):
        super().__init__(timeout=10)
        self._level = level
        self._votes = {}  # {user_id: True|False}

    def get_result(self) -> bool:
        """回傳是否要提示。平手時不給提示。"""
        yes = sum(1 for v in self._votes.values() if v)
        no = sum(1 for v in self._votes.values() if not v)
        return yes > no

    def get_summary(self) -> str:
        yes = sum(1 for v in self._votes.values() if v)
        no = sum(1 for v in self._votes.values() if not v)
        return f"要提示 {yes} 票 / 不要提示 {no} 票"

    async def on_timeout(self):
        """超時後禁用按鈕。"""
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="💡 要提示", style=discord.ButtonStyle.success, custom_id="turtle_soup_hint_vote_yes")
    async def vote_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._votes[interaction.user.id] = True
        await interaction.response.send_message(f"✅ {interaction.user.display_name} 投了：要提示", ephemeral=True)

    @discord.ui.button(label="🚫 不要提示", style=discord.ButtonStyle.secondary, custom_id="turtle_soup_hint_vote_no")
    async def vote_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._votes[interaction.user.id] = False
        await interaction.response.send_message(f"✅ {interaction.user.display_name} 投了：不要提示", ephemeral=True)


# ── Discord UI: 提示按鈕面板（舊，保留給投票後生成提示用）──
class TurtleSoupHintView(discord.ui.View):
    def __init__(self, level: int = 1):
        super().__init__(timeout=300)  # 5 分鐘內有效
        self._level = level

    async def on_timeout(self):
        """超時後移除按鈕，保留訊息內容。"""
        for child in self.children:
            child.disabled = True
        try:
            # 嘗試更新第一個找到的訊息
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass

    async def _give_hint(self, interaction: discord.Interaction, want_hint: bool):
        global _turtle_soup_state
        _turtle_soup_state["hint_panel_active"] = False
        if not _turtle_soup_state["active"]:
            await interaction.response.edit_message(content="⚠️ 遊戲已結束。", view=None)
            return
        if want_hint:
            # 先立刻回應（3秒內），避免等待AI生成提示時互動逾時導致「此交互失敗」，
            # 之後用 edit_original_response 更新內容就沒有3秒限制了
            await interaction.response.edit_message(content="🤔 正在生成提示...", view=None)
            try:
                hint = await _generate_turtle_soup_hint(
                    _turtle_soup_state["truth"], _turtle_soup_state["qa_history"],
                    level=self._level,
                )
                _turtle_soup_state["hints_given"] += 1
                level_desc = {1: "模糊", 2: "中等", 3: "明顯", 4: "直白"}.get(self._level, "直白")
                await interaction.edit_original_response(
                    content=f"💡 **線索（{level_desc}）：** {hint}",
                )
            except Exception as e:
                print(f"⚠️ Turtle soup hint generation failed: {e}")
                try:
                    await interaction.edit_original_response(content="⚠️ 線索生成失敗。")
                except Exception:
                    pass
        else:
            next_milestone = (
                (_turtle_soup_state["questions_used"] // 5 + 1) * 5
            )
            extra = (
                f"\n（下次提問到第 {next_milestone} 次時還會再問一次要不要提示）"
                if next_milestone < _turtle_soup_state["max_questions"] else ""
            )
            await interaction.response.edit_message(
                content=f"👍 好的，繼續推理！{extra}", view=None,
            )

    @discord.ui.button(label="是，給我提示", style=discord.ButtonStyle.success, custom_id="turtle_soup_hint_yes")
    async def hint_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._give_hint(interaction, True)

    @discord.ui.button(label="不用了", style=discord.ButtonStyle.secondary, custom_id="turtle_soup_hint_no")
    async def hint_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._give_hint(interaction, False)


# ── Discord UI: 加時投票面板 ──
class TurtleSoupExtraTimeView(discord.ui.View):
    """20秒加時投票面板。每人一票，時間到多數決。"""
    def __init__(self, game_id: int = 0):
        super().__init__(timeout=20)
        self._game_id = game_id
        self._votes = {}  # {user_id: True|False}

    def get_result(self) -> bool:
        """回傳是否要加時。平手時不加時（直接公佈湯底）。"""
        yes = sum(1 for v in self._votes.values() if v)
        no = sum(1 for v in self._votes.values() if not v)
        return yes > no

    def get_summary(self) -> str:
        yes = sum(1 for v in self._votes.values() if v)
        no = sum(1 for v in self._votes.values() if not v)
        return f"加時 {yes} 票 / 放棄 {no} 票"

    async def on_timeout(self):
        """超時後由 vote waiter 處理結果，這裡只禁用按鈕。"""
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="➕ 加時 +5 次", style=discord.ButtonStyle.success, custom_id="turtle_soup_extra_yes")
    async def vote_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._votes[interaction.user.id] = True
        await interaction.response.send_message(
            f"✅ {interaction.user.display_name} 投了：加時 +5 次", ephemeral=True
        )

    @discord.ui.button(label="👎 放棄，公佈湯底", style=discord.ButtonStyle.danger, custom_id="turtle_soup_extra_no")
    async def vote_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._votes[interaction.user.id] = False
        await interaction.response.send_message(
            f"✅ {interaction.user.display_name} 投了：放棄", ephemeral=True
        )


# ── Discord UI: 邀請面板按鈕 ──
class TurtleSoupStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🍜 開始海龜湯", style=discord.ButtonStyle.primary, custom_id="turtle_soup_start")
    async def start_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _turtle_soup_state, _turtle_soup_invite_msg_id, _turtle_soup_game_id

        if _turtle_soup_state["active"]:
            await interaction.response.send_message(
                "⚠️ 已經有一局海龜湯正在進行中！", ephemeral=True,
            )
            return

        # 先佔位防止併發雙開，但標記為「難度投票中」
        _turtle_soup_game_id += 1
        _turtle_soup_state.update({
            "active": True,
            "surface": "",
            "truth": "",
            "difficulty": "medium",
            "max_questions": 0,
            "questions_used": 0,
            "qa_history": [],
            "game_msg_id": None,
            "channel_id": interaction.channel.id,
            "processing": False,
            "queue": [],
            "started_at": _time.time(),
            "starter_user_id": str(interaction.user.id),
            "hints_given": 0,
            "extra_time_used": False,
            "hint_panel_active": False,
            "game_id": _turtle_soup_game_id,
        })
        this_game_id = _turtle_soup_game_id

        # 刪除舊的邀請面板
        if _turtle_soup_invite_msg_id:
            try:
                old_msg = await interaction.channel.fetch_message(_turtle_soup_invite_msg_id)
                await old_msg.delete()
            except Exception:
                pass
            _turtle_soup_invite_msg_id = None

        # ── 難度投票階段（60秒）──
        vote_view = TurtleSoupDifficultyVoteView()
        vote_msg = await interaction.channel.send(
            "🗳️ **難度投票開始！**\n"
            f"由 {interaction.user.mention} 發起，請大家在 **60 秒內**投票選擇本局難度。\n"
            "簡單=15題 / 中等=20題 / 困難=25題",
            view=vote_view,
        )
        await interaction.response.send_message("✅ 已發起難度投票，等待大家投票中...", ephemeral=True)

        # 等待 60 秒
        await asyncio.sleep(60)

        # 確認還是同一局（防止被中途取消）
        if _turtle_soup_state.get("game_id") != this_game_id or not _turtle_soup_state["active"]:
            return

        # 結算投票
        votes = vote_view._votes
        difficulty = vote_view.get_result()
        vote_text = vote_view.get_summary()

        # 禁用按鈕
        for child in vote_view.children:
            child.disabled = True
        try:
            await vote_msg.edit(content=f"🗳️ **投票結束！**\n{vote_text}\n🍜 難度：**{difficulty}**，正在熬湯中...", view=vote_view)
        except Exception:
            pass

        # 確認還是同一局
        if _turtle_soup_state.get("game_id") != this_game_id or not _turtle_soup_state["active"]:
            return

        # 生成湯底
        soup_data, gen_error = await _generate_turtle_soup(difficulty)
        if not soup_data:
            _turtle_soup_state["active"] = False
            if gen_error == "circuit_open":
                await interaction.channel.send(
                    f"⚠️ AI 服務目前被供應商暫時封鎖（熔斷保護中），請約 2 分鐘後再試一次。"
                )
            else:
                await interaction.channel.send(
                    "⚠️ 湯底生成失敗（AI 回應逾時或格式異常，已自動重試一次仍失敗），請稍後再試。"
                )
            return

        # 再次確認（AI 生成期間可能被取消）
        if _turtle_soup_state.get("game_id") != this_game_id or not _turtle_soup_state["active"]:
            return

        _turtle_soup_state.update({
            "surface": soup_data["surface"],
            "truth": soup_data["truth"],
            "difficulty": soup_data["difficulty"],
            "max_questions": soup_data["max_questions"],
        })

        # 發送遊戲開始訊息
        embed = discord.Embed(
            title="🍜 海龜湯開始！",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="📖 湯面", value=soup_data["surface"], inline=False)
        embed.add_field(name="🎯 難度", value=soup_data["difficulty"], inline=True)
        embed.add_field(name="❓ 提問上限", value=f"{soup_data['max_questions']} 次", inline=True)
        embed.add_field(
            name="📋 規則",
            value=(
                "• 只能問**是/否問題**\n"
                "• 直接在頻道打字提問即可\n"
                f"• 全局共 {soup_data['max_questions']} 次提問機會\n"
                "• 猜中湯底即破案！"
            ),
            inline=False,
        )
        embed.set_footer(text="在這個頻道直接發訊息提問（結尾加 ?）→ AI 回答 是/不是/是但也無關/無關")

        game_msg = await interaction.channel.send(embed=embed)
        _turtle_soup_state["game_msg_id"] = game_msg.id

        print(f"🍜 Turtle soup started: difficulty={soup_data['difficulty']}, max_q={soup_data['max_questions']}, game_id={this_game_id}")

async def _turtle_soup_hint_vote_waiter(vote_view, vote_msg, hint_level, game_id, channel):
    """等待10秒提示投票結束後，依結果生成提示或跳過。"""
    await asyncio.sleep(10)

    # 確認還是同一局遊戲
    if _turtle_soup_state.get("game_id") != game_id or not _turtle_soup_state["active"]:
        return

    _turtle_soup_state["hint_panel_active"] = False
    result = vote_view.get_result()
    summary = vote_view.get_summary()
    level_desc = {1: "模糊", 2: "中等", 3: "明顯", 4: "直白"}.get(hint_level, "直白")

    # 禁用按鈕
    for child in vote_view.children:
        child.disabled = True

    if result:
        try:
            await vote_msg.edit(
                content=f"🗳️ {summary}\n🤔 正在生成提示（{level_desc}）...",
                view=vote_view,
            )
        except Exception:
            pass
        try:
            hint = await _generate_turtle_soup_hint(
                _turtle_soup_state["truth"], _turtle_soup_state["qa_history"],
                level=hint_level,
            )
            _turtle_soup_state["hints_given"] += 1
            await vote_msg.edit(
                content=f"🗳️ {summary}\n💡 **線索（{level_desc}）：** {hint}",
            )
        except Exception as e:
            print(f"⚠️ Turtle soup hint generation failed: {e}")
            try:
                await vote_msg.edit(content=f"🗳️ {summary}\n⚠️ 線索生成失敗。")
            except Exception:
                pass
    else:
        next_milestone = (
            (_turtle_soup_state["questions_used"] // 5 + 1) * 5
        )
        extra = (
            f"\n（下次提問到第 {next_milestone} 次時還會再問一次）"
            if next_milestone < _turtle_soup_state["max_questions"] else ""
        )
        try:
            await vote_msg.edit(
                content=f"🗳️ {summary}\n👍 不給提示，繼續推理！{extra}",
                view=vote_view,
            )
        except Exception:
            pass


async def _turtle_soup_extra_time_vote_waiter(vote_view, vote_msg, game_id, channel):
    """等待20秒加時投票結束後，依結果加時或公佈湯底。"""
    await asyncio.sleep(20)

    # 確認還是同一局遊戲
    if _turtle_soup_state.get("game_id") != game_id or not _turtle_soup_state["active"]:
        return

    result = vote_view.get_result()
    summary = vote_view.get_summary()

    # 禁用按鈕
    for child in vote_view.children:
        child.disabled = True

    if result:
        # 多數要加時
        _turtle_soup_state["extra_time_used"] = True
        _turtle_soup_state["max_questions"] += 5
        new_remaining = _turtle_soup_state["max_questions"] - _turtle_soup_state["questions_used"]
        try:
            await vote_msg.edit(
                content=f"🗳️ {summary}\n⏰ **加時成功！** 提問次數 +5，現在剩餘 {new_remaining} 次。繼續推理吧！",
                view=vote_view,
            )
        except Exception:
            pass
        print(f"🍜 Turtle soup extra time used: +5, new max={_turtle_soup_state['max_questions']}, game_id={game_id}")
    else:
        # 多數放棄（或平手）
        try:
            await vote_msg.edit(
                content=f"🗳️ {summary}\n👎 不加時，即將公佈湯底...",
                view=vote_view,
            )
        except Exception:
            pass
        await _end_turtle_soup(channel, solved=False)


# ── 發送/更新邀請面板 ──
async def _post_turtle_soup_invite(channel):
    """在海龜湯頻道發送邀請面板。"""
    global _turtle_soup_invite_msg_id

    # 如果已經有面板，先刪除
    if _turtle_soup_invite_msg_id:
        try:
            old_msg = await channel.fetch_message(_turtle_soup_invite_msg_id)
            await old_msg.delete()
        except Exception:
            pass

    embed = discord.Embed(
        title="🍜 AI 海龜湯",
        description=(
            "沒有進行中的海龜湯遊戲。\n"
            "點擊下方按鈕開始一局新的海龜湯！\n\n"
            "**怎麼玩：**\n"
            "AI 會出一個懸疑故事（湯面），你要透過問是/否問題來推理出完整真相（湯底）。\n"
            "回答只會是：是 / 不是 / 是但也無關 / 無關\n"
            "猜中關鍵真相就破案！"
        ),
        color=discord.Color.teal(),
        timestamp=discord.utils.utcnow(),
    )
    difficulty = chat_ai_settings.get("turtle_soup_difficulty", "medium")
    embed.add_field(name="目前難度", value=difficulty, inline=True)
    embed.set_footer(text="面板會在過期後自動重發")

    msg = await channel.send(embed=embed, view=TurtleSoupStartView())
    _turtle_soup_invite_msg_id = msg.id
    print(f"🍜 Turtle soup invite posted (msg_id={msg.id})")

# ── 海龜湯背景循環 ──
async def turtle_soup_loop():
    """背景任務：管理海龜湯邀請面板，遊戲結束後自動重發。"""
    global _turtle_soup_invite_msg_id
    await asyncio.sleep(30)  # 等待 bot 就緒
    while True:
        try:
            if not chat_ai_settings.get("turtle_soup_enabled"):
                await asyncio.sleep(15)
                continue

            channel_id = chat_ai_settings.get("turtle_soup_channel_id")
            if not channel_id:
                await asyncio.sleep(15)
                continue

            channel = get_channel_any(int(channel_id))
            if not channel:
                await asyncio.sleep(15)
                continue

            # 如果沒有遊戲進行中，確保有邀請面板
            if not _turtle_soup_state["active"]:
                # 檢查現有面板是否還在
                needs_post = True
                if _turtle_soup_invite_msg_id:
                    try:
                        msg = await channel.fetch_message(_turtle_soup_invite_msg_id)
                        # 面板還在，不需要重發
                        needs_post = False
                    except discord.NotFound:
                        # 面板已過期/被刪除，需要重發
                        _turtle_soup_invite_msg_id = None
                    except Exception:
                        _turtle_soup_invite_msg_id = None

                if needs_post:
                    await _post_turtle_soup_invite(channel)

            await asyncio.sleep(30)  # 每30秒檢查一次
        except Exception as e:
            print(f"⚠️ Turtle soup loop error: {e}")
            await asyncio.sleep(30)

# ── 處理頻道內提問 ──
async def _handle_turtle_soup_message(message):
    """處理海龜湯頻道內的玩家提問。回傳 True 如果訊息被海龜湯消化。"""
    global _turtle_soup_state

    if not chat_ai_settings.get("turtle_soup_enabled"):
        return False

    channel_id = chat_ai_settings.get("turtle_soup_channel_id")
    if not channel_id or message.channel.id != int(channel_id):
        return False

    if message.author.bot:
        return False

    # 如果沒有遊戲進行中，不攔截（讓邀請面板按鈕處理）
    if not _turtle_soup_state["active"]:
        return False

    # 忽略系統指令
    content = message.content.strip()
    if not content or content.startswith("/"):
        return False

    # 只有結尾帶問號（半形 ? 或全型 ？）的訊息才算「提問」
    # 沒有問號的訊息當作玩家間的閒聊討論，完全忽略，不送 AI
    if not content.endswith("?") and not content.endswith("？"):
        return False  # 不是提問，放行讓其他模組處理（或單純忽略）

    user_id = str(message.author.id)
    user_name = message.author.display_name

    # 檢查是否還有提問次數
    if _turtle_soup_state["questions_used"] >= _turtle_soup_state["max_questions"]:
        if _turtle_soup_state["extra_time_used"]:
            await message.reply(
                f"❌ 本局提問次數已用完（含加時共 {_turtle_soup_state['max_questions']} 次）！\n"
                f"即將公佈湯底...",
                mention_author=False,
            )
            await _end_turtle_soup(message.channel, solved=False)
        else:
            this_game_id = _turtle_soup_state.get("game_id", 0)
            vote_view = TurtleSoupExtraTimeView(game_id=this_game_id)
            vote_msg = await message.channel.send(
                f"❌ 提問次數已用完（{_turtle_soup_state['max_questions']} 次）！\n"
                f"要加時 +5 次嗎？— **20 秒內投票，多數決！**",
                view=vote_view,
            )
            asyncio.create_task(
                _turtle_soup_extra_time_vote_waiter(vote_view, vote_msg, this_game_id, message.channel)
            )
        return True

    # 如果 AI 正在處理，加入排隊
    if _turtle_soup_state["processing"]:
        queue_pos = len(_turtle_soup_state["queue"]) + 1
        _turtle_soup_state["queue"].append({
            "user_id": user_id,
            "user_name": user_name,
            "question": content,
            "message": message,
        })
        await message.reply(
            f"⏳ AI 正在思考中... 你的問題排在第 {queue_pos} 位",
            mention_author=False,
            ephemeral=True,
        )
        return True

    # 處理提問
    await _process_turtle_soup_question(message, content, user_id, user_name)
    return True

async def _process_turtle_soup_question(message, question, user_id, user_name):
    """處理一個提問：鎖定 → AI 判定 → 記錄 → 解鎖 → 處理排隊。"""
    global _turtle_soup_state
    _turtle_soup_state["processing"] = True

    try:
        # 呼叫 AI 判定
        answer = await _judge_turtle_soup_question(
            question, _turtle_soup_state["truth"], _turtle_soup_state["qa_history"]
        )

        # 記錄問答
        _turtle_soup_state["questions_used"] += 1
        _turtle_soup_state["qa_history"].append({
            "q": question,
            "a": answer,
            "asked_by": user_name,
        })

        # 回覆玩家
        remaining = _turtle_soup_state["max_questions"] - _turtle_soup_state["questions_used"]
        answer_emoji = {
            "是": "✅",
            "不是": "❌",
            "是但也無關": "🟡",
            "無關": "⚠️",
            "答對了！恭喜破案！": "🎉",
        }.get(answer, "❓")

        reply_text = f"{answer_emoji} **{answer}**\n📝 提問者：{user_name}｜剩餘提問：{remaining} 次"

        # 每 5 次提問就詢問是否需要提示（改為10秒投票制）
        # 提示等級依「已用/總提問次數」比例決定，越接近尾聲提示越明顯，
        # 不受玩家是否接受過提示影響（避免跳級或level跟次數脫節的問題）
        if (_turtle_soup_state["questions_used"] % 5 == 0
                and not _turtle_soup_state["hint_panel_active"]
                and answer != "答對了！恭喜破案！"
                and _turtle_soup_state["questions_used"] < _turtle_soup_state["max_questions"]):
            _turtle_soup_state["hint_panel_active"] = True
            this_game_id = _turtle_soup_state.get("game_id", 0)
            hint_level = _turtle_soup_hint_level()
            level_desc = {1: "模糊", 2: "中等", 3: "明顯", 4: "直白"}.get(hint_level, "直白")
            vote_view = TurtleSoupHintVoteView(level=hint_level)
            vote_msg = await message.channel.send(
                f"🤔 已用 {_turtle_soup_state['questions_used']} 次提問，需要提示嗎？\n"
                f"（提示等級：{level_desc}）— **10 秒內投票，多數決！**",
                view=vote_view,
            )
            # 啟動背景任務等待投票結果
            asyncio.create_task(
                _turtle_soup_hint_vote_waiter(vote_view, vote_msg, hint_level, this_game_id, message.channel)
            )

        await message.reply(reply_text, mention_author=False)

        # 檢查是否破案
        if answer == "答對了！恭喜破案！":
            await _end_turtle_soup(message.channel, solved=True, winner=user_name, winner_id=user_id)
            return

        # 檢查是否用完提問
        if _turtle_soup_state["questions_used"] >= _turtle_soup_state["max_questions"]:
            if _turtle_soup_state["extra_time_used"]:
                await message.reply(
                    f"❌ 本局提問次數已用完（含加時共 {_turtle_soup_state['max_questions']} 次）！\n"
                    f"即將公佈湯底...",
                    mention_author=False,
                )
                await _end_turtle_soup(message.channel, solved=False)
            else:
                await message.channel.send(
                    f"❌ 提問次數已用完（{_turtle_soup_state['max_questions']} 次）！\n"
                    f"要加時 +5 次嗎？（每人限一次）",
                    view=TurtleSoupExtraTimeView(game_id=_turtle_soup_state.get("game_id", 0)),
                )
            return

    except Exception as e:
        print(f"⚠️ Turtle soup question processing error: {e}")
        try:
            await message.reply("⚠️ 處理你的問題時發生錯誤，請再試一次。", mention_author=False)
        except Exception:
            pass
    finally:
        _turtle_soup_state["processing"] = False

    # 處理排隊中的提問
    await _drain_turtle_soup_queue(message.channel)

async def _drain_turtle_soup_queue(channel):
    """處理排隊中的提問。"""
    global _turtle_soup_state
    while _turtle_soup_state["queue"] and _turtle_soup_state["active"]:
        if _turtle_soup_state["processing"]:
            break
        next_item = _turtle_soup_state["queue"].pop(0)
        msg = next_item["message"]

        # 更新排隊通知（僅提問者可見）
        try:
            await msg.reply(f"🔄 輪到你了！正在處理你的問題...", mention_author=False, ephemeral=True)
        except Exception:
            pass

        await _process_turtle_soup_question(
            msg, next_item["question"], next_item["user_id"], next_item["user_name"]
        )

async def _end_turtle_soup(channel, solved: bool, winner: str = None, winner_id: str = None):
    """結束海龜湯遊戲。"""
    global _turtle_soup_state

    embed = discord.Embed(
        title="🍜 海龜湯結束！",
        color=discord.Color.green() if solved else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    if solved:
        winner_display = winner
        eco_reward_msg = ""
        if winner_id:
            try:
                eco_reward, eco_new_bal = reward_turtle_soup_win(winner_id, winner)
                eco_reward_msg = f"\n💰 破案獎勵：**{eco_reward} {currency_name()}**（餘額：{eco_new_bal}）"
            except Exception as e:
                print(f"⚠️ 海龜湯經濟獎勵失敗：{e}")
        embed.add_field(name="🎉 破案者", value=f"{winner_display}{eco_reward_msg}", inline=False)
    else:
        embed.add_field(name="😔 无人破案", value="提問次數已用完或遊戲結束", inline=False)

    embed.add_field(
        name="📖 湯面",
        value=_turtle_soup_state["surface"],
        inline=False,
    )
    embed.add_field(
        name="🔑 湯底（真相）",
        value=_turtle_soup_state["truth"],
        inline=False,
    )

    if _turtle_soup_state["qa_history"]:
        history_text = "\n".join(
            f"Q: {qa['q']} → A: {qa['a']}（{qa['asked_by']}）"
            for qa in _turtle_soup_state["qa_history"][-10:]
        )
        if len(_turtle_soup_state["qa_history"]) > 10:
            history_text = f"（僅顯示最近10則）\n{history_text}"
        embed.add_field(name="📜 提問記錄", value=history_text[:1024], inline=False)

    await channel.send(embed=embed)

    # 重置狀態
    _turtle_soup_state = {
        "active": False,
        "surface": "", "truth": "", "difficulty": "medium",
        "max_questions": 20, "questions_used": 0,
        "qa_history": [],         "game_msg_id": None, "channel_id": None,
        "processing": False, "queue": [],
        "started_at": 0, "starter_user_id": None,
        "hints_given": 0,
        "extra_time_used": False,
        "hint_panel_active": False,
        "game_id": _turtle_soup_state.get("game_id", 0),
    }

    print(f"🍜 Turtle soup ended: solved={solved}, winner={winner}")

    # 重新發送邀請面板
    await asyncio.sleep(3)
    await _post_turtle_soup_invite(channel)

# ── Slash Command Group ──
class TurtleSoupGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="soup", description="AI 海龜湯遊戲")

    @app_commands.command(name="toggle", description="開啟/關閉 AI 海龜湯功能（管理員限定）")
    async def soup_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        chat_ai_settings["turtle_soup_enabled"] = not chat_ai_settings.get("turtle_soup_enabled", False)
        _save_turtle_soup()
        status = "開啟" if chat_ai_settings["turtle_soup_enabled"] else "關閉"
        await interaction.response.send_message(f"✅ AI 海龜湯已{status}。", ephemeral=True)

    @app_commands.command(name="channel", description="設定海龜湯頻道（管理員限定）")
    @app_commands.describe(channel="要設為海龜湯頻道的頻道")
    async def soup_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        chat_ai_settings["turtle_soup_channel_id"] = str(channel.id)
        _save_turtle_soup()
        await interaction.response.send_message(
            f"✅ 海龜湯頻道已設為 {channel.mention}。\n"
            f"啟用後，沒有遊戲進行時會自動發送邀請面板。",
            ephemeral=True,
        )

    @app_commands.command(name="difficulty", description="設定海龜湯難度（管理員限定）")
    @app_commands.describe(level="easy / medium / hard")
    @app_commands.choices(level=[
        app_commands.Choice(name="簡單", value="easy"),
        app_commands.Choice(name="中等", value="medium"),
        app_commands.Choice(name="困難", value="hard"),
    ])
    async def soup_difficulty(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        chat_ai_settings["turtle_soup_difficulty"] = level.value
        _save_turtle_soup()
        await interaction.response.send_message(
            f"✅ 海龜湯難度已設為 **{level.name}**。", ephemeral=True,
        )

    @app_commands.command(name="end", description="強制結束當前海龜湯遊戲（管理員限定）")
    async def soup_end(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        global _turtle_soup_state
        if not _turtle_soup_state["active"]:
            await interaction.response.send_message("⚠️ 目前沒有進行中的海龜湯遊戲。", ephemeral=True)
            return
        await _end_turtle_soup(interaction.channel, solved=False)
        await interaction.response.send_message("✅ 海龜湯遊戲已強制結束。", ephemeral=True)

    @app_commands.command(name="status", description="查看海龜湯遊戲狀態")
    async def soup_status(self, interaction: discord.Interaction):
        global _turtle_soup_state
        embed = discord.Embed(title="🍜 AI 海龜湯狀態", color=discord.Color.teal())
        embed.add_field(name="功能狀態", value="開啟" if chat_ai_settings.get("turtle_soup_enabled") else "關閉", inline=True)
        ch_id = chat_ai_settings.get("turtle_soup_channel_id")
        embed.add_field(name="頻道", value=f"<#{ch_id}>" if ch_id else "未設定", inline=True)
        embed.add_field(name="難度", value=chat_ai_settings.get("turtle_soup_difficulty", "medium"), inline=True)

        if _turtle_soup_state["active"]:
            embed.add_field(name="遊戲進行中", value="是", inline=True)
            embed.add_field(name="已用提問", value=f"{_turtle_soup_state['questions_used']}/{_turtle_soup_state['max_questions']}", inline=True)
            embed.add_field(name="排隊中", value=f"{len(_turtle_soup_state['queue'])} 人", inline=True)
            elapsed = int(_time.time() - _turtle_soup_state["started_at"])
            embed.add_field(name="已進行", value=f"{elapsed//60}m{elapsed%60}s", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Slash Command Group ──

