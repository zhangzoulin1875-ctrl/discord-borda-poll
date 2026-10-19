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


with gr.Blocks(title="Discord Bot") as demo:
    gr.Markdown("# 🤖 Discord Bot 控制頁")
    gr.Markdown("這個頁面只是讓 Space 保持服務的殼，bot 本體在背景執行。")
    status_md = gr.Markdown(_status_text())
    refresh_btn = gr.Button("🔄 刷新狀態")
    refresh_btn.click(fn=_status_text, outputs=status_md)

demo.launch(server_name="0.0.0.0", server_port=int(os.getenv("PORT", 7860)))
