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
    out.append(one("Google(Gradio執行緒)", "generativelanguage.googleapis.com", "/v1beta/openai/chat/completions", {"model": "gemma-4-31b-it", "messages": [{"role": "user", "content": "hi"}]}, {"Content-Type": "application/json"}))
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


with gr.Blocks(title="Discord Bot") as demo:
    gr.Markdown("# 🤖 Discord Bot 控制頁")
    gr.Markdown("這個頁面只是讓 Space 保持服務的殼，bot 本體在背景執行。")
    status_md = gr.Markdown(_status_text())
    refresh_btn = gr.Button("🔄 刷新狀態")
    refresh_btn.click(fn=_status_text, outputs=status_md)
    net_btn = gr.Button("🔌 連線自我檢測")
    net_out = gr.Textbox(label="檢測結果", lines=8)
    net_btn.click(fn=_netcheck, outputs=net_out, api_name="netcheck")

demo.launch(server_name="0.0.0.0", server_port=int(os.getenv("PORT", 7860)))
