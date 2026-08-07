# ═══════════════════════════════════════════════════════════════════
# Module: 150_data_library
# 資料檔案庫 — 上傳 Excel/Word/CSV/Google Docs 等檔案作為 AI 額外知識庫
# 支援格式：txt, csv, xlsx, docx, json, md, html, Google Docs 連結
# 所有檔案解析使用 Python 內建模組，無需額外安裝依賴
# ═══════════════════════════════════════════════════════════════════

import asyncio
import io
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
import csv as _csv_mod
import json as _json_mod
import aiohttp

# ── 資料儲存 ──
DATA_LIBRARY_FILE = os.path.join(DATA_DIR, "data_library.json")
_data_library: dict = {"entries": []}

# 每個 entry 結構：
# {
#   "id": "uuid",
#   "filename": "原始檔名",
#   "file_type": "xlsx|csv|docx|txt|json|md|html|gdoc",
#   "title": "使用者自訂標題",
#   "description": "使用者自訂描述",
#   "content": "純文字內容（從檔案萃取）",
#   "content_size": 12345,  # 原始文字長度
#   "uploaded_by": "Discord用戶名",
#   "uploaded_by_id": "Discord用戶ID",
#   "uploaded_at": "2026-08-07T10:00:00+08:00",
#   "updated_at": "...",
#   "enabled": True,
#   "tags": ["經濟", "人口"],  # 使用者自訂標籤
#   "source_url": ""  # Google Docs 連結等
# }

# 每個 entry 的 content 上限（避免單個檔案塞爆 system prompt）
_MAX_CONTENT_PER_ENTRY = 8000  # 字元數
# 搜尋時最多注入幾個 entry
_MAX_ENTRIES_INJECT = 3
# 每個 entry 注入時最多用多少字元
_MAX_INJECT_CHARS = 2000


def _gen_id():
    import uuid
    return str(uuid.uuid4())[:12]


def load_data_library():
    """從本地載入資料檔案庫（Drive 同步）。"""
    global _data_library
    try:
        if os.path.exists(DATA_LIBRARY_FILE):
            with open(DATA_LIBRARY_FILE, "r", encoding="utf-8") as f:
                _data_library = _json_mod.loads(f.read())
            if "entries" not in _data_library:
                _data_library["entries"] = []
            print(f"📊 資料檔案庫已載入：{len(_data_library['entries'])} 筆")
    except Exception as e:
        print(f"⚠️ 資料檔案庫載入失敗：{e}")
        _data_library = {"entries": []}


def save_data_library():
    """儲存資料檔案庫到本地（自動同步到 Drive）。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _save_json_file(DATA_LIBRARY_FILE, _data_library)
    except Exception as e:
        print(f"⚠️ 資料檔案庫儲存失敗：{e}")


# ═══════════════════════════════════════════════════════════════════
# 檔案解析器 — 全部使用 Python 內建模組，無需安裝額外套件
# ═══════════════════════════════════════════════════════════════════

def _parse_xlsx(file_bytes: bytes) -> str:
    """解析 .xlsx 檔案（Excel 2007+）。xlsx 本質是 ZIP，內含 XML。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        # 共享字串表（儲存所有字串值，儲存格用索引引用）
        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            tree = ET.parse(io.BytesIO(zf.read("xl/sharedStrings.xml")))
            ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in tree.findall(".//a:si", ns):
                texts = si.findall(".//a:t", ns)
                shared_strings.append("".join(t.text or "" for t in texts))

        lines = []
        # 找出所有工作表
        sheet_files = sorted([f for f in zf.namelist() if f.startswith("xl/worksheets/sheet") and f.endswith(".xml")])
        for sheet_idx, sheet_file in enumerate(sheet_files):
            tree = ET.parse(io.BytesIO(zf.read(sheet_file)))
            ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sheet_name = f"工作表{sheet_idx + 1}"

            # 嘗試從 workbook.xml 取得工作表名稱
            if sheet_idx == 0:
                try:
                    wb_tree = ET.parse(io.BytesIO(zf.read("xl/workbook.xml")))
                    wb_ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                    sheets = wb_tree.findall(".//a:sheet", wb_ns)
                    if sheets and sheet_idx < len(sheets):
                        sheet_name = sheets[sheet_idx].get("name", sheet_name)
                except Exception:
                    pass

            rows_data = []
            for row in tree.findall(".//a:row", ns):
                cells = []
                for c in row.findall("a:c", ns):
                    v = c.find("a:v", ns)
                    if v is None:
                        cells.append("")
                        continue
                    cell_type = c.get("t", "")
                    val = v.text or ""
                    if cell_type == "s":  # shared string
                        try:
                            val = shared_strings[int(val)]
                        except (ValueError, IndexError):
                            pass
                    cells.append(val)
                if any(cells):  # 跳過全空行
                    rows_data.append("\t".join(cells))
            if rows_data:
                lines.append(f"[{sheet_name}]")
                lines.extend(rows_data)
                lines.append("")
        zf.close()
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ Excel解析失敗：{e}"


def _parse_docx(file_bytes: bytes) -> str:
    """解析 .docx 檔案（Word 2007+）。docx 本質是 ZIP，內含 XML。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        if "word/document.xml" not in zf.namelist():
            return "⚠️ Word文件解析失敗：找不到 document.xml"
        tree = ET.parse(io.BytesIO(zf.read("word/document.xml")))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

        lines = []
        for para in tree.findall(".//w:p", ns):
            # 取得段落樣式（標題等）
            style_elem = para.find(".//w:pStyle", ns)
            style = style_elem.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "") if style_elem is not None else ""

            # 取得所有文字片段
            texts = para.findall(".//w:t", ns)
            text = "".join(t.text or "" for t in texts)
            if text.strip():
                if "Heading" in style or "heading" in style or "Title" in style:
                    lines.append(f"\n## {text.strip()}")
                else:
                    lines.append(text.strip())
        zf.close()
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ Word文件解析失敗：{e}"


def _parse_csv(file_bytes: bytes) -> str:
    """解析 .csv 檔案。"""
    try:
        # 嘗試偵測編碼
        text = None
        for encoding in ("utf-8", "utf-8-sig", "big5", "gb2312", "latin-1"):
            try:
                text = file_bytes.decode(encoding)
                break
            except (UnicodeDecodeError, Exception):
                continue
        if not text:
            text = file_bytes.decode("utf-8", errors="replace")

        reader = _csv_mod.reader(io.StringIO(text))
        lines = []
        for row in reader:
            lines.append("\t".join(row))
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ CSV解析失敗：{e}"


def _parse_html(file_bytes: bytes) -> str:
    """解析 .html 檔案，萃取純文字。"""
    try:
        text = None
        for encoding in ("utf-8", "utf-8-sig", "big5", "latin-1"):
            try:
                text = file_bytes.decode(encoding)
                break
            except (UnicodeDecodeError, Exception):
                continue
        if not text:
            text = file_bytes.decode("utf-8", errors="replace")

        # 移除 script 和 style 標籤
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # 將 <br>, <p>, <div>, <tr>, <li> 換成換行
        text = re.sub(r'<(?:br|/p|/div|/tr|/li|/h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
        # 移除所有標籤
        text = re.sub(r'<[^>]+>', '', text)
        # 解碼 HTML 實體
        import html as _html_mod
        text = _html_mod.unescape(text)
        # 壓縮空白
        lines = [line.strip() for line in text.split("\n")]
        lines = [line for line in lines if line]
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ HTML解析失敗：{e}"


def _parse_plain_text(file_bytes: bytes) -> str:
    """解析純文字檔案（.txt, .md, .json 等）。"""
    for encoding in ("utf-8", "utf-8-sig", "big5", "gb2312", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except (UnicodeDecodeError, Exception):
            continue
    return file_bytes.decode("utf-8", errors="replace")


def _parse_json(file_bytes: bytes) -> str:
    """解析 .json 檔案，壓平成可讀文字。"""
    try:
        text = _parse_plain_text(file_bytes)
        data = _json_mod.loads(text)
        return _flatten_json(data)
    except Exception:
        return _parse_plain_text(file_bytes)


def _flatten_json(data, prefix="", depth=0) -> str:
    """將 JSON 結構壓平成可讀文字。"""
    lines = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and depth < 3:
                lines.append(f"{'  ' * depth}{k}:")
                lines.append(_flatten_json(v, prefix=k, depth=depth + 1))
            else:
                lines.append(f"{'  ' * depth}{k}: {v}")
    elif isinstance(data, list):
        for i, item in enumerate(data[:50]):  # 最多50項
            if isinstance(item, (dict, list)) and depth < 3:
                lines.append(f"{'  ' * depth}[{i}]:")
                lines.append(_flatten_json(item, depth=depth + 1))
            else:
                lines.append(f"{'  ' * depth}[{i}]: {item}")
    else:
        lines.append(str(data))
    return "\n".join(lines)


# ── Google Docs 下載 ──
async def _download_google_doc(url: str) -> str:
    """從 Google Docs 分享連結下載純文字內容。
    支援格式：https://docs.google.com/document/d/DOC_ID/edit?..."""
    try:
        # 從連結提取 document ID
        match = re.search(r'/document/d/([a-zA-Z0-9_-]+)', url)
        if not match:
            return ""
        doc_id = match.group(1)
        # 匯出為純文字
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                export_url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0 (compatible; DiscordBot)"}
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Google Docs 匯出可能含多餘空行
                    lines = [l.strip() for l in text.split("\n")]
                    lines = [l for l in lines if l]
                    return "\n".join(lines)
                else:
                    print(f"⚠️ Google Docs 下載失敗: HTTP {resp.status}")
                    return ""
    except Exception as e:
        print(f"⚠️ Google Docs 下載失敗: {e}")
        return ""


async def _download_google_sheet(url: str) -> str:
    """從 Google Sheets 分享連結下載 CSV 內容。"""
    try:
        match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
        if not match:
            return ""
        sheet_id = match.group(1)
        # 嘗試匯出為 CSV（gid=0 是第一個工作表）
        export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                export_url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0 (compatible; DiscordBot)"}
            ) as resp:
                if resp.status == 200:
                    raw = await resp.read()
                    return _parse_csv(raw)
                else:
                    print(f"⚠️ Google Sheets 下載失敗: HTTP {resp.status}")
                    return ""
    except Exception as e:
        print(f"⚠️ Google Sheets 下載失敗: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
# 檔案類型偵測與分派
# ═══════════════════════════════════════════════════════════════════

def _get_file_type(filename: str) -> str:
    """從副檔名判斷檔案類型。"""
    ext = os.path.splitext(filename.lower())[1]
    return {
        ".xlsx": "xlsx",
        ".xls": "xlsx",  # 舊版 Excel 暫不支援二進位格式，但試試
        ".csv": "csv",
        ".docx": "docx",
        ".doc": "docx",
        ".txt": "txt",
        ".md": "md",
        ".json": "json",
        ".html": "html",
        ".htm": "html",
        ".xml": "html",  # XML 用 HTML 解析器
    }.get(ext, "")


def _parse_file(filename: str, file_bytes: bytes) -> str:
    """根據檔案類型分派到對應的解析器。"""
    file_type = _get_file_type(filename)
    if not file_type:
        return ""

    parsers = {
        "xlsx": _parse_xlsx,
        "docx": _parse_docx,
        "csv": _parse_csv,
        "json": _parse_json,
        "html": _parse_html,
        "txt": _parse_plain_text,
        "md": _parse_plain_text,
    }

    parser = parsers.get(file_type)
    if not parser:
        return ""

    try:
        content = parser(file_bytes)
        # 截斷過長內容
        if len(content) > _MAX_CONTENT_PER_ENTRY:
            content = content[:_MAX_CONTENT_PER_ENTRY] + "\n...（內容過長，已截斷）"
        return content
    except Exception as e:
        return f"⚠️ 解析失敗：{e}"


# ═══════════════════════════════════════════════════════════════════
# 搜尋引擎 — bigram + keyword matching（對齊現有知識庫搜尋邏輯）
# ═══════════════════════════════════════════════════════════════════

def _search_data_library(query: str, top_n: int = 3) -> list:
    """搜尋資料檔案庫，返回最相關的 entry。"""
    query = query.strip()
    query_bg = _bigrams(query)
    if not query_bg:
        return []

    entries = [e for e in _data_library.get("entries", []) if e.get("enabled", True) and e.get("content")]
    if not entries:
        return []

    scored = []
    for e in entries:
        # 搜尋範圍：標題 + 描述 + 標籤 + 內容前 N 字
        text = (
            e.get("title", "") + " "
            + e.get("description", "") + " "
            + " ".join(e.get("tags", [])) + " "
            + e.get("content", "")[:3000]
        )
        text_bg = _bigrams(text)
        if not text_bg:
            continue
        overlap = query_bg & text_bg
        substring_hit = bool(query) and query in text
        keyword_hit = _keyword_substring_hit(query, text)
        if not overlap and not substring_hit and not keyword_hit:
            continue
        containment = len(overlap) / len(query_bg) if query_bg else 0
        if not substring_hit and not keyword_hit and containment < 0.15:
            continue
        score = max(containment, 0.5) if keyword_hit else containment
        scored.append((e, score, substring_hit or keyword_hit))

    scored.sort(key=lambda x: (-x[2], -x[1]))
    return [e for e, _, _ in scored[:top_n]]


def _build_data_library_context(query: str) -> str:
    """根據使用者查詢，建構要注入 AI system prompt 的資料檔案庫上下文。"""
    matched = _search_data_library(query, top_n=_MAX_ENTRIES_INJECT)
    if not matched:
        return ""

    blocks = []
    for entry in matched:
        title = entry.get("title", entry.get("filename", "未命名"))
        desc = entry.get("description", "")
        content = entry.get("content", "")

        # 截斷每個 entry 的注入內容
        if len(content) > _MAX_INJECT_CHARS:
            content = content[:_MAX_INJECT_CHARS] + "…"

        header = f"📄 【{title}】"
        if desc:
            header += f"\n   描述：{desc}"
        if entry.get("tags"):
            header += f"\n   標籤：{'、'.join(entry['tags'])}"

        blocks.append(f"{header}\n{content}")

    return (
        f"\n\n─── 資料檔案庫（管理者上傳的參考資料）───\n"
        f"以下是管理者上傳的資料檔案內容，根據使用者問題自動搜尋比對到。"
        f"這些是可信的參考資料，請優先參考這些內容來回答問題。\n\n"
        + "\n\n---\n\n".join(blocks)
    )


# ═══════════════════════════════════════════════════════════════════
# Discord 指令群組
# ═══════════════════════════════════════════════════════════════════

class DataLibraryGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="data", description="資料檔案庫管理")

    # ── 上傳檔案 ──
    @app_commands.command(name="upload", description="上傳資料檔案到知識庫（支援 xlsx/csv/docx/txt/md/json/html）")
    @app_commands.describe(
        file="要上傳的檔案",
        title="資料標題（用於搜尋和顯示）",
        description="資料描述（選填）",
        tags="標籤，用逗號分隔（選填，例如：經濟,人口,貿易）"
    )
    async def upload(self, interaction: discord.Interaction, file: discord.Attachment, title: str, description: str = "", tags: str = ""):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        # 檢查檔案類型
        file_type = _get_file_type(file.filename)
        if not file_type:
            await interaction.response.send_message(
                f"❌ 不支援的檔案格式：`{file.filename}`\n"
                f"支援的格式：xlsx, csv, docx, txt, md, json, html",
                ephemeral=True
            )
            return

        # 檢查檔案大小（Discord 附件上限 25MB，但我們截斷內容到 8KB）
        if file.size > 10 * 1024 * 1024:  # 10MB
            await interaction.response.send_message(
                "❌ 檔案太大（上限 10MB）。請壓縮或分割後再上傳。",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # 下載檔案內容
            file_bytes = await file.read()

            # 解析檔案
            content = _parse_file(file.filename, file_bytes)
            if not content or content.startswith("⚠️"):
                await interaction.followup.send(
                    f"❌ 檔案解析失敗：{content or '無法萃取內容'}\n"
                    f"請確認檔案格式正確。",
                    ephemeral=True
                )
                return

            # 解析標籤
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

            # 建立 entry
            entry = {
                "id": _gen_id(),
                "filename": file.filename,
                "file_type": file_type,
                "title": title.strip(),
                "description": description.strip(),
                "content": content,
                "content_size": len(content),
                "uploaded_by": interaction.user.display_name,
                "uploaded_by_id": str(interaction.user.id),
                "uploaded_at": _now_iso(),
                "updated_at": _now_iso(),
                "enabled": True,
                "tags": tag_list,
                "source_url": "",
            }

            _data_library["entries"].append(entry)
            save_data_library()

            embed = discord.Embed(
                title="✅ 資料上傳成功",
                color=discord.Color.green(),
                timestamp=_now_dt()
            )
            embed.add_field(name="標題", value=title, inline=False)
            embed.add_field(name="檔案", value=file.filename, inline=True)
            embed.add_field(name="類型", value=file_type, inline=True)
            embed.add_field(name="內容長度", value=f"{len(content):,} 字元", inline=True)
            if description:
                embed.add_field(name="描述", value=description[:200], inline=False)
            if tag_list:
                embed.add_field(name="標籤", value="、".join(tag_list), inline=False)
            embed.set_footer(text=f"上傳者：{interaction.user.display_name}")

            await interaction.followup.send(embed=embed, ephemeral=True)
            print(f"📊 資料檔案庫：上傳 '{title}' ({file.filename}, {len(content)} chars)")

        except Exception as e:
            await interaction.followup.send(f"❌ 上傳失敗：{e}", ephemeral=True)
            print(f"⚠️ 資料檔案庫上傳失敗：{e}")

    # ── 從 Google Docs/Sheets 連結匯入 ──
    @app_commands.command(name="import-url", description="從 Google Docs 或 Google Sheets 連結匯入資料")
    @app_commands.describe(
        url="Google Docs 或 Google Sheets 的分享連結",
        title="資料標題",
        description="資料描述（選填）",
        tags="標籤，用逗號分隔（選填）"
    )
    async def import_url(self, interaction: discord.Interaction, url: str, title: str, description: str = "", tags: str = ""):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        is_gdoc = "docs.google.com/document" in url
        is_gsheet = "docs.google.com/spreadsheets" in url

        if not is_gdoc and not is_gsheet:
            await interaction.response.send_message(
                "❌ 請提供 Google Docs 或 Google Sheets 的連結。\n"
                "格式：`https://docs.google.com/document/d/...` 或 `https://docs.google.com/spreadsheets/d/...`",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            if is_gdoc:
                content = await _download_google_doc(url)
                file_type = "gdoc"
            else:
                content = await _download_google_sheet(url)
                file_type = "gsheet"

            if not content or len(content.strip()) < 10:
                await interaction.followup.send(
                    "❌ 下載失敗或內容為空。\n"
                    "請確認文件已設為「知道連結的人都能檢視」。",
                    ephemeral=True
                )
                return

            # 截斷
            if len(content) > _MAX_CONTENT_PER_ENTRY:
                content = content[:_MAX_CONTENT_PER_ENTRY] + "\n...（內容過長，已截斷）"

            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

            entry = {
                "id": _gen_id(),
                "filename": title,
                "file_type": file_type,
                "title": title.strip(),
                "description": description.strip(),
                "content": content,
                "content_size": len(content),
                "uploaded_by": interaction.user.display_name,
                "uploaded_by_id": str(interaction.user.id),
                "uploaded_at": _now_iso(),
                "updated_at": _now_iso(),
                "enabled": True,
                "tags": tag_list,
                "source_url": url,
            }

            _data_library["entries"].append(entry)
            save_data_library()

            embed = discord.Embed(
                title="✅ Google 文件匯入成功",
                color=discord.Color.green(),
                timestamp=_now_dt()
            )
            embed.add_field(name="標題", value=title, inline=False)
            embed.add_field(name="來源", value="Google Docs" if is_gdoc else "Google Sheets", inline=True)
            embed.add_field(name="內容長度", value=f"{len(content):,} 字元", inline=True)
            if description:
                embed.add_field(name="描述", value=description[:200], inline=False)
            if tag_list:
                embed.add_field(name="標籤", value="、".join(tag_list), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
            print(f"📊 資料檔案庫：Google 匯入 '{title}' ({len(content)} chars)")

        except Exception as e:
            await interaction.followup.send(f"❌ 匯入失敗：{e}", ephemeral=True)
            print(f"⚠️ 資料檔案庫 Google 匯入失敗：{e}")

    # ── 列出所有資料 ──
    @app_commands.command(name="list", description="列出資料檔案庫中的所有資料")
    async def list_entries(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        entries = _data_library.get("entries", [])
        if not entries:
            await interaction.followup.send("📂 資料檔案庫目前是空的。使用 `/data upload` 上傳檔案。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📊 資料檔案庫（共 {len(entries)} 筆）",
            color=discord.Color.blue(),
            timestamp=_now_dt()
        )

        for i, e in enumerate(entries):
            status = "✅" if e.get("enabled", True) else "⛔"
            tags_str = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
            embed.add_field(
                name=f"{status} {i+1}. {e['title']}",
                value=(
                    f"類型：{e['file_type']} | 內容：{e.get('content_size', 0):,} 字元\n"
                    f"上傳：{e.get('uploaded_at', '?')[:10]} by {e.get('uploaded_by', '?')}\n"
                    f"ID：`{e['id']}`{tags_str}"
                ),
                inline=False
            )

        embed.set_footer(text="使用 /data edit 修改、/data delete 刪除、/data toggle 啟用/停用")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── 刪除資料 ──
    @app_commands.command(name="delete", description="刪除資料檔案庫中的一筆資料")
    @app_commands.describe(entry_id="要刪除的資料 ID（可用 /data list 查看）")
    async def delete_entry(self, interaction: discord.Interaction, entry_id: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        original_len = len(_data_library["entries"])
        _data_library["entries"] = [e for e in _data_library["entries"] if e["id"] != entry_id.strip()]

        if len(_data_library["entries"]) == original_len:
            await interaction.followup.send(f"❌ 找不到 ID 為 `{entry_id}` 的資料。", ephemeral=True)
            return

        save_data_library()
        await interaction.followup.send(f"✅ 已刪除資料（ID: `{entry_id}`）。", ephemeral=True)

    # ── 啟用/停用 ──
    @app_commands.command(name="toggle", description="啟用或停用一筆資料（停用後 AI 不會參考它）")
    @app_commands.describe(entry_id="要切換的資料 ID")
    async def toggle_entry(self, interaction: discord.Interaction, entry_id: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        for e in _data_library["entries"]:
            if e["id"] == entry_id.strip():
                e["enabled"] = not e.get("enabled", True)
                e["updated_at"] = _now_iso()
                save_data_library()
                state = "啟用" if e["enabled"] else "停用"
                await interaction.followup.send(f"✅ 「{e['title']}」已{state}。", ephemeral=True)
                return

        await interaction.followup.send(f"❌ 找不到 ID 為 `{entry_id}` 的資料。", ephemeral=True)

    # ── 編輯資料 ──
    @app_commands.command(name="edit", description="修改資料的標題、描述或標籤")
    @app_commands.describe(
        entry_id="要修改的資料 ID",
        title="新標題（留空不改）",
        description="新描述（留空不改）",
        tags="新標籤（用逗號分隔，留空不改）"
    )
    async def edit_entry(self, interaction: discord.Interaction, entry_id: str, title: str = "", description: str = "", tags: str = ""):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        for e in _data_library["entries"]:
            if e["id"] == entry_id.strip():
                if title:
                    e["title"] = title.strip()
                if description:
                    e["description"] = description.strip()
                if tags:
                    e["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
                e["updated_at"] = _now_iso()
                save_data_library()

                embed = discord.Embed(
                    title="✅ 資料已更新",
                    color=discord.Color.green(),
                    timestamp=_now_dt()
                )
                embed.add_field(name="標題", value=e["title"], inline=False)
                if e.get("description"):
                    embed.add_field(name="描述", value=e["description"][:200], inline=False)
                if e.get("tags"):
                    embed.add_field(name="標籤", value="、".join(e["tags"]), inline=False)

                await interaction.followup.send(embed=embed, ephemeral=True)
                return

        await interaction.followup.send(f"❌ 找不到 ID 為 `{entry_id}` 的資料。", ephemeral=True)

    # ── 重新上傳/更新檔案內容 ──
    @app_commands.command(name="update", description="重新上傳檔案以更新現有資料的內容")
    @app_commands.describe(
        entry_id="要更新的資料 ID",
        file="新的檔案"
    )
    async def update_file(self, interaction: discord.Interaction, entry_id: str, file: discord.Attachment):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        target = None
        for e in _data_library["entries"]:
            if e["id"] == entry_id.strip():
                target = e
                break

        if not target:
            await interaction.followup.send(f"❌ 找不到 ID 為 `{entry_id}` 的資料。", ephemeral=True)
            return

        file_type = _get_file_type(file.filename)
        if not file_type:
            await interaction.followup.send(
                f"❌ 不支援的檔案格式：`{file.filename}`", ephemeral=True
            )
            return

        try:
            file_bytes = await file.read()
            content = _parse_file(file.filename, file_bytes)
            if not content or content.startswith("⚠️"):
                await interaction.followup.send(f"❌ 解析失敗：{content}", ephemeral=True)
                return

            target["content"] = content
            target["content_size"] = len(content)
            target["filename"] = file.filename
            target["file_type"] = file_type
            target["updated_at"] = _now_iso()
            save_data_library()

            await interaction.followup.send(
                f"✅ 「{target['title']}」內容已更新（{len(content):,} 字元）。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 更新失敗：{e}", ephemeral=True)

    # ── 搜尋測試 ──
    @app_commands.command(name="search", description="測試搜尋資料檔案庫（查看 AI 會參考哪些資料）")
    @app_commands.describe(query="搜尋關鍵字")
    async def search_test(self, interaction: discord.Interaction, query: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        matched = _search_data_library(query, top_n=5)
        if not matched:
            await interaction.followup.send(
                f"🔍 搜尋「{query}」：沒有找到匹配的資料。",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🔍 搜尋「{query}」的結果",
            description=f"找到 {len(matched)} 筆匹配資料：",
            color=discord.Color.blue(),
            timestamp=_now_dt()
        )

        for i, e in enumerate(matched):
            preview = e.get("content", "")[:200].replace("\n", " ")
            embed.add_field(
                name=f"{i+1}. {e['title']}",
                value=f"類型：{e['file_type']} | {e.get('content_size', 0):,} 字元\n預覽：{preview}…",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── 查看單筆資料詳情 ──
    @app_commands.command(name="view", description="查看一筆資料的詳細內容")
    @app_commands.describe(entry_id="資料 ID", preview_length="預覽字數（預設500）")
    async def view_entry(self, interaction: discord.Interaction, entry_id: str, preview_length: int = 500):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        for e in _data_library["entries"]:
            if e["id"] == entry_id.strip():
                preview = e.get("content", "")[:max(100, min(preview_length, 2000))]
                embed = discord.Embed(
                    title=f"📊 {e['title']}",
                    color=discord.Color.blue() if e.get("enabled", True) else discord.Color.greyple(),
                    timestamp=_now_dt()
                )
                embed.add_field(name="ID", value=e["id"], inline=True)
                embed.add_field(name="類型", value=e["file_type"], inline=True)
                embed.add_field(name="狀態", value="啟用" if e.get("enabled", True) else "停用", inline=True)
                embed.add_field(name="檔名", value=e.get("filename", ""), inline=True)
                embed.add_field(name="內容長度", value=f"{e.get('content_size', 0):,} 字元", inline=True)
                embed.add_field(name="上傳時間", value=e.get("uploaded_at", "?")[:16], inline=True)
                if e.get("description"):
                    embed.add_field(name="描述", value=e["description"][:200], inline=False)
                if e.get("tags"):
                    embed.add_field(name="標籤", value="、".join(e["tags"]), inline=False)
                if e.get("source_url"):
                    embed.add_field(name="來源連結", value=e["source_url"][:200], inline=False)
                embed.add_field(name="內容預覽", value=f"```\n{preview}\n```", inline=False)

                await interaction.followup.send(embed=embed, ephemeral=True)
                return

        await interaction.followup.send(f"❌ 找不到 ID 為 `{entry_id}` 的資料。", ephemeral=True)

    # ── 清空全部 ──
    @app_commands.command(name="clear", description="清空資料檔案庫（刪除所有資料）")
    async def clear_all(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 只有機器人擁有者可以使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        count = len(_data_library["entries"])
        if count == 0:
            await interaction.followup.send("📂 資料檔案庫已經是空的。", ephemeral=True)
            return

        _data_library["entries"] = []
        save_data_library()
        await interaction.followup.send(f"✅ 已清空資料檔案庫（刪除了 {count} 筆資料）。", ephemeral=True)


# ── 註冊指令群組 ──
bot.tree.add_command(DataLibraryGroup())

# ── 啟動時載入 ──
load_data_library()
save_data_library()  # 確保檔案存在以觸發 Drive 同步

print("📊 資料檔案庫模組已載入")
