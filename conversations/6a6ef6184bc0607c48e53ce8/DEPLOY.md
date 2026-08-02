# 部署到 Railway

## 方式一：一鍵部署
1. 到 https://railway.app 註冊/登入
2. 點 "New Project" → "Deploy from Docker Image" 或連接你的 GitHub repo
3. 上傳這些檔案到一個 GitHub repo：
   - `discord_borda_poll.py`
   - `requirements.txt`
   - `Dockerfile`
4. 在 Railway 的 Variables 裡設定：
   - `DISCORD_BOT_TOKEN` = 你的 bot token
5. 部署完成，bot 會 24/7 運行

## 方式二：部署到 Render
1. 到 https://render.com 註冊
2. 新建 "Web Service" → 連接 GitHub repo（含上述檔案）
3. 設定：
   - Environment: Python 3
   - Build: `pip install -r requirements.txt`
   - Start: `python discord_borda_poll.py`
4. 環境變數加入 `DISCORD_BOT_TOKEN`
5. 部署

## 方式三：直接在 VPS 上跑
```bash
# 上傳檔案到伺服器後
pip install -r requirements.txt
export DISCORD_BOT_TOKEN="你的token"
nohup python discord_borda_poll.py &
```
