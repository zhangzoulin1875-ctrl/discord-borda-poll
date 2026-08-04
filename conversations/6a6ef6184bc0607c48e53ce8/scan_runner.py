#!/usr/bin/env python3
"""
scan_runner.py — 本地百科掃描器
在你自己的電腦上執行，爬取 micropedia.site 全部條目，
分批傳送給 Render 上的機器人進行 AI 分析。

使用方式：
  pip install aiohttp
  python scan_runner.py --url https://你的render網址 --guild 伺服器ID [--key API密鑰]

也可以用環境變數：
  RENDER_URL=https://xxx.onrender.com
  GUILD_ID=123456
  SCAN_API_KEY=secret  (如果有設)
  MICROPEDIA_BATCH_SIZE=8  (預設8)
  MAX_CONCURRENT=3  (最多同時幾批在飛)
"""

import asyncio
import aiohttp
import json
import re
import sys
import os
import time
import argparse
import urllib.parse as up

SKIP_PREFIXES = ("特殊:", "File:", "Category:", "Template:", "Help:", "MediaWiki:", "Module:")
MICROPEDIA_BASE = "https://www.micropedia.site/api.php"
USER_AGENT = "ICEA-ScanRunner/1.0 (local crawler)"

def clean_wikitext(text: str) -> str:
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"\[\[[^]]*?\|([^]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^]]*)\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\]", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\|.*?\|\}", "", text, flags=re.DOTALL)
    text = re.sub(r"^=+\s*(.*?)\s*=+$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^[\*#:]+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

async def fetch_all_titles(session):
    all_titles = []
    apfrom = ""
    for _ in range(50):
        url = f"{MICROPEDIA_BASE}?action=query&list=allpages&aplimit=500&format=json"
        if apfrom:
            url += f"&apfrom={up.quote(apfrom)}"
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                print(f"⚠️ allpages API 回傳 {resp.status}")
                break
            data = await resp.json()
        pages = data.get("query", {}).get("allpages", [])
        all_titles.extend(p["title"] for p in pages)
        apcontinue = data.get("continue", {}).get("apcontinue")
        if not apcontinue:
            break
        apfrom = apcontinue
        print(f"  已取得 {len(all_titles)} 個標題...", end="\r")
    titles = [t for t in all_titles if not any(t.startswith(p) for p in SKIP_PREFIXES)]
    print(f"  總共 {len(titles)} 個有效條目（過濾掉 {len(all_titles) - len(titles)} 個系統頁面）")
    return titles

async def fetch_batch_content(session, titles):
    titles_param = "|".join(up.quote(t) for t in titles)
    api_url = (
        f"{MICROPEDIA_BASE}?action=query"
        f"&titles={titles_param}"
        f"&prop=revisions&rvprop=content&format=json&redirects=1"
    )
    articles = []
    try:
        async with session.get(api_url, headers={"User-Agent": USER_AGENT},
                               timeout=aiohttp.ClientTimeout(total=30, connect=10)) as resp:
            if resp.status != 200:
                print(f"  ⚠️ 取得內文失敗: HTTP {resp.status}")
                return articles
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
                clean = clean_wikitext(wikitext)
                if clean and len(clean) > 10:
                    p_title = page.get("title", "?")
                    if len(clean) > 2000:
                        clean = clean[:2000] + "..."
                    articles.append({"title": p_title, "content": clean})
    except Exception as e:
        print(f"  ⚠️ 取得內文異常: {e}")
    return articles

async def send_batch_to_render(session, render_url, guild_id, articles, batch_idx, batch_count, api_key):
    endpoint = f"{render_url}/api/guilds/{guild_id}/global-scan/batch"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Scan-Key"] = api_key
    payload = {
        "articles": articles,
        "batch_idx": batch_idx,
        "batch_count": batch_count,
    }
    try:
        async with session.post(endpoint, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=120, connect=10)) as resp:
            result = await resp.json()
            if resp.status != 200:
                print(f"  ❌ 批次 {batch_idx + 1} 上傳失敗: HTTP {resp.status} - {result.get('error', '?')}")
                return None
            return result
    except Exception as e:
        print(f"  ❌ 批次 {batch_idx + 1} 上傳異常: {e}")
        return None

async def run_scan(render_url, guild_id, api_key, batch_size, max_concurrent):
    print("=" * 60)
    print("🌐 ICEA 百科全局掃描 — 本地爬蟲模式")
    print("=" * 60)
    print(f"  Render: {render_url}")
    print(f"  Guild:   {guild_id}")
    print(f"  批次大小: {batch_size} 篇")
    print(f"  並發數:   {max_concurrent} 批同時")
    print()

    async with aiohttp.ClientSession() as session:
        # Step 1: Fetch all titles
        print("📥 正在取得所有條目標題...")
        titles = await fetch_all_titles(session)
        if not titles:
            print("❌ 找不到任何條目，結束。")
            return

        total = len(titles)
        batches = [titles[i:i + batch_size] for i in range(0, total, batch_size)]
        batch_count = len(batches)
        print(f"📦 共 {batch_count} 個批次（每批 {batch_size} 篇）")
        print()

        # Step 2: Initialize scan on Render
        print("🔗 初始化 Render 掃描工作階段...")
        init_endpoint = f"{render_url}/api/guilds/{guild_id}/global-scan/init"
        init_headers = {"Content-Type": "application/json"}
        if api_key:
            init_headers["X-Scan-Key"] = api_key
        try:
            async with session.post(init_endpoint, json={"total": total}, headers=init_headers,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                init_result = await resp.json()
                print(f"  ✅ {init_result.get('status', '?')} — 共 {init_result.get('total', 0)} 篇")
        except Exception as e:
            print(f"  ❌ 初始化失敗: {e}")
            return
        print()

        # Step 3: Pipeline — fetch + send concurrently
        print("🚀 開始爬取 + 分析流水線...")
        print()

        semaphore = asyncio.Semaphore(max_concurrent)
        progress = {"done": 0, "sent": 0, "extracted_total": 0}
        start_time = time.time()

        async def process_batch(batch_idx, batch_titles):
            async with semaphore:
                articles = await fetch_batch_content(session, batch_titles)
                if not articles:
                    print(f"  批次 {batch_idx + 1}/{batch_count}: 無有效內容，跳過")
                    progress["done"] += 1
                    return
                titles_preview = ", ".join(a["title"] for a in articles[:3])
                print(f"  📤 批次 {batch_idx + 1}/{batch_count} ({len(articles)} 篇): {titles_preview}...")
                result = await send_batch_to_render(session, render_url, guild_id,
                                                   articles, batch_idx, batch_count, api_key)
                progress["done"] += 1
                progress["sent"] += len(articles)
                if result and result.get("status") == "ok":
                    extracted_count = result.get("extracted_count", 0)
                    progress["extracted_total"] += extracted_count
                    pct = min(100, round(progress["sent"] / total * 100))
                    elapsed = time.time() - start_time
                    eta = (elapsed / max(progress["done"], 1)) * (batch_count - progress["done"])
                    print(f"  ✅ 批次 {batch_idx + 1} 完成 — 提取 {extracted_count} 項 | "
                          f"進度 {progress['sent']}/{total} ({pct}%) | "
                          f"已用 {elapsed:.0f}s | 預估剩餘 {eta:.0f}s")
                else:
                    print(f"  ⚠️ 批次 {batch_idx + 1} 已傳送但結果異常")

        tasks = [process_batch(i, batch) for i, batch in enumerate(batches)]
        await asyncio.gather(*tasks)

        # Step 4: Final consolidation
        print()
        print("🏁 全部批次完成，正在進行最終彙整...")
        finish_endpoint = f"{render_url}/api/guilds/{guild_id}/global-scan/finish"
        finish_headers = {"Content-Type": "application/json"}
        if api_key:
            finish_headers["X-Scan-Key"] = api_key
        try:
            async with session.post(finish_endpoint, json={}, headers=finish_headers,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                result = await resp.json()
                print()
                print("=" * 60)
                print("✅ 全局百科掃描完成！")
                print("=" * 60)
                print(f"  🏰 國家/組織: {result.get('countries', '?')}")
                print(f"  🔗 關係:      {result.get('relationships', '?')}")
                print(f"  👤 關鍵人物:  {result.get('key_figures', '?')}")
                print(f"  📜 重大事件:  {result.get('major_events', '?')}")
                print()
                print(f"  總共掃描 {total} 篇條目，用時 {time.time() - start_time:.0f} 秒")
        except Exception as e:
            print(f"  ❌ 最終彙整失敗: {e}")

def main():
    parser = argparse.ArgumentParser(description="ICEA 百科全局掃描 — 本地爬蟲")
    parser.add_argument("--url", default=os.getenv("RENDER_URL", ""),
                       help="Render 服務網址")
    parser.add_argument("--guild", default=os.getenv("GUILD_ID", ""),
                       help="Discord 伺服器 ID")
    parser.add_argument("--key", default=os.getenv("SCAN_API_KEY", ""),
                       help="API 密鑰")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("MICROPEDIA_BATCH_SIZE", "8")),
                       help="每批條目數量 (預設 8)")
    parser.add_argument("--max-concurrent", type=int, default=int(os.getenv("MAX_CONCURRENT", "3")),
                       help="最多同時幾批 (預設 3)")
    args = parser.parse_args()

    if not args.url:
        print("❌ 請提供 Render 網址: --url https://xxx.onrender.com 或設 RENDER_URL")
        sys.exit(1)
    if not args.guild:
        print("❌ 請提供伺服器 ID: --guild 123456 或設 GUILD_ID")
        sys.exit(1)

    render_url = args.url.rstrip("/")
    if not render_url.startswith("http"):
        render_url = "https://" + render_url
    asyncio.run(run_scan(render_url, args.guild, args.key, args.batch_size, args.max_concurrent))

if __name__ == "__main__":
    main()
