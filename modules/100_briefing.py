# ═════════════════════════════════════════════════════════════════
# Module: 100_briefing (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

class BriefingGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="briefing", description="每日快報與每週公報")

    @app_commands.command(name="daily_set", description="設定每日自動快報時間（機器人擁有者限定）")
    @app_commands.describe(time="執行時間 HH:MM（例如：23:00）", channel="發佈快報的頻道")
    async def daily_set(self, interaction: discord.Interaction, time: str, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        # Validate time format
        try:
            h, m = time.strip().split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ 時間格式錯誤。請用 HH:MM 格式，例如 `23:00`。", ephemeral=True)
            return
        briefing_settings["daily_enabled"] = True
        briefing_settings["daily_time"] = time.strip()
        briefing_settings["daily_channel_id"] = channel.id
        save_briefing_settings()
        await interaction.response.send_message(
            f"✅ 每日快報已設定\n⏰ 每天 `{time.strip()}` 自動發佈到 {channel.mention}",
            ephemeral=True
        )

    @app_commands.command(name="daily_off", description="關閉每日自動快報（機器人擁有者限定）")
    async def daily_off(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        briefing_settings["daily_enabled"] = False
        save_briefing_settings()
        await interaction.response.send_message("✅ 每日自動快報已關閉。可用 `/briefing daily_now` 手動執行。", ephemeral=True)

    @app_commands.command(name="daily_now", description="立即生成每日快報（機器人擁有者限定）")
    @app_commands.describe(channel="發佈快報的頻道（預設：當前頻道）")
    async def daily_now(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not ai_settings["api_key"]:
            await interaction.response.send_message("❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定。", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.send_message(f"📝 每日快報開始生成，請到 {target.mention} 查看。", ephemeral=True)
        await run_briefing(target, hours=24, mode="daily")

    @app_commands.command(name="weekly_set", description="設定每週自動公報時間（機器人擁有者限定）")
    @app_commands.describe(
        day="星期幾",
        time="執行時間 HH:MM",
        channel="發佈公報的頻道",
    )
    @app_commands.choices(day=[
        app_commands.Choice(name="週一", value="0"),
        app_commands.Choice(name="週二", value="1"),
        app_commands.Choice(name="週三", value="2"),
        app_commands.Choice(name="週四", value="3"),
        app_commands.Choice(name="週五", value="4"),
        app_commands.Choice(name="週六", value="5"),
        app_commands.Choice(name="週日", value="6"),
    ])
    async def weekly_set(self, interaction: discord.Interaction, day: app_commands.Choice[str], time: str, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        try:
            h, m = time.strip().split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ 時間格式錯誤。請用 HH:MM 格式，例如 `23:00`。", ephemeral=True)
            return
        briefing_settings["weekly_enabled"] = True
        briefing_settings["weekly_day"] = int(day.value)
        briefing_settings["weekly_time"] = time.strip()
        briefing_settings["weekly_channel_id"] = channel.id
        save_briefing_settings()
        day_name = WEEKDAY_NAMES.get(int(day.value), day.name)
        await interaction.response.send_message(
            f"✅ 每週公報已設定\n⏰ 每{day_name} `{time.strip()}` 自動發佈到 {channel.mention}",
            ephemeral=True
        )

    @app_commands.command(name="weekly_off", description="關閉每週自動公報（機器人擁有者限定）")
    async def weekly_off(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        briefing_settings["weekly_enabled"] = False
        save_briefing_settings()
        await interaction.response.send_message("✅ 每週自動公報已關閉。可用 `/briefing weekly_now` 手動執行。", ephemeral=True)

    @app_commands.command(name="weekly_now", description="立即生成每週公報（機器人擁有者限定）")
    @app_commands.describe(channel="發佈公報的頻道（預設：當前頻道）")
    async def weekly_now(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not ai_settings["api_key"]:
            await interaction.response.send_message("❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定。", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.send_message(f"📝 每週公報開始生成，請到 {target.mention} 查看。", ephemeral=True)
        await run_briefing(target, hours=168, mode="weekly")

    @app_commands.command(name="status", description="查看快報與公報設定")
    async def briefing_status(self, interaction: discord.Interaction):
        daily_on = "✅ 開啟" if briefing_settings["daily_enabled"] else "❌ 關閉"
        weekly_on = "✅ 開啟" if briefing_settings["weekly_enabled"] else "❌ 關閉"
        daily_time = briefing_settings.get("daily_time", "23:00")
        daily_ch = f"<#{briefing_settings['daily_channel_id']}>" if briefing_settings.get("daily_channel_id") else "未設定"
        weekly_day_name = WEEKDAY_NAMES.get(int(briefing_settings.get("weekly_day", 6)), "週日")
        weekly_time = briefing_settings.get("weekly_time", "23:00")
        weekly_ch = f"<#{briefing_settings['weekly_channel_id']}>" if briefing_settings.get("weekly_channel_id") else "未設定"

        embed = discord.Embed(title="📰 快報與公報設定", color=discord.Color.blue())
        embed.add_field(
            name="📊 每日快報",
            value=f"狀態：{daily_on}\n時間：每天 `{daily_time}`\n頻道：{daily_ch}",
            inline=False
        )
        embed.add_field(
            name="📋 每週公報",
            value=f"狀態：{weekly_on}\n時間：每{weekly_day_name} `{weekly_time}`\n頻道：{weekly_ch}",
            inline=False
        )
        embed.set_footer(text="使用 /briefing daily_set, weekly_set 設定 | daily_off, weekly_off 關閉")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────
# 國號評價 (Nation Name Rating)
# ──────────────────────────────────────────────

async def _rate_nation_name(nation_name: str, ai_settings: dict, nation_info: str = "", gov_info: str = "") -> dict:
    """Call the AI to rate a micronation name and return structured result.
    Returns {"score": float, "comment": str, "suggestions": str, "error": str?}."""
    context_section = ""
    if nation_info or gov_info:
        context_section = "\n\n─── 創作者提供的背景資料 ───\n"
        if nation_info:
            context_section += f"【國情簡介】{nation_info}\n"
        if gov_info:
            context_section += f"【政體簡介】{gov_info}\n"
        context_section += (
            "以上是創作者自己對這個微國家的描述，請納入評分考量——"
            "國號是否與其設定的國情/政體調性一致、是否有效傳達其理念。"
            "這些資料是加分參考（幫助你理解名稱背後的脈絡），不是額外的評分項目。\n"
        )

    prompt = (
        f"你是微國家社群的國號評鑑專家。請對以下國號進行評價。\n\n"
        f"國號：「{nation_name}」\n\n"
        f"{context_section}"
        f"⚠️⚠️ 評分鐵則（極重要，違反此原則的評分視為無效）：\n"
        f"微國家（micronation）是個人或小群體基於理念、藝術創作、政治實驗、幽默諷刺、"
        f"角色扮演等目的自行成立的虛擬國家/組織，國號本來就常常刻意跳脫真實主權國家的"
        f"命名慣例——不需要看起來像聯合國會員國的名字。絕對禁止用「像不像一個真實主權"
        f"國家的正式名稱」作為評分依據，也絕對不要因為以下這些原因扣分：\n"
        f"- 使用簡體字、異體字、罕見字、自創字\n"
        f"- 全稱刻意冗長、堆疊多個修飾語或敘事性描述（這是微國家很常見的「全稱敘事」"
        f"風格，不是缺點）\n"
        f"- 風格詼諧、諷刺、惡搞、二次元、網路用語，而非莊重嚴肅\n"
        f"- 結構跳脫傳統「地名+政體」公式（如共和國/王國/聯邦等傳統詞尾不是必需品）\n"
        f"這些通常是創作者刻意的選擇，只要它們服務於這個國號自身想傳達的理念/故事/"
        f"幽默感，就應該視為加分而非扣分。你評的是「這個名字有沒有把自己想做的事做好」，"
        f"不是「這個名字像不像正常國家」。\n\n"
        f"請從以下維度綜合評分（1.0 到 10.0，精確到小數第一位）：\n"
        f"- 創意與獨特性（有沒有記憶點，是否落入菜市場名或跟其他微國家撞名）\n"
        f"- 概念完整度（名稱能否清楚傳達它想表達的理念/背景故事/幽默感，不論走向是"
        f"嚴肅、詼諧還是實驗性）\n"
        f"- 音韻與美感（唸起來、看起來是否舒服自然，這不代表一定要傳統莊重）\n"
        f"- 辨識度（社群裡好不好記、好不好簡稱、討論時容不容易辨識）\n"
        f"- 內部一致性（名稱風格跟它自己設定的理念/文化調性搭不搭，而非跟「正式國名」"
        f"比較）\n\n"
        f"請嚴格按以下格式回覆（不要加其他多餘內容）：\n"
        f"評分：X.X\n"
        f"評論：（100-200字的中文評論，說明為什麼給這個分數，包含優點和缺點——"
        f"缺點必須是名稱本身概念/音韻/辨識度上的問題，不能是「不像真實國家」這類理由）\n"
        f"建議：（50-100字的具體修改建議，如果已經很好可以說「無需修改」並簡短說明原因——"
        f"建議方向應該是強化這個國號自己的理念/風格，不是讓它「更像一個正式國家」）"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位專業的微國家（micronation）國號評鑑專家，深刻理解微國家文化——"
                "這是個人/小群體基於理念、藝術、政治實驗或幽默諷刺自創的虛擬國家，命名本來就"
                "常常刻意跳脫真實主權國家的正式命名慣例。你的評分基準是「這個名字有沒有做好"
                "自己想做的事」，絕對不是「像不像一個真實國家」。用繁體中文回答，語氣專業但親切。"
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        result = await call_chat_api(
            messages,
            {"api_url": ai_settings["api_url"], "api_key": ai_settings["api_key"], "model": ai_settings.get("model", "gpt-4o-mini"), "model_fallback_chain": ai_settings.get("model_fallback_chain", "")},
            max_tokens=1200, fallback_mode="disabled", category="admin",  # generous budget — reasoning models can burn
                              # a few hundred tokens on internal preamble
                              # before ever reaching the requested format
        )
        # call_chat_api returns the assistant MESSAGE dict directly
        # (e.g. {"role": "assistant", "content": "..."}), not a full
        # {"choices": [...]} response — no extra unwrapping needed here.
        text = result.get("content", "") if isinstance(result, dict) else ""
        if not text:
            return {"error": "AI 回應為空"}

        # Parse the response
        import re as _re
        score_match = _re.search(r'評分[：:]\s*(\d+(?:\.\d+)?)', text)
        score = float(score_match.group(1)) if score_match else 0.0
        if score > 10:
            score = 10.0
        elif score < 0:
            score = 0.0

        comment_match = _re.search(r'評論[：:]\s*(.+?)(?=建議[：:]|$)', text, _re.DOTALL)
        comment = comment_match.group(1).strip() if comment_match else ""

        suggest_match = _re.search(r'建議[：:]\s*(.+)', text, _re.DOTALL)
        suggestions = suggest_match.group(1).strip() if suggest_match else ""

        if not comment:
            comment = text[:200]

        return {"score": score, "comment": comment, "suggestions": suggestions}
    except asyncio.TimeoutError:
        return {"error": "AI 回應逾時，請稍後再試一次"}
    except Exception as e:
        # call_chat_api already retries once internally on a hollow/failed
        # response — if we're still here, both attempts failed. Show a
        # friendly message instead of the raw API error dump.
        print(f"⚠️ 國號評價 AI 呼叫失敗：{e}")
        return {"error": "AI 暫時沒有給出有效回覆，可能是評鑑模型當下比較忙，稍後再試一次應該就能過"}


