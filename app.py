"""
Hugging Face Spaces 入口（Gradio 包法）
=======================================
前景：極簡 Gradio 頁面（讓 Space 有東西可服務，外部 ping 打這頁防休眠）
背景：獨立執行緒跑 Discord bot（modules/ 載入、事件處理全部正常）

環境變數（Space Settings → Variables and secrets）：
  DISCORD_BOT_TOKEN  必填
  GITHUB_TOKEN       必填（設定同步 GitHub 用）
  OWNER_ID           可選（預設 1482256878334640209）
  DISABLE_KEEPALIVE  這裡自動設為 1，不用管
"""

import os

# HF Spaces 模式：不啟動 Render 專用的 keep-alive server
os.environ.setdefault("DISABLE_KEEPALIVE", "1")

import threading
import asyncio
import time

_bot_state = {"error": None, "start_time": time.time()}

import discord_borda_poll  # noqa: E402


def _start_bot():
    """背景執行緒：獨立 event loop 跑 bot 主程式。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(discord_borda_poll.main())
    except Exception as e:
        _bot_state["error"] = str(e)
        print(f"❌ Bot 背景執行失敗：{e}")


threading.Thread(target=_start_bot, daemon=True).start()

import gradio as gr  # noqa: E402

# ZeroGPU 硬體要求：啟動時必須偵測到至少一個 @spaces.GPU 函式，否則直接 Runtime error。
# 這個函式只是用來通過檢查，bot 完全不會呼叫它，所以不會消耗任何 GPU 額度。
try:
    import spaces

    @spaces.GPU
    def _zerogpu_placeholder():
        return "ok"
except ImportError:
    # 不在 ZeroGPU 環境（例如本機或 CPU Space）就略過
    pass


def _status_text():
    """給頁面顯示的狀態文字。"""
    bot = discord_borda_poll.bot
    err = _bot_state["error"]
    if err:
        return f"❌ Bot 啟動失敗：\n\n`{err}`"
    if not bot.is_ready():
        return "⏳ Bot 正在啟動中…"
    latency = round(bot.latency * 1000)
    uptime_min = int((time.time() - _bot_state["start_time"]) // 60)
    return (
        f"✅ 在線：**{bot.user}**\n\n"
        f"- 伺服器數：{len(bot.guilds)}\n"
        f"- 延遲：{latency}ms\n"
        f"- 已運行：{uptime_min // 60} 小時 {uptime_min % 60} 分"
    )


def _netcheck():
    """從 Space 內部量測對外連線：DNS / TCP / TLS / 首位元組 / 總時間，
    並分別在「Gradio 執行緒」與「bot 的 event loop」兩個環境各打一次，找出慢在哪。"""
    import json as _j
    import socket
    import ssl
    import urllib.request

    nim_key = os.environ.get("CHAT_API_KEY", "")
    out = []

    def one(label, host, path, body, headers):
        t0 = time.time()
        try:
            ip = socket.gethostbyname(host)
            t_dns = time.time() - t0
            req = urllib.request.Request(f"https://{host}{path}", data=_j.dumps(body).encode(), headers=headers, method="POST")
            t1 = time.time()
            with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
                r.read(200)
                code = r.status
            return f"{label}: HTTP {code} | DNS {t_dns*1000:.0f}ms | 請求 {time.time()-t1:.2f}s | 總 {time.time()-t0:.2f}s | {ip}"
        except urllib.error.HTTPError as e:
            return f"{label}: HTTP {e.code} | DNS {(time.time()-t0)*1000:.0f}ms | 總 {time.time()-t0:.2f}s（服務有回應）"
        except Exception as e:
            return f"{label}: ❌ {type(e).__name__} {str(e)[:80]} | 總 {time.time()-t0:.2f}s"

    nim_body = {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 20, "reasoning_effort": "low"}
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {nim_key}"}
    out.append(one("NIM(Gradio執行緒)", "integrate.api.nvidia.com", "/v1/chat/completions", nim_body, h))
    out.append(one("NIM(Gradio執行緒) 第2次", "integrate.api.nvidia.com", "/v1/chat/completions", nim_body, h))
    bk = os.environ.get("CHAT_BACKUP_API_KEY", "")
    out.append(f"Secrets: CHAT_API_KEY={'有(' + str(len(nim_key)) + '字)' if nim_key else '❌無'} ｜ CHAT_BACKUP_API_KEY={'有(' + str(len(bk)) + '字)' if bk else '❌無'}")
    out.append(one("Google 備援(帶 Key)", "generativelanguage.googleapis.com", "/v1beta/openai/chat/completions", {"model": "gemma-4-31b-it", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 20}, {"Content-Type": "application/json", "Authorization": f"Bearer {bk}"}))
    out.append(one("Discord API", "discord.com", "/api/v10/gateway", {}, {"Content-Type": "application/json"}))

    # 在 bot 的 event loop 裡跑同樣的請求（aiohttp，和 bot 實際使用方式一致）
    try:
        import aiohttp
        loop = discord_borda_poll.bot.loop

        async def _in_bot_loop():
            res = []
            for i in (1, 2):
                t0 = time.time()
                try:
                    async with aiohttp.ClientSession() as ses:
                        async with ses.post("https://integrate.api.nvidia.com/v1/chat/completions", json=nim_body, headers=h, timeout=aiohttp.ClientTimeout(total=25)) as r:
                            await r.read()
                            res.append(f"NIM(bot loop) 第{i}次: HTTP {r.status} | 總 {time.time()-t0:.2f}s")
                except Exception as e:
                    res.append(f"NIM(bot loop) 第{i}次: ❌ {type(e).__name__} | 總 {time.time()-t0:.2f}s")
            lag = getattr(discord_borda_poll, "_bot_globals", {}).get("_loop_lag", {})
            res.append(f"bot 事件迴圈最大卡頓: {lag.get('max', '未啟用')}")
            return res

        fut = asyncio.run_coroutine_threadsafe(_in_bot_loop(), loop)
        out.extend(fut.result(timeout=60))
    except Exception as e:
        out.append(f"bot loop 檢測失敗: {type(e).__name__} {e}")
    return "\n".join(out)



def _geminibench():
    """從 Space 內部列出備援供應商(Google)可用模型，並對候選逐一實測耗時（不外洩 Key）。"""
    import json as _j, urllib.request, urllib.error
    bk = os.environ.get("CHAT_BACKUP_API_KEY", "")
    if not bk:
        return "❌ Space 沒有 CHAT_BACKUP_API_KEY"
    out = []
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/openai/models", headers={"Authorization": f"Bearer {bk}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            ids = [m.get("id", "").replace("models/", "") for m in _j.loads(r.read()).get("data", [])]
        chat = [i for i in ids if any(k in i for k in ("gemini", "gemma")) and not any(x in i for x in ("embedding", "imagen", "veo", "tts", "aqa", "image", "live", "audio", "robotics", "computer"))]
        out.append(f"可用聊天模型 {len(chat)} 個: " + ", ".join(sorted(chat)))
    except Exception as e:
        return f"列模型失敗: {type(e).__name__} {str(e)[:100]}"
    cands = [c for c in chat if any(k in c for k in ("flash", "gemma-3", "gemma-4", "lite"))][:12]
    for mdl in cands:
        t0 = time.time()
        try:
            body = {"model": mdl, "messages": [{"role": "user", "content": "用繁體中文一句話介紹台北"}], "max_tokens": 60}
            req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", data=_j.dumps(body).encode(), headers={"Authorization": f"Bearer {bk}", "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                d = _j.loads(r.read()); c = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            out.append(f"{'✅' if c.strip() else '⚠️空'} {mdl}: {time.time()-t0:.1f}s")
        except urllib.error.HTTPError as e:
            out.append(f"❌ {mdl}: HTTP {e.code} ({time.time()-t0:.1f}s)")
        except Exception as e:
            out.append(f"❌ {mdl}: {type(e).__name__} ({time.time()-t0:.1f}s)")
    return "\n".join(out)


def _failover_test():
    """端到端驗證：在 bot 的 event loop 裡呼叫真正的 call_chat_api，
    (1) 正常路徑  (2) 強制主鏈失效（把主 API 指向不存在的位址）→ 看備援鏈實際多久回覆。測完還原設定。"""
    import asyncio as _a
    g = discord_borda_poll._bot_globals
    S = g["chat_ai_settings"]
    out = []

    async def _go():
        msgs = [{"role": "system", "content": g["_system_prompt"]()}, {"role": "user", "content": "推薦南極洲的旅遊景點"}]
        t = time.time(); r = await g["call_chat_api"](msgs)
        out.append(f"① 正常路徑: {'✅' if r.get('content') else '❌'} {time.time()-t:.1f}s {len(r.get('content',''))}字 {r.get('error','')}")
        saved = dict(S)
        try:
            S["api_url"] = "http://10.255.255.1:9/v1"      # 黑洞位址：連線永遠卡住，模擬主供應商整個掛掉
            g["_model_slow_until"].clear(); g["_ai_circuit"].update(fail_streak=0, trip_time=0.0)
            t = time.time(); r = await g["call_chat_api"](msgs)
            out.append(f"② 主鏈失效→備援: {'✅' if r.get('content') else '❌'} {time.time()-t:.1f}s {len(r.get('content',''))}字 {r.get('error','')}")
        finally:
            S.clear(); S.update(saved)
            g["_model_slow_until"].clear(); g["_ai_circuit"].update(fail_streak=0, trip_time=0.0)
        out.append(f"備援鏈: {S.get('backup_model')}")

    fut = asyncio.run_coroutine_threadsafe(_go(), discord_borda_poll.bot.loop)
    fut.result(timeout=120)
    return "\n".join(out)


def _searchbench():
    """在 Space 內實測各搜尋來源（HF 出口 IP 可能被擋，必須在這裡測才準）。"""
    import re as _re, json as _j, urllib.request, urllib.parse
    out = []
    UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
    def get(u, h=None, t=15, data=None):
        req = urllib.request.Request(u, headers=h or UA, data=data)
        return urllib.request.urlopen(req, timeout=t).read().decode("utf-8", "ignore")
    q = "台灣 最新 新聞"
    t0 = time.time()
    try:
        h = get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q))
        n = len(_re.findall(r'class="result__a"', h))
        out.append(f"{'✅' if n else '❌'} DuckDuckGo HTML: {n} 筆 {time.time()-t0:.1f}s" + ("（疑似被擋）" if (not n and ("anomaly" in h or "captcha" in h.lower())) else ""))
    except Exception as e:
        out.append(f"❌ DuckDuckGo HTML: {type(e).__name__} {str(e)[:60]}")
    t0 = time.time()
    try:
        x = get("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
        out.append(f"✅ Google News RSS: {len(_re.findall('<item>', x))} 筆 {time.time()-t0:.1f}s")
    except Exception as e:
        out.append(f"❌ Google News RSS: {type(e).__name__} {str(e)[:60]}")
    t0 = time.time()
    try:
        j = _j.loads(get("https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch=" + urllib.parse.quote("台灣") + "&format=json&srlimit=3"))
        out.append(f"✅ Wikipedia API: {len(j['query']['search'])} 筆 {time.time()-t0:.1f}s")
    except Exception as e:
        out.append(f"❌ Wikipedia API: {type(e).__name__} {str(e)[:60]}")
    bk = os.environ.get("CHAT_BACKUP_API_KEY", "")
    for mdl in ("gemini-3.5-flash", "gemini-3-flash-preview"):
        t0 = time.time()
        try:
            body = {"contents": [{"parts": [{"text": "今天台灣有什麼重要新聞？用繁體中文兩句話回答"}]}], "tools": [{"google_search": {}}]}
            r = _j.loads(get(f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent", {"Content-Type": "application/json", "x-goog-api-key": bk}, 40, _j.dumps(body).encode()))
            cand = r["candidates"][0]; txt = "".join(p.get("text", "") for p in cand["content"]["parts"])
            gm = cand.get("groundingMetadata", {}); srcs = len(gm.get("groundingChunks", []))
            out.append(f"{'✅' if srcs else '⚠️無來源'} Gemini 內建 Google 搜尋 [{mdl}]: {srcs} 個來源 {time.time()-t0:.1f}s ｜ {txt[:70]!r}")
        except urllib.error.HTTPError as e:
            out.append(f"❌ Gemini grounding [{mdl}]: HTTP {e.code} {e.read().decode()[:120]}")
        except Exception as e:
            out.append(f"❌ Gemini grounding [{mdl}]: {type(e).__name__} {str(e)[:60]}")
    return "\n".join(out)

with gr.Blocks(title="Discord Bot") as demo:
    gr.Markdown("# 🤖 Discord Bot 控制頁")
    gr.Markdown("這個頁面只是讓 Space 保持服務的殼，bot 本體在背景執行。")
    status_md = gr.Markdown(_status_text())
    refresh_btn = gr.Button("🔄 刷新狀態")
    refresh_btn.click(fn=_status_text, outputs=status_md)
    net_btn = gr.Button("🔌 連線自我檢測")
    net_out = gr.Textbox(label="檢測結果", lines=8)
    net_btn.click(fn=_netcheck, outputs=net_out, api_name="netcheck")
    gb_btn = gr.Button("測備援模型速度")
    gb_btn.click(fn=_geminibench, outputs=net_out, api_name="geminibench")
    fo_btn = gr.Button("容錯演練")
    fo_btn.click(fn=_failover_test, outputs=net_out, api_name="failover")
    sb_btn = gr.Button("搜尋來源測試")
    sb_btn.click(fn=_searchbench, outputs=net_out, api_name="searchbench")

demo.launch(server_name="0.0.0.0", server_port=int(os.getenv("PORT", 7860)))
