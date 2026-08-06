# ═════════════════════════════════════════════════════════════════
# Module: 70_quiz (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

def load_quiz_data():
    """Load quiz data from disk."""
    global quiz_settings, quiz_scores, quiz_champions, quiz_active_questions, _quiz_last_question_time
    try:
        if os.path.exists(QUIZ_SETTINGS_FILE):
            with open(QUIZ_SETTINGS_FILE, "r", encoding="utf-8") as f:
                quiz_settings.update(json_module.load(f))
        if os.path.exists(QUIZ_SCORES_FILE):
            with open(QUIZ_SCORES_FILE, "r", encoding="utf-8") as f:
                quiz_scores = json_module.load(f)
        if os.path.exists(QUIZ_CHAMPIONS_FILE):
            with open(QUIZ_CHAMPIONS_FILE, "r", encoding="utf-8") as f:
                quiz_champions = json_module.load(f)
        if os.path.exists(QUIZ_STATE_FILE):
            with open(QUIZ_STATE_FILE, "r", encoding="utf-8") as f:
                state = json_module.load(f)
                quiz_active_questions = state.get("active_questions", {})
                _quiz_last_question_time = state.get("last_question_time", 0)
        global quiz_asked_questions, quiz_recent_titles
        if os.path.exists(QUIZ_ASKED_FILE):
            with open(QUIZ_ASKED_FILE, "r", encoding="utf-8") as f:
                quiz_asked_questions = json_module.load(f)
            print(f"✅ 問答歷史載入：{len(quiz_asked_questions)} 題已出過")
        if os.path.exists(QUIZ_RECENT_TITLES_FILE):
            with open(QUIZ_RECENT_TITLES_FILE, "r", encoding="utf-8") as f:
                quiz_recent_titles = json_module.load(f)
            print(f"✅ 問答近期文章載入：{len(quiz_recent_titles)} 篇避免重複")
        print(f"✅ 問答資料載入：{'啟用' if quiz_settings.get('enabled') else '停用'}, "
              f"{len(quiz_scores)} 位玩家, {len(quiz_champions)} 位冠軍, "
              f"{len(quiz_active_questions)} 個活躍題目")
    except Exception as e:
        print(f"⚠️ Quiz data load failed: {e}")


def _normalize_quiz_question(q: str) -> str:
    """Normalize a quiz question for dedup comparison: strip whitespace,
    punctuation, and lowercase, so trivial wording changes don't bypass the
    duplicate check."""
    import re as _re
    # Remove all whitespace, common punctuation, and lowercase
    cleaned = _re.sub(r'[\s\W_]+', '', q).lower().strip()
    return cleaned


def _is_duplicate_question(question: str) -> bool:
    """Check if a question has been asked before (fuzzy: normalized match)."""
    if not question:
        return False
    norm = _normalize_quiz_question(question)
    for prev in quiz_asked_questions:
        prev_norm = _normalize_quiz_question(prev)
        # Exact normalized match = duplicate
        if norm == prev_norm:
            return True
        # Also check substring match (catches minor additions/removals)
        if len(norm) > 10 and (norm in prev_norm or prev_norm in norm):
            return True
    return False


async def _generate_quiz_question() -> dict | None:
    """Fetch a random micropedia article and generate a quiz question via AI.
    Returns {question, options: [4], correct_index: 0-3, source_title, source_url} or None.
    Retries up to 3 times if the generated question is a duplicate of one
    previously asked."""
    if not chat_ai_settings.get("api_key"):
        print("⚠️ Quiz: No AI API key configured")
        return None

    # Pick a random broad search term to get varied articles. To avoid the
    # quiz repeatedly landing on the same dominant article (e.g. a big,
    # comprehensive nation article that happens to rank well for many broad
    # category terms), we search several terms, gather MULTIPLE candidate
    # titles per term, and explicitly filter out anything asked recently —
    # only falling back to a repeat if truly nothing fresh is available.
    search_terms = [
        "共和國", "聯邦", "王國", "帝國", "公國", "共和",
        "自由邦", "城邦", "聯盟", "組織", "條約", "宣言",
        "憲法", "政府", "選舉", "文化", "歷史", "經濟",
        "外交", "國旗", "國歌", "節日", "軍事", "教育",
    ]
    shuffled_terms = list(search_terms)
    _quiz_random.shuffle(shuffled_terms)

    article_text = ""
    source_title = ""
    source_url = ""
    try:
        if _shared_session and not _shared_session.closed:
            _session_cm = None
            session = _shared_session
        else:
            _session_cm = aiohttp.ClientSession()
            session = await _session_cm.__aenter__()
        try:
            for term in shuffled_terms[:5]:
                try:
                    titles = await asyncio.wait_for(
                        _micropedia_search_api(session, term, 8),
                        timeout=6
                    )
                except Exception:
                    continue
                if not titles:
                    continue
                fresh_titles = [t for t in titles if t not in quiz_recent_titles]
                candidates = fresh_titles if fresh_titles else titles  # fall back to a repeat only if nothing fresh anywhere
                chosen_title = _quiz_random.choice(candidates)
                try:
                    content_text = await asyncio.wait_for(
                        _micropedia_fetch_content(session, [chosen_title]),
                        timeout=6
                    )
                except Exception:
                    continue
                if content_text and len(content_text.strip()) >= 50:
                    article_text = content_text[:3000]
                    source_title = chosen_title
                    if fresh_titles:
                        break  # got a genuinely fresh article, stop searching
                    # else: keep this as a fallback but keep trying other terms for a fresh one
        finally:
            if _session_cm is not None:
                await _session_cm.__aexit__(None, None, None)
    except asyncio.TimeoutError:
        print("⚠️ Quiz: Micropedia fetch timed out")
    except Exception as e:
        print(f"⚠️ Quiz: Micropedia fetch error: {e}")

    if not article_text or len(article_text.strip()) < 50:
        print("⚠️ Quiz: Not enough content from micropedia")
        return None

    if source_title:
        import urllib.parse as _up_quiz
        source_url = f"https://www.micropedia.site/wiki/{_up_quiz.quote(source_title)}"
    else:
        source_url = ""

    # Generate question via AI
    system_prompt = (
        "你是微國家百科問答出題機。根據提供的百科資料，出一道單選題。\n"
        "題目要求：\n"
        "- 只出單選題，4個選項\n"
        "- 題目要清楚明確，答案必須能從資料中找到\n"
        "- 選項要合理，有迷惑性但不能有爭議\n"
        "- 正確答案的位置要隨機（不要總是放在同一個位置）\n\n"
        "請嚴格回覆以下 JSON 格式（不要加 markdown code block，不要加其他文字）：\n"
        '{"question": "題目", "options": ["選項A", "選項B", "選項C", "選項D"], "correct_index": 0}'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"百科資料：\n{article_text}\n\n請根據以上資料出一道單選題。"}
    ]

    try:
        _quiz_settings = dict(chat_ai_settings)
        _quiz_settings["model"] = chat_ai_settings.get("quiz_model") or chat_ai_settings["model"]
        result = await asyncio.wait_for(
            call_chat_api(messages, _quiz_settings, max_tokens=600, fallback_mode="disabled"),
            timeout=30
        )
    except asyncio.TimeoutError:
        print("⚠️ Quiz: AI question generation timed out")
        return None
    except Exception as e:
        print(f"⚠️ Quiz: AI API error: {e}")
        return None

    # Parse the AI response
    raw_reply = result.get("content", "")
    if not raw_reply:
        # Try tool_calls if content is empty
        tool_calls = result.get("tool_calls", [])
        if tool_calls:
            raw_reply = tool_calls[0].get("function", {}).get("arguments", "")

    # Strip markdown code blocks if present
    raw_reply = raw_reply.strip()
    if raw_reply.startswith("```"):
        raw_reply = raw_reply.split("\n", 1)[-1] if "\n" in raw_reply else raw_reply[3:]
    if raw_reply.endswith("```"):
        raw_reply = raw_reply[:-3]
    raw_reply = raw_reply.strip()

    try:
        quiz_data = json_module.loads(raw_reply)
    except Exception:
        # Try to extract JSON from the text
        import re
        match = re.search(r'\{[^{}]*"question"[^{}]*\}', raw_reply, re.DOTALL)
        if match:
            try:
                quiz_data = json_module.loads(match.group())
            except Exception:
                print(f"⚠️ Quiz: Cannot parse AI response: {raw_reply[:200]}")
                return None
        else:
            print(f"⚠️ Quiz: Cannot parse AI response: {raw_reply[:200]}")
            return None

    # Validate schema
    if not isinstance(quiz_data, dict):
        print("⚠️ Quiz: AI response is not a dict")
        return None
    if "question" not in quiz_data or "options" not in quiz_data or "correct_index" not in quiz_data:
        print("⚠️ Quiz: AI response missing required fields")
        return None
    options = quiz_data["options"]
    if not isinstance(options, list) or len(options) != 4:
        print(f"⚠️ Quiz: options must have 4 items, got {len(options)}")
        return None
    correct_index = quiz_data["correct_index"]
    if not isinstance(correct_index, int) or correct_index < 0 or correct_index > 3:
        print(f"⚠️ Quiz: correct_index must be 0-3, got {correct_index}")
        return None
    question = quiz_data["question"]
    if not isinstance(question, str) or len(question.strip()) < 5:
        print(f"⚠️ Quiz: question too short")
        return None

    return {
        "question": question.strip(),
        "options": [str(o).strip() for o in options],
        "correct_index": correct_index,
        "source_title": source_title,
        "source_url": source_url,
    }


# ── Dedup wrapper: retry with different articles if AI generates a duplicate ──
async def _generate_quiz_question_with_dedup() -> dict | None:
    """Wrap _generate_quiz_question with duplicate detection: retry up to 3
    times if the generated question matches one previously asked."""
    global quiz_asked_questions, quiz_recent_titles
    quiz_data = None
    for attempt in range(3):
        quiz_data = await _generate_quiz_question()
        if not quiz_data:
            continue
        if _is_duplicate_question(quiz_data["question"]):
            print(f"🔄 Quiz: Question #{attempt+1} is duplicate, retrying...")
            continue
        # Not a duplicate — record it and return
        quiz_asked_questions.append(quiz_data["question"])
        # Trim history to prevent unbounded growth
        if len(quiz_asked_questions) > _QUIZ_MAX_HISTORY:
            quiz_asked_questions = quiz_asked_questions[-_QUIZ_MAX_HISTORY:]
        # Remember the source article too, so future rounds actively steer
        # away from it for a while — even a reworded question about the same
        # article feels repetitive to players.
        source_title = quiz_data.get("source_title")
        if source_title:
            if source_title in quiz_recent_titles:
                quiz_recent_titles.remove(source_title)
            quiz_recent_titles.append(source_title)
            if len(quiz_recent_titles) > _QUIZ_RECENT_TITLES_MAX:
                quiz_recent_titles = quiz_recent_titles[-_QUIZ_RECENT_TITLES_MAX:]
        return quiz_data
    # All 3 attempts were duplicates or failed
    print("⚠️ Quiz: Could not generate a non-duplicate question after 3 attempts")
    # Return the last generated one anyway (better than no question at all)
    return quiz_data


class QuizAnswerView(discord.ui.View):
    """Interactive buttons for quiz answers. Times out after 10 minutes."""

    def __init__(self, question_data: dict, message_id: int):
        super().__init__(timeout=600)  # 10 minutes
        self.question_data = question_data
        self.message_id = message_id
        self.answered = False
        self.correct_user_id = None

        for i, option_text in enumerate(question_data["options"]):
            label = f"{'🇦🇧🇨🇩'[i]} {option_text}" if i < 4 else option_text
            # Use simple letter labels to keep buttons short
            letter = "ABCD"[i]
            btn = discord.ui.Button(
                label=f"{letter}. {option_text[:70]}",
                style=discord.ButtonStyle.primary,
                custom_id=f"quiz_{message_id}_{i}"
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            await self._handle_answer(interaction, index)
        return callback

    async def _handle_answer(self, interaction: discord.Interaction, selected_index: int):
        user_id_str = str(interaction.user.id)

        # Each user gets exactly ONE attempt per question
        if not hasattr(self, '_user_attempted'):
            self._user_attempted = set()
        if user_id_str in self._user_attempted:
            if user_id_str == self.correct_user_id:
                await interaction.response.send_message("你已經答對了！🎉", ephemeral=True)
            else:
                await interaction.response.send_message("你已經答過了，這題沒有機會了！", ephemeral=True)
            return

        # Already answered correctly by someone
        if self.answered:
            await interaction.response.send_message("已經有人搶答成功了！⚡", ephemeral=True)
            return

        # Mark this user as having used their one attempt
        self._user_attempted.add(user_id_str)

        correct_index = self.question_data["correct_index"]

        if selected_index == correct_index:
            # First correct answer!
            self.answered = True
            self.correct_user_id = user_id_str

            # Award 5 points
            today = datetime.now(GMT8).strftime("%Y-%m-%d")
            user_entry = quiz_scores.get(user_id_str, {
                "username": interaction.user.display_name,
                "daily_score": 0,
                "total_score": 0,
                "date": today,
            })
            # Reset daily score if date changed
            if user_entry.get("date") != today:
                user_entry["daily_score"] = 0
                user_entry["date"] = today
            user_entry["username"] = interaction.user.display_name
            user_entry["daily_score"] = user_entry.get("daily_score", 0) + 5
            user_entry["total_score"] = user_entry.get("total_score", 0) + 5
            quiz_scores[user_id_str] = user_entry
            save_quiz_data()

            # Update the active question
            quiz_active_questions[str(self.message_id)]["answered_by"] = user_id_str

            # Edit the embed to show the answer
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.color = discord.Color.green()
                embed.add_field(
                    name="🎉 搶答成功！",
                    value=f"**{interaction.user.display_name}** 最先答對，獲得 **5 分**！\n"
                          f"正確答案：**{'ABCD'[correct_index]}. {self.question_data['options'][correct_index]}**",
                    inline=False
                )
                if self.question_data.get("source_url"):
                    embed.add_field(
                        name="📚 來源",
                        value=f"[{self.question_data.get('source_title', '查看原文')}]({self.question_data['source_url']})",
                        inline=False
                    )
                # Disable all buttons
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.send_message(
                    f"🎉 答對了！+5 分！你今天累積 {user_entry['daily_score']} 分。",
                    ephemeral=True
                )

            # Also send a public celebration message
            try:
                await interaction.followup.send(
                    f"🎉 **{interaction.user.display_name}** 搶答成功！+5 分！"
                    f"（今日累積 {user_entry['daily_score']} 分）",
                    ephemeral=False
                )
            except Exception as e:
                print("⚠️ 靜默例外:", e)
            print(f"🎉 Quiz: {interaction.user.display_name} answered correctly (+5 pts, daily={user_entry['daily_score']})")
        else:
            # Wrong answer — one strike and you're out
            await interaction.response.send_message(
                "❌ 答錯了！這題你已經沒有機會了，等下一題吧！",
                ephemeral=True
            )

    async def on_timeout(self):
        """Reveal the answer when no one answers in time."""
        if self.answered:
            return  # Already answered, nothing to do

        # Mark as timed out
        quiz_active_questions.pop(str(self.message_id), None)
        save_quiz_data()

        # Try to edit the message with the answer
        try:
            channel_id = quiz_settings.get("channel_id")
            if channel_id:
                channel = bot.get_channel(int(channel_id))
                if channel:
                    correct_idx = self.question_data["correct_index"]
                    embed = discord.Embed(
                        title="⏰ 時間到！無人答對",
                        color=discord.Color.orange(),
                    )
                    embed.add_field(
                        name="正確答案",
                        value=f"**{'ABCD'[correct_idx]}. {self.question_data['options'][correct_idx]}**",
                        inline=False
                    )
                    if self.question_data.get("source_url"):
                        embed.add_field(
                            name="📚 來源",
                            value=f"[{self.question_data.get('source_title', '查看原文')}]({self.question_data['source_url']})",
                            inline=False
                        )
                    # Disable all buttons
                    for child in self.children:
                        child.disabled = True
                    # Get the original message
                    msg = await channel.fetch_message(self.message_id)
                    if msg:
                        await msg.edit(embed=embed, view=self)
                    print("⏰ Quiz: Question timed out, answer revealed")
        except Exception as e:
            print(f"⚠️ Quiz timeout reveal failed: {e}")


_quiz_last_question_time = 0  # timestamp of last posted question

async def quiz_question_loop():
    """Background task: post a new quiz question at the configured interval.
    Uses short 15-second poll cycles so interval changes take effect immediately."""
    global _quiz_last_question_time
    await asyncio.sleep(60)  # Wait for bot to be ready
    while True:
        try:
            interval_secs = quiz_settings.get("interval_minutes", 30) * 60

            # Not enabled? Short sleep and re-check
            if not quiz_settings.get("enabled"):
                await asyncio.sleep(15)
                continue

            channel_id = quiz_settings.get("channel_id")
            if not channel_id:
                await asyncio.sleep(15)
                continue

            # Has enough time passed since the last question?
            now = _time.time()
            if _quiz_last_question_time and (now - _quiz_last_question_time) < interval_secs:
                await asyncio.sleep(15)
                continue

            # Clean up stale active questions (older than 10 minutes)
            stale_keys = [
                k for k, v in quiz_active_questions.items()
                if (now - v.get("created_at", 0)) > 600
            ]
            for k in stale_keys:
                quiz_active_questions.pop(k, None)
                print(f"🧹 Quiz: Cleaned up stale question {k}")
            if stale_keys:
                save_quiz_data()

            # Check if there's an unanswered active question — don't pile up
            if quiz_active_questions:
                print(f"ℹ️ Quiz: {len(quiz_active_questions)} question(s) still active, skipping this round")
                await asyncio.sleep(15)
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                print(f"⚠️ Quiz: Cannot find channel {channel_id}")
                await asyncio.sleep(15)
                continue

            # Generate the question (with dedup — won't repeat previously asked questions)
            print("📝 Quiz: Generating new question...")
            quiz_data = await _generate_quiz_question_with_dedup()
            if not quiz_data:
                print("⚠️ Quiz: Failed to generate question, will retry next cycle")
                _quiz_last_question_time = now  # Reset timer to avoid immediate retry spam
                await asyncio.sleep(15)
                continue

            # Create embed
            embed = discord.Embed(
                title="🧠 微國家百科問答",
                description=f"**{quiz_data['question']}\n\n快選出正確答案！最先答對得 5 分！",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="⏱️ 10 分鐘內搶答 | 最先答對得 5 分")

            # Send the question
            msg = await channel.send(embed=embed)
            view = QuizAnswerView(quiz_data, msg.id)
            await msg.edit(view=view)

            # Store the active question
            quiz_active_questions[str(msg.id)] = {
                "question": quiz_data["question"],
                "options": quiz_data["options"],
                "correct_index": quiz_data["correct_index"],
                "source_title": quiz_data.get("source_title", ""),
                "source_url": quiz_data.get("source_url", ""),
                "answered_by": None,
                "created_at": _time.time(),
            }

            _quiz_last_question_time = _time.time()
            save_quiz_data()
            print(f"✅ Quiz: Question posted in #{channel.name} (msg_id={msg.id})")
        except Exception as e:
            print(f"⚠️ Quiz loop error: {e}")

        await asyncio.sleep(15)  # Short poll cycle — interval changes take effect immediately


async def quiz_settlement_loop():
    """Background task: settle daily champion at 22:00 every day."""
    await asyncio.sleep(60)  # Wait for bot to be ready
    while True:
        try:
            # Check if it's 22:00 (check every 30 seconds for precision)
            now = datetime.now(GMT8)
            if now.hour == 22 and now.minute == 0 and now.second < 30:
                today = datetime.now(GMT8).strftime("%Y-%m-%d")

                # Find today's top scorer(s)
                today_scores = []
                for uid, entry in quiz_scores.items():
                    if entry.get("date") == today and entry.get("daily_score", 0) > 0:
                        today_scores.append((uid, entry["username"], entry["daily_score"]))

                channel_id = quiz_settings.get("channel_id")
                channel = bot.get_channel(int(channel_id)) if channel_id else None

                if not today_scores:
                    if channel:
                        embed = discord.Embed(
                            title="📊 今日問答結算",
                            description="今天沒有人得分，再接再厲！明天 22:00 再結算～",
                            color=discord.Color.orange(),
                        )
                        await channel.send(embed=embed)
                    # Still reset daily scores
                    for uid, entry in quiz_scores.items():
                        if entry.get("date") == today:
                            entry["daily_score"] = 0
                    save_quiz_data()
                    print("📊 Quiz: Daily settlement — no scores today")
                else:
                    # Sort by score descending
                    today_scores.sort(key=lambda x: -x[2])
                    champion_uid, champion_name, champion_score = today_scores[0]
                    runner_up_name = today_scores[1][1] if len(today_scores) > 1 else "—"
                    runner_up_score = today_scores[1][2] if len(today_scores) > 1 else 0

                    # Check for ties
                    tied = [(uid, name, score) for uid, name, score in today_scores if score == champion_score]

                    # Record champion(s)
                    for uid, name, score in tied:
                        quiz_champions.append({
                            "date": today,
                            "champion_id": uid,
                            "champion_name": name,
                            "champion_score": score,
                            "runner_up_name": runner_up_name,
                            "runner_up_score": runner_up_score,
                        })

                    if channel:
                        if len(tied) > 1:
                            embed = discord.Embed(
                                title="🏆 今日問答結算 — 共同冠軍！",
                                color=discord.Color.gold(),
                                timestamp=discord.utils.utcnow(),
                            )
                            champ_text = "\n".join(f"👑 **{name}** — {score} 分" for _, name, score in tied)
                            embed.add_field(name="共同冠軍", value=champ_text, inline=False)
                        else:
                            embed = discord.Embed(
                                title="🏆 今日問答結算",
                                color=discord.Color.gold(),
                                timestamp=discord.utils.utcnow(),
                            )
                            embed.add_field(
                                name="🥇 冠軍",
                                value=f"**{champion_name}** — {champion_score} 分",
                                inline=False
                            )
                            if len(today_scores) > 1:
                                embed.add_field(
                                    name="🥈 亞軍",
                                    value=f"**{runner_up_name}** — {runner_up_score} 分",
                                    inline=False
                                )
                            embed.add_field(
                                name="📊 完整排名",
                                value="\n".join(
                                    f"{i+1}. {name} — {score} 分"
                                    for i, (_, name, score) in enumerate(today_scores[:10])
                                ),
                                inline=False
                            )
                        embed.set_footer(text="每日 22:00 自動結算 | 明日重新計分")
                        await channel.send(embed=embed)

                    # Reset daily scores for the new day
                    for uid, entry in quiz_scores.items():
                        if entry.get("date") == today:
                            entry["daily_score"] = 0
                    save_quiz_data()
                    print(f"🏆 Quiz: Champion settled — {champion_name} ({champion_score} pts)")

                # Sleep past this minute to avoid double-settling
                await asyncio.sleep(60)
        except Exception as e:
            print(f"⚠️ Quiz settlement error: {e}")

        await asyncio.sleep(30)


# ──────────────────────────────────────────────
# AI 精煉系統 — 背景任務
class QuizGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="quiz", description="AI 問答系統")

    @app_commands.command(name="toggle", description="開啟/關閉 AI 問答功能（機器人擁有者限定）")
    async def quiz_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        quiz_settings["enabled"] = not quiz_settings.get("enabled", False)
        save_quiz_data()
        status = "開啟" if quiz_settings["enabled"] else "關閉"
        await interaction.response.send_message(f"✅ AI 問答已{status}。", ephemeral=True)

    @app_commands.command(name="channel", description="設定 AI 問答頻道（機器人擁有者限定）")
    @app_commands.describe(channel="要設為問答頻道的頻道")
    async def quiz_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        quiz_settings["channel_id"] = str(channel.id)
        quiz_settings["guild_id"] = str(interaction.guild.id) if interaction.guild else None
        save_quiz_data()
        await interaction.response.send_message(
            f"✅ AI 問答頻道已設為 {channel.mention}。\n"
            f"每 30 分鐘會自動出題，最先答對得 5 分，每晚 22:00 結算冠軍。",
            ephemeral=True
        )

    @app_commands.command(name="interval", description="設定出題間隔分鐘數（機器人擁有者限定）")
    @app_commands.describe(minutes="間隔分鐘數（預設 30）")
    async def quiz_interval(self, interaction: discord.Interaction, minutes: int):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if minutes < 5:
            await interaction.response.send_message("❌ 間隔至少 5 分鐘。", ephemeral=True)
            return
        quiz_settings["interval_minutes"] = minutes
        save_quiz_data()
        await interaction.response.send_message(f"✅ 出題間隔已設為 {minutes} 分鐘。", ephemeral=True)

    @app_commands.command(name="scoreboard", description="查看問答積分榜")
    async def quiz_scoreboard(self, interaction: discord.Interaction):
        today = datetime.now(GMT8).strftime("%Y-%m-%d")
        today_scores = []
        all_time_scores = []
        for uid, entry in quiz_scores.items():
            if entry.get("date") == today and entry.get("daily_score", 0) > 0:
                today_scores.append((entry["username"], entry["daily_score"]))
            total = entry.get("total_score", 0)
            if total > 0:
                all_time_scores.append((entry["username"], total))

        today_scores.sort(key=lambda x: -x[1])
        all_time_scores.sort(key=lambda x: -x[1])

        embed = discord.Embed(
            title="📊 AI 問答積分榜",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )

        if today_scores:
            embed.add_field(
                name=f"📅 今日排名 ({today})",
                value="\n".join(
                    f"{i+1}. {name} — {score} 分"
                    for i, (name, score) in enumerate(today_scores[:10])
                ),
                inline=False
            )
        else:
            embed.add_field(name="📅 今日排名", value="尚無得分紀錄", inline=False)

        if all_time_scores:
            embed.add_field(
                name="🏆 總排行",
                value="\n".join(
                    f"{i+1}. {name} — {score} 分"
                    for i, (name, score) in enumerate(all_time_scores[:10])
                ),
                inline=False
            )
        else:
            embed.add_field(name="🏆 總排行", value="尚無得分紀錄", inline=False)

        embed.set_footer(text="每日 22:00 結算 | 最先答對得 5 分")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="champion", description="查看歷屆問答冠軍")
    async def quiz_champion(self, interaction: discord.Interaction):
        if not quiz_champions:
            await interaction.response.send_message("尚無冠軍紀錄。每晚 22:00 自動結算。", ephemeral=True)
            return

        recent = quiz_champions[-7:]  # last 7 days
        embed = discord.Embed(
            title="🏆 歷屆問答冠軍",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        for champ in reversed(recent):
            embed.add_field(
                name=f"📅 {champ['date']}",
                value=f"👑 **{champ['champion_name']}** — {champ['champion_score']} 分\n"
                      f"🥈 {champ.get('runner_up_name', '—')} — {champ.get('runner_up_score', 0)} 分",
                inline=False
            )
        embed.set_footer(text="顯示最近 7 天 | 每晚 22:00 自動結算")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="now", description="立即出題（機器人擁有者限定）")
    async def quiz_now(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        channel_id = quiz_settings.get("channel_id")
        if not channel_id:
            await interaction.response.send_message("❌ 尚未設定問答頻道。請先用 `/quiz channel` 設定。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        quiz_data = await _generate_quiz_question_with_dedup()
        if not quiz_data:
            await interaction.followup.send("❌ 出題失敗，請稍後再試。", ephemeral=True)
            return
        channel = bot.get_channel(int(channel_id))
        if not channel:
            try:
                channel = await bot.fetch_channel(int(channel_id))
            except Exception as e:
                print("⚠️ 靜默例外:", e)
        if not channel:
            await interaction.followup.send("❌ 找不到問答頻道。", ephemeral=True)
            return
        embed = discord.Embed(
            title="🧠 微國家百科問答",
            description=f"**{quiz_data['question']}**\n\n快選出正確答案！最先答對得 5 分！",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="⏱️ 10 分鐘內搶答 | 最先答對得 5 分")
        msg = await channel.send(embed=embed)
        view = QuizAnswerView(quiz_data, msg.id)
        await msg.edit(view=view)
        quiz_active_questions[str(msg.id)] = {
            "question": quiz_data["question"],
            "options": quiz_data["options"],
            "correct_index": quiz_data["correct_index"],
            "source_title": quiz_data.get("source_title", ""),
            "source_url": quiz_data.get("source_url", ""),
            "answered_by": None,
            "created_at": _time.time(),
        }
        await interaction.followup.send(f"✅ 已在 {channel.mention} 出題。", ephemeral=True)

    @app_commands.command(name="status", description="查看問答系統狀態")
    async def quiz_status(self, interaction: discord.Interaction):
        today = datetime.now(GMT8).strftime("%Y-%m-%d")
        today_players = sum(1 for e in quiz_scores.values() if e.get("date") == today and e.get("daily_score", 0) > 0)
        total_questions_answered = sum(1 for q in quiz_active_questions.values() if q.get("answered_by"))
        embed = discord.Embed(
            title="📋 AI 問答系統狀態",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="狀態", value="✅ 啟用" if quiz_settings.get("enabled") else "❌ 停用", inline=True)
        embed.add_field(name="出題間隔", value=f"{quiz_settings.get('interval_minutes', 30)} 分鐘", inline=True)
        ch = quiz_settings.get("channel_id")
        embed.add_field(name="頻道", value=f"<#{ch}>" if ch else "未設定", inline=True)
        embed.add_field(name="今日玩家", value=str(today_players), inline=True)
        embed.add_field(name="總玩家", value=str(len(quiz_scores)), inline=True)
        embed.add_field(name="冠軍紀錄", value=str(len(quiz_champions)), inline=True)
        embed.add_field(name="活躍題目", value=str(len(quiz_active_questions)), inline=True)
        embed.set_footer(text="每日 22:00 自動結算 | /quiz toggle 開關 | /quiz channel 設定頻道")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────
# 快報與公報指令
# ──────────────────────────────────────────────

