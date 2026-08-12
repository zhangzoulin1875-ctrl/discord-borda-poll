#!/usr/bin/env python3
"""Patch discord_borda_poll.py to add GitHub-based persistence."""
import json

with open("discord_borda_poll.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add imports
old_imports = (
    "import asyncio\n"
    "import json\n"
    "import os\n"
    "import sys\n"
    "import traceback\n"
    "from pathlib import Path\n"
    "from datetime import datetime, timezone, timedelta\n"
    "\n"
    "import discord\n"
    "from discord import app_commands\n"
    "from aiohttp import web"
)
new_imports = (
    "import asyncio\n"
    "import json\n"
    "import os\n"
    "import sys\n"
    "import base64\n"
    "import traceback\n"
    "from pathlib import Path\n"
    "from datetime import datetime, timezone, timedelta\n"
    "import urllib.request\n"
    "import urllib.error\n"
    "\n"
    "import discord\n"
    "from discord import app_commands\n"
    "from aiohttp import web"
)
assert old_imports in content, "imports block not found"
content = content.replace(old_imports, new_imports)

# 2. Add GitHub config after DATA_DIR.mkdir
old_constants = "DATA_DIR.mkdir(exist_ok=True)\n\n# \u2500\u2500\u2500 Bot Instance"
new_constants = (
    "DATA_DIR.mkdir(exist_ok=True)\n"
    "\n"
    "# \u2500\u2500\u2500 GitHub Persistence Config \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "GITHUB_TOKEN = os.getenv(\"GITHUB_TOKEN\", \"\")\n"
    "GITHUB_REPO = os.getenv(\"GITHUB_REPO\", \"zhangzoulin1875-ctrl/discord-borda-poll\")\n"
    "GITHUB_BRANCH = os.getenv(\"GITHUB_BRANCH\", \"main\")\n"
    "_github_file_shas = {}\n"
    "# Files to persist to GitHub (settings only - records are rebuildable from Discord)\n"
    "_PERSIST_FILES = {\n"
    "    \"proposal_settings.json\",\n"
    "    \"application_settings.json\",\n"
    "}\n"
    "\n"
    "# \u2500\u2500\u2500 Bot Instance"
)
assert old_constants in content, "constants block not found"
content = content.replace(old_constants, new_constants)

# 3. Add GitHub persistence functions after is_owner()
old_marker = (
    "def is_owner(interaction: discord.Interaction) -> bool:\n"
    "    return interaction.user.id == OWNER_ID\n"
    "\n"
    "\n"
    "# \u2500\u2500\u2500 Keep-Alive HTTP Server"
)

new_marker = (
    "def is_owner(interaction: discord.Interaction) -> bool:\n"
    "    return interaction.user.id == OWNER_ID\n"
    "\n"
    "\n"
    "# \u2500\u2500\u2500 GitHub Persistence (replaces Google Drive) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "def _github_api_url(filename: str) -> str:\n"
    "    return f\"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{filename}\"\n"
    "\n"
    "\n"
    "def _github_headers() -> dict:\n"
    "    return {\n"
    "        \"Authorization\": f\"Bearer {GITHUB_TOKEN}\",\n"
    "        \"Accept\": \"application/vnd.github+json\",\n"
    "        \"X-GitHub-Api-Version\": \"2022-11-28\",\n"
    "    }\n"
    "\n"
    "\n"
    "def github_pull_json(filename: str):\n"
    "    \"\"\"Sync read JSON settings from GitHub (called at startup).\"\"\"\n"
    "    if not GITHUB_TOKEN:\n"
    "        return None\n"
    "    try:\n"
    "        url = _github_api_url(filename) + f\"?ref={GITHUB_BRANCH}\"\n"
    "        req = urllib.request.Request(url, headers=_github_headers(), method=\"GET\")\n"
    "        with urllib.request.urlopen(req, timeout=15) as resp:\n"
    "            if resp.status != 200:\n"
    "                return None\n"
    "            data = json.loads(resp.read().decode(\"utf-8\"))\n"
    "            sha = data.get(\"sha\", \"\")\n"
    "            content_b64 = data.get(\"content\", \"\")\n"
    "            if sha:\n"
    "                _github_file_shas[filename] = sha\n"
    "            if not content_b64:\n"
    "                return None\n"
    "            decoded = base64.b64decode(content_b64).decode(\"utf-8\")\n"
    "            return json.loads(decoded)\n"
    "    except urllib.error.HTTPError as e:\n"
    "        if e.code == 404:\n"
    "            print(f\"\\U0001F4C2 GitHub: {filename} not found (first deploy)\")\n"
    "        else:\n"
    "            print(f\"\\u26a0\\ufe0f GitHub pull {filename} failed (HTTP {e.code})\")\n"
    "        return None\n"
    "    except Exception as e:\n"
    "        print(f\"\\u26a0\\ufe0f GitHub pull {filename} failed: {e}\")\n"
    "        return None\n"
    "\n"
    "\n"
    "def github_pull_all():\n"
    "    \"\"\"Pull all persisted settings from GitHub at startup.\"\"\"\n"
    "    if not GITHUB_TOKEN:\n"
    "        print(\"\\u2139\\ufe0f GITHUB_TOKEN not set, skipping GitHub persistence\")\n"
    "        return\n"
    "    print(f\"\\U0001F504 Syncing settings from GitHub ({GITHUB_REPO})...\")\n"
    "    pulled = 0\n"
    "    for filename in _PERSIST_FILES:\n"
    "        data = github_pull_json(filename)\n"
    "        if data is not None:\n"
    "            path = DATA_DIR / filename\n"
    "            with open(path, \"w\", encoding=\"utf-8\") as f:\n"
    "                json.dump(data, f, ensure_ascii=False, indent=2)\n"
    "            print(f\"  \\u2705 Pulled {filename} from GitHub\")\n"
    "            pulled += 1\n"
    "    print(f\"\\U0001F504 GitHub sync complete: {pulled} files\")\n"
    "\n"
    "\n"
    "async def github_push_json(filename: str, data) -> None:\n"
    "    \"\"\"Async push JSON settings to GitHub (called after save_json).\"\"\"\n"
    "    if not GITHUB_TOKEN or filename not in _PERSIST_FILES:\n"
    "        return\n"
    "    try:\n"
    "        content_str = json.dumps(data, ensure_ascii=False, indent=2)\n"
    "        content_b64 = base64.b64encode(content_str.encode(\"utf-8\")).decode(\"ascii\")\n"
    "        body_obj = {\n"
    "            \"message\": f\"Auto-sync {filename}\",\n"
    "            \"content\": content_b64,\n"
    "            \"branch\": GITHUB_BRANCH,\n"
    "        }\n"
    "        sha = _github_file_shas.get(filename)\n"
    "        if sha:\n"
    "            body_obj[\"sha\"] = sha\n"
    "        body = json.dumps(body_obj).encode(\"utf-8\")\n"
    "\n"
    "        import aiohttp\n"
    "        async with aiohttp.ClientSession() as session:\n"
    "            async with session.put(\n"
    "                _github_api_url(filename),\n"
    "                data=body,\n"
    "                headers=_github_headers(),\n"
    "                timeout=aiohttp.ClientTimeout(total=15),\n"
    "            ) as resp:\n"
    "                if resp.status in (200, 201):\n"
    "                    resp_data = await resp.json()\n"
    "                    new_sha = resp_data.get(\"content\", {}).get(\"sha\", \"\")\n"
    "                    if new_sha:\n"
    "                        _github_file_shas[filename] = new_sha\n"
    "                    print(f\"\\u2705 GitHub push {filename} success\")\n"
    "                else:\n"
    "                    err_text = await resp.text()\n"
    "                    print(f\"\\u26a0\\ufe0f GitHub push {filename} failed (HTTP {resp.status}): {err_text[:200]}\")\n"
    "    except Exception as e:\n"
    "        print(f\"\\u26a0\\ufe0f GitHub push {filename} failed: {e}\")\n"
    "\n"
    "\n"
    "# \u2500\u2500\u2500 Keep-Alive HTTP Server"
)

assert old_marker in content, "is_owner marker not found"
content = content.replace(old_marker, new_marker)

# 4. Modify save_json to also push to GitHub
old_save = (
    "def save_json(filename: str, data) -> None:\n"
    "    \"\"\"Atomic JSON write to data/ directory.\"\"\"\n"
    "    path = DATA_DIR / filename\n"
    "    tmp = path.with_suffix(\".tmp\")\n"
    "    with open(tmp, \"w\", encoding=\"utf-8\") as f:\n"
    "        json.dump(data, f, ensure_ascii=False, indent=2)\n"
    "    tmp.replace(path)"
)
new_save = (
    "def save_json(filename: str, data) -> None:\n"
    "    \"\"\"Atomic JSON write to data/ directory + GitHub sync.\"\"\"\n"
    "    path = DATA_DIR / filename\n"
    "    tmp = path.with_suffix(\".tmp\")\n"
    "    with open(tmp, \"w\", encoding=\"utf-8\") as f:\n"
    "        json.dump(data, f, ensure_ascii=False, indent=2)\n"
    "    tmp.replace(path)\n"
    "    # Async push to GitHub (non-blocking)\n"
    "    if GITHUB_TOKEN and filename in _PERSIST_FILES:\n"
    "        try:\n"
    "            loop = asyncio.get_event_loop()\n"
    "            if loop.is_running():\n"
    "                asyncio.ensure_future(github_push_json(filename, data))\n"
    "        except Exception:\n"
    "            pass"
)
assert old_save in content, "save_json block not found"
content = content.replace(old_save, new_save)

# 5. Add github_pull_all() call in main() before load_modules()
old_main = (
    "    # Load feature modules\n"
    "    load_modules()"
)
new_main = (
    "    # Pull persisted settings from GitHub (replaces Google Drive)\n"
    "    github_pull_all()\n"
    "\n"
    "    # Load feature modules\n"
    "    load_modules()"
)
assert old_main in content, "main load_modules block not found"
content = content.replace(old_main, new_main)

# 6. Add github_push_json to _bot_globals
old_globals = (
    "        \"asyncio\": asyncio,\n"
    "    })"
)
new_globals = (
    "        \"asyncio\": asyncio,\n"
    "        \"github_push_json\": github_push_json,\n"
    "    })"
)
assert old_globals in content, "globals update block not found"
content = content.replace(old_globals, new_globals)

with open("discord_borda_poll.py", "w", encoding="utf-8") as f:
    f.write(content)

# 7. Update render.yaml
with open("render.yaml", "r") as f:
    render_content = f.read()

old_env = '      - key: PYTHONUNBUFFERED\n        value: "1"'
new_env = '      - key: PYTHONUNBUFFERED\n        value: "1"\n      - key: GITHUB_TOKEN\n        sync: false'
assert old_env in render_content, "render.yaml env block not found"
render_content = render_content.replace(old_env, new_env)

with open("render.yaml", "w") as f:
    f.write(render_content)

print("All patches applied successfully")
