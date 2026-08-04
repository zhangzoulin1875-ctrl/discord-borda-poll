import re

with open('discord_borda_poll.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add routes
route_target = 'app.router.add_delete("/api/guilds/{gid}/nations/{nid}", api_delete_nation)'
route_addition = '''app.router.add_delete("/api/guilds/{gid}/nations/{nid}", api_delete_nation)
    app.router.add_post("/api/guilds/{gid}/global-scan/start", api_global_scan_start)
    app.router.add_get("/api/guilds/{gid}/global-scan/status", api_global_scan_status)
    app.router.add_get("/api/guilds/{gid}/global-scan/result", api_global_scan_result)'''

if route_target in content:
    content = content.replace(route_target, route_addition, 1)
    print("✅ 1. Added route registrations")
else:
    print("❌ 1. Could not find route target")

# 2. Add API handler functions right after api_delete_nation
handler_target = '''    print(f"❌ api_delete_nation 例外：{ex}")
    traceback.print_exc()
    return web.json_response({"error": f"伺服器錯誤：{ex}"}, status=500)'''

handler_addition = '''    print(f"❌ api_delete_nation 例外：{ex}")
    traceback.print_exc()
    return web.json_response({"error": f"伺服器錯誤：{ex}"}, status=500)


# ── Global Micropedia Scan API ──

async def api_global_scan_start(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    if _global_scan_state.get("status") == "running":
        return web.json_response({"error": "Scan is already running"}, status=409)
    global _global_scan_task
    _global_scan_task = asyncio.ensure_future(_run_global_micropedia_scan())
    return web.json_response({"status": "started"})


async def api_global_scan_status(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_global_scan_state)


async def api_global_scan_result(request):
    user = await _get_session_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_global_scan_result)'''

if handler_target in content:
    content = content.replace(handler_target, handler_addition, 1)
    print("✅ 2. Added API handlers")
else:
    print("❌ 2. Could not find handler target")

# 3. Startup load
startup_target = '    _load_community_chronicle()\n'
m_start = content.find(startup_target)
if m_start != -1:
    content = content[:m_start] + '    _load_community_chronicle()\n    _load_global_scan_result()\n' + content[m_start + len(startup_target):]
    print("✅ 3. Added startup load call")
else:
    print("❌ 3. Could not find startup load target")

# 4. Global state variables and scan functions
chronicle_target = '''def _load_community_chronicle():
    global _community_chronicle
    try:
        if os.path.exists(COMMUNITY_CHRONICLE_FILE):
            with open(COMMUNITY_CHRONICLE_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
                if isinstance(loaded, dict):
                    _community_chronicle.update(loaded)
                    print(f"📜 社群編年史：已載入（更新於 {loaded.get('last_updated', '?')}）")
    except Exception as e:
        print(f"⚠️ 社群編年史載入失敗：{e}")'''

scan_system_code = '''def _load_community_chronicle():
    global _community_chronicle
    try:
        if os.path.exists(COMMUNITY_CHRONICLE_FILE):
            with open(COMMUNITY_CHRONICLE_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.load(f)
                if isinstance(loaded, dict):
                    _community_chronicle.update(loaded)
                    print(f"📜 社群編年史：已載入（更新於 {loaded.get('last_updated', '?')}）")
    except Exception as e:
        print(f"⚠️ 社群編年史載入失敗：{e}")


# ═════════════════════════════════════════════════════════════════
# 全球微國家百科全站掃描 (Global Micropedia Scan)
# ═════════════════════════════════════════════════════════════════

GLOBAL_SCAN_FILE = os.path.join(DATA_DIR, 'global_scan_result.json')
_global_scan_state = {
    'status': 'idle',
    'progress': 0,
    'total': 0,
    'current_batch': '',
    'started_at': '',
    'completed_at': '',
    'error': '',
}
_global_scan_result = {
    'last_updated': '',
    'total_articles': 0,
    'countries': [],
    'relationships': [],
    'key_figures': [],
    'major_events': [],
}
_global_scan_task = None


def _save_global_scan_result():
    _save_json_file(GLOBAL_SCAN_FILE, _global_scan_result)


def _load_global_scan_result():
    global _global_scan_result
    try:
        if os.path.exists(GLOBAL_SCAN_FILE):
            with open(GLOBAL_SCAN_FILE, 'r', encoding='utf-8') as f:
                loaded = json_module.load(f)
                if isinstance(loaded, dict):
                    _global_scan_result.update(loaded)
                    print(f'全球掃描結果已載入')
    except Exception as e:
        print(f'全球掃描結果載入失敗: {e}')


def _merge_scan_batch(extracted: dict):
    if not isinstance(extracted, dict):
        return

    # 1. countries (dedupe by name)
    existing_countries = {
        c.get("name", "").strip().lower(): c
        for c in _global_scan_result.get("countries", [])
        if isinstance(c, dict) and c.get("name")
    }
    for c in extracted.get("countries", []):
        if not isinstance(c, dict) or not c.get("name"):
            continue
        name_key = c["name"].strip().lower()
        if name_key in existing_countries:
            curr = existing_countries[name_key]
            existing_aliases = set(curr.get("aliases", [])) if isinstance(curr.get("aliases"), list) else set()
            new_aliases = c.get("aliases", []) if isinstance(c.get("aliases"), list) else []
            curr["aliases"] = list(existing_aliases.union(new_aliases))
            if c.get("description") and len(c["description"]) > len(curr.get("description", "")):
                curr["description"] = c["description"]
            if c.get("status") and c["status"] != "unknown":
                curr["status"] = c["status"]
            if c.get("type"):
                curr["type"] = c["type"]
        else:
            existing_countries[name_key] = c
            _global_scan_result.setdefault("countries", []).append(c)

    # 2. relationships (dedupe by from + to + type)
    existing_rels = {
        (r.get("from", "").strip().lower(), r.get("to", "").strip().lower(), r.get("type", "").strip().lower()): r
        for r in _global_scan_result.get("relationships", [])
        if isinstance(r, dict)
    }
    for r in extracted.get("relationships", []):
        if not isinstance(r, dict) or not r.get("from") or not r.get("to"):
            continue
        rel_key = (r.get("from", "").strip().lower(), r.get("to", "").strip().lower(), r.get("type", "").strip().lower())
        if rel_key not in existing_rels:
            existing_rels[rel_key] = r
            _global_scan_result.setdefault("relationships", []).append(r)

    # 3. key_figures (dedupe by name)
    existing_figs = {
        f.get("name", "").strip().lower(): f
        for f in _global_scan_result.get("key_figures", [])
        if isinstance(f, dict) and f.get("name")
    }
    for f in extracted.get("key_figures", []):
        if not isinstance(f, dict) or not f.get("name"):
            continue
        fig_key = f["name"].strip().lower()
        if fig_key not in existing_figs:
            existing_figs[fig_key] = f
            _global_scan_result.setdefault("key_figures", []).append(f)

    # 4. major_events (dedupe by event name)
    existing_events = {
        e.get("event", "").strip().lower(): e
        for e in _global_scan_result.get("major_events", [])
        if isinstance(e, dict) and e.get("event")
    }
    for e in extracted.get("major_events", []):
        if not isinstance(e, dict) or not e.get("event"):
            continue
        ev_key = e["event"].strip().lower()
        if ev_key not in existing_events:
            existing_events[ev_key] = e
            _global_scan_result.setdefault("major_events", []).append(e)


async def _consolidate_global_scan_graph():
    """Use AI to consolidate accumulated global scan result graph (merge duplicate entities, resolve conflicts, keep compact)."""
    current_data = {
        "countries": _global_scan_result.get("countries", []),
        "relationships": _global_scan_result.get("relationships", []),
        "key_figures": _global_scan_result.get("key_figures", []),
        "major_events": _global_scan_result.get("major_events", []),
    }
    if not any(current_data.values()):
        return

    system_prompt = (
        "你是一位資料庫整合專家與微國家歷史學家。請審視並整合以下微國家關係圖譜資料，合併重複條目、融合別名與敘述、整合矛盾狀態，並保持資料精簡與精準。\n"
        "請以繁體中文輸出嚴格 JSON 格式（不可使用 markdown 程式碼區塊 ```json ... ```），包含以下 4 個 key：\n"
        "1. \"countries\": [{\"name\": \"...\", \"aliases\": [\"...\"], \"type\": \"micronation/organization/individual\", \"description\": \"...\", \"status\": \"active/dissolved/unknown\"}]\n"
        "2. \"relationships\": [{\"from\": \"...\", \"to\": \"...\", \"type\": \"alliance/conflict/treaty/trade/diplomatic/cultural/personal\", \"description\": \"...\", \"context\": \"...\", \"status\": \"active/historical/ended\"}]\n"
        "3. \"key_figures\": [{\"name\": \"...\", \"affiliation\": \"...\", \"role\": \"...\", \"description\": \"...\"}]\n"
        "4. \"major_events\": [{\"event\": \"...\", \"participants\": [\"...\"], \"date\": \"...\", \"description\": \"...\", \"consequences\": \"...\"}]\n"
        "僅輸出 JSON 物件，請勿附加任何額外文字。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"當前圖譜資料：\n{json_module.dumps(current_data, ensure_ascii=False)}"}
    ]

    try:
        resp = await call_chat_api(messages, chat_ai_settings, max_tokens=4000)
        ai_text = resp.get("content") or ""
        ai_text_clean = ai_text.strip()
        if ai_text_clean.startswith("```"):
            ai_text_clean = re.sub(r"^```(?:json)?\s*", "", ai_text_clean, flags=re.IGNORECASE)
            ai_text_clean = re.sub(r"\s*```$", "", ai_text_clean)
            ai_text_clean = ai_text_clean.strip()

        consolidated = None
        try:
            consolidated = json_module.loads(ai_text_clean)
        except Exception:
            m = re.search(r"\{.*\}", ai_text, re.DOTALL)
            if m:
                try:
                    consolidated = json_module.loads(m.group(0))
                except Exception:
                    consolidated = None

        if isinstance(consolidated, dict):
            for k in ["countries", "relationships", "key_figures", "major_events"]:
                if k in consolidated and isinstance(consolidated[k], list):
                    _global_scan_result[k] = consolidated[k]
            _save_global_scan_result()
            print("✨ 全球掃描圖譜彙整完成")
    except Exception as e:
        print(f"⚠️ 全球掃描圖譜彙整失敗: {e}")


async def _run_global_micropedia_scan():
    global _global_scan_state, _global_scan_result
    _global_scan_state["status"] = "running"
    _global_scan_state["progress"] = 0
    _global_scan_state["total"] = 0
    _global_scan_state["current_batch"] = "初始化中..."
    _global_scan_state["started_at"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
    _global_scan_state["completed_at"] = ""
    _global_scan_state["error"] = ""

    try:
        async with aiohttp.ClientSession() as session:
            raw_titles = await _fetch_all_micropedia_titles(session)
            titles = [t for t in raw_titles if not any(t.startswith(p) for p in _MICROPEDIA_SKIP_PREFIXES)]
            _global_scan_state["total"] = len(titles)
            _global_scan_result["total_articles"] = len(titles)
            _save_global_scan_result()

            if not titles:
                _global_scan_state["status"] = "completed"
                _global_scan_state["completed_at"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
                _global_scan_result["last_updated"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
                _save_global_scan_result()
                return

            batch_size = 8
            batches = [titles[i:i + batch_size] for i in range(0, len(titles), batch_size)]

            for b_idx, batch in enumerate(batches):
                titles_preview = ", ".join(batch[:3])
                _global_scan_state["current_batch"] = f"批次 {b_idx + 1}/{len(batches)}: {titles_preview}..."

                import urllib.parse as _up
                titles_param = "|".join(_up.quote(t) for t in batch)
                api_url = (
                    f"https://www.micropedia.site/api.php?action=query"
                    f"&titles={titles_param}"
                    f"&prop=revisions&rvprop=content&format=json&redirects=1"
                )

                content_parts = []
                try:
                    timeout = aiohttp.ClientTimeout(total=15, connect=5)
                    async with session.get(api_url, headers={"User-Agent": "DiscordBot (micropedia-integration/1.0)"}, timeout=timeout) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pages = data.get("query", {}).get("pages", {})
                            for pid, page in pages.items():
                                if pid == "-1" or "missing" in page:
                                    continue
                                revs = page.get("revisions", [])
                                if not revs:
                                    continue
                                wikitext = revs[0].get("*", "")
                                if not wikitext or len(wikitext) < 10:
                                    continue
                                clean = _clean_wikitext(wikitext)
                                if clean and len(clean) > 10:
                                    p_title = page.get("title", "?")
                                    if len(clean) > 2000:
                                        clean = clean[:2000] + "..."
                                    content_parts.append(f"【{p_title}】\n{clean}")
                except Exception as fe:
                    print(f"⚠️ 全球掃描取得內文失敗 (批次 {b_idx + 1}): {fe}")

                if content_parts:
                    batch_text = "\n\n".join(content_parts)
                    system_prompt = (
                        "你是一位歷史學家與微國家學學者。請分析以下維基條目內容，提取國家/組織/個人、關係、關鍵人物、重大事件。\n"
                        "請以繁體中文輸出嚴格 JSON 格式（不可使用 markdown 程式碼區塊 ```json ... ```），包含以下 4 個 key：\n"
                        "1. \"countries\": [{\"name\": \"...\", \"aliases\": [\"...\"], \"type\": \"micronation/organization/individual\", \"description\": \"...\", \"status\": \"active/dissolved/unknown\"}]\n"
                        "2. \"relationships\": [{\"from\": \"...\", \"to\": \"...\", \"type\": \"alliance/conflict/treaty/trade/diplomatic/cultural/personal\", \"description\": \"...\", \"context\": \"...\", \"status\": \"active/historical/ended\"}]\n"
                        "3. \"key_figures\": [{\"name\": \"...\", \"affiliation\": \"...\", \"role\": \"...\", \"description\": \"...\"}]\n"
                        "4. \"major_events\": [{\"event\": \"...\", \"participants\": [\"...\"], \"date\": \"...\", \"description\": \"...\", \"consequences\": \"...\"}]\n"
                        "僅輸出 JSON 物件，請勿附加任何額外文字。"
                    )

                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"條目內容：\n{batch_text}"}
                    ]

                    try:
                        resp = await call_chat_api(messages, chat_ai_settings, max_tokens=4000)
                        ai_text = resp.get("content") or ""
                        ai_text_clean = ai_text.strip()
                        if ai_text_clean.startswith("```"):
                            ai_text_clean = re.sub(r"^```(?:json)?\s*", "", ai_text_clean, flags=re.IGNORECASE)
                            ai_text_clean = re.sub(r"\s*```$", "", ai_text_clean)
                            ai_text_clean = ai_text_clean.strip()

                        extracted = None
                        try:
                            extracted = json_module.loads(ai_text_clean)
                        except Exception:
                            m = re.search(r"\{.*\}", ai_text, re.DOTALL)
                            if m:
                                try:
                                    extracted = json_module.loads(m.group(0))
                                except Exception:
                                    extracted = None

                        if isinstance(extracted, dict):
                            _merge_scan_batch(extracted)
                    except Exception as aie:
                        print(f"⚠️ 全球掃描 AI 解析失敗 (批次 {b_idx + 1}): {aie}")

                _global_scan_state["progress"] += len(batch)
                _global_scan_result["last_updated"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")

                # Consolidation every 10 batches
                if (b_idx + 1) % 10 == 0:
                    await _consolidate_global_scan_graph()

                _save_global_scan_result()
                await asyncio.sleep(0.5)

            # Final consolidation pass
            await _consolidate_global_scan_graph()

            _global_scan_state["status"] = "completed"
            _global_scan_state["completed_at"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
            _global_scan_result["last_updated"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
            _save_global_scan_result()

    except Exception as e:
        import traceback
        err_msg = f"{e}\n{traceback.format_exc()}"
        print(f"❌ 全球掃描失敗: {err_msg}")
        _global_scan_state["status"] = "error"
        _global_scan_state["error"] = str(e)'''

if chronicle_target in content:
    content = content.replace(chronicle_target, scan_system_code, 1)
    print("✅ 4. Added global scan state variables & scan functions")
else:
    print("❌ 4. Could not find chronicle target")

with open('discord_borda_poll.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Modified discord_borda_poll.py")
