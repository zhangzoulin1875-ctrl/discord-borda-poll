# AI 寫小說工具（Novel Writer）
# 提供網頁介面讓使用者管理角色庫、關係鏈、劇本，並用 AI 生成小說。
# 支援 OpenAI 相容格式與 Gemini 格式的 AI API。
# 數據持久化到 JSON + GitHub，重啟後不會消失。
#
# ─── Shared globals injected by the main file ──────────────────────────────
# bot, tree, save_json, load_json, is_owner, now_str, OWNER_ID, TZ_TAIPEI,
# DATA_DIR, discord, app_commands, asyncio, github_push_json, _bot_ready_hooks

import json
import uuid as _uuid
import time as _time
import aiohttp
from aiohttp import web
from pathlib import Path

# ═════════════════════════════════════════════════════════════════
# 資料載入 & 持久化
# ═════════════════════════════════════════════════════════════════

novel_settings = {}
novel_characters = []
novel_relationships = []
novel_projects = []


def load_novel_data():
    global novel_settings, novel_characters, novel_relationships, novel_projects
    novel_settings = load_json("novel_settings.json", {
        "api_type": "openai",
        "api_url": "https://api.openai.com",
        "api_key": "",
        "model": "gpt-4o",
        "temperature": 0.8,
        "max_tokens": 4096,
    })
    novel_characters = load_json("novel_characters.json", [])
    novel_relationships = load_json("novel_relationships.json", [])
    novel_projects = load_json("novel_projects.json", [])
    print(f"📖 小說工具已載入：{len(novel_characters)} 角色, {len(novel_relationships)} 關係, {len(novel_projects)} 專案")


async def _persist(filename, data):
    path = DATA_DIR / filename
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    try:
        await github_push_json(filename, data)
    except Exception as e:
        print(f"⚠️ 小說工具 GitHub 同步失敗：{e}")


def _gen_id():
    return str(_uuid.uuid4())


def _now():
    return _time.strftime("%Y-%m-%d %H:%M:%S")


# ═════════════════════════════════════════════════════════════════
# AI API 呼叫（OpenAI 相容 / Gemini）
# ═════════════════════════════════════════════════════════════════

async def _ai_generate(messages, system_prompt="", max_tokens=None, temperature=None):
    """呼叫 AI API 生成文字。支援 OpenAI 相容格式與 Gemini 格式。
    messages: [{"role": "user"/"assistant", "content": "..."}]
    回傳生成的文字字串。
    """
    s = novel_settings
    api_type = s.get("api_type", "openai")
    api_url = s.get("api_url", "").rstrip("/")
    api_key = s.get("api_key", "")
    model = s.get("model", "gpt-4o")
    temp = temperature if temperature is not None else s.get("temperature", 0.8)
    max_tok = max_tokens or s.get("max_tokens", 4096)

    if not api_url or not api_key:
        raise ValueError("API URL 或 API Key 未設定")

    timeout = aiohttp.ClientTimeout(total=120)

    if api_type == "gemini":
        url = f"{api_url}/models/{model}:generateContent"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-goog-api-key"] = api_key

        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temp,
                "maxOutputTokens": max_tok,
            },
        }
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    raise ValueError(f"Gemini API 錯誤 ({resp.status}): {err_text[:500]}")
                data = await resp.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    raise ValueError(f"Gemini 回應格式異常: {json.dumps(data)[:500]}")

    else:
        url = f"{api_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        body = {
            "model": model,
            "messages": full_messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    raise ValueError(f"OpenAI API 錯誤 ({resp.status}): {err_text[:500]}")
                data = await resp.json()
                try:
                    return data["choices"][0]["message"]["content"]
                except (KeyError, IndexError):
                    raise ValueError(f"OpenAI 回應格式異常: {json.dumps(data)[:500]}")


async def _ai_summarize(text, max_len=300):
    """讓 AI 濃縮一段文字成摘要。"""
    try:
        summary = await _ai_generate(
            [{"role": "user", "content": f"請將以下小說章節濃縮成 {max_len} 字以內的摘要，保留關鍵劇情、角色行動和重要轉折：\n\n{text}"}],
            system_prompt="你是一個專業的小說摘要助手。",
            max_tokens=1024,
            temperature=0.3,
        )
        return summary.strip()
    except Exception as e:
        print(f"⚠️ 摘要生成失敗：{e}")
        return text[:max_len]


# ═════════════════════════════════════════════════════════════════
# Prompt 建構
# ═════════════════════════════════════════════════════════════════

def _build_character_context(char_ids):
    """從角色 ID 列表建構角色資料文字。"""
    char_map = {c["id"]: c for c in novel_characters if c["id"] in char_ids}
    lines = []
    for cid in char_ids:
        c = char_map.get(cid)
        if not c:
            continue
        role_label = {"protagonist": "主角", "supporting": "配角", "antagonist": "反派", "minor": "次要角色"}.get(c.get("role", "supporting"), "配角")
        lines.append(f"  • {c['name']}（{role_label}）：{c.get('personality', '')}。{c.get('background', '')}")
    return "\n".join(lines) if lines else "  （未指定角色）"


def _build_relationship_context(char_ids):
    """建構角色之間的關係鏈文字。"""
    lines = []
    for r in novel_relationships:
        if r["character_a"] in char_ids and r["character_b"] in char_ids:
            a_name = next((c["name"] for c in novel_characters if c["id"] == r["character_a"]), r["character_a"])
            b_name = next((c["name"] for c in novel_characters if c["id"] == r["character_b"]), r["character_b"])
            lines.append(f"  • {a_name} → {b_name}（{r['relationship_type']}）：{r.get('description', '')}")
    return "\n".join(lines) if lines else "  （無指定關係）"


def _build_outline_prompt(project):
    """建構生成大綱的 system prompt + user message。"""
    char_ids = project.get("character_ids", [])
    system_prompt = (
        "你是一個專業小說大綱編劇。根據使用者提供的題材、角色和概念，"
        "設計一個完整的分章大綱。每章包含：標題、劇情摘要、主要出場角色、情緒基調。"
        "確保劇情有起承轉合，角色有發展空間，整體節奏合理。"
        "用 JSON 格式輸出，結構為：{\"outline\": [{\"title\": \"章節標題\", \"summary\": \"劇情摘要\", \"characters\": [\"角色名\"], \"mood\": \"情緒基調\"}]}"
    )
    char_ctx = _build_character_context(char_ids)
    rel_ctx = _build_relationship_context(char_ids)
    total_chapters = project.get("generation_config", {}).get("total_chapters", 10)
    words_per_chapter = project.get("generation_config", {}).get("words_per_chapter", 3000)

    user_msg = (
        f"題材類型：{project.get('genre', '不限')}\n"
        f"整體概念：{project.get('concept', '')}\n"
        f"章節數：{total_chapters} 章\n"
        f"每章字數：約 {words_per_chapter} 字\n\n"
        f"角色資料：\n{char_ctx}\n\n"
        f"角色關係：\n{rel_ctx}\n\n"
        f"請根據以上資訊，生成 {total_chapters} 章的分章大綱。"
    )
    return system_prompt, [{"role": "user", "content": user_msg}]


def _build_chapter_prompt(project, chapter_num, chapter_outline, prev_summaries):
    """建構生成單一章節的 prompt。"""
    char_ids = project.get("character_ids", [])
    words = project.get("generation_config", {}).get("words_per_chapter", 3000)

    system_prompt = (
        "你是一個專業小說家。根據大綱和角色設定撰寫小說章節。"
        "文字要生動、有畫面感，角色對話自然。"
        f"本章目標字數：約 {words} 字。"
    )

    char_ctx = _build_character_context(char_ids)
    rel_ctx = _build_relationship_context(char_ids)

    prev_text = ""
    if prev_summaries:
        prev_text = "\n\n【前情提要】\n"
        for i, s in enumerate(prev_summaries, 1):
            prev_text += f"第 {i} 章摘要：{s}\n"

    outline_chars = chapter_outline.get("characters", [])
    char_names = [next((c["name"] for c in novel_characters if c["name"] == cn), cn) for cn in outline_chars]

    user_msg = (
        f"小說標題：{project.get('title', '')}\n"
        f"題材：{project.get('genre', '')}\n"
        f"整體概念：{project.get('concept', '')}\n\n"
        f"角色資料：\n{char_ctx}\n\n"
        f"角色關係：\n{rel_ctx}\n\n"
        f"本章大綱：\n  標題：{chapter_outline.get('title', '')}\n"
        f"  劇情摘要：{chapter_outline.get('summary', '')}\n"
        f"  出場角色：{', '.join(char_names) if char_names else '全部'}\n"
        f"  情緒基調：{chapter_outline.get('mood', '')}\n"
        f"{prev_text}\n"
        f"請撰寫第 {chapter_num} 章的完整內容。直接輸出小說正文，不要加任何說明或後記。"
    )
    return system_prompt, [{"role": "user", "content": user_msg}]


# ═════════════════════════════════════════════════════════════════
# 認證檢查
# ═════════════════════════════════════════════════════════════════

def _check_auth(request):
    """檢查請求者的 owner 身分。"""
    token = request.headers.get("X-Auth", "")
    return token == str(OWNER_ID)


# ═════════════════════════════════════════════════════════════════
# 路由處理函式
# ═════════════════════════════════════════════════════════════════

async def _handle_page(request):
    """提供 novel.html 頁面。"""
    html_path = DATA_DIR.parent / "novel.html"
    if not html_path.exists():
        return web.Response(text="novel.html 不存在", status=404)
    return web.FileResponse(html_path)


async def _handle_settings_get(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    s = dict(novel_settings)
    if s.get("api_key"):
        s["api_key_masked"] = s["api_key"][:4] + "****" + s["api_key"][-4:] if len(s["api_key"]) > 8 else "****"
    s["api_key"] = ""
    return web.json_response(s)


async def _handle_settings_put(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        data = await request.json()
        for key in ["api_type", "api_url", "model", "temperature", "max_tokens"]:
            if key in data:
                novel_settings[key] = data[key]
        if data.get("api_key"):
            novel_settings["api_key"] = data["api_key"]
        await _persist("novel_settings.json", novel_settings)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_characters_get(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    return web.json_response(novel_characters)


async def _handle_characters_post(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        data = await request.json()
        char = {
            "id": _gen_id(),
            "name": data.get("name", ""),
            "role": data.get("role", "supporting"),
            "personality": data.get("personality", ""),
            "background": data.get("background", ""),
            "appearance": data.get("appearance", ""),
            "notes": data.get("notes", ""),
        }
        novel_characters.append(char)
        await _persist("novel_characters.json", novel_characters)
        return web.json_response(char)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_characters_put(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        char_id = request.match_info["id"]
        data = await request.json()
        for c in novel_characters:
            if c["id"] == char_id:
                for key in ["name", "role", "personality", "background", "appearance", "notes"]:
                    if key in data:
                        c[key] = data[key]
                await _persist("novel_characters.json", novel_characters)
                return web.json_response(c)
        return web.json_response({"error": "找不到角色"}, status=404)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_characters_delete(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        char_id = request.match_info["id"]
        novel_characters[:] = [c for c in novel_characters if c["id"] != char_id]
        novel_relationships[:] = [r for r in novel_relationships if r["character_a"] != char_id and r["character_b"] != char_id]
        await _persist("novel_characters.json", novel_characters)
        await _persist("novel_relationships.json", novel_relationships)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_relationships_get(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    char_map = {c["id"]: c["name"] for c in novel_characters}
    result = []
    for r in novel_relationships:
        rr = dict(r)
        rr["character_a_name"] = char_map.get(r["character_a"], "?")
        rr["character_b_name"] = char_map.get(r["character_b"], "?")
        result.append(rr)
    return web.json_response(result)


async def _handle_relationships_post(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        data = await request.json()
        rel = {
            "id": _gen_id(),
            "character_a": data.get("character_a", ""),
            "character_b": data.get("character_b", ""),
            "relationship_type": data.get("relationship_type", ""),
            "description": data.get("description", ""),
            "direction": data.get("direction", "mutual"),
        }
        novel_relationships.append(rel)
        await _persist("novel_relationships.json", novel_relationships)
        return web.json_response(rel)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_relationships_delete(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        rel_id = request.match_info["id"]
        novel_relationships[:] = [r for r in novel_relationships if r["id"] != rel_id]
        await _persist("novel_relationships.json", novel_relationships)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_projects_get(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    result = []
    for p in novel_projects:
        pp = {
            "id": p["id"],
            "title": p.get("title", ""),
            "genre": p.get("genre", ""),
            "concept": p.get("concept", ""),
            "outline_confirmed": p.get("outline_confirmed", False),
            "outline_count": len(p.get("outline", [])),
            "chapters_count": len(p.get("chapters", [])),
            "total_chapters": p.get("generation_config", {}).get("total_chapters", 0),
            "created_at": p.get("created_at", ""),
            "updated_at": p.get("updated_at", ""),
        }
        result.append(pp)
    return web.json_response(result)


async def _handle_project_get(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    pid = request.match_info["id"]
    for p in novel_projects:
        if p["id"] == pid:
            char_map = {c["id"]: c for c in novel_characters}
            p_copy = json.loads(json.dumps(p))
            p_copy["characters"] = [
                {"id": cid, "name": char_map.get(cid, {}).get("name", "?"), "role": char_map.get(cid, {}).get("role", "")}
                for cid in p.get("character_ids", [])
            ]
            return web.json_response(p_copy)
    return web.json_response({"error": "找不到專案"}, status=404)


async def _handle_projects_post(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        data = await request.json()
        project = {
            "id": _gen_id(),
            "title": data.get("title", ""),
            "genre": data.get("genre", ""),
            "concept": data.get("concept", ""),
            "character_ids": data.get("character_ids", []),
            "outline": [],
            "outline_confirmed": False,
            "generation_config": {
                "total_chapters": data.get("total_chapters", 10),
                "words_per_chapter": data.get("words_per_chapter", 3000),
            },
            "chapters": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        novel_projects.append(project)
        await _persist("novel_projects.json", novel_projects)
        return web.json_response(project)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_project_put(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        pid = request.match_info["id"]
        data = await request.json()
        for p in novel_projects:
            if p["id"] == pid:
                for key in ["title", "genre", "concept", "character_ids", "outline", "outline_confirmed", "generation_config"]:
                    if key in data:
                        p[key] = data[key]
                p["updated_at"] = _now()
                await _persist("novel_projects.json", novel_projects)
                return web.json_response(p)
        return web.json_response({"error": "找不到專案"}, status=404)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_project_delete(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        pid = request.match_info["id"]
        novel_projects[:] = [p for p in novel_projects if p["id"] != pid]
        await _persist("novel_projects.json", novel_projects)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_generate_outline(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        pid = request.match_info["id"]
        project = next((p for p in novel_projects if p["id"] == pid), None)
        if not project:
            return web.json_response({"error": "找不到專案"}, status=404)

        system_prompt, messages = _build_outline_prompt(project)
        raw = await _ai_generate(messages, system_prompt=system_prompt, max_tokens=8192, temperature=0.7)

        import re
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            outline_data = json.loads(json_match.group())
            outline = outline_data.get("outline", [])
        else:
            outline = []
            lines = raw.strip().split("\n")
            current = {}
            for line in lines:
                if line.strip().startswith("第") and "章" in line:
                    if current:
                        outline.append(current)
                    current = {"title": line.strip(), "summary": "", "characters": [], "mood": ""}
                elif current and not current["summary"]:
                    current["summary"] = line.strip()
            if current:
                outline.append(current)

        project["outline"] = outline
        project["updated_at"] = _now()
        await _persist("novel_projects.json", novel_projects)
        return web.json_response({"outline": outline})
    except json.JSONDecodeError as e:
        return web.json_response({"error": f"AI 回應無法解析為 JSON：{e}", "raw": raw[:1000]}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_generate_chapter(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    try:
        pid = request.match_info["id"]
        data = await request.json()
        chapter_num = data.get("chapter", 1)

        project = next((p for p in novel_projects if p["id"] == pid), None)
        if not project:
            return web.json_response({"error": "找不到專案"}, status=404)

        outline = project.get("outline", [])
        if chapter_num < 1 or chapter_num > len(outline):
            return web.json_response({"error": f"章節數超出範圍（1-{len(outline)}）"}, status=400)

        chapter_outline = outline[chapter_num - 1]

        chapters = project.get("chapters", [])
        prev_summaries = []
        for ch in sorted(chapters, key=lambda x: x["chapter"]):
            if ch["chapter"] < chapter_num:
                prev_summaries.append(ch.get("summary", ch.get("content", "")[:300]))

        system_prompt, messages = _build_chapter_prompt(project, chapter_num, chapter_outline, prev_summaries)
        content = await _ai_generate(messages, system_prompt=system_prompt, max_tokens=8192, temperature=0.85)

        summary = await _ai_summarize(content)

        chapter_data = {
            "chapter": chapter_num,
            "title": chapter_outline.get("title", f"第 {chapter_num} 章"),
            "content": content,
            "summary": summary,
            "word_count": len(content),
            "generated_at": _now(),
        }

        existing = next((ch for ch in chapters if ch["chapter"] == chapter_num), None)
        if existing:
            existing.update(chapter_data)
        else:
            chapters.append(chapter_data)
            chapters.sort(key=lambda x: x["chapter"])

        project["chapters"] = chapters
        project["updated_at"] = _now()
        await _persist("novel_projects.json", novel_projects)

        return web.json_response(chapter_data)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _handle_export(request):
    if not _check_auth(request):
        return web.json_response({"error": "未授權"}, status=401)
    pid = request.match_info["id"]
    project = next((p for p in novel_projects if p["id"] == pid), None)
    if not project:
        return web.json_response({"error": "找不到專案"}, status=404)

    chapters = sorted(project.get("chapters", []), key=lambda x: x["chapter"])
    lines = [project.get("title", ""), ""]
    for ch in chapters:
        lines.append(ch.get("title", f"第 {ch['chapter']} 章"))
        lines.append("")
        lines.append(ch.get("content", ""))
        lines.append("")
        lines.append("---")
        lines.append("")
    text = "\n".join(lines)
    return web.json_response({"text": text, "title": project.get("title", "")})


# ═════════════════════════════════════════════════════════════════
# 路由註冊
# ═════════════════════════════════════════════════════════════════

def setup_novel_routes(app):
    """向 aiohttp app 註冊小說工具的所有路由。"""
    app.router.add_get("/novel", _handle_page)
    app.router.add_get("/api/novel/settings", _handle_settings_get)
    app.router.add_put("/api/novel/settings", _handle_settings_put)
    app.router.add_get("/api/novel/characters", _handle_characters_get)
    app.router.add_post("/api/novel/characters", _handle_characters_post)
    app.router.add_put("/api/novel/characters/{id}", _handle_characters_put)
    app.router.add_delete("/api/novel/characters/{id}", _handle_characters_delete)
    app.router.add_get("/api/novel/relationships", _handle_relationships_get)
    app.router.add_post("/api/novel/relationships", _handle_relationships_post)
    app.router.add_delete("/api/novel/relationships/{id}", _handle_relationships_delete)
    app.router.add_get("/api/novel/projects", _handle_projects_get)
    app.router.add_post("/api/novel/projects", _handle_projects_post)
    app.router.add_get("/api/novel/projects/{id}", _handle_project_get)
    app.router.add_put("/api/novel/projects/{id}", _handle_project_put)
    app.router.add_delete("/api/novel/projects/{id}", _handle_project_delete)
    app.router.add_post("/api/novel/projects/{id}/generate-outline", _handle_generate_outline)
    app.router.add_post("/api/novel/projects/{id}/generate-chapter", _handle_generate_chapter)
    app.router.add_get("/api/novel/projects/{id}/export", _handle_export)
    print("📖 小說工具路由已註冊：/novel + /api/novel/*")


# ═════════════════════════════════════════════════════════════════
# 啟動載入
# ═════════════════════════════════════════════════════════════════

load_novel_data()
