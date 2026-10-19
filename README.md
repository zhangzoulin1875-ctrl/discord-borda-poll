---
title: Discord Bot
emoji: 🤖
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: mit
short_description: 通用 AI 聊天 Discord 機器人
---

# Discord Bot（通用版 v3）

Gradio 頁面只是保持 Space 服務的殼，Discord bot 本體在背景執行緒運行。

## 需要設定的 Secrets（Settings → Variables and secrets）

| 名稱 | 說明 |
|---|---|
| `DISCORD_BOT_TOKEN` | 必填，Discord bot token |
| `GITHUB_TOKEN` | 必填，用來同步設定到 GitHub repo |
| `OWNER_ID` | 可選，機器人擁有者 Discord ID（預設已內建） |

## 防休眠

免費 Space 48 小時沒有連線會休眠。請用 cron-job.org 或 UptimeRobot
每 5 分鐘 GET 一次本 Space 網址（`https://<username>-<space-name>.hf.space/`）。
