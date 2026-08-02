#!/usr/bin/env python3
"""Modify discord_borda_poll.py to add AI meeting minutes feature."""

with open("discord_borda_poll.py", "r") as f:
    content = f.read()

changes = []

# 1. Add imports
old_imports = "import random\nimport string"
new_imports = "import random\nimport string\nimport re\nfrom datetime import datetime, timedelta"
if old_imports in content:
    content = content.replace(old_imports, new_imports)
    changes.append("✅ Added imports: re, datetime, timedelta")
else:
    if "import re" not in content:
        content = content.replace("import string", "import string\nimport re\nfrom datetime import datetime, timedelta")
        changes.append("✅ Added imports (fallback)")

# 2. Add AI settings and helper functions
old_data_structures = """# ──────────────────────────────────────────────
# 資料結構
# ──────────────────────────────────────────────"""

# Use single-quoted triple strings to avoid conflict
ai_code = '''# ──────────────────────────────────────────────
# AI 會議紀錄設定
# ──────────────────────────────────────────────

DEFAULT_AI_SYSTEM_PROMPT = """你是一個專業的議會會議紀錄整理助手。請根據以下 Discord 頻道的對話紀錄，整理出結構化的會議紀錄。

格式要求：
## 會議資訊
- 日期時間
- 頻道
- 出席人員（列出所有發言者）

## 討論議題
按時間順序列出討論的議題，每個議題用標題標示

## 各議題重點
每個議題下列出各成員的發言摘要，標明發言者用戶名

## 動議與提案
列出所有提出的動議

## 投票結果（如有）
列出投票的項目和結果

## 結論與決議
列出會議的結論和後續事項

請用繁體中文輸出，保持客觀中立的語氣。如果對話中沒有明確的議題分界，請根據內容自動歸類。只整理有意義的討論內容，忽略閒聊和系統訊息。"""

ai_settings = {
    "api_url": os.getenv("AI_API_URL", "https://api.openai.com/v1/chat/completions"),
    "api_key": os.getenv("AI_API_KEY", ""),
    "model": os.getenv("AI_MODEL", "gpt-4o-mini"),
    "system_prompt": os.getenv("AI_SYSTEM_PROMPT", DEFAULT_AI_SYSTEM_PROMPT),
}


def parse_since(since_str: str):
    """Parse a time string and return UTC datetime."""
    since_str = since_str.strip().lower()
    now_utc = datetime.utcnow()

    # "Nh" or "Nhours"
    m = re.match(r'^(\\d+(?:\\.\\d+)?)\\s*h(?:ours?)?$', since_str)
    if m:
        return now_utc - timedelta(hours=float(m.group(1)))

    # "Nm" or "Nmin" or "Nminutes"
    m = re.match(r'^(\\d+(?:\\.\\d+)?)\\s*m(?:in(?:utes?)?)?$', since_str)
    if m:
        return now_utc - timedelta(minutes=float(m.group(1)))

    # "NhNm" like "1h30m"
    m = re.match(r'^(?:(\\d+)h)?(?:(\\d+)m)?$', since_str)
    if m and (m.group(1) or m.group(2)):
        h = int(m.group(1) or 0)
        mi = int(m.group(2) or 0)
        return now_utc - timedelta(hours=h, minutes=mi)

    # "HH:MM" (assume UTC+8)
    try:
        t = datetime.strptime(since_str, "%H:%M")
        target = now_utc.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        target -= timedelta(hours=8)
        if target > now_utc:
            target -= timedelta(days=1)
        return target
    except ValueError:
        pass

    # "YYYY-MM-DD"
    try:
        d = datetime.strptime(since_str, "%Y-%m-%d")
        return d - timedelta(hours=8)
    except ValueError:
        pass

    # "YYYY-MM-DD HH:MM"
    try:
        d = datetime.strptime(since_str, "%Y-%m-%d %H:%M")
        return d - timedelta(hours=8)
    except ValueError:
        pass

    return None


async def call_ai_api(conversation: str, settings: dict) -> str:
    """Call an OpenAI-compatible API to summarize the conversation."""
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.get("model", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": settings.get("system_prompt", DEFAULT_AI_SYSTEM_PROMPT)},
            {"role": "user", "content": conversation},
        ],
        "temperature": 0.3,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(settings["api_url"], json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(f"AI API returned {resp.status}: {error_text[:500]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]


async def api_get_ai_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    key = ai_settings["api_key"]
    masked = key[:6] + "..." + key[-4:] if len(key) > 10 else ("***" if key else "")
    return web.json_response({
        "api_url": ai_settings["api_url"],
        "api_key_masked": masked,
        "has_key": bool(key),
        "model": ai_settings["model"],
        "system_prompt": ai_settings["system_prompt"],
    })


async def api_set_ai_settings(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    if "api_url" in body and body["api_url"]:
        ai_settings["api_url"] = body["api_url"]
    if "api_key" in body and body["api_key"]:
        ai_settings["api_key"] = body["api_key"]
    if "model" in body and body["model"]:
        ai_settings["model"] = body["model"]
    if "system_prompt" in body:
        ai_settings["system_prompt"] = body["system_prompt"]
    return web.json_response({"ok": True})


# ──────────────────────────────────────────────
# 資料結構
# ──────────────────────────────────────────────'''

if old_data_structures in content:
    content = content.replace(old_data_structures, ai_code)
    changes.append("✅ AI settings code inserted")
else:
    changes.append("❌ data structures pattern not found")

# 3. Add MeetingGroup class
old_launch = """# ──────────────────────────────────────────────
# 啟動
# ──────────────────────────────────────────────

def main():"""

meeting_code = '''# ──────────────────────────────────────────────
# 會議指令群組
# ──────────────────────────────────────────────


class MeetingGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="meeting", description="會議相關指令")

    @app_commands.command(name="adjourn", description="整理會議紀錄（管理員限定）")
    @app_commands.describe(
        channel="要整理的頻道",
        since="起始時間（例如：2h=2小時前、1h30m、14:00、2026-08-02）",
    )
    async def adjourn(self, interaction: discord.Interaction, channel: discord.TextChannel, since: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        if not ai_settings["api_key"]:
            await interaction.response.send_message(
                "❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定 中設定。", ephemeral=True
            )
            return

        after_time = parse_since(since)
        if not after_time:
            await interaction.response.send_message(
                "❌ 無法解析時間。支援格式：`2h`（2小時前）、`1h30m`、`14:00`、`2026-08-02`、`2026-08-02 14:00`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        # Collect messages
        formatted = []
        count = 0
        try:
            async for msg in channel.history(after=after_time, limit=500):
                if msg.author.bot:
                    continue
                content = msg.content.strip()
                if not content:
                    if msg.attachments:
                        content = f"[傳送了 {len(msg.attachments)} 個附件]"
                    elif msg.embeds:
                        content = f"[傳送了嵌入訊息: {msg.embeds[0].title or '無標題'}]"
                    else:
                        continue
                if len(content) > 500:
                    content = content[:500] + "..."
                time_str = msg.created_at.strftime("%H:%M")
                name = msg.author.display_name
                formatted.append(f"[{time_str}] {name}: {content}")
                count += 1
        except discord.Forbidden:
            await interaction.followup.send("❌ 沒有權限讀取該頻道的訊息。", ephemeral=True)
            return

        if not formatted:
            await interaction.followup.send(
                f"❌ 在指定時間後未找到任何訊息（頻道：{channel.mention}，起始：{after_time.strftime('%Y-%m-%d %H:%M UTC')}）",
                ephemeral=True,
            )
            return

        # Build conversation log
        log_text = f"頻道: #{channel.name}\\n時間範圍: {after_time.strftime('%Y-%m-%d %H:%M')} UTC ~ 整理時間\\n訊息數: {count}\\n\\n"
        log_text += "\\n".join(reversed(formatted))

        if len(log_text) > 30000:
            log_text = log_text[:30000] + "\\n...（後續訊息已截斷）"

        await interaction.followup.send(f"📝 正在整理 {count} 則訊息，請稍候...", ephemeral=True)

        # Call AI
        try:
            result = await call_ai_api(log_text, ai_settings)
        except Exception as e:
            await interaction.followup.send(f"❌ AI 整理失敗：{e}", ephemeral=True)
            return

        # Post result
        embed = discord.Embed(
            title=f"📋 會議紀錄 — {channel.name}",
            description=f"整理範圍：{after_time.strftime('%Y-%m-%d %H:%M')} UTC 起\\n共 {count} 則訊息\\nAI 模型：{ai_settings['model']}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"由 {interaction.user.display_name} 整理")

        if len(result) <= 4096:
            embed.add_field(name="會議紀錄", value=result, inline=False)
            await interaction.followup.send(embed=embed)
        else:
            import io
            file_content = f"# 會議紀錄 — #{channel.name}\\n# 整理範圍：{after_time.strftime('%Y-%m-%d %H:%M')} UTC 起\\n# 共 {count} 則訊息\\n# 由 {interaction.user.display_name} 整理\\n# AI 模型：{ai_settings['model']}\\n\\n---\\n\\n{result}"
            file = discord.File(io.BytesIO(file_content.encode("utf-8")), filename=f"meeting_minutes_{channel.name}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.md")
            embed.add_field(name="會議紀錄", value="（內容過長，已附加為 .md 檔案）", inline=False)
            await interaction.followup.send(embed=embed, file=file)

    @app_commands.command(name="test", description="測試 AI API 連線（管理員限定）")
    async def test_ai(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not ai_settings["api_key"]:
            await interaction.response.send_message("❌ 尚未設定 AI API Key。請到 Dashboard → ⚙️ AI 設定 中設定。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            result = await call_ai_api("請回覆：AI 連線測試成功！", ai_settings)
            await interaction.followup.send(f"✅ AI API 連線成功！\\n模型：{ai_settings['model']}\\n回覆：{result}")
        except Exception as e:
            await interaction.followup.send(f"❌ AI API 連線失敗：{e}")


# ──────────────────────────────────────────────
# 啟動
# ──────────────────────────────────────────────

def main():'''

if old_launch in content:
    content = content.replace(old_launch, meeting_code)
    changes.append("✅ MeetingGroup class added")
else:
    changes.append("❌ launch section pattern not found")

# 4. Register MeetingGroup in on_ready
old_on_ready = "    bot.tree.add_command(PollGroup())"
new_on_ready = "    bot.tree.add_command(PollGroup())\n    bot.tree.add_command(MeetingGroup())"

if old_on_ready in content:
    content = content.replace(old_on_ready, new_on_ready)
    changes.append("✅ MeetingGroup registered in on_ready")
else:
    changes.append("❌ on_ready pattern not found")

# 5. Add AI settings routes
old_health_route = 'app.router.add_get("/health", health)'
new_health_route = '''app.router.add_get("/health", health)

        # AI settings API
        app.router.add_get("/api/ai-settings", api_get_ai_settings)
        app.router.add_put("/api/ai-settings", api_set_ai_settings)'''

if old_health_route in content:
    content = content.replace(old_health_route, new_health_route)
    changes.append("✅ AI settings routes added")
else:
    changes.append("❌ health route pattern not found")

# 6. Update env config comment
old_env_comment = "  OAUTH_REDIRECT_URI  - OAuth 回調 URL，例如 https://你的服務.onrender.com/callback"
new_env_comment = """  OAUTH_REDIRECT_URI  - OAuth 回調 URL，例如 https://你的服務.onrender.com/callback
  AI_API_URL    - AI API 端點（預設 OpenAI: https://api.openai.com/v1/chat/completions）
  AI_API_KEY    - AI API 金鑰（也可在 Dashboard 中設定）
  AI_MODEL      - AI 模型名稱（預設 gpt-4o-mini，也可在 Dashboard 中設定）
  AI_SYSTEM_PROMPT - AI 系統提示詞（預設為會議紀錄整理格式）"""

if old_env_comment in content:
    content = content.replace(old_env_comment, new_env_comment)
    changes.append("✅ Env config comment updated")

# 7. Update _poll_to_dict
old_poll_dict = '''    return {
        "poll_id": poll.poll_id,
        "title": poll.title,
        "mode": poll.mode,
        "status": poll.status,
        "vote_count": poll.vote_count(),
        "option_count": poll.option_count(),
        "allowed_roles": poll.allowed_roles,
        "options": [{"text": opt.text} for opt in poll.options],
        "votes": {str(uid): v for uid, v in poll.votes.items()},
    }'''

new_poll_dict = '''    return {
        "poll_id": poll.poll_id,
        "title": poll.title,
        "mode": poll.mode,
        "status": poll.status,
        "description": getattr(poll, "description", ""),
        "vote_count": poll.vote_count(),
        "option_count": poll.option_count(),
        "allowed_roles": poll.allowed_roles,
        "options": [{"text": opt.text} for opt in poll.options],
        "votes": {str(uid): v for uid, v in poll.votes.items()},
    }'''

if old_poll_dict in content:
    content = content.replace(old_poll_dict, new_poll_dict)
    changes.append("✅ _poll_to_dict updated")

# Syntax check
import tempfile, subprocess
with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
    f.write(content)
    fname = f.name
result = subprocess.run(["python3", "-m", "py_compile", fname], capture_output=True, text=True)
if result.returncode == 0:
    print("✅ Syntax check passed")
else:
    print("❌ Syntax error:", result.stderr)
    for line in result.stderr.splitlines()[:15]:
        print(line)

with open("discord_borda_poll.py", "w") as f:
    f.write(content)

for c in changes:
    print(c)
print(f"Total lines: {len(content.splitlines())}")
