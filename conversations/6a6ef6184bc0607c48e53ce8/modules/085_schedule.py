# ═════════════════════════════════════════════════════════════════
# Module: 85_schedule (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

def _find_cjk_font():
    """Find a CJK-capable font. Prefers the bundled repo font (always
    available regardless of Render's build system); falls back to
    scanning common system font paths in case the bundled file is
    missing for some reason."""
    global _CJK_FONT_PATH_CACHE
    if _CJK_FONT_PATH_CACHE:
        return _CJK_FONT_PATH_CACHE

    if os.path.isfile(_BUNDLED_CJK_FONT_PATH):
        _CJK_FONT_PATH_CACHE = _BUNDLED_CJK_FONT_PATH
        return _CJK_FONT_PATH_CACHE

    print("⚠️ 找不到隨附字體 fonts/NotoSansTC-Variable.ttf，改用系統字體搜尋（可能找不到，中文將顯示空白）")
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for pat in [
        "/usr/share/fonts/**/NotoSansCJK*",
        "/usr/share/fonts/**/NotoSansCJKjp*",
        "/usr/share/fonts/**/NotoSansSC*",
        "/usr/share/fonts/**/*CJK*",
        "/usr/share/fonts/**/*WenQuanYi*",
        "/usr/share/fonts/**/*wqy*",
        "/usr/share/fonts/**/*DroidSansFallback*",
    ]:
        try:
            candidates.extend(glob.glob(pat, recursive=True))
        except Exception:
            pass
    for path in candidates:
        if os.path.isfile(path):
            _CJK_FONT_PATH_CACHE = path
            return path
    return None


def _load_font(size: int, bold: bool = False):
    """Load a CJK font at the given size. The bundled font is a variable
    font with weight axes (Thin..Black); we select Bold/Regular via
    set_variation_by_name when available."""
    font_path = _find_cjk_font()
    if font_path:
        try:
            font = ImageFont.truetype(font_path, size)
            try:
                font.set_variation_by_name("Bold" if bold else "Regular")
            except Exception:
                pass  # not a variable font, or freetype build lacks var-font support — fine, use default instance
            return font
        except Exception as e:
            print(f"⚠️ 字體載入失敗 {font_path}: {e}")
    # Last resort: Pillow's built-in bitmap font (no CJK glyphs — better than crashing)
    try:
        return ImageFont.load_default()
    except Exception:
        return None


async def _ai_summarize_for_schedule(entries: list) -> list:
    """Use AI to summarize accepted proposals into concise schedule-display text.
    Returns a list of {proposal_type, summary, proposer_name} dicts."""
    if not entries:
        return []
    # Build a combined prompt for all proposals
    proposal_list = []
    for i, e in enumerate(entries):
        proposal_list.append(
            f"提案{i+1}：\n"
            f"  種類：{e.get('proposal_type', '?')}\n"
            f"  摘要：{e.get('summary', '')}\n"
            f"  提案人：{e.get('proposer_name', '?')}\n"
            f"  原文：{e.get('raw_content', '')[:300]}"
        )
    combined = "\n\n".join(proposal_list)
    
    prompt = (
        "你是微國家組織的秘書助理。以下是本次會議排程中所有已受理的提案。"
        "請將每個提案整理成適合放在會議通知圖片上的精簡顯示文字。\n\n"
        "要求：\n"
        "- 每個提案一行，不超過40字\n"
        "- 格式：[提案種類] 精簡內容描述\n"
        "- 去除冗餘資訊，只留核心議題\n"
        "- 保持原文意思，不竄改內容\n\n"
        f"提案清單：\n{combined}\n\n"
        "請以 JSON 陣列格式回覆（不要加 markdown code block）：\n"
        '[{"type": "提案種類", "text": "精簡顯示文字"}, ...]\n'
        "只回覆 JSON，不要加其他文字。"
    )
    
    ps_ai = proposal_settings.get("ai_settings", {})
    settings = {
        "api_url": ps_ai.get("api_url") or chat_ai_settings.get("api_url", ""),
        "api_key": ps_ai.get("api_key") or chat_ai_settings.get("api_key", ""),
        "model": ps_ai.get("model") or chat_ai_settings.get("model", "gpt-4o-mini"),
        "system_prompt": "你是秘書助理，負責精簡整理提案內容。",
    }
    
    if not settings["api_url"] or not settings["api_key"]:
        # Fallback: use raw summaries
        return [{"type": e.get("proposal_type", "?"), "text": e.get("summary", "")[:40]} for e in entries]
    
    try:
        result = await call_ai_api(prompt, settings)
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json_module.loads(result)
        if isinstance(parsed, list):
            # Merge with original entries for proposer_name
            return [
                {
                    "type": item.get("type", entries[i].get("proposal_type", "?") if i < len(entries) else "?"),
                    "text": item.get("text", "")[:60],
                    "proposer_name": entries[i].get("proposer_name", "?") if i < len(entries) else "?",
                }
                for i, item in enumerate(parsed)
            ]
    except Exception as e:
        print(f"⚠️ 排程 AI 整理失敗，使用原始摘要：{e}")
    
    # Fallback
    return [{"type": e.get("proposal_type", "?"), "text": e.get("summary", "")[:40], "proposer_name": e.get("proposer_name", "?")} for e in entries]


def _draw_gradient_bar(draw, xy, color1, color2, direction="horizontal"):
    """Draw a smooth gradient bar. xy = (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = xy
    if direction == "horizontal":
        steps = max(x1 - x0, 1)
        for i in range(steps):
            t = i / steps
            r = int(color1[0] + (color2[0] - color1[0]) * t)
            g = int(color1[1] + (color2[1] - color1[1]) * t)
            b = int(color1[2] + (color2[2] - color1[2]) * t)
            draw.line([(x0 + i, y0), (x0 + i, y1)], fill=(r, g, b))
    else:
        steps = max(y1 - y0, 1)
        for i in range(steps):
            t = i / steps
            r = int(color1[0] + (color2[0] - color1[0]) * t)
            g = int(color1[1] + (color2[1] - color1[1]) * t)
            b = int(color1[2] + (color2[2] - color1[2]) * t)
            draw.line([(x0, y0 + i), (x1, y0 + i)], fill=(r, g, b))


def _draw_rounded_card(img, xy, radius=16, fill=(54, 57, 63), border=None, border_width=1):
    """Draw a rounded card with optional border."""
    x0, y0, x1, y1 = xy
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=border, width=border_width if border else 0)
    return draw


def _text_size(draw, text, font):
    """Return (width, height, y_offset) for text, robust to Pillow bbox quirks."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        y_off = max(0, -bbox[1])
        return w, h, y_off
    except Exception:
        return len(text) * (font.size if hasattr(font, "size") else 14), (font.size if hasattr(font, "size") else 14), 0


def _draw_badge(draw, xy, text, font, bg_color, text_color=(255, 255, 255), padding_x=10, padding_y=5):
    """Draw a small rounded badge with text. xy = (x, y) = top-left corner.
    Returns (x_end, y_end) = bottom-right corner of the badge.

    Uses anchor="mm" to center text on the pill — manual bbox-offset math doesn't
    reliably match a font's real ascender/descender metrics (this caused text to sit
    too close to the bottom edge, looking squeezed against the pill border)."""
    x, y = xy
    tw, th, _ = _text_size(draw, text, font)
    bw = tw + padding_x * 2
    bh = th + padding_y * 2
    draw.rounded_rectangle([x, y, x + bw, y + bh], radius=min(bh // 2, 9), fill=bg_color)
    draw.text((x + bw / 2, y + bh / 2), text, fill=text_color, font=font, anchor="mm")
    return (x + bw, y + bh)


_ICEA_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icea_logo_white.png")
_ICEA_LOGO_CACHE = None


def _load_icea_logo():
    """Load the ICEA emblem (white rings, transparent background) bundled in assets/."""
    global _ICEA_LOGO_CACHE
    if _ICEA_LOGO_CACHE is not None:
        return _ICEA_LOGO_CACHE
    try:
        if os.path.isfile(_ICEA_LOGO_PATH):
            _ICEA_LOGO_CACHE = Image.open(_ICEA_LOGO_PATH).convert("RGBA")
        else:
            _ICEA_LOGO_CACHE = False
    except Exception as e:
        print(f"⚠️ 無法載入國際總會標誌：{e}")
        _ICEA_LOGO_CACHE = False
    return _ICEA_LOGO_CACHE


def _render_schedule_image(
    meeting_type: str,
    meeting_no: int,
    proposals: list,
    settings: dict,
    meeting_date: str = "",
) -> bytes:
    """Render the meeting schedule notification image using Pillow.

    v3 — high-resolution redesign:
    - 1200px wide canvas (up from 800) for crisp display on retina/mobile screens
    - ICEA emblem (bundled logo asset) in the header, properly spaced from title/subtitle
    - No emoji glyphs anywhere (our CJK font has no color-emoji table -> they rendered as
      tofu boxes). All icon-like accents are now plain vector shapes (dots/bars) instead.
    - Header title/subtitle vertical positions are computed from measured text bbox
      heights instead of hardcoded offsets, which is what caused the overlap bug.
    - Organisation name corrected to 國際總會 ICEA.

    Returns PNG bytes.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError("Pillow 未安裝，無法渲染排程圖")

    # ── Layout constants ──
    IMG_W = 1200
    MARGIN = 36

    # ── Color palette (Discord dark theme) ──
    BG_COLOR = (32, 33, 36)
    CARD_COLOR = (49, 51, 56)
    CARD_ALT = (43, 45, 49)
    HEADER_GRADIENT_START = (88, 101, 242)    # #5865f2
    HEADER_GRADIENT_END = (118, 75, 185)      # #764bb9
    TEXT_PRIMARY = (255, 255, 255)
    TEXT_SECONDARY = (185, 187, 190)
    TEXT_MUTED = (120, 124, 130)
    DIVIDER_COLOR = (65, 68, 73)

    BADGE_COLORS = {
        "升格案": (87, 181, 96),
        "選舉案": (88, 101, 242),
        "政策提案": (250, 168, 50),
        "入盟案": (235, 69, 158),
        "罷免案": (237, 66, 69),
        "修憲案": (155, 89, 182),
        "其他": (100, 100, 110),
    }

    TIMELINE_COLORS = [
        (87, 181, 96),
        (250, 168, 50),
        (88, 101, 242),
        (235, 69, 158),
        (237, 66, 69),
    ]

    if meeting_date:
        date_str = meeting_date
        weekday_str = ""
    else:
        today = datetime.now(GMT8)
        date_str = today.strftime("%Y年%m月%d日")
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        weekday_str = f"星期{weekdays[today.weekday()]}"

    # ── Fonts (sized up ~1.5x vs the previous 800px-wide version for sharper rendering) ──
    font_huge = _load_font(44, bold=True)
    font_subtitle = _load_font(22)
    font_section = _load_font(25, bold=True)
    font_body = _load_font(21)
    font_body_bold = _load_font(21, bold=True)
    font_small = _load_font(18)
    font_badge = _load_font(17, bold=True)
    font_time = _load_font(21, bold=True)
    font_time_label = _load_font(19)
    font_footer = _load_font(16)

    temp_img = Image.new("RGB", (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)

    # ── Header logo sizing ──
    logo_src = _load_icea_logo()
    logo_h = 84
    logo_w = 0
    logo_resized = None
    if logo_src:
        ratio = logo_src.width / logo_src.height
        logo_w = int(logo_h * ratio)
        logo_resized = logo_src.resize((logo_w, logo_h), Image.LANCZOS)

    # ── Measure header title/subtitle to compute a non-overlapping layout ──
    title_text = f"{meeting_type}第{meeting_no}次"
    subtitle_text = "會議排程通知"
    title_w, title_h, title_yoff = _text_size(temp_draw, title_text, font_huge)
    sub_w, sub_h, sub_yoff = _text_size(temp_draw, subtitle_text, font_subtitle)

    TITLE_SUB_GAP = 10
    text_block_h = title_h + TITLE_SUB_GAP + sub_h
    HEADER_H = max(logo_h, text_block_h) + 56  # generous top+bottom padding

    # ── Time schedule data ──
    checkin_start = settings.get("checkin_start", "13:00")
    checkin_end = settings.get("checkin_end", "21:00")
    review_time = settings.get("review_time", "15:00")
    motion_time = settings.get("motion_time", "20:00")
    vote_time = settings.get("vote_time", "21:00")

    schedule_items = [
        ("簽到時間", f"{checkin_start} — {checkin_end}", TIMELINE_COLORS[0]),
        ("提案審理", f"{review_time}", TIMELINE_COLORS[1]),
        ("臨時動議", f"{motion_time}", TIMELINE_COLORS[2]),
        ("投票結算", f"{vote_time}", TIMELINE_COLORS[3]),
        ("散會公告", f"{vote_time}", TIMELINE_COLORS[4]),
    ]

    # ── Pre-calculate proposal cards ──
    proposal_cards = []
    for p in proposals:
        ptype = p.get("type", "其他")
        ptext = p.get("text", "")
        proposer = p.get("proposer_name", "")
        badge_color = BADGE_COLORS.get(ptype, BADGE_COLORS["其他"])

        max_text_w = IMG_W - MARGIN * 2 - 40
        lines = []
        current_line = ""
        for ch in ptext:
            test = current_line + ch
            tw, th, _ = _text_size(temp_draw, test, font_body)
            if tw > max_text_w and current_line:
                lines.append(current_line)
                current_line = ch
            else:
                current_line = test
        if current_line:
            lines.append(current_line)

        card_h = max(70, len(lines) * 30 + 46 + (26 if proposer else 0))
        proposal_cards.append({
            "type": ptype,
            "text_lines": lines,
            "proposer": proposer,
            "badge_color": badge_color,
            "card_h": card_h,
        })

    # ── Calculate total image height ──
    DATE_PILL_H = 0
    dw, dh, _ = _text_size(temp_draw, date_str, font_subtitle)
    DATE_PILL_H = dh + 24

    SECTION_TITLE_H = 44
    SECTION_GAP = 16
    timeline_h = 5 * 52 + 24
    proposals_h = sum(c["card_h"] + 12 for c in proposal_cards) + 16 if proposal_cards else 70
    notes_h = 3 * 32 + 24
    FOOTER_H = 50

    img_h = (
        HEADER_H
        + 24
        + DATE_PILL_H
        + SECTION_GAP
        + SECTION_TITLE_H + timeline_h
        + SECTION_GAP
        + SECTION_TITLE_H + proposals_h
        + SECTION_GAP
        + SECTION_TITLE_H + notes_h
        + 16
        + FOOTER_H
        + 24
    )
    img_h = max(img_h, 560)

    # ── Create image ──
    img = Image.new("RGB", (IMG_W, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ═══════════════════════════════════════════════
    # HEADER — gradient bar, logo + title/subtitle stack (measured, non-overlapping)
    # ═══════════════════════════════════════════════
    _draw_gradient_bar(draw, (0, 0, IMG_W, HEADER_H), HEADER_GRADIENT_START, HEADER_GRADIENT_END, "horizontal")

    text_block_y0 = (HEADER_H - text_block_h) // 2
    text_x0 = MARGIN

    if logo_resized:
        logo_y = (HEADER_H - logo_h) // 2
        img.paste(logo_resized, (MARGIN, logo_y), logo_resized)
        text_x0 = MARGIN + logo_w + 28

    # draw.text needs y + y_off so the glyph visual top lands exactly at the
    # y we computed (this offset mismatch is what caused the old overlap bug).
    draw.text((text_x0, text_block_y0 + title_yoff), title_text, fill=TEXT_PRIMARY, font=font_huge)
    subtitle_y = text_block_y0 + title_h + TITLE_SUB_GAP
    draw.text((text_x0, subtitle_y + sub_yoff), subtitle_text, fill=(214, 217, 252), font=font_subtitle)

    y = HEADER_H + 24

    # ═══════════════════════════════════════════════
    # DATE ROW — date pill (plain text, no emoji — avoids missing-glyph tofu boxes)
    # ═══════════════════════════════════════════════
    date_text = f"{date_str}　{weekday_str}" if weekday_str else date_str
    dw, dh, _ = _text_size(draw, date_text, font_subtitle)
    pill_w = dw + 36
    pill_h = dh + 22
    pill_x = (IMG_W - pill_w) / 2
    draw.rounded_rectangle([pill_x, y, pill_x + pill_w, y + pill_h], radius=12, fill=CARD_ALT)
    # anchor="mm" centers on real font metrics — same fix as _draw_badge, avoids
    # the manual-offset mismatch that squeezed text against the pill edge.
    draw.text((pill_x + pill_w / 2, y + pill_h / 2), date_text, fill=TEXT_SECONDARY, font=font_subtitle, anchor="mm")
    y += pill_h + 20

    # ═══════════════════════════════════════════════
    # SECTION: 會議時間表
    # ═══════════════════════════════════════════════
    y += SECTION_GAP
    draw.rounded_rectangle([MARGIN, y + 4, MARGIN + 6, y + 30], radius=3, fill=HEADER_GRADIENT_START)
    draw.text((MARGIN + 18, y), "會議時間表", fill=TEXT_PRIMARY, font=font_section)
    y += SECTION_TITLE_H

    card_y0 = y
    card_y1 = y + timeline_h
    _draw_rounded_card(img, (MARGIN, card_y0, IMG_W - MARGIN, card_y1), radius=16, fill=CARD_COLOR, border=DIVIDER_COLOR, border_width=1)
    inner_x = MARGIN + 30

    ty = card_y0 + 22
    for i, (label, time_val, color) in enumerate(schedule_items):
        dot_x = inner_x + 12
        dot_y = ty + 12
        draw.ellipse([dot_x - 7, dot_y - 7, dot_x + 7, dot_y + 7], fill=color)

        if i < len(schedule_items) - 1:
            draw.line([(dot_x, dot_y + 9), (dot_x, dot_y + 43)], fill=DIVIDER_COLOR, width=2)

        draw.text((dot_x + 26, ty), time_val, fill=color, font=font_time)
        draw.text((dot_x + 26 + 190, ty + 2), label, fill=TEXT_SECONDARY, font=font_time_label)

        ty += 52

    y = card_y1 + SECTION_GAP

    # ═══════════════════════════════════════════════
    # SECTION: 本次議案清單
    # ═══════════════════════════════════════════════
    draw.rounded_rectangle([MARGIN, y + 4, MARGIN + 6, y + 30], radius=3, fill=HEADER_GRADIENT_START)
    draw.text((MARGIN + 18, y), "本次議案清單", fill=TEXT_PRIMARY, font=font_section)
    y += SECTION_TITLE_H

    if proposal_cards:
        total_prop_h = sum(c["card_h"] + 12 for c in proposal_cards) + 8
        card_y0 = y
        card_y1 = y + total_prop_h
        _draw_rounded_card(img, (MARGIN, card_y0, IMG_W - MARGIN, card_y1), radius=16, fill=CARD_COLOR, border=DIVIDER_COLOR, border_width=1)

        py = card_y0 + 16
        for idx, pc in enumerate(proposal_cards):
            badge_y = py + 2
            _, badge_bottom = _draw_badge(draw, (MARGIN + 28, badge_y), pc["type"], font_badge, pc["badge_color"], (255, 255, 255), 10, 5)

            text_y = badge_bottom + 10
            for line in pc["text_lines"]:
                draw.text((MARGIN + 28, text_y), line, fill=TEXT_PRIMARY, font=font_body)
                text_y += 30

            if pc["proposer"]:
                draw.text((MARGIN + 28, text_y), f"提案人：{pc['proposer']}", fill=TEXT_MUTED, font=font_small)
                text_y += 26

            py = text_y + 12

            if idx < len(proposal_cards) - 1:
                draw.line([(MARGIN + 28, py), (IMG_W - MARGIN - 28, py)], fill=DIVIDER_COLOR, width=1)
                py += 10

        y = card_y1 + SECTION_GAP
    else:
        card_y0 = y
        card_y1 = y + 70
        _draw_rounded_card(img, (MARGIN, card_y0, IMG_W - MARGIN, card_y1), radius=16, fill=CARD_COLOR, border=DIVIDER_COLOR, border_width=1)
        draw.text((MARGIN + 28, y + 24), "本次無待審議案", fill=TEXT_MUTED, font=font_body)
        y = card_y1 + SECTION_GAP

    # ═══════════════════════════════════════════════
    # SECTION: 注意事項
    # ═══════════════════════════════════════════════
    draw.rounded_rectangle([MARGIN, y + 4, MARGIN + 6, y + 30], radius=3, fill=HEADER_GRADIENT_END)
    draw.text((MARGIN + 18, y), "注意事項", fill=TEXT_PRIMARY, font=font_section)
    y += SECTION_TITLE_H

    notes = [
        ("請各會員國代表準時簽到並參與表決", (87, 181, 96)),
        ("提案審理期間歡迎各國代表發表意見", (250, 168, 50)),
    ]

    notes_card_h = len(notes) * 32 + 24
    card_y0 = y
    card_y1 = y + notes_card_h
    _draw_rounded_card(img, (MARGIN, card_y0, IMG_W - MARGIN, card_y1), radius=16, fill=CARD_COLOR, border=DIVIDER_COLOR, border_width=1)

    ny = card_y0 + 16
    for note_text, dot_color in notes:
        draw.ellipse([MARGIN + 28, ny + 8, MARGIN + 28 + 8, ny + 16], fill=dot_color)
        draw.text((MARGIN + 50, ny), note_text, fill=TEXT_SECONDARY, font=font_body)
        ny += 32

    y = card_y1 + 20

    # ═══════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════
    draw.line([(MARGIN, y), (IMG_W - MARGIN, y)], fill=DIVIDER_COLOR, width=1)
    y += 16

    footer_text = f"國際總會 ICEA　|　{date_str}"
    fw, fh, fy_off = _text_size(draw, footer_text, font_footer)
    draw.text(((IMG_W - fw) / 2, y + fy_off), footer_text, fill=TEXT_MUTED, font=font_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Schedule confirm/send button ──
class ScheduleSendView(discord.ui.View):
    """Button view shown alongside the schedule preview image.
    Secretariat clicks '發送' to push the image to the target channel."""

    def __init__(self, schedule_id: str):
        super().__init__(timeout=600)
        self.schedule_id = schedule_id

    @discord.ui.button(label="📤 發送排程通知", style=discord.ButtonStyle.success, custom_id="schedule_send")
    async def send_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此操作僅限管理員。", ephemeral=True)
            return
        
        sched = _pending_schedules.get(self.schedule_id)
        if not sched:
            await interaction.response.send_message("❌ 找不到排程資料（可能已過期，請重新 /schedule generate）。", ephemeral=True)
            return
        
        target_ch_id = sched.get("target_channel_id")
        mention_role_id = sched.get("mention_role_id")
        
        # Find target channel
        target_ch = None
        for g in bot.guilds:
            ch = g.get_channel(int(target_ch_id)) if target_ch_id else None
            if ch:
                target_ch = ch
                break
        
        if not target_ch:
            await interaction.response.send_message("❌ 找不到目標發送頻道，請至 Dashboard 檢查設定。", ephemeral=True)
            return
        
        # Send the image + mention
        png_bytes = sched.get("png")
        if not png_bytes:
            await interaction.response.send_message("❌ 排程圖資料遺失，請重新 /schedule generate。", ephemeral=True)
            return
        
        content = ""
        if mention_role_id:
            content = f"<@&{mention_role_id}>"
        
        _meeting_date = sched.get("meeting_date", "")
        _date_display = _meeting_date if _meeting_date else datetime.now(GMT8).strftime("%Y年%m月%d日")
        embed = discord.Embed(
            title=f"📢 {sched.get('meeting_type', '會議')}第{sched.get('meeting_no', '?')}次 — 會議排程通知",
            description=(
                f"📅 **{_date_display}**\n"
                f"請各會員國代表留意會議時間表及待審議案，準時出席。"
            ),
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url="attachment://schedule.png")
        embed.set_footer(text="國際總會 ICEA | 會議排程自動通知系統")
        
        try:
            await target_ch.send(
                content=content,
                embed=embed,
                file=discord.File(io.BytesIO(png_bytes), filename="schedule.png"),
            )
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ 無權限在頻道 #{target_ch.name} 發送訊息。", ephemeral=True)
            return
        except Exception as e:
            await interaction.response.send_message(f"❌ 發送失敗：{e}", ephemeral=True)
            return
        
        # ── Proposals are NOT auto-removed after send ──
        # Use /schedule clear_proposals to manually mark proposals as scheduled.
        
        # ── Increment meeting number ──
        if sched.get("meeting_type") == "例行會議":
            schedule_settings["regular_meeting_no"] += 1
        else:
            schedule_settings["briefing_meeting_no"] += 1
        save_schedule_settings()
        
        # ── Clear pending schedule ──
        del _pending_schedules[self.schedule_id]
        
        # Update the confirmation message
        try:
            await interaction.response.edit_message(
                content=f"✅ 排程通知已成功發送至 #{target_ch.name}" + (f" 並 @ 了身分組" if mention_role_id else "") + "\n💡 提案存檔已保留。確認無誤後可用 `/schedule clear_proposals` 清除。",
                embed=None,
                view=None,
                attachments=[],
            )
        except Exception:
            try:
                await interaction.followup.send(f"✅ 排程通知已成功發送至 #{target_ch.name}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="✏️ 編輯場次資訊", style=discord.ButtonStyle.secondary, custom_id="schedule_edit")
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此操作僅限管理員。", ephemeral=True)
            return

        sched = _pending_schedules.get(self.schedule_id)
        if not sched:
            await interaction.response.send_message("❌ 找不到排程資料（可能已過期，請重新 /schedule generate）。", ephemeral=True)
            return

        await interaction.response.send_modal(ScheduleEditModal(self.schedule_id, sched, interaction.message))

    @discord.ui.button(label="📋 增刪議案", style=discord.ButtonStyle.secondary, custom_id="schedule_proposals")
    async def proposals_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此操作僅限管理員。", ephemeral=True)
            return

        sched = _pending_schedules.get(self.schedule_id)
        if not sched:
            await interaction.response.send_message("❌ 找不到排程資料（可能已過期，請重新 /schedule generate）。", ephemeral=True)
            return

        all_accepted = [p for p in _proposals.get("entries", []) if p.get("status") == "accepted"]
        if not all_accepted:
            await interaction.response.send_message("ℹ️ 目前沒有可選擇的已受理提案。", ephemeral=True)
            return

        current_ids = set(sched.get("proposal_ids", []))
        options = []
        for p in all_accepted:
            pid = p.get("id", "")
            ptype = p.get("proposal_type", "?")
            summary = p.get("summary", "")[:40]
            label = f"[{ptype}] {summary}"
            if len(label) > 100:
                label = label[:97] + "..."
            included = pid in current_ids
            desc_text = f"提案人：{p.get('proposer_name', '?')}" + (" | ✓ 目前已選" if included else "")
            options.append(discord.SelectOption(label=label, value=pid, description=desc_text[:100]))

        view = ScheduleProposalSelectView(self.schedule_id, options)
        await interaction.response.send_message(
            "請選擇要納入排程圖的提案（已選取的會保留，未選取的會移除）：",
            view=view,
            ephemeral=True,
        )


class ScheduleProposalSelectView(discord.ui.View):
    """Select-menu view for adding/removing proposals from the schedule."""

    def __init__(self, schedule_id: str, options: list):
        super().__init__(timeout=120)
        self.schedule_id = schedule_id
        select = discord.ui.Select(
            placeholder="選擇要納入排程的提案...",
            options=options[:25],
            min_values=0,
            max_values=len(options[:25]),
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        sched = _pending_schedules.get(self.schedule_id)
        if not sched:
            await interaction.response.edit_message(content="❌ 排程資料已過期，請重新 /schedule generate。", view=None)
            return

        selected_ids = set(interaction.data.get("values", []))
        if not selected_ids:
            await interaction.response.edit_message(content="ℹ️ 未選擇任何提案，排程圖保持不變。", view=None)
            return

        await interaction.response.edit_message(content="⏳ 正在重新整理提案並渲染排程圖...", view=None)

        all_accepted = [p for p in _proposals.get("entries", []) if p.get("status") == "accepted"]
        selected_entries = [p for p in all_accepted if p.get("id") in selected_ids]

        if not selected_entries:
            await interaction.followup.send("❌ 找不到對應的提案資料。", ephemeral=True)
            return

        try:
            summarized = await _ai_summarize_for_schedule(selected_entries)
        except Exception:
            summarized = [{"type": p.get("proposal_type", "?"), "text": p.get("summary", "")[:40], "proposer_name": p.get("proposer_name", "?")} for p in selected_entries]

        meeting_type = sched.get("meeting_type", "例行會議")
        meeting_no = sched.get("meeting_no", 1)
        meeting_date = sched.get("meeting_date", "")

        try:
            new_png = _render_schedule_image(meeting_type, meeting_no, summarized, schedule_settings, meeting_date=meeting_date)
        except Exception as e:
            await interaction.followup.send(f"❌ 重新渲染失敗：{e}", ephemeral=True)
            return

        sched["png"] = new_png
        sched["proposal_ids"] = [p.get("id") for p in selected_entries]
        sched["summarized_proposals"] = summarized

        # Update the original preview message
        target_ch_id = sched.get("target_channel_id")
        mention_role_id = sched.get("mention_role_id")
        date_display = f" | 日期：{meeting_date}" if meeting_date else ""

        new_embed = discord.Embed(
            title=f"📅 {meeting_type}第{meeting_no}次 — 排程通知預覽",
            description=(
                f"共 {len(selected_entries)} 件提案{date_display}\n"
                f"目標頻道：{'<#' + str(target_ch_id) + '>' if target_ch_id else '⚠️ 未設定'}\n"
                f"提及身分組：{'<@&' + str(mention_role_id) + '>' if mention_role_id else '無'}\n\n"
                f"可使用下方按鈕編輯場次資訊、增刪議案、或直接發送。"
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        new_embed.set_image(url="attachment://schedule_preview.png")
        new_embed.set_footer(text="確認排程 | 增刪議案已更新")

        try:
            channel = interaction.channel
            if channel:
                async for msg in channel.history(limit=20):
                    if (msg.author.id == bot.user.id
                        and msg.attachments
                        and msg.attachments[0].filename == "schedule_preview.png"):
                        await msg.edit(
                            embed=new_embed,
                            attachments=[discord.File(io.BytesIO(new_png), filename="schedule_preview.png")],
                            view=ScheduleSendView(self.schedule_id),
                        )
                        break
        except Exception as e:
            print(f"⚠️ 更新排程預覽訊息失敗：{e}")

        await interaction.followup.send(
            f"✅ 已更新排程圖，共 {len(selected_entries)} 件提案。",
            ephemeral=True,
        )


class ScheduleEditModal(discord.ui.Modal, title="編輯場次資訊"):
    """Lets an admin correct the meeting type / meeting number and re-render
    the schedule image in place, without having to re-run /schedule generate."""

    meeting_type_input = discord.ui.TextInput(
        label="會議種類",
        placeholder="例如：例行會議 / 簡務會議",
        required=True,
        max_length=20,
    )
    meeting_no_input = discord.ui.TextInput(
        label="第幾次",
        placeholder="例如：3",
        required=True,
        max_length=6,
    )
    meeting_date_input = discord.ui.TextInput(
        label="會議日期（例如：8月10日 星期一）",
        placeholder="留空則顯示今日日期",
        required=False,
        max_length=30,
    )

    def __init__(self, schedule_id: str, sched: dict, original_message: discord.Message = None):
        super().__init__()
        self.schedule_id = schedule_id
        self.original_message = original_message
        self.meeting_type_input.default = sched.get("meeting_type", "例行會議")
        self.meeting_no_input.default = str(sched.get("meeting_no", 1))
        self.meeting_date_input.default = sched.get("meeting_date", "")

    async def on_submit(self, interaction: discord.Interaction):
        sched = _pending_schedules.get(self.schedule_id)
        if not sched:
            await interaction.response.send_message("❌ 排程資料已過期，請重新 /schedule generate。", ephemeral=True)
            return

        new_type = self.meeting_type_input.value.strip() or sched.get("meeting_type", "例行會議")
        try:
            new_no = int(self.meeting_no_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ 「第幾次」必須是數字。", ephemeral=True)
            return
        new_date = self.meeting_date_input.value.strip()

        if not _PIL_AVAILABLE:
            await interaction.response.send_message("❌ Pillow 未安裝，無法重新渲染。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            new_png = _render_schedule_image(
                new_type, new_no, sched.get("summarized_proposals", []), schedule_settings,
                meeting_date=new_date,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 重新渲染失敗：{e}", ephemeral=True)
            return

        sched["meeting_type"] = new_type
        sched["meeting_no"] = new_no
        sched["meeting_date"] = new_date
        sched["png"] = new_png

        accepted_count = len(sched.get("proposal_ids", []))
        target_ch_id = sched.get("target_channel_id")
        mention_role_id = sched.get("mention_role_id")

        new_embed = discord.Embed(
            title=f"📅 {new_type}第{new_no}次 — 排程通知預覽",
            description=(
                f"共 {accepted_count} 件提案" + (f" | 日期：{new_date}" if new_date else "") + "\n"
                f"目標頻道：{'<#' + str(target_ch_id) + '>' if target_ch_id else '⚠️ 未設定'}\n"
                f"提及身分組：{'<@&' + str(mention_role_id) + '>' if mention_role_id else '無'}\n\n"
                "可使用下方按鈕編輯場次資訊、增刪議案、或直接發送。\n"
                ""
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        new_embed.set_image(url="attachment://schedule_preview.png")
        new_embed.set_footer(text="確認排程 | 發送後提案不自動刪除")

        target_message = self.original_message or interaction.message
        try:
            if target_message is None:
                raise RuntimeError("找不到原始預覽訊息")
            await target_message.edit(
                embed=new_embed,
                attachments=[discord.File(io.BytesIO(new_png), filename="schedule_preview.png")],
                view=ScheduleSendView(self.schedule_id),
            )
            await interaction.followup.send(f"✅ 已更新為「{new_type}第{new_no}次」", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ 已重新渲染，但更新預覽訊息失敗：{e}", ephemeral=True)


class ScheduleGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="schedule", description="會議排程通知系統")

    @app_commands.command(name="generate", description="生成會議排程通知圖（管理員限定）")
    @app_commands.describe(
        meeting_type="會議種類",
        meeting_date="會議日期（例如：8月10日 星期一）",
    )
    @app_commands.choices(meeting_type=[
        app_commands.Choice(name="例行會議", value="例行會議"),
        app_commands.Choice(name="簡務會議", value="簡務會議"),
    ])
    async def generate(self, interaction: discord.Interaction, meeting_type: str = "例行會議", meeting_date: str = ""):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        
        if not _PIL_AVAILABLE:
            await interaction.response.send_message("❌ Pillow 未安裝，無法渲染排程圖。請聯繫管理員安裝 Pillow。", ephemeral=True)
            return
        
        await interaction.response.defer(thinking=True)
        
        # Get all accepted proposals that haven't been scheduled yet
        accepted = [
            p for p in _proposals.get("entries", [])
            if p.get("status") == "accepted"
        ]
        
        if not accepted:
            await interaction.followup.send("ℹ️ 目前沒有已受理待排程的提案。先在提案區受理提案後再執行此指令。", ephemeral=True)
            return
        
        # AI summarize
        summarized = await _ai_summarize_for_schedule(accepted)
        
        # Determine meeting number
        if meeting_type == "例行會議":
            meeting_no = schedule_settings.get("regular_meeting_no", 1)
        else:
            meeting_no = schedule_settings.get("briefing_meeting_no", 1)
        
        # Render image
        try:
            png_bytes = _render_schedule_image(meeting_type, meeting_no, summarized, schedule_settings, meeting_date=meeting_date)
        except Exception as e:
            await interaction.followup.send(f"❌ 排程圖渲染失敗：{e}", ephemeral=True)
            return
        
        # Determine review channel (fallback to proposal secretariat channel)
        review_ch_id = schedule_settings.get("review_channel_id") or proposal_settings.get("secretariat_channel")
        target_ch_id = schedule_settings.get("target_channel_id")
        mention_role_id = schedule_settings.get("mention_role_id")
        
        # Store pending schedule
        schedule_id = str(int(_time.time() * 1000))
        _pending_schedules[schedule_id] = {
            "png": png_bytes,
            "meeting_type": meeting_type,
            "meeting_no": meeting_no,
            "meeting_date": meeting_date,
            "proposal_ids": [p.get("id") for p in accepted],
            "summarized_proposals": summarized,
            "target_channel_id": target_ch_id,
            "mention_role_id": mention_role_id,
            "created_at": _time.time(),
        }
        
        # Build preview embed
        date_display = f" | 日期：{meeting_date}" if meeting_date else ""
        preview_embed = discord.Embed(
            title=f"📅 {meeting_type}第{meeting_no}次 — 排程通知預覽",
            description=(
                f"共 {len(accepted)} 件已受理提案" + date_display + "\n"
                f"目標頻道：{'<#' + str(target_ch_id) + '>' if target_ch_id else '⚠️ 未設定'}\n"
                f"提及身分組：{'<@&' + str(mention_role_id) + '>' if mention_role_id else '無'}\n\n"
                "可使用下方按鈕編輯場次資訊、增刪議案、或直接發送。\n"
                ""
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        preview_embed.set_image(url="attachment://schedule_preview.png")
        preview_embed.set_footer(text="確認排程 | 發送後提案不自動刪除")
        
        view = ScheduleSendView(schedule_id)
        
        try:
            await interaction.followup.send(
                embed=preview_embed,
                file=discord.File(io.BytesIO(png_bytes), filename="schedule_preview.png"),
                view=view,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 預覽發送失敗：{e}", ephemeral=True)
            return
        
        print(f"📅 排程預覽已生成：{meeting_type}#{meeting_no}，{len(accepted)} 件提案")

    @app_commands.command(name="set_target", description="設定排程通知發送頻道（管理員限定）")
    @app_commands.describe(channel="排程圖最終發送的頻道")
    async def set_target(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        schedule_settings["target_channel_id"] = channel.id
        save_schedule_settings()
        await interaction.response.send_message(f"✅ 排程通知發送頻道已設為 #{channel.name}", ephemeral=True)

    @app_commands.command(name="set_mention", description="設定排程通知 @ 的身分組（管理員限定）")
    @app_commands.describe(role="發送排程通知時 @ 提及的身分組")
    async def set_mention(self, interaction: discord.Interaction, role: discord.Role):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        schedule_settings["mention_role_id"] = role.id
        save_schedule_settings()
        await interaction.response.send_message(f"✅ 排程通知提及身分組已設為 {role.mention}", ephemeral=True)

    @app_commands.command(name="clear_proposals", description="清除已排程的提案存檔（管理員限定）")
    @app_commands.describe(action="清除方式")
    @app_commands.choices(action=[
        app_commands.Choice(name="標記為已排程（保留記錄，下次不再列出）", value="mark_scheduled"),
        app_commands.Choice(name="徹底刪除所有已受理提案", value="delete_all"),
    ])
    async def clear_proposals(self, interaction: discord.Interaction, action: str = "mark_scheduled"):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        accepted = [p for p in _proposals.get("entries", []) if p.get("status") == "accepted"]
        if not accepted:
            await interaction.response.send_message("ℹ️ 目前沒有已受理的提案需要清除。", ephemeral=True)
            return

        count = len(accepted)
        if action == "delete_all":
            _proposals["entries"] = [p for p in _proposals.get("entries", []) if p.get("status") != "accepted"]
            save_proposals()
            await interaction.response.send_message(f"✅ 已徹底刪除 {count} 筆已受理提案。", ephemeral=True)
        else:
            for p in _proposals.get("entries", []):
                if p.get("status") == "accepted":
                    p["status"] = "scheduled"
                    p["schedule_date"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M")
            save_proposals()
            await interaction.response.send_message(f"✅ 已將 {count} 筆提案標記為已排程，下次 /schedule generate 不會再列出。", ephemeral=True)


    @app_commands.command(name="set_review", description="設定排程預覽確認頻道（管理員限定）")
    @app_commands.describe(channel="秘書處確認排程圖的頻道")
    async def set_review(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        schedule_settings["review_channel_id"] = channel.id
        save_schedule_settings()
        await interaction.response.send_message(f"✅ 排程預覽確認頻道已設為 #{channel.name}", ephemeral=True)

    @app_commands.command(name="set_time", description="設定會議時間表（管理員限定）")
    @app_commands.describe(
        checkin_start="簽到開始 HH:MM",
        checkin_end="簽到結束 HH:MM",
        review_time="提案審理時間 HH:MM",
        motion_time="臨時動議時間 HH:MM",
        vote_time="投票結算時間 HH:MM",
    )
    async def set_time(self, interaction: discord.Interaction,
                       checkin_start: str = None,
                       checkin_end: str = None,
                       review_time: str = None,
                       motion_time: str = None,
                       vote_time: str = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if checkin_start: schedule_settings["checkin_start"] = checkin_start
        if checkin_end: schedule_settings["checkin_end"] = checkin_end
        if review_time: schedule_settings["review_time"] = review_time
        if motion_time: schedule_settings["motion_time"] = motion_time
        if vote_time: schedule_settings["vote_time"] = vote_time
        save_schedule_settings()
        await interaction.response.send_message(
            f"✅ 會議時間表已更新：\n"
            f"簽到 {schedule_settings['checkin_start']}~{schedule_settings['checkin_end']} / "
            f"審理 {schedule_settings['review_time']} / 動議 {schedule_settings['motion_time']} / "
            f"投票 {schedule_settings['vote_time']}",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="查看排程系統設定狀態")
    async def status(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        accepted = [p for p in _proposals.get("entries", []) if p.get("status") == "accepted"]
        embed = discord.Embed(title="📅 會議排程系統狀態", color=discord.Color.blue())
        embed.add_field(name="下次例行會議", value=f"第{schedule_settings.get('regular_meeting_no', 1)}次", inline=True)
        embed.add_field(name="下次簡務會議", value=f"第{schedule_settings.get('briefing_meeting_no', 1)}次", inline=True)
        embed.add_field(name="待排程提案數", value=str(len(accepted)), inline=True)
        embed.add_field(name="發送頻道", value=f"<#{schedule_settings.get('target_channel_id', 0)}>" if schedule_settings.get("target_channel_id") else "未設定", inline=True)
        embed.add_field(name="提及身分組", value=f"<@&{schedule_settings.get('mention_role_id', 0)}>" if schedule_settings.get("mention_role_id") else "未設定", inline=True)
        embed.add_field(name="確認頻道", value=f"<#{schedule_settings.get('review_channel_id', 0)}>" if schedule_settings.get("review_channel_id") else "未設定", inline=True)
        embed.add_field(name="時間表", value=f"簽到 {schedule_settings.get('checkin_start','?')}~{schedule_settings.get('checkin_end','?')}\n審理 {schedule_settings.get('review_time','?')} / 動議 {schedule_settings.get('motion_time','?')} / 投票 {schedule_settings.get('vote_time','?')}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ════════════════════════════════════════════════════════════
# 入盟申請自動回覆系統
# When a new thread/message appears in a designated application channel,
# auto-reply with confirmation, check required fields, and notify the
# secretariat channel with 審核通過/退回 buttons.
# ════════════════════════════════════════════════════════════
