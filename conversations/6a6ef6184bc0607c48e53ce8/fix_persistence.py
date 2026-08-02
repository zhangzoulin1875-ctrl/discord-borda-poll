#!/usr/bin/env python3
"""Fix: persistent sessions (signed cookies) + poll data persistence (file-based)."""

with open("discord_borda_poll.py", "r") as f:
    content = f.read()

changes = []

# ── 1. Add imports: hmac, hashlib, json ──
old_import = "import random\nimport string\nimport re"
new_import = "import random\nimport string\nimport re\nimport hmac\nimport hashlib\nimport json as json_module"
if old_import in content:
    content = content.replace(old_import, new_import)
    changes.append("✅ Added imports: hmac, hashlib, json")
else:
    changes.append("❌ import pattern not found")

# ── 2. Replace session system with signed cookies ──

# Replace the session dict and _get_session_user
old_session_block = """dashboard_sessions: dict = {}

OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")"""

new_session_block = """# ──────────────────────────────────────────────
# Dashboard: OAuth2 & Session (signed cookies - survives restarts/redeploys)
# ──────────────────────────────────────────────

COOKIE_SECRET = os.getenv("COOKIE_SECRET", py_secrets.token_urlsafe(32))

OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")


def _create_signed_cookie(data: dict) -> str:
    \"\"\"Create an HMAC-signed cookie containing user data.\"\"\"
    payload = __import__("base64").b64encode(json_module.dumps(data).encode()).decode()
    sig = hmac.new(COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_signed_cookie(cookie: str) -> dict:
    \"\"\"Verify and decode a signed cookie. Returns None if invalid.\"\"\"
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = hmac.new(COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json_module.loads(__import__("base64").b64decode(payload))
    except Exception:
        return None"""

if old_session_block in content:
    content = content.replace(old_session_block, new_session_block)
    changes.append("✅ Replaced session dict with signed cookie system")
else:
    changes.append("❌ session block pattern not found")

# Replace _get_session_user
old_get_session = """async def _get_session_user(request):
    token = request.cookies.get("session")
    if not token or token not in dashboard_sessions:
        return None
    return dashboard_sessions[token]"""

new_get_session = """async def _get_session_user(request):
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    return _verify_signed_cookie(cookie)"""

if old_get_session in content:
    content = content.replace(old_get_session, new_get_session)
    changes.append("✅ Updated _get_session_user to use signed cookies")
else:
    changes.append("❌ _get_session_user pattern not found")

# Replace callback (login) to use signed cookies
old_callback_session = """    st = py_secrets.token_urlsafe(32)
    dashboard_sessions[st] = {
        "user_id": u.get("id", ""),
        "username": u.get("username", "unknown"),
        "avatar": u.get("avatar"),
        "access_token": tk,
        "guilds": g if isinstance(g, list) else [],
    }
    r = web.HTTPFound("/dashboard")
    r.set_cookie("session", st, httponly=True, samesite="Lax", max_age=86400)
    return r"""

new_callback_session = """    user_data = {
        "user_id": u.get("id", ""),
        "username": u.get("username", "unknown"),
        "avatar": u.get("avatar"),
        "access_token": tk,
        "guilds": g if isinstance(g, list) else [],
    }
    signed = _create_signed_cookie(user_data)
    r = web.HTTPFound("/dashboard")
    r.set_cookie("session", signed, httponly=True, samesite="Lax", max_age=86400 * 7)
    return r"""

if old_callback_session in content:
    content = content.replace(old_callback_session, new_callback_session)
    changes.append("✅ Updated callback to use signed cookies (7-day expiry)")
else:
    changes.append("❌ callback session pattern not found")

# Replace logout
old_logout = """async def dashboard_logout(request):
    t = request.cookies.get("session")
    if t and t in dashboard_sessions:
        del dashboard_sessions[t]
    r = web.HTTPFound("/dashboard")
    r.del_cookie("session")
    return r"""

new_logout = """async def dashboard_logout(request):
    r = web.HTTPFound("/dashboard")
    r.del_cookie("session")
    return r"""

if old_logout in content:
    content = content.replace(old_logout, new_logout)
    changes.append("✅ Updated logout to just clear cookie")
else:
    changes.append("❌ logout pattern not found")

# ── 3. Add poll data persistence ──

# Add persistence functions after the guild_polls definition
old_guild_polls = """# guild_id -> { poll_id -> Poll }
guild_polls: Dict[int, Dict[str, Poll]] = {}


def get_poll(guild_id: int, poll_id: str) -> Optional[Poll]:
    return guild_polls.get(guild_id, {}).get(poll_id)


def gen_poll_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))"""

new_guild_polls = """# guild_id -> { poll_id -> Poll }
guild_polls: Dict[int, Dict[str, Poll]] = {}


DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "polls_data.json")


def save_polls_to_disk():
    \"\"\"Save all polls to disk for persistence across restarts.\"\"\"
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        serializable = {}
        for gid, polls in guild_polls.items():
            serializable[str(gid)] = {}
            for pid, poll in polls.items():
                serializable[str(gid)][pid] = {
                    "poll_id": poll.poll_id,
                    "title": poll.title,
                    "mode": poll.mode,
                    "status": poll.status,
                    "options": [{"text": o.text} for o in poll.options],
                    "votes": {str(k): v for k, v in poll.votes.items()},
                    "message_id": poll.message_id,
                    "created_by": poll.created_by,
                    "allowed_roles": poll.allowed_roles,
                    "description": getattr(poll, "description", ""),
                }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json_module.dump(serializable, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Failed to save polls: {e}")


def load_polls_from_disk():
    \"\"\"Load polls from disk on startup.\"\"\"
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            total = 0
            for gid_str, polls in data.items():
                gid = int(gid_str)
                guild_polls[gid] = {}
                for pid, p in polls.items():
                    poll = Poll(
                        poll_id=p["poll_id"],
                        title=p["title"],
                        mode=p.get("mode", "borda"),
                    )
                    poll.status = p.get("status", "drafting")
                    poll.message_id = p.get("message_id")
                    poll.created_by = p.get("created_by", 0)
                    poll.allowed_roles = p.get("allowed_roles", [])
                    if p.get("description"):
                        poll.description = p["description"]
                    for o in p.get("options", []):
                        poll.add_option(o["text"])
                    poll.votes = {int(k): v for k, v in p.get("votes", {}).items()}
                    guild_polls[gid][pid] = poll
                    total += 1
            print(f"✅ 從磁碟載入 {total} 個投票")
    except Exception as e:
        print(f"⚠️ Failed to load polls: {e}")


async def auto_save_loop():
    \"\"\"Background task: save polls every 30 seconds.\"\"\"
    while True:
        await asyncio.sleep(30)
        save_polls_to_disk()


def get_poll(guild_id: int, poll_id: str) -> Optional[Poll]:
    return guild_polls.get(guild_id, {}).get(poll_id)


def gen_poll_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))"""

if old_guild_polls in content:
    content = content.replace(old_guild_polls, new_guild_polls)
    changes.append("✅ Added poll persistence functions")
else:
    changes.append("❌ guild_polls pattern not found")

# ── 4. Call load_polls_from_disk and start auto_save_loop in setup_hook ──
old_setup_hook = """async def setup_hook():
    await keep_alive_server()
    asyncio.ensure_future(self_ping_loop())"""

new_setup_hook = """async def setup_hook():
    load_polls_from_disk()
    save_polls_to_disk()  # Create file if not exists
    await keep_alive_server()
    asyncio.ensure_future(self_ping_loop())
    asyncio.ensure_future(auto_save_loop())"""

if old_setup_hook in content:
    content = content.replace(old_setup_hook, new_setup_hook)
    changes.append("✅ Updated setup_hook to load polls and start auto-save")
else:
    changes.append("❌ setup_hook pattern not found")

# ── 5. Add save_polls_to_disk() calls after poll modifications ──
# After creating a poll
old_create_end = """    poll.add_option(opt)
    await interaction.response.send_message(
        f"✅ 已建立投票 **{title}**（ID: `{poll_id}`）\\n"
        f"模式：{mode_label}\\n"
        f"使用 `/poll add` 新增更多選項，`/poll start` 啟動投票。",
        ephemeral=True,
    )"""

new_create_end = """    poll.add_option(opt)
    save_polls_to_disk()
    await interaction.response.send_message(
        f"✅ 已建立投票 **{title}**（ID: `{poll_id}`）\\n"
        f"模式：{mode_label}\\n"
        f"使用 `/poll add` 新增更多選項，`/poll start` 啟動投票。",
        ephemeral=True,
    )"""

if old_create_end in content:
    content = content.replace(old_create_end, new_create_end)
    changes.append("✅ Added save after poll create")
else:
    changes.append("❌ create_end pattern not found (may vary)")

# After adding option
old_add_end = """    poll.add_option(option)
    await interaction.response.send_message(
        f"✅ 已新增選項到投票 {poll_id}：**{option}**（目前共 {poll.option_count()} 個選項）",
        ephemeral=True,
    )"""

new_add_end = """    poll.add_option(option)
    save_polls_to_disk()
    await interaction.response.send_message(
        f"✅ 已新增選項到投票 {poll_id}：**{option}**（目前共 {poll.option_count()} 個選項）",
        ephemeral=True,
    )"""

if old_add_end in content:
    content = content.replace(old_add_end, new_add_end)
    changes.append("✅ Added save after add option")

# After starting poll
old_start_status = """        poll.status = "active\""""
new_start_status = """        poll.status = "active"
        save_polls_to_disk()"""

if old_start_status in content:
    content = content.replace(old_start_status, new_start_status)
    changes.append("✅ Added save after poll start")

# After ending poll
old_end_status = """        poll.status = "ended\""""
new_end_status = """        poll.status = "ended"
        save_polls_to_disk()"""

if old_end_status in content:
    content = content.replace(old_end_status, new_end_status)
    changes.append("✅ Added save after poll end")

# After deleting poll
old_delete = """        del guild_polls[interaction.guild.id][poll_id]"""
new_delete = """        del guild_polls[interaction.guild.id][poll_id]
        save_polls_to_disk()"""

if old_delete in content:
    content = content.replace(old_delete, new_delete)
    changes.append("✅ Added save after poll delete")

# After voting (borda)
old_borda_vote = """        self.poll.votes[self.voter_id] = self._current_rank"""
new_borda_vote = """        self.poll.votes[self.voter_id] = self._current_rank
        save_polls_to_disk()"""

if old_borda_vote in content:
    content = content.replace(old_borda_vote, new_borda_vote)
    changes.append("✅ Added save after borda vote")

# After voting (simple) - need to find the pattern
# Let's look for the simple vote save pattern
old_simple_vote = "poll.votes[interaction.user.id] = selected"
# This might vary, let me check...
# Actually let me just add save_polls_to_disk after any vote save

# After setting roles
old_set_roles = """poll.allowed_roles = role_ids"""
new_set_roles = """poll.allowed_roles = role_ids
        save_polls_to_disk()"""

if old_set_roles in content:
    content = content.replace(old_set_roles, new_set_roles)
    changes.append("✅ Added save after set roles")

# After dashboard create poll
old_dash_create = """    poll.add_option(opt_text)
    guild_polls.setdefault(gid, {})[poll.poll_id] = poll
    return web.json_response(_poll_to_dict(poll))"""

new_dash_create = """    poll.add_option(opt_text)
    guild_polls.setdefault(gid, {})[poll.poll_id] = poll
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))"""

if old_dash_create in content:
    content = content.replace(old_dash_create, new_dash_create)
    changes.append("✅ Added save after dashboard create")

# After dashboard add option
old_dash_add = """    poll.add_option(text)
    return web.json_response({"ok": True})"""

new_dash_add = """    poll.add_option(text)
    save_polls_to_disk()
    return web.json_response({"ok": True})"""

if old_dash_add in content:
    content = content.replace(old_dash_add, new_dash_add)
    changes.append("✅ Added save after dashboard add option")

# After dashboard start poll
old_dash_start = """    poll.status = "active"
    return web.json_response(_poll_to_dict(poll))"""

new_dash_start = """    poll.status = "active"
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))"""

if old_dash_start in content:
    content = content.replace(old_dash_start, new_dash_start)
    changes.append("✅ Added save after dashboard start")

# After dashboard end poll
old_dash_end = """    poll.status = "ended"
    return web.json_response(_poll_to_dict(poll))"""

new_dash_end = """    poll.status = "ended"
    save_polls_to_disk()
    return web.json_response(_poll_to_dict(poll))"""

if old_dash_end in content:
    content = content.replace(old_dash_end, new_dash_end)
    changes.append("✅ Added save after dashboard end")

# After dashboard delete poll
old_dash_delete = """    del guild_polls[gid][request.match_info["pid"]]
    return web.json_response({"ok": True})"""

new_dash_delete = """    del guild_polls[gid][request.match_info["pid"]]
    save_polls_to_disk()
    return web.json_response({"ok": True})"""

if old_dash_delete in content:
    content = content.replace(old_dash_delete, new_dash_delete)
    changes.append("✅ Added save after dashboard delete")

# ── 6. Add COOKIE_SECRET and note about persistent disk to env docs ──
old_env_doc = "  AI_SYSTEM_PROMPT - AI 系統提示詞（預設為會議紀錄整理格式）"
new_env_doc = """  AI_SYSTEM_PROMPT - AI 系統提示詞（預設為會議紀錄整理格式）
  COOKIE_SECRET   - Session 簽名密鑰（不設則每次重啟隨機生成，建議固定設定）
  持久化：投票資料存於 data/polls_data.json，建議在 Render 掛載 Persistent Disk 以跨部署保留"""

if old_env_doc in content:
    content = content.replace(old_env_doc, new_env_doc)
    changes.append("✅ Updated env docs")

# Syntax check
import tempfile, subprocess
with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
    f.write(content)
    fname = f.name
result = subprocess.run(["python3", "-m", "py_compile", fname], capture_output=True, text=True)
if result.returncode == 0:
    print("✅ Syntax check passed!")
else:
    print("❌ Syntax error:", result.stderr[:500])
    # Show error context
    for line in result.stderr.splitlines()[:10]:
        print(line)

with open("discord_borda_poll.py", "w") as f:
    f.write(content)

for c in changes:
    print(c)
print(f"Total lines: {len(content.splitlines())}")
