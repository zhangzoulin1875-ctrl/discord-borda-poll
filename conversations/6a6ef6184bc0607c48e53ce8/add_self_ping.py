"""Patch discord_borda_poll.py to add self-ping loop (prevents Render sleep)."""

with open("discord_borda_poll.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add import aiohttp (needed for self-ping HTTP calls)
old_import = "from aiohttp import web"
new_import = "from aiohttp import web\nimport aiohttp"
assert old_import in content, "aiohttp import not found"
content = content.replace(old_import, new_import)

# 2. Add self_ping_loop function after keep_alive_server
old_marker = '    print(f"\U0001f310 Keep-alive HTTP server started on port {port}")\n\n\n# \u2500\u2500\u2500 Module Loader'
new_marker = '''    print(f"\U0001f310 Keep-alive HTTP server started on port {port}")


# \u2500\u2500\u2500 Self-Ping Loop (prevent Render free tier sleep) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
async def self_ping_loop():
    """Every ~4.5 min, ping our own /health endpoint to prevent Render from
    spinning down the free-tier Web Service after 15 min of inactivity.

    Uses SELF_URL or RENDER_EXTERNAL_URL env var (Render auto-injects the latter).
    """
    await asyncio.sleep(30)  # wait for bot to be fully online
    base_url = os.getenv("SELF_URL") or os.getenv("RENDER_EXTERNAL_URL") or ""
    if not base_url:
        print("\u2139\ufe0f SELF_URL not set, self-ping disabled")
        print("\u2139\ufe0f Add SELF_URL=https://your-service.onrender.com in Render env vars")
        return
    health_url = base_url.rstrip("/") + "/health"
    print(f"\U0001f501 Self-ping started: {health_url}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    health_url,
                    headers={"User-Agent": "SelfPing/1.0"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    pass  # status doesn't matter, just need to wake it
            except Exception:
                pass  # silently ignore, will retry
            await asyncio.sleep(270)  # 4.5 min


# \u2500\u2500\u2500 Module Loader'''

assert old_marker in content, "keep_alive marker not found"
content = content.replace(old_marker, new_marker)

# 3. Start self_ping_loop in on_ready
old_on_ready = '''    # Set nickname to ICEA official
    for guild in bot.guilds:
        try:
            member = guild.get_member(bot.user.id)
            if member and member.nick != "ICEA official":
                await member.edit(nick="ICEA official")
        except Exception:
            pass'''

new_on_ready = '''    # Set nickname to ICEA official
    for guild in bot.guilds:
        try:
            member = guild.get_member(bot.user.id)
            if member and member.nick != "ICEA official":
                await member.edit(nick="ICEA official")
        except Exception:
            pass

    # Start self-ping loop (prevent Render free tier sleep)
    asyncio.ensure_future(self_ping_loop())'''

assert old_on_ready in content, "on_ready marker not found"
content = content.replace(old_on_ready, new_on_ready)

# 4. Add SELF_URL to render.yaml
with open("render.yaml", "r") as f:
    render_content = f.read()

old_env = '      - key: GITHUB_TOKEN\n        sync: false'
new_env = '      - key: GITHUB_TOKEN\n        sync: false\n      - key: SELF_URL\n        sync: false'
assert old_env in render_content, "render.yaml GITHUB_TOKEN not found"
render_content = render_content.replace(old_env, new_env)

with open("render.yaml", "w") as f:
    f.write(render_content)

with open("discord_borda_poll.py", "w", encoding="utf-8") as f:
    f.write(content)

print("All patches applied")
