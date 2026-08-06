# ═════════════════════════════════════════════════════════════════
# Module: 30_system (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

class SystemGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="system", description="系統診斷工具")

    @app_commands.command(name="drive_authorize", description="用你的 Google 帳號授權 Drive 存取（機器人擁有者限定）")
    async def drive_authorize(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        client_id = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
        if not client_id:
            await interaction.response.send_message(
                "❌ 尚未設定 OAuth Client ID 環境變數。\n"
                "請先到 Render Environment 新增 `GOOGLE_CLIENT_ID` 和 `GOOGLE_CLIENT_SECRET`（或 `OAUTH_CLIENT_ID` 和 `OAUTH_CLIENT_SECRET`），來自你的 Google Cloud OAuth 用戶端。",
                ephemeral=True
            )
            return

        base_url = os.getenv("SELF_URL") or os.getenv("RENDER_EXTERNAL_URL") or ""
        if not base_url:
            await interaction.response.send_message(
                "❌ 尚未設定 `SELF_URL` 環境變數，無法產生回調網址。",
                ephemeral=True
            )
            return

        redirect_uri = _drive_oauth_redirect_uri()
        state = _sign_drive_oauth_state(interaction.user.id)

        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={urllib.parse.quote(client_id)}"
            f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
            "&response_type=code"
            "&scope=" + urllib.parse.quote("https://www.googleapis.com/auth/drive")
            + "&access_type=offline"
            "&prompt=consent"
            f"&state={urllib.parse.quote(state)}"
        )

        embed = discord.Embed(
            title="🔑 Google Drive 個人帳號授權",
            description=(
                "**點下面連結，用你要拿來存放資料的 Google 帳號登入並同意授權：**\n"
                f"{auth_url}\n\n"
                "**⚠️ 重要：在點擊前，請先確認這個回調網址已加到 Google Cloud Console 的「已授權的重新導向 URI」：**\n"
                f"`{redirect_uri}`\n\n"
                "位置：Google Cloud Console → API 和服務 → 憑證 → 你的 OAuth 用戶端 ID → 編輯\n\n"
                "授權成功後，網頁會顯示一組 `refresh_token`，把它複製到 Render 環境變數 "
                "`GOOGLE_DRIVE_REFRESH_TOKEN`。\n\n"
                "此連結 10 分鐘內有效。"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="drive_test", description="測試 Google Drive 連線並顯示詳細錯誤（機器人擁有者限定）")
    async def drive_test(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        lines = ["**🔍 Google Drive 診斷**", ""]

        # 1. Check env vars
        creds_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "")
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
        refresh_token = os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")
        g_client_id = os.getenv("GOOGLE_CLIENT_ID", "") or os.getenv("OAUTH_CLIENT_ID", "")
        g_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "") or os.getenv("OAUTH_CLIENT_SECRET", "")

        lines.append(f"**1. 環境變數**")
        lines.append(f"  認證方式：{'🟢 OAuth 個人帳號（推薦）' if refresh_token else ('🟡 服務帳號（需 Shared Drive）' if creds_b64 else '❌ 未設定任何認證')}")
        lines.append(f"  GOOGLE_DRIVE_REFRESH_TOKEN: {'✅ 已設定' if refresh_token else '❌ 未設定'}")
        lines.append(f"  GOOGLE_CLIENT_ID: {'✅ 已設定' if g_client_id else '❌ 未設定'}")
        lines.append(f"  GOOGLE_CLIENT_SECRET: {'✅ 已設定' if g_client_secret else '❌ 未設定'}")
        lines.append(f"  GOOGLE_SERVICE_ACCOUNT_B64: {'✅ 已設定 (' + str(len(creds_b64)) + ' 字元)' if creds_b64 else '❌ 未設定（備援方案）'}")
        lines.append(f"  GOOGLE_DRIVE_FOLDER_ID: {'✅ `' + folder_id + '`' if folder_id else '❌ 未設定'}")

        if not refresh_token and not creds_b64:
            lines.append("")
            lines.append("→ 尚未設定任何認證方式。建議執行 `/system drive_authorize` 用你的個人 Google 帳號授權（有真正的儲存配額）。")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        # 2. Decode and validate service account JSON (only relevant if OAuth isn't set up)
        if refresh_token:
            lines.append("")
            lines.append(f"**2. 服務帳號金鑰解析**")
            lines.append(f"  ⏭️ 已使用 OAuth 個人帳號認證，跳過服務帳號檢查。")
        elif creds_b64:
            lines.append("")
            lines.append(f"**2. 服務帳號金鑰解析**")
            try:
                creds_info = json_module.loads(base64.b64decode(creds_b64).decode())
                lines.append(f"  ✅ Base64 + JSON 解析成功")
                lines.append(f"  type: `{creds_info.get('type', '(缺少)')}`")
                lines.append(f"  client_email: `{creds_info.get('client_email', '(缺少)')}`")
                has_key = "private_key" in creds_info
                lines.append(f"  private_key: {'✅ 存在' if has_key else '❌ 缺少'}")
                if creds_info.get("type") != "service_account":
                    lines.append(f"  ⚠️ type 不是 service_account！你可能上傳了錯誤的金鑰類型（例如 OAuth 用戶端）")
            except Exception as e:
                lines.append(f"  ❌ 解析失敗：{e}")
                lines.append("")
                lines.append("→ GOOGLE_SERVICE_ACCOUNT_B64 內容有誤，請重新 base64 編碼服務帳號 JSON")
                await interaction.followup.send("\n".join(lines), ephemeral=True)
                return

        # 3. Get access token
        lines.append("")
        lines.append(f"**3. 取得存取權杖**")
        _drive_token_cache["token"] = None  # force fresh token for test
        token = await _get_drive_access_token()
        if token:
            lines.append(f"  ✅ 成功取得 token")
        else:
            lines.append(f"  ❌ 取得 token 失敗（詳細錯誤請看 Render logs）")
            lines.append("")
            lines.append("→ 常見原因：JSON 錯誤、Drive API 未啟用、服務帳號被刪除")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        # 4. Try uploading a test file
        lines.append("")
        lines.append(f"**4. 測試上傳**")
        test_content = f'{{"test": true, "time": "{datetime.now(GMT8).isoformat()}"}}'
        success, detail = await _drive_upload("_connection_test.json", test_content, return_detail=True)
        if success:
            lines.append(f"  ✅ 測試檔案上傳成功！請到 Drive 資料夾確認 `_connection_test.json`")
        else:
            lines.append(f"  ❌ 上傳失敗")
            lines.append(f"  詳細：`{detail}`")
            lines.append("")
            if "storageQuotaExceeded" in detail or "quota" in detail.lower():
                lines.append("→ 這是**服務帳號儲存配額**問題！服務帳號本身沒有 Drive 儲存空間。")
                lines.append("  解法：把資料夾改成「共用雲端硬碟」(Shared Drive)，而非個人「我的雲端硬碟」內的資料夾。")
                lines.append("  （注意：免費 Gmail 帳號可能無法建立共用雲端硬碟，需要 Google Workspace）")
            elif "404" in detail or "File not found" in detail:
                lines.append("→ 常見原因：")
                lines.append("  • GOOGLE_DRIVE_FOLDER_ID 錯誤")
                lines.append("  • 資料夾沒有共用給服務帳號 email")
            elif "403" in detail:
                lines.append("→ 常見原因：")
                lines.append("  • 資料夾沒有共用給服務帳號 email（見上方 client_email）")
                lines.append("  • 服務帳號權限不是「編輯者」")
                lines.append("  • Drive API 沒有在 Google Cloud 專案中啟用")
            else:
                lines.append("→ 請把上面「詳細」的錯誤內容回報，才能進一步排查。")

        await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)

    @app_commands.command(name="feedback", description="查看使用者的讚/倒讚評價統計（機器人擁有者限定）")
    @app_commands.describe(action="stats=統計總覽, recent=查看最近的評價")
    @app_commands.choices(action=[
        app_commands.Choice(name="統計總覽", value="stats"),
        app_commands.Choice(name="查看最近評價", value="recent"),
    ])
    async def system_feedback(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        entries = _feedback.get("entries", [])
        if not entries:
            await interaction.followup.send("📊 目前沒有任何評價資料。", ephemeral=True)
            return

        if action.value == "stats":
            likes = [e for e in entries if e.get("rating") == "like"]
            dislikes = [e for e in entries if e.get("rating") == "dislike"]
            with_image = [e for e in entries if e.get("image_url")]

            from collections import Counter
            like_reasons = Counter(e.get("reason", "?") for e in likes)
            dislike_reasons = Counter(e.get("reason", "?") for e in dislikes)

            lines = [
                f"📊 **評價統計**",
                f"👍 讚：{len(likes)}　👎 倒讚：{len(dislikes)}　📷 含附圖：{len(with_image)}",
                "",
                "**👍 讚的原因分佈：**",
            ]
            for reason, count in like_reasons.most_common():
                lines.append(f"  • {reason}：{count}")
            lines.append("")
            lines.append("**👎 倒讚的原因分佈：**")
            for reason, count in dislike_reasons.most_common():
                lines.append(f"  • {reason}：{count}")

            await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)

        elif action.value == "recent":
            recent = sorted(entries, key=lambda e: e.get("_ts", 0), reverse=True)[:10]
            lines = []
            for e in recent:
                emoji = "👍" if e.get("rating") == "like" else "👎"
                line = (
                    f"{emoji} **{e.get('user_name', '?')}** | {e.get('date', '?')}\n"
                    f"  原因：{e.get('reason', '?')}"
                )
                if e.get("custom_text"):
                    line += f"\n  補充：{e['custom_text'][:100]}"
                if e.get("image_url"):
                    line += f"\n  附圖：{e['image_url']}"
                lines.append(line)
            await interaction.followup.send(
                f"📋 **最近評價（{len(recent)} 筆）**\n\n" + "\n\n".join(lines[:10]),
                ephemeral=True,
            )

    @app_commands.command(name="corrections", description="查看/審核使用者提交的修正建議（機器人擁有者限定）")
    @app_commands.describe(action="list=列出待審核, approve=批准, reject=拒絕", entry_id="要審核的修正 ID（approve/reject 時必填）")
    @app_commands.choices(action=[
        app_commands.Choice(name="列出待審核", value="list"),
        app_commands.Choice(name="列出全部", value="all"),
        app_commands.Choice(name="批准", value="approve"),
        app_commands.Choice(name="拒絕", value="reject"),
    ])
    async def system_corrections(self, interaction: discord.Interaction, action: app_commands.Choice[str], entry_id: str = ""):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        entries = _corrections.get("entries", [])

        if action.value == "list":
            pending = [e for e in entries if e.get("validation_status") == "pending"]
            if not pending:
                await interaction.followup.send("✅ 沒有待審核的修正建議。", ephemeral=True)
                return
            lines = []
            for e in pending[:10]:
                lines.append(
                    f"**ID: {e['id']}**\n"
                    f"  使用者：{e.get('user_name', '?')}\n"
                    f"  問題：{e.get('question', '')[:80]}\n"
                    f"  修正：{e.get('correction', '')[:150]}\n"
                    f"  AI 審核：{e.get('ai_validation', '')[:80]}"
                )
            await interaction.followup.send(
                f"📝 **待審核修正（{len(pending)} 筆）**\n\n" + "\n\n".join(lines),
                ephemeral=True,
            )

        elif action.value == "all":
            if not entries:
                await interaction.followup.send("📝 目前沒有任何修正資料。", ephemeral=True)
                return
            approved = [e for e in entries if e.get("validated")]
            rejected = [e for e in entries if e.get("validation_status") == "rejected"]
            pending = [e for e in entries if e.get("validation_status") == "pending"]
            summary = (
                f"📊 **修正資料統計**\n"
                f"  總計：{len(entries)}\n"
                f"  ✅ 已批准：{len(approved)}\n"
                f"  ❌ 已拒絕：{len(rejected)}\n"
                f"  ⏳ 待審核：{len(pending)}"
            )
            await interaction.followup.send(summary, ephemeral=True)

        elif action.value == "approve":
            if not entry_id:
                await interaction.followup.send("❌ 請提供要批准的修正 ID。", ephemeral=True)
                return
            for e in entries:
                if e.get("id") == entry_id:
                    e["validated"] = True
                    e["validation_status"] = "approved"
                    e["ai_validation"] = "管理員手動批准"
                    save_corrections()
                    await interaction.followup.send(
                        f"✅ 已批准修正 ID {entry_id}。AI 之後會參考這個修正回答問題。",
                        ephemeral=True,
                    )
                    return
            await interaction.followup.send(f"❌ 找不到 ID 為 {entry_id} 的修正。", ephemeral=True)

        elif action.value == "reject":
            if not entry_id:
                await interaction.followup.send("❌ 請提供要拒絕的修正 ID。", ephemeral=True)
                return
            for e in entries:
                if e.get("id") == entry_id:
                    e["validated"] = False
                    e["validation_status"] = "rejected"
                    save_corrections()
                    await interaction.followup.send(
                        f"❌ 已拒絕修正 ID {entry_id}。AI 不會參考這個修正。",
                        ephemeral=True,
                    )
                    return
            await interaction.followup.send(f"❌ 找不到 ID 為 {entry_id} 的修正。", ephemeral=True)

    @app_commands.command(name="blacklist", description="管理用戶黑名單（機器人擁有者限定）")
    @app_commands.describe(
        action="add=加入黑名單, remove=移除, list=查看名單",
        user="要加入/移除的用戶",
        reason="加入黑名單的原因（可選）",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="加入黑名單", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="查看名單", value="list"),
    ])
    async def system_blacklist(
        self, interaction: discord.Interaction,
        action: app_commands.Choice[str],
        user: discord.User = None,
        reason: str = "",
    ):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        if action.value == "list":
            users = _blacklist.get("users", [])
            if not users:
                await interaction.response.send_message("📋 黑名單目前是空的。", ephemeral=True)
                return
            lines = []
            for u in users:
                lines.append(
                    f"• **{u.get('user_name', '?')}** (ID: {u.get('user_id', '?')})\n"
                    f"  原因：{u.get('reason', '未指定')} | 日期：{u.get('date', '?')}"
                )
            await interaction.response.send_message(
                f"📋 **黑名單（{len(users)} 人）**\n\n" + "\n\n".join(lines),
                ephemeral=True,
            )

        elif action.value == "add":
            if not user:
                await interaction.response.send_message("❌ 請指定要加入黑名單的用戶。", ephemeral=True)
                return
            if user.id == BOT_OWNER_ID:
                await interaction.response.send_message("❌ 不能將機器人擁有者加入黑名單。", ephemeral=True)
                return
            if is_blacklisted(user.id):
                await interaction.response.send_message(
                    f"⚠️ {user.display_name} 已經在黑名單中了。", ephemeral=True,
                )
                return
            entry = {
                "user_id": str(user.id),
                "user_name": user.display_name,
                "reason": reason or "未指定",
                "date": datetime.now(GMT8).strftime("%Y-%m-%d %H:%M"),
                "added_by": interaction.user.display_name,
            }
            _blacklist.setdefault("users", []).append(entry)
            save_blacklist()
            await interaction.response.send_message(
                f"🚫 已將 **{user.display_name}** 加入黑名單。\n"
                f"原因：{reason or '未指定'}\n"
                f"該用戶將無法使用機器人任何功能，AI 也會自動屏蔽其所有訊息。",
                ephemeral=True,
            )

        elif action.value == "remove":
            if not user:
                await interaction.response.send_message("❌ 請指定要移除的用戶。", ephemeral=True)
                return
            users = _blacklist.get("users", [])
            original_len = len(users)
            _blacklist["users"] = [u for u in users if str(u.get("user_id")) != str(user.id)]
            if len(_blacklist["users"]) == original_len:
                await interaction.response.send_message(
                    f"⚠️ {user.display_name} 不在黑名單中。", ephemeral=True,
                )
                return
            save_blacklist()
            await interaction.response.send_message(
                f"✅ 已將 **{user.display_name}** 從黑名單移除。", ephemeral=True,
            )

    @app_commands.command(name="forum_index", description="查看/刷新論壇貼文搜尋索引（機器人擁有者限定）")
    async def system_forum_index(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        # Hard cap so this command can NEVER leave the user staring at
        # "思考中..." forever — if indexing genuinely takes longer than 60s
        # (large server, many threads/replies), let it keep running as a
        # background task and tell the user to check back with this same
        # command shortly instead of blocking the interaction indefinitely.
        try:
            posts = await asyncio.wait_for(_refresh_forum_index(interaction.guild), timeout=60)
        except asyncio.TimeoutError:
            print("⚠️ /system forum_index 手動刷新超過 60s，轉為背景執行")
            asyncio.ensure_future(_refresh_forum_index(interaction.guild))
            await interaction.followup.send(
                "⏳ 索引的貼文/回覆數量較多，60 秒內沒跑完，已轉為背景繼續執行。"
                "大約 1-2 分鐘後再用這個指令查看結果就會是最新的。",
                ephemeral=True,
            )
            return

        forum_count = len(list(interaction.guild.forums))

        embed = discord.Embed(
            title="🗂️ 論壇貼文搜尋索引",
            description=(
                f"論壇頻道數：{forum_count}\n"
                f"已索引貼文數：{len(posts)}\n"
                f"快取有效期：每 15 分鐘自動刷新（此指令可手動立即刷新）"
            ),
            color=discord.Color.blue(),
        )
        if posts:
            sample = "\n".join(f"• 【{p['channel_name']}】{p['title']}" for p in posts[:15])
            if len(posts) > 15:
                sample += f"\n...還有 {len(posts) - 15} 篇"
            embed.add_field(name="已索引的貼文（部分）", value=sample[:1024] or "無", inline=False)
        embed.set_footer(text="這個索引讓 AI 的 search_discord 工具能找到論壇貼文內容（含 Embed），不只是純文字訊息")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="channel_index", description="查看/刷新一般頻道訊息搜尋索引（機器人擁有者限定）")
    @app_commands.describe(query="選填：直接測試搜尋這個關鍵字，看看會不會命中")
    async def system_channel_index(self, interaction: discord.Interaction, query: str = ""):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        try:
            entries = await asyncio.wait_for(_refresh_channel_index(interaction.guild), timeout=60)
        except asyncio.TimeoutError:
            print("⚠️ /system channel_index 手動刷新超過 60s，轉為背景執行")
            asyncio.ensure_future(_refresh_channel_index(interaction.guild))
            await interaction.followup.send(
                "⏳ 頻道數量較多，60 秒內沒跑完，已轉為背景繼續執行。稍後再用這個指令查看結果。",
                ephemeral=True,
            )
            return

        # Per-channel breakdown so we can see exactly which channels got
        # skipped (excluded as test/log, no permission, or no qualifying
        # messages) vs indexed, and how many messages each contributed.
        from collections import Counter
        ch_counts = Counter(e["channel_name"] for e in entries)
        all_text_channels = [ch.name for ch in interaction.guild.text_channels]
        cached = _channel_index_cache.get(interaction.guild.id, {})
        skip_reasons = cached.get("skip_reasons", {})
        excluded_as_test_log = cached.get("excluded_channels", [])

        embed = discord.Embed(
            title="📢 頻道訊息搜尋索引",
            description=(
                f"伺服器文字頻道總數：{len(all_text_channels)}\n"
                f"已索引訊息數：{len(entries)}\n"
                f"快取有效期：每 30 分鐘自動刷新（此指令可手動立即刷新）"
            ),
            color=discord.Color.blue(),
        )
        if ch_counts:
            breakdown = "\n".join(f"• #{name}：{count} 則" for name, count in ch_counts.most_common(20))
            embed.add_field(name="已索引頻道（訊息數，前20）", value=breakdown[:1024] or "無", inline=False)
        if excluded_as_test_log:
            embed.add_field(
                name="🚫 被判定為測試/紀錄頻道而排除（不索引）",
                value=", ".join(f"#{n}" for n in excluded_as_test_log)[:1024],
                inline=False,
            )
        if skip_reasons:
            reason_lines = "\n".join(f"• #{name}：{reason}" for name, reason in list(skip_reasons.items())[:15])
            embed.add_field(
                name="⚠️ 有讀取但沒有索引到任何訊息的頻道（含原因）",
                value=reason_lines[:1024] or "無",
                inline=False,
            )

        if query.strip():
            matched = _search_channel_index(query.strip(), entries, top_n=5)
            if matched:
                preview = "\n".join(
                    f"• #{m['channel_name']} | {m['author']} ({m['date']}): {m['text'][:120]}"
                    for m in matched
                )
                embed.add_field(name=f"🔍 搜尋「{query}」的結果（{len(matched)} 則）", value=preview[:1024], inline=False)
            else:
                embed.add_field(name=f"🔍 搜尋「{query}」的結果", value="沒有命中任何已索引的訊息", inline=False)

        embed.set_footer(text="這個索引讓 AI 的 search_discord 工具能搜到一般頻道的公告/訊息（含 Embed）")
        await interaction.followup.send(embed=embed, ephemeral=True)


    # ── 提案系統指令 ──
    @app_commands.command(name="proposal_toggle", description="開啟/關閉提案區 AI 自動受理系統（機器人擁有者限定）")
    async def proposal_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        proposal_settings["enabled"] = not proposal_settings.get("enabled", False)
        save_proposal_settings()
        status = "啟用" if proposal_settings["enabled"] else "停用"
        await interaction.response.send_message(f"📋 提案系統已{status}。", ephemeral=True)

    @app_commands.command(name="proposal_channel", description="新增/移除提案區頻道（機器人擁有者限定，文字頻道或論壇頻道皆可）")
    @app_commands.describe(action="add=新增頻道, remove=移除頻道, list=列出所有頻道", channel="要新增/移除的頻道（支援文字頻道與論壇頻道）")
    async def proposal_channel(self, interaction: discord.Interaction,
                               action: str,
                               channel: Union[discord.TextChannel, discord.ForumChannel] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if action == "list":
            channels = proposal_settings.get("proposal_channels", [])
            if not channels:
                await interaction.response.send_message("📋 目前沒有設定任何提案區頻道。", ephemeral=True)
                return
            lines = [f"• <#{cid}> (`{cid}`)" for cid in channels]
            await interaction.response.send_message(f"📋 **提案區頻道列表（{len(channels)} 個）**\n" + "\n".join(lines), ephemeral=True)
            return
        if not channel:
            await interaction.response.send_message("❌ 請指定一個頻道。", ephemeral=True)
            return
        channels = proposal_settings.get("proposal_channels", [])
        if action == "add":
            if channel.id not in channels:
                channels.append(channel.id)
                proposal_settings["proposal_channels"] = channels
                save_proposal_settings()
                await interaction.response.send_message(f"✅ 已新增 #{channel.name} 為提案區頻道。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 已經是提案區頻道。", ephemeral=True)
        elif action == "remove":
            if channel.id in channels:
                channels.remove(channel.id)
                proposal_settings["proposal_channels"] = channels
                save_proposal_settings()
                await interaction.response.send_message(f"✅ 已移除 #{channel.name} 的提案區頻道設定。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 不在提案區頻道列表中。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ action 只能是 add、remove 或 list。", ephemeral=True)

    @app_commands.command(name="proposal_secretariat", description="設定秘書處通知頻道（機器人擁有者限定）")
    @app_commands.describe(channel="秘書處頻道（AI 會在此發送提案通知供管理員受理/駁回）")
    async def proposal_secretariat(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        proposal_settings["secretariat_channel"] = channel.id
        save_proposal_settings()
        await interaction.response.send_message(f"✅ 秘書處通知頻道已設為 #{channel.name}。", ephemeral=True)

    @app_commands.command(name="proposal_status", description="查看提案系統目前設定狀態（機器人擁有者限定）")
    async def proposal_status(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        enabled = proposal_settings.get("enabled", False)
        channels = proposal_settings.get("proposal_channels", [])
        sec_id = proposal_settings.get("secretariat_channel")
        ai_settings = proposal_settings.get("ai_settings", {})
        has_own_ai = bool(ai_settings.get("api_url") and ai_settings.get("api_key"))

        lines = [f"📋 **提案系統狀態**", ""]
        lines.append(f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用（用 /system proposal_toggle 開啟）'}")
        if channels:
            ch_list = "\n".join(f"  • <#{cid}> (`{cid}`)" for cid in channels)
            lines.append(f"提案區頻道（{len(channels)} 個）：\n{ch_list}")
        else:
            lines.append("提案區頻道：❌ 尚未設定任何頻道")
        if sec_id:
            lines.append(f"秘書處通知頻道：<#{sec_id}> (`{sec_id}`)")
        else:
            lines.append("秘書處通知頻道：❌ 尚未設定（用 /system proposal_secretariat 設定）")
        lines.append(f"AI 分析設定：{'使用專屬設定' if has_own_ai else '沿用 /chat 的 AI 設定'} "
                     f"（{'✅ 已就緒' if (has_own_ai or (chat_ai_settings.get('api_url') and chat_ai_settings.get('api_key'))) else '⚠️ 未設定 API，將使用關鍵字啟發式分析'}）")
        lines.append("")
        lines.append(f"已收錄提案總數：{len(_proposals.get('entries', []))} 筆")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="proposal_list", description="查看提案記錄（機器人擁有者限定）")
    @app_commands.describe(status="篩選狀態：pending=待審, accepted=已受理, rejected=已駁回, all=全部")
    async def proposal_list(self, interaction: discord.Interaction, status: str = "all"):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        entries = _proposals.get("entries", [])
        if status != "all":
            entries = [e for e in entries if e.get("status") == status]
        if not entries:
            await interaction.followup.send("📋 沒有符合條件的提案記錄。", ephemeral=True)
            return
        recent = sorted(entries, key=lambda e: e.get("_ts", 0), reverse=True)[:15]
        lines = []
        for e in recent:
            emoji = {"pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(e.get("status", ""), "?")
            line = (
                f"{emoji} **{e.get('proposal_type', '?')}** | {e.get('proposer_name', '?')} | {e.get('date', '?')}\n"
                f"  摘要：{e.get('summary', '')[:80]}\n"
                f"  狀態：{e.get('status', '?')} | ID: `{e.get('id', '')}`"
            )
            if e.get("reject_reason"):
                line += f"\n  駁回原因：{e['reject_reason'][:80]}"
            lines.append(line)
        await interaction.followup.send(
            f"📋 **提案記錄（{len(recent)}/{len(entries)} 筆）**\n\n" + "\n\n".join(lines),
            ephemeral=True,
        )


    # ── 入盟申請系統指令 ──
    @app_commands.command(name="application_toggle", description="開啟/關閉入盟申請自動回覆系統（機器人擁有者限定）")
    async def application_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        application_settings["enabled"] = not application_settings.get("enabled", False)
        save_application_settings()
        status = "啟用" if application_settings["enabled"] else "停用"
        await interaction.response.send_message(f"📝 入盟申請系統已{status}。", ephemeral=True)

    @app_commands.command(name="application_channel", description="新增/移除入盟申請區頻道（機器人擁有者限定）")
    @app_commands.describe(action="add=新增頻道, remove=移除頻道, list=列出所有頻道", channel="要新增/移除的頻道（支援文字頻道與論壇頻道）")
    async def application_channel(self, interaction: discord.Interaction,
                                  action: str,
                                  channel: Union[discord.TextChannel, discord.ForumChannel] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if action == "list":
            channels = application_settings.get("application_channels", [])
            if not channels:
                await interaction.response.send_message("📝 目前沒有設定任何入盟申請區頻道。", ephemeral=True)
                return
            lines = [f"• <#{cid}> (`{cid}`)" for cid in channels]
            await interaction.response.send_message(f"📝 **入盟申請區頻道列表（{len(channels)} 個）**\n" + "\n".join(lines), ephemeral=True)
            return
        if not channel:
            await interaction.response.send_message("❌ 請指定一個頻道。", ephemeral=True)
            return
        channels = application_settings.get("application_channels", [])
        if action == "add":
            if channel.id not in channels:
                channels.append(channel.id)
                application_settings["application_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 已新增 #{channel.name} 為入盟申請區頻道。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 已經是入盟申請區頻道。", ephemeral=True)
        elif action == "remove":
            if channel.id in channels:
                channels.remove(channel.id)
                application_settings["application_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 已移除 #{channel.name} 的入盟申請區頻道設定。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 不在入盟申請區頻道列表中。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ action 只能是 add、remove 或 list。", ephemeral=True)

    @app_commands.command(name="application_council_channel", description="新增/移除理事國入盟申請區頻道（機器人擁有者限定）")
    @app_commands.describe(action="add=新增頻道, remove=移除頻道, list=列出已設定的頻道")
    async def application_council_channel(self, interaction: discord.Interaction,
                                            action: str,
                                            channel: Union[discord.TextChannel, discord.ForumChannel] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        channels = application_settings.get("council_channels", [])
        if action == "list":
            if channels:
                ch_list = "\n".join(f"  • <#{cid}> (`{cid}`)" for cid in channels)
                text = f"📝 **理事國入盟申請區頻道列表**（{len(channels)} 個）：\n{ch_list}"
            else:
                text = "📝 目前未設定任何理事國入盟申請區頻道。"
            await interaction.response.send_message(text, ephemeral=True)
        elif action == "add" and channel:
            if channel.id not in channels:
                channels.append(channel.id)
                application_settings["council_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 理事國入盟申請區頻道已新增 #{channel.name}。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 已在理事國入盟申請區頻道列表中。", ephemeral=True)
        elif action == "remove" and channel:
            if channel.id in channels:
                channels.remove(channel.id)
                application_settings["council_channels"] = channels
                save_application_settings()
                await interaction.response.send_message(f"✅ 理事國入盟申請區頻道已移除 #{channel.name}。", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ #{channel.name} 不在理事國入盟申請區頻道列表中。", ephemeral=True)
        else:
            await interaction.response.send_message("用法：`application_council_channel add/remove <#channel>` 或 `list`", ephemeral=True)

    @app_commands.command(name="application_secretariat", description="設定入盟申請秘書處通知頻道（機器人擁有者限定）")
    @app_commands.describe(channel="秘書處頻道（系統會在此發送申請通知供管理員審核）")
    async def application_secretariat(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        application_settings["secretariat_channel"] = channel.id
        save_application_settings()
        await interaction.response.send_message(f"✅ 入盟申請秘書處通知頻道已設為 #{channel.name}。", ephemeral=True)

    @app_commands.command(name="application_council", description="設定理事國審核通知頻道（機器人擁有者限定）")
    @app_commands.describe(channel="理事國頻道（系統會在此發送申請通知供理事國審核）")
    async def application_council(self, interaction: discord.Interaction, channel: Union[discord.TextChannel, discord.ForumChannel]):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        application_settings["council_channel"] = channel.id
        save_application_settings()
        await interaction.response.send_message(f"✅ 理事國審核通知頻道已設為 #{channel.name}。", ephemeral=True)

    @app_commands.command(name="application_status", description="查看入盟申請系統目前設定狀態（機器人擁有者限定）")
    async def application_status(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        enabled = application_settings.get("enabled", False)
        channels = application_settings.get("application_channels", [])
        sec_id = application_settings.get("secretariat_channel")

        lines = [f"📝 **入盟申請系統狀態**", ""]
        lines.append(f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用（用 /system application_toggle 開啟）'}")
        if channels:
            ch_list = "\n".join(f"  • <#{cid}> (`{cid}`)" for cid in channels)
            lines.append(f"秘書處入盟申請區頻道（{len(channels)} 個）：\n{ch_list}")
        else:
            lines.append("秘書處入盟申請區頻道：❌ 尚未設定（用 /system application_channel add 設定）")
        council_chs = application_settings.get("council_channels", [])
        if council_chs:
            ch_list2 = "\n".join(f"  • <#{cid}> (`{cid}`)" for cid in council_chs)
            lines.append(f"理事國入盟申請區頻道（{len(council_chs)} 個）：\n{ch_list2}")
        else:
            lines.append("理事國入盟申請區頻道：❌ 尚未設定（用 /system application_council_channel add 設定）")
        if sec_id:
            lines.append(f"秘書處通知頻道：<#{sec_id}> (`{sec_id}`)")
        else:
            lines.append("秘書處通知頻道：❌ 尚未設定（用 /system application_secretariat 設定）")
        council_id = application_settings.get("council_channel")
        if council_id:
            lines.append(f"理事國審核頻道：<#{council_id}> (`{council_id}`)")
        else:
            lines.append("理事國審核頻道：❌ 尚未設定（用 /system application_council 設定）")
        lines.append("")
        lines.append(f"已收錄申請總數：{len(_applications.get('entries', []))} 筆")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── 會員國管理白名單指令 ──

    @app_commands.command(name="nation_whitelist", description="管理會員國操作白名單（機器人擁有者限定）")
    @app_commands.describe(
        action="add 新增 / remove 移除 / list 列出",
        user="要放行或移除的使用者（@提及）",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="新增", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="列表", value="list"),
    ])
    async def nation_whitelist(self, interaction: discord.Interaction,
                               action: app_commands.Choice[str],
                               user: discord.Member = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        wl = application_settings.get("nation_admin_whitelist", [])
        act = action.value

        if act == "list":
            if not wl:
                await interaction.response.send_message("📋 會員國操作白名單目前為空。", ephemeral=True)
            else:
                names = []
                for uid in wl:
                    try:
                        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
                        names.append(f"• <@{uid}> (`{uid}`)" + (f" — {member.display_name}" if member else ""))
                    except (ValueError, TypeError):
                        names.append(f"• `{uid}`")
                await interaction.response.send_message(
                    f"📋 **會員國操作白名單**（{len(wl)} 人）：\n" + "\n".join(names), ephemeral=True
                )
            return

        if act in ("add", "remove"):
            if not user:
                await interaction.response.send_message("❌ 請指定使用者（@提及）。", ephemeral=True)
                return
            uid_str = str(user.id)
            if act == "add":
                if uid_str in wl:
                    await interaction.response.send_message(f"⚠️ {user.display_name} 已在白名單中。", ephemeral=True)
                    return
                wl.append(uid_str)
                application_settings["nation_admin_whitelist"] = wl
                save_application_settings()
                await interaction.response.send_message(
                    f"✅ 已將 {user.display_name}（`{uid_str}`）加入會員國操作白名單。\n"
                    f"此使用者現在可以在 Dashboard 及 /nation 指令中管理會員國。", ephemeral=True
                )
            elif act == "remove":
                if uid_str not in wl:
                    await interaction.response.send_message(f"⚠️ {user.display_name} 不在白名單中。", ephemeral=True)
                    return
                wl = [w for w in wl if str(w) != uid_str]
                application_settings["nation_admin_whitelist"] = wl
                save_application_settings()
                await interaction.response.send_message(
                    f"✅ 已將 {user.display_name}（`{uid_str}`）從會員國操作白名單移除。", ephemeral=True
                )

    # ── AI 精煉系統指令 ──

    @app_commands.command(name="refine_toggle", description="開啟/關閉 AI 精煉系統（機器人擁有者限定）")
    async def refine_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        ai_refine_settings["enabled"] = not ai_refine_settings.get("enabled", False)
        ai_refine_settings["guild_id"] = str(interaction.guild.id) if interaction.guild else ai_refine_settings.get("guild_id")
        save_refine_settings()
        status = "啟用" if ai_refine_settings["enabled"] else "停用"
        await interaction.response.send_message(f"🔬 AI 精煉系統已{status}。", ephemeral=True)

    @app_commands.command(name="refine_channel", description="設定 AI 精煉自言自語頻道（機器人擁有者限定）")
    @app_commands.describe(channel="機器人發布精煉知識的頻道")
    async def refine_channel(self, interaction: discord.Interaction,
                             channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        ai_refine_settings["channel_id"] = str(channel.id)
        ai_refine_settings["guild_id"] = str(interaction.guild.id) if interaction.guild else ai_refine_settings.get("guild_id")
        save_refine_settings()
        await interaction.response.send_message(f"✅ AI 精煉頻道已設為 #{channel.name}。", ephemeral=True)

    @app_commands.command(name="refine_interval", description="設定 AI 精煉間隔分鐘數（機器人擁有者限定）")
    @app_commands.describe(minutes="間隔分鐘數（建議 3-30）")
    async def refine_interval(self, interaction: discord.Interaction, minutes: int):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if minutes < 1:
            await interaction.response.send_message("❌ 間隔至少 1 分鐘。", ephemeral=True)
            return
        if minutes > 120:
            await interaction.response.send_message("❌ 間隔最多 120 分鐘。", ephemeral=True)
            return
        ai_refine_settings["interval_minutes"] = minutes
        save_refine_settings()
        await interaction.response.send_message(f"✅ AI 精煉間隔已設為 {minutes} 分鐘。", ephemeral=True)

    @app_commands.command(name="refine_purge", description="清空 AI 精煉知識庫（機器人擁有者限定）")
    async def refine_purge(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        count = len(ai_refined_knowledge)
        ai_refined_knowledge.clear()
        save_refine_knowledge()
        await interaction.response.send_message(
            f"🧹 已清空 AI 精煉知識庫（原本 {count} 條）。\n"
            f"新的知識將在下次精煉時重新累積（僅接受高可信度百科知識）。",
            ephemeral=True,
        )

    @app_commands.command(name="refine_status", description="查看 AI 精煉系統狀態（機器人擁有者限定）")
    async def refine_status(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        enabled = ai_refine_settings.get("enabled", False)
        ch_id = ai_refine_settings.get("channel_id")
        interval = ai_refine_settings.get("interval_minutes", 5)
        knowledge_count = len(ai_refined_knowledge)

        lines = ["🔬 **AI 精煉系統狀態**", ""]
        lines.append(f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用'}")
        lines.append(f"基準間隔：{interval} 分鐘（動態調整中）")
        # Show current dynamic interval
        dynamic_secs = _compute_dynamic_refine_interval()
        cpm = _get_api_calls_per_minute()
        max_entries = ai_refine_settings.get("max_knowledge_entries", 500)
        kb_ratio = knowledge_count / max(1, max_entries)
        if kb_ratio >= 0.9:
            lines.append(f"派工間隔：60 分鐘（知識庫已滿 {kb_ratio:.0%}，自動放慢）")
        elif cpm > 20:
            lines.append(f"派工間隔：{dynamic_secs // 60} 分鐘（API 流量高 {cpm} calls/min，已降速）")
        elif cpm > 10:
            lines.append(f"派工間隔：{dynamic_secs // 60} 分鐘（API 流量中等 {cpm} calls/min）")
        else:
            lines.append(f"派工間隔：{dynamic_secs} 秒（API 流量低 {cpm} calls/min）")
        lines.append(f"當前 API 速率：{cpm} calls/min")
        lines.append(f"併發執行中：{len(_refine_active_tasks)} 個精煉週期（不互相阻塞）")
        # Confidence breakdown
        high_count = sum(1 for k in ai_refined_knowledge if k.get("confidence", "high") == "high")
        low_count = knowledge_count - high_count
        lines.append(f"知識庫：{knowledge_count}/{max_entries} ({kb_ratio:.0%})")
        lines.append(f"  ├─ 高可信度（百科驗證）：{high_count} 條")
        lines.append(f"  └─ 低可信度（社群未驗證）：{low_count} 條（仍會注入 AI 上下文）")
        if _refine_empty_streak > 0:
            lines.append(f"⚠️ 連續空手：{_refine_empty_streak} 次（找不到新知識，已觸發退避）")
        if ch_id:
            lines.append(f"發布頻道：<#{ch_id}> (`{ch_id}`)")
        else:
            lines.append("發布頻道：❌ 尚未設定（用 /system refine_channel 設定）")
        if knowledge_count > 0:
            lines.append("")
            lines.append("**近期精煉知識（最後 5 條）：**")
            for k in ai_refined_knowledge[-5:]:
                lines.append(f"• [{k.get('date', '?')}] **{k.get('topic', '?')}** — {k.get('summary', '')[:60]}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)



# ═════════════════════════════════════════════════════════════════
# Community Chronicle System (社群編年史)
# 補足即時感知的深度不足——掃描更深的歷史（論壇全文 + 深層頻道訊息），
# 讓 AI 理解持續數月甚至數年的恩怨、聯盟、條約、事件因果。
#
# 雙層感知架構：
# - 即時脈搏（community_awareness）：20 分鐘週期，看最近動態
# - 深度編年史（community_chronicle）：每日週期，看長期歷史
#
# 編年史包含：
# 1. 重大聯盟 — 誰跟誰結盟、什麼時候、為什麼、目前狀態
# 2. 重大衝突 — 誰跟誰有恩怨、起因、演變、目前狀態
# 3. 關鍵歷史事件 — 重要事件的因果鏈與影響
# 4. 條約與協議 — 簽了什麼、條件、目前是否有效
# 5. 權力動態 — 誰有影響力、怎麼形成的、怎麼演變的
# 6. 文化傳統 — 社群特有的規範與傳統
# 7. 重要人物 — 關鍵角色的歷史與現狀
# ═════════════════════════════════════════════════════════════════

COMMUNITY_CHRONICLE_FILE = os.path.join(DATA_DIR, "community_chronicle.json")

_community_chronicle = {
    "last_updated": "",
    "last_deep_scan": "",
    "major_alliances": [],       # [{name, members, formed, context, status}]
    "major_conflicts": [],       # [{parties, started, cause, status, resolution, current_state}]
    "key_events": [],            # [{date, event, participants, consequences, significance}]
    "treaties_agreements": [],   # [{name, parties, date, terms, status}]
    "power_dynamics": [],        # [{description, context, evolution}]
    "cultural_traditions": [],   # [{norm, origin, context}]
    "notable_figures": [],       # [{name, role, history, current_status}]
}

_chronicle_last_run = 0
_CHRONICLE_INTERVAL = 86400  # 24 hours in seconds


def _save_community_chronicle():
    _save_json_file(COMMUNITY_CHRONICLE_FILE, _community_chronicle)


def _load_community_chronicle():
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


def _append_unique_text(existing: str, addition: str) -> str:
    """Append `addition` onto `existing` unless it's already substantively
    present — never discards existing text, only grows it. This is the core
    primitive that lets repeated mentions of the same entity across many
    articles ACCUMULATE detail instead of one mention overwriting/dropping
    another (the user's hard 'never delete for the sake of merging' rule)."""
    existing = (existing or "").strip()
    addition = (addition or "").strip()
    if not addition:
        return existing
    if not existing:
        return addition
    if addition in existing:
        return existing
    return existing + "\n" + addition


def _merge_unique_list(existing: list, addition: list) -> list:
    """Union two lists of strings/dicts, preserving order, de-duplicating
    only EXACT repeats (never drops distinct items)."""
    existing = existing if isinstance(existing, list) else []
    addition = addition if isinstance(addition, list) else []
    seen = set()
    out = []
    for item in existing + addition:
        key = json_module.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merge_scan_batch(extracted: dict):
    """Fold one batch's extraction into the running global scan result.
    HARD RULE: this function must never delete or overwrite-away existing
    data. Every dedupe path below ENRICHES the existing entry (unions list
    fields, appends genuinely-new description text) rather than skipping or
    replacing — a person/event/country mentioned across many articles keeps
    accumulating detail (disputes, anecdotes, causal links) instead of only
    the first or the AI's-preferred mention surviving."""
    if not isinstance(extracted, dict):
        return

    # 1. countries (dedupe by name — enrich, never overwrite)
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
            curr["description"] = _append_unique_text(curr.get("description", ""), c.get("description", ""))
            if c.get("status") and c["status"] != "unknown":
                curr["status"] = c["status"]
            if c.get("type"):
                curr["type"] = c["type"]
        else:
            existing_countries[name_key] = c
            _global_scan_result.setdefault("countries", []).append(c)

    # 2. relationships (dedupe by from + to + type — enrich, never overwrite)
    existing_rels = {
        (r.get("from", "").strip().lower(), r.get("to", "").strip().lower(), r.get("type", "").strip().lower()): r
        for r in _global_scan_result.get("relationships", [])
        if isinstance(r, dict)
    }
    for r in extracted.get("relationships", []):
        if not isinstance(r, dict) or not r.get("from") or not r.get("to"):
            continue
        rel_key = (r.get("from", "").strip().lower(), r.get("to", "").strip().lower(), r.get("type", "").strip().lower())
        if rel_key in existing_rels:
            curr = existing_rels[rel_key]
            curr["description"] = _append_unique_text(curr.get("description", ""), r.get("description", ""))
            curr["context"] = _append_unique_text(curr.get("context", ""), r.get("context", ""))
            if r.get("status"):
                curr["status"] = r["status"]
        else:
            existing_rels[rel_key] = r
            _global_scan_result.setdefault("relationships", []).append(r)

    # 3. key_figures (dedupe by name — enrich: union disputes/anecdotes, never skip)
    existing_figs = {
        f.get("name", "").strip().lower(): f
        for f in _global_scan_result.get("key_figures", [])
        if isinstance(f, dict) and f.get("name")
    }
    for f in extracted.get("key_figures", []):
        if not isinstance(f, dict) or not f.get("name"):
            continue
        fig_key = f["name"].strip().lower()
        if fig_key in existing_figs:
            curr = existing_figs[fig_key]
            curr["description"] = _append_unique_text(curr.get("description", ""), f.get("description", ""))
            if f.get("affiliation") and not curr.get("affiliation"):
                curr["affiliation"] = f["affiliation"]
            if f.get("role") and not curr.get("role"):
                curr["role"] = f["role"]
            curr["disputes"] = _merge_unique_list(curr.get("disputes", []), f.get("disputes", []))
            curr["anecdotes"] = _merge_unique_list(curr.get("anecdotes", []), f.get("anecdotes", []))
        else:
            f.setdefault("disputes", [])
            f.setdefault("anecdotes", [])
            existing_figs[fig_key] = f
            _global_scan_result.setdefault("key_figures", []).append(f)

    # 4. major_events (dedupe by event name — enrich: union participants/leads_to/caused_by)
    existing_events = {
        e.get("event", "").strip().lower(): e
        for e in _global_scan_result.get("major_events", [])
        if isinstance(e, dict) and e.get("event")
    }
    for e in extracted.get("major_events", []):
        if not isinstance(e, dict) or not e.get("event"):
            continue
        ev_key = e["event"].strip().lower()
        if ev_key in existing_events:
            curr = existing_events[ev_key]
            curr["description"] = _append_unique_text(curr.get("description", ""), e.get("description", ""))
            curr["consequences"] = _append_unique_text(curr.get("consequences", ""), e.get("consequences", ""))
            curr["participants"] = _merge_unique_list(curr.get("participants", []), e.get("participants", []))
            curr["leads_to"] = _merge_unique_list(curr.get("leads_to", []), e.get("leads_to", []))
            curr["caused_by"] = _merge_unique_list(curr.get("caused_by", []), e.get("caused_by", []))
            if e.get("date") and not curr.get("date"):
                curr["date"] = e["date"]
        else:
            e.setdefault("leads_to", [])
            e.setdefault("caused_by", [])
            existing_events[ev_key] = e
            _global_scan_result.setdefault("major_events", []).append(e)


async def _link_event_causal_chains():
    """Build causal chains between already-extracted events WITHOUT ever
    rewriting or deleting the events themselves — this is a purely additive
    pass that only fills in `leads_to`/`caused_by` cross-references.

    The old approach dumped the ENTIRE accumulated graph (which only grows
    as the scan progresses — potentially thousands of entries) into one AI
    call asking it to regenerate a 'consolidated, compact' version. That can
    NEVER satisfy 'never drop anything' once the dataset is large: no output
    token budget fits thousands of full entries, so the AI was forced to
    summarize/drop things every single time. This replaces that with an
    approach that scales with dataset size instead of choking on it:

    1. Group events by shared participant (a country/person appearing in
       both events is a strong signal they're causally related — treaties,
       conflicts, and their consequences tend to involve the same actors).
    2. For each group (chunked to a safe size), send ONLY light-weight
       {event, date, description, consequences} — never the disputes/
       anecdotes/full text — and ask for nothing but link references:
       [{"event": "...", "leads_to": [...], "caused_by": [...]}], where
       every name referenced MUST be one of the events actually given in
       that chunk (never invented).
    3. Merge those references into the existing event records via
       _merge_unique_list — additive only, so nothing already recorded via
       _merge_scan_batch is ever touched, let alone dropped.

    This runs once at /finish. It scales because each AI call's input/output
    is bounded by chunk size, not by total corpus size — doubling the number
    of events just means more (small) chunks, not one impossibly large call."""
    events = _global_scan_result.get("major_events", [])
    if not events or len(events) < 2:
        return

    # Group event *indices* by participant so related events land in the
    # same chunk together (an event can belong to multiple groups).
    groups: dict = {}
    for idx, e in enumerate(events):
        if not isinstance(e, dict):
            continue
        for p in (e.get("participants") or []):
            key = str(p).strip().lower()
            if key:
                groups.setdefault(key, set()).add(idx)

    CHUNK_SIZE = 35
    chunks_seen: list = []  # frozenset(idx) already processed, to skip near-duplicate groups
    processed_chunks = 0

    for participant, idx_set in groups.items():
        if len(idx_set) < 2:
            continue
        idx_list = sorted(idx_set)
        sub_chunks = [idx_list[i:i + CHUNK_SIZE] for i in range(0, len(idx_list), CHUNK_SIZE)]

        for chunk_idxs in sub_chunks:
            frz = frozenset(chunk_idxs)
            if frz in chunks_seen:
                continue
            chunks_seen.append(frz)

            light_events = [
                {
                    "event": events[i].get("event", ""),
                    "date": events[i].get("date", ""),
                    "description": (events[i].get("description") or "")[:300],
                    "consequences": (events[i].get("consequences") or "")[:300],
                }
                for i in chunk_idxs
            ]

            system_prompt = (
                '你是微國家歷史學家，正在分析以下這組事件（它們都跟同一位參與者「' + str(participant) + '」有關），'
                '找出事件之間明確的因果關係鏈：哪個事件導致了哪個事件。\n'
                '規則：\n'
                '1. 只能引用下面清單中「已經存在」的事件名稱，絕對不能編造清單以外的事件名稱。\n'
                '2. 如果兩個事件之間沒有明確因果關係，不要硬湊，寧可留空。\n'
                '3. leads_to 指這個事件之後導致了哪些清單中的其他事件；caused_by 指這個事件是被清單中'
                '哪些其他事件所導致。\n'
                '請以嚴格 JSON 陣列輸出（不可使用 markdown 程式碼區塊），格式：\n'
                '[{"event": "事件名稱（須完全match清單中的名稱）", "leads_to": ["..."], "caused_by": ["..."]}]\n'
                '只需要輸出有因果關係的事件，沒有關係的事件不用輸出。若完全沒有因果關係，輸出 []。'
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json_module.dumps(light_events, ensure_ascii=False)}
            ]

            try:
                resp = await call_chat_api(messages, chat_ai_settings, max_tokens=2000)
                ai_text = (resp.get("content") or "").strip()
                if ai_text.startswith("```"):
                    ai_text = re.sub(r"^```(?:json)?\s*", "", ai_text, flags=re.IGNORECASE)
                    ai_text = re.sub(r"\s*```$", "", ai_text).strip()
                try:
                    links = json_module.loads(ai_text)
                except Exception:
                    m = re.search(r"\[.*\]", ai_text, re.DOTALL)
                    links = json_module.loads(m.group(0)) if m else []

                if not isinstance(links, list):
                    continue

                valid_names = {events[i].get("event", "").strip().lower() for i in chunk_idxs}
                by_name = {events[i].get("event", "").strip().lower(): events[i] for i in chunk_idxs}

                for link in links:
                    if not isinstance(link, dict):
                        continue
                    ev_name = str(link.get("event", "")).strip().lower()
                    target = by_name.get(ev_name)
                    if not target:
                        continue
                    leads_to = [n for n in (link.get("leads_to") or []) if str(n).strip().lower() in valid_names]
                    caused_by = [n for n in (link.get("caused_by") or []) if str(n).strip().lower() in valid_names]
                    target["leads_to"] = _merge_unique_list(target.get("leads_to", []), leads_to)
                    target["caused_by"] = _merge_unique_list(target.get("caused_by", []), caused_by)
                processed_chunks += 1
            except Exception as e:
                print(f"⚠️ 因果鏈分析失敗（參與者：{participant}）: {e}")
                continue

    _save_global_scan_result()
    print(f"✨ 事件因果鏈分析完成，共處理 {processed_chunks} 個關聯群組（原始資料完全保留，僅新增因果標註）")


async def _rescue_orphan_entities():
    """Post-scan pass that finds entities referenced in relationships,
    event participants, or figure affiliations but lacking their own
    standalone country/figure entry — then builds proper entries for them
    from whatever scattered mentions exist across the entire dataset.

    This addresses the user's core complaint: some people/countries never
    had their own micropedia article, so their info is fragmented across
    many other articles' descriptions. The per-batch extraction already
    tries to create entries for them (prompt rule #1), but if the AI
    missed some — or if the entity was only mentioned obliquely (by an
    alias, or embedded in a relationship's from/to without a matching
    countries entry) — this pass catches them.

    Algorithm:
    1. Collect all names that appear as from/to in relationships,
       participants in events, or affiliations in key_figures.
    2. Check each against existing countries (by name+aliases) and
       key_figures (by name). Any name not found = orphan.
    3. For each orphan, gather every text fragment across the entire
       dataset that mentions it (from descriptions, contexts,
       consequences, disputes, anecdotes, event descriptions).
    4. Chunk orphans (≤20 at a time) and send the gathered fragments
       to AI with instructions to build standalone entries. Add the
       results back via the same merge logic (additive only).
    """
    countries = _global_scan_result.get("countries", [])
    relationships = _global_scan_result.get("relationships", [])
    key_figures = _global_scan_result.get("key_figures", [])
    major_events = _global_scan_result.get("major_events", [])

    # Build name lookup sets (lowercased, including aliases)
    country_names = set()
    for c in countries:
        if not isinstance(c, dict):
            continue
        n = c.get("name", "").strip().lower()
        if n:
            country_names.add(n)
        for a in (c.get("aliases") or []):
            country_names.add(str(a).strip().lower())

    figure_names = set()
    for f in key_figures:
        if not isinstance(f, dict):
            continue
        n = f.get("name", "").strip().lower()
        if n:
            figure_names.add(n)

    all_known = country_names | figure_names

    # Collect all referenced names
    referenced = set()
    for r in relationships:
        if not isinstance(r, dict):
            continue
        for field in ("from", "to"):
            val = str(r.get(field, "")).strip().lower()
            if val:
                referenced.add(val)
    for e in major_events:
        if not isinstance(e, dict):
            continue
        for p in (e.get("participants") or []):
            referenced.add(str(p).strip().lower())
    for f in key_figures:
        if not isinstance(f, dict):
            continue
        aff = str(f.get("affiliation", "")).strip().lower()
        if aff:
            referenced.add(aff)

    # Orphans = referenced but not in any known entry
    orphans = referenced - all_known
    # Filter out generic/empty terms
    _SKIP_ORPHAN = {"", "unknown", "未知", "無", "none", "n/a", "various", "多個", "多位"}
    orphans = {o for o in orphans if o not in _SKIP_ORPHAN and len(o) >= 2}

    if not orphans:
        print("✨ 孤兒救援：沒有遺漏的實體，所有被提及的名稱都有獨立條目")
        return

    print(f"🔍 孤兒救援：發現 {len(orphans)} 個被提及但沒有獨立條目的實體，開始彙集散落資訊...")

    # For each orphan, gather all text fragments mentioning it
    all_texts = []
    for c in countries:
        if isinstance(c, dict):
            all_texts.append(("country", c.get("name", ""), c.get("description", "")))
    for r in relationships:
        if isinstance(r, dict):
            all_texts.append(("rel", f"{r.get('from','')}→{r.get('to','')}", f"{r.get('description','')} {r.get('context','')}"))
    for f in key_figures:
        if isinstance(f, dict):
            all_texts.append(("figure", f.get("name", ""), f"{f.get('description','')} {' '.join(f.get('disputes',[]))} {' '.join(f.get('anecdotes',[]))}"))
    for e in major_events:
        if isinstance(e, dict):
            all_texts.append(("event", e.get("event", ""), f"{e.get('description','')} {e.get('consequences','')}"))

    orphan_fragments = {}
    for orphan in orphans:
        frags = []
        for kind, source_name, text in all_texts:
            if not text:
                continue
            # Check if orphan name appears in the text or source name
            if orphan in text.lower() or orphan in source_name.lower():
                # Extract a window of text around the mention
                idx = text.lower().find(orphan)
                while idx != -1:
                    start = max(0, idx - 100)
                    end = min(len(text), idx + len(orphan) + 200)
                    frag = text[start:end].strip()
                    if frag and frag not in frags:
                        frags.append(frag)
                    idx = text.lower().find(orphan, idx + 1)
        if frags:
            orphan_fragments[orphan] = frags

    if not orphan_fragments:
        print("✨ 孤兒救援：雖有遺漏實體名稱，但在現有資料中找不到足夠的散落文字，跳過")
        return

    # Chunk orphans (≤20 per AI call) and build standalone entries
    orphan_list = list(orphan_fragments.keys())
    CHUNK = 20
    rescued = 0

    for i in range(0, len(orphan_list), CHUNK):
        chunk_orphans = orphan_list[i:i + CHUNK]
        fragment_text = ""
        for orphan in chunk_orphans:
            frags = orphan_fragments[orphan][:5]  # cap at 5 fragments per orphan
            fragment_text += f"\n\n【{orphan}】\n" + "\n---\n".join(frags)

        system_prompt = (
            '你是微國家歷史學家。以下是一些在微國家百科中沒有自己獨立條目、'
            '但其相關資訊散落在其他條目中的人物/國家/組織。請根據這些散落的文字片段，'
            '為每個實體建立盡可能完整的獨立條目。\n\n'
            '【鐵律】\n'
            '1. 必須為每一個列出的實體都建立條目，不准跳過任何一個。\n'
            '2. 只使用以下散落文字中提到的資訊，不要編造不存在的事實。如果某個欄位'
            '在文字中找不到資訊，就留空或寫「未知」，不要猜測。\n'
            '3. 盡可能從文字中挖掘出恩怨、軼事、參與的事件等細節。\n'
            '4. 如果散落文字中有提到此實體參與的事件，也請在 major_events 中建立事件條目。\n\n'
            '請以嚴格 JSON 格式輸出（不可使用 markdown 程式碼區塊），包含以下 key：\n'
            '1. countries: [{"name": "...", "aliases": ["..."], "type": "micronation/organization/individual", '
            '"description": "...", "status": "active/dissolved/unknown"}]\n'
            '2. key_figures: [{"name": "...", "affiliation": "...", "role": "...", "description": "...", '
            '"disputes": ["..."], "anecdotes": ["..."]}]\n'
            '3. major_events: [{"event": "...", "participants": ["..."], "date": "...", "description": "...", '
            '"consequences": "...", "leads_to": [], "caused_by": []}]\n'
            '4. relationships: [{"from": "...", "to": "...", "type": "alliance/conflict/treaty/trade/diplomatic/cultural/personal", '
            '"description": "...", "context": "...", "status": "active/historical/ended"}]\n'
            '每個實體只需放入 countries 或 key_figures 其中一個（判斷它是國家/組織還是個人）。\n'
            '如果某個實體從散落文字中看不出是什麼類型，預設放入 key_figures。\n'
            '僅輸出 JSON 物件，請勿附加任何額外文字。'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "以下是散落在其他條目中的資訊片段，請為每個實體建立獨立條目：" + fragment_text}
        ]

        try:
            resp = await call_chat_api(messages, chat_ai_settings, max_tokens=4000)
            ai_text = (resp.get("content") or "").strip()
            if ai_text.startswith("```"):
                ai_text = re.sub(r"^```(?:json)?\s*", "", ai_text, flags=re.IGNORECASE)
                ai_text = re.sub(r"\s*```$", "", ai_text).strip()

            rescued_data = None
            try:
                rescued_data = json_module.loads(ai_text)
            except Exception:
                m = re.search(r"\{.*\}", ai_text, re.DOTALL)
                if m:
                    try:
                        rescued_data = json_module.loads(m.group(0))
                    except Exception:
                        pass
                if rescued_data is None:
                    salvaged = _salvage_scan_extraction(ai_text)
                    if salvaged:
                        rescued_data = salvaged

            if isinstance(rescued_data, dict):
                before_counts = {
                    k: len(_global_scan_result.get(k, []))
                    for k in ("countries", "key_figures", "major_events", "relationships")
                }
                _merge_scan_batch(rescued_data)
                after_counts = {
                    k: len(_global_scan_result.get(k, []))
                    for k in ("countries", "key_figures", "major_events", "relationships")
                }
                new_items = sum(after_counts[k] - before_counts[k] for k in before_counts)
                rescued += new_items
                print(f"  ✅ 孤兒救援批次 {i//CHUNK + 1}: 新增 {new_items} 項條目")
        except Exception as e:
            print(f"  ⚠️ 孤兒救援批次 {i//CHUNK + 1} 失敗: {e}")
            continue

    _save_global_scan_result()
    print(f"✨ 孤兒救援完成：從散落資訊中為 {len(orphan_fragments)} 個實體建立了獨立條目（共新增 {rescued} 項）")


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
                                    # Same 2000->3000 bump as above — tables/
                                    # infoboxes now produce real text instead
                                    # of being deleted.
                                    if len(clean) > 3000:
                                        clean = clean[:3000] + "..."
                                    content_parts.append(f"【{p_title}】\n{clean}")
                except Exception as fe:
                    print(f"⚠️ 全球掃描取得內文失敗 (批次 {b_idx + 1}): {fe}")

                if content_parts:
                    batch_text = "\n\n".join(content_parts)
                    system_prompt = (
                        '你是一位歷史學家與微國家學學者。請分析以下維基條目內容，'
                        '提取國家/組織/個人、關係、關鍵人物、重大事件。\n'
                        '請以繁體中文輸出嚴格 JSON 格式（不可使用 markdown 程式碼區塊），包含以下 4 個 key：\n'
                        '1. countries: [{"name": "...", "aliases": ["..."], "type": "micronation/organization/individual", '
                        '"description": "...", "status": "active/dissolved/unknown"}]\n'
                        '2. relationships: [{"from": "...", "to": "...", "type": "alliance/conflict/treaty/trade/diplomatic/cultural/personal", '
                        '"description": "...", "context": "...", "status": "active/historical/ended"}]\n'
                        '3. key_figures: [{"name": "...", "affiliation": "...", "role": "...", "description": "..."}]\n'
                        '4. major_events: [{"event": "...", "participants": ["..."], "date": "...", "description": "...", "consequences": "..."}]\n'
                        '僅輸出 JSON 物件，請勿附加任何額外文字。'
                    )

                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": '條目內容:\n' + batch_text}
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

                _save_global_scan_result()
                await asyncio.sleep(0.5)

            # Final passes: rescue orphans, then link causal chains (all additive, never drops data)
            await _rescue_orphan_entities()
            await _link_event_causal_chains()

            _global_scan_state["status"] = "completed"
            _global_scan_state["completed_at"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
            _global_scan_result["last_updated"] = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M:%S")
            _save_global_scan_result()

    except Exception as e:
        import traceback
        err_msg = f"{e}\n{traceback.format_exc()}"
        print(f"❌ 全球掃描失敗: {err_msg}")
        _global_scan_state["status"] = "error"
        _global_scan_state["error"] = str(e)


async def _gather_deep_history(guild, max_channels=10, msgs_per_channel=100) -> str:
    """Gather deep history from channels — much deeper than the awareness
    scan. Fetches up to 100 messages per channel from the most active
    channels, covering weeks to months of history depending on channel
    activity."""
    _log_ch_id = chat_ai_settings.get("log_channel_id")
    _EXCLUDE_MARKERS = ("測試", "test", "log", "紀錄")

    def _is_excluded(ch):
        if _log_ch_id and ch.id == _log_ch_id:
            return True
        name_lower = ch.name.lower()
        return any(m.lower() in name_lower for m in _EXCLUDE_MARKERS)

    candidates = [
        ch for ch in guild.text_channels
        if ch.type in (discord.ChannelType.text, discord.ChannelType.news)
        and not _is_excluded(ch)
    ]

    # Sort by recent activity — most active first
    channel_ts = []
    for ch in candidates:
        try:
            ts = 0
            async for m in ch.history(limit=1):
                ts = m.created_at.timestamp()
            channel_ts.append((ts, ch))
        except Exception:
            channel_ts.append((0, ch))
    channel_ts.sort(key=lambda x: -x[0])
    selected = [ch for _, ch in channel_ts[:max_channels]]

    snippets = []
    for ch in selected:
        try:
            msgs = []
            async for msg in ch.history(limit=msgs_per_channel):
                text_parts = []
                if msg.content and msg.content.strip():
                    text_parts.append(msg.content.strip())
                for emb in msg.embeds:
                    if emb.title:
                        text_parts.append(str(emb.title))
                    if emb.description:
                        text_parts.append(str(emb.description))
                    for field in emb.fields:
                        text_parts.append(f"{field.name}: {field.value}")
                full = "\n".join(p for p in text_parts if p).strip()
                if full and len(full) >= 5 and not msg.author.bot:
                    ts_str = msg.created_at.astimezone(GMT8).strftime("%Y-%m-%d")
                    msgs.append(f"[{ts_str}] {msg.author.display_name}: {full[:120]}")
            if msgs:
                snippets.append(f"── #{ch.name} ──\n" + "\n".join(msgs))
        except Exception:
            continue
    return "\n\n".join(snippets)


async def _gather_forum_digest(guild) -> str:
    """Build a compact digest of ALL forum posts — titles, dates, tags,
    and key content — as the backbone of the chronicle. Forum posts are
    where formal events happen (proposals, elections, treaties, applications)."""
    try:
        posts = await _get_forum_index(guild)
    except Exception:
        posts = _forum_index_cache.get(guild.id, {}).get("posts", [])

    if not posts:
        return ""

    lines = []
    for p in posts:
        title = p.get("title", "?")
        date = p.get("created_at", "?")
        tags = p.get("tags", [])
        author = p.get("author", "?")
        channel = p.get("channel_name", "?")
        last_activity = p.get("last_activity", "")

        # Compact: [date] #channel: "title" (tags) by author
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        line = f"[{date}] #{channel}: \"{title}\"{tag_str} by {author}"
        if last_activity and last_activity != date:
            line += f" (最後活動: {last_activity})"

        # Add first 150 chars of content for context
        text = p.get("text", "")
        # Strip the title/tags we already included
        content_lines = text.split("\n")
        # Find the actual content (skip title and tags lines)
        content_start = 0
        for i, cl in enumerate(content_lines):
            if cl.strip() and cl.strip() != title and cl.strip() not in (tags or []):
                content_start = i
                break
        content_text = "\n".join(content_lines[content_start:])[:150]
        if content_text.strip():
            line += f" — {content_text.strip()}"

        # Add last reply (latest status update)
        reply_lines = p.get("reply_lines", [])
        if reply_lines:
            last_reply = reply_lines[-1][:100]
            line += f" → 最新進展: {last_reply}"

        lines.append(line)

    return "\n".join(lines)


async def _deep_scan_community(guild) -> bool:
    """Run one deep chronicle scan: gather deep channel history + forum
    digest, have the AI synthesize/update the community chronicle."""
    global _community_chronicle
    if not chat_ai_settings.get("api_key"):
        return False

    # Gather data
    print("📜 社群編年史：正在收集論壇摘要...")
    forum_digest = await _gather_forum_digest(guild)

    print("📜 社群編年史：正在收集深層頻道歷史...")
    channel_history = await _gather_deep_history(guild, max_channels=10, msgs_per_channel=100)

    if not forum_digest and not channel_history:
        print("📜 社群編年史：沒有足夠的歷史資料，跳過")
        return False

    # Build previous chronicle summary for the AI to update
    prev = _community_chronicle
    prev_summary = ""
    if prev.get("last_updated"):
        prev_lines = []
        for a in prev.get("major_alliances", [])[:10]:
            prev_lines.append(f"- 聯盟：{', '.join(a.get('members', []))} — {a.get('context', '')}（{a.get('status', '?')}）")
        for c in prev.get("major_conflicts", [])[:10]:
            prev_lines.append(f"- 衝突：{', '.join(c.get('parties', []))} — {c.get('cause', '')}（{c.get('status', '?')}）→ {c.get('current_state', '')}")
        for e in prev.get("key_events", [])[:10]:
            prev_lines.append(f"- 事件：[{e.get('date', '?')}] {e.get('event', '')} — {e.get('consequences', '')}")
        for t in prev.get("treaties_agreements", [])[:8]:
            prev_lines.append(f"- 條約：{t.get('name', '?')} — {', '.join(t.get('parties', []))}（{t.get('status', '?')}）")
        for p in prev.get("power_dynamics", [])[:5]:
            prev_lines.append(f"- 權力：{p.get('description', '')} — {p.get('evolution', '')}")
        for f in prev.get("notable_figures", [])[:10]:
            prev_lines.append(f"- 人物：{f.get('name', '?')} — {f.get('role', '?')} — {f.get('current_status', '?')}")
        for ct in prev.get("cultural_traditions", [])[:5]:
            prev_lines.append(f"- 傳統：{ct.get('norm', '')} — {ct.get('origin', '')}")
        prev_summary = "\n".join(prev_lines)

    system_prompt = (
        "你是一個微國家 Discord 社群的歷史學家，你的任務是從大量的歷史訊息中，"
        "整理出這個社群的「編年史」——一份長期的、深度的歷史記錄。\n\n"
        "你會收到：\n"
        "1. 論壇所有貼文的摘要（標題、日期、標籤、內容片段、最新進展）\n"
        "2. 多個頻道的深層歷史訊息（最近 100 則，可能跨越數週到數月）\n\n"
        "請從這些資料中分析出以下七個維度：\n\n"
        "1. 重大聯盟（major_alliances）：成員國之間的長期結盟關係。\n"
        "   - name: 聯盟名稱（如果有）\n"
        "   - members: 成員列表\n"
        "   - formed: 大約形成時間\n"
        "   - context: 為什麼結盟（背景原因）\n"
        "   - status: active（活躍）/ fractured（出現裂痕）/ dissolved（解散）\n\n"
        "2. 重大衝突（major_conflicts）：成員國之間的長期恩怨或對立。\n"
        "   - parties: 對立雙方\n"
        "   - started: 大約開始時間\n"
        "   - cause: 起因\n"
        "   - status: ongoing（持續中）/ resolved（已解決）/ escalated（升級）/ cold（冷卻）\n"
        "   - resolution: 如果已解決，怎麼解決的\n"
        "   - current_state: 目前狀態的一句話描述\n\n"
        "3. 關鍵歷史事件（key_events）：影響社群走向的重大事件。\n"
        "   - date: 日期\n"
        "   - event: 事件描述\n"
        "   - participants: 參與者\n"
        "   - consequences: 後果/影響\n"
        "   - significance: 為什麼重要（長期影響）\n\n"
        "4. 條約與協議（treaties_agreements）：正式簽署的條約、協議、公約。\n"
        "   - name: 條約名稱\n"
        "   - parties: 簽署方\n"
        "   - date: 簽署日期\n"
        "   - terms: 條件摘要\n"
        "   - status: active（有效）/ suspended（暫停）/ voided（失效）\n\n"
        "5. 權力動態（power_dynamics）：社群內的影響力結構。\n"
        "   - description: 誰有影響力、什麼樣的影響力\n"
        "   - context: 脈絡\n"
        "   - evolution: 怎麼演變來的\n\n"
        "6. 文化傳統（cultural_traditions）：社群特有的規範、傳統、潛規則。\n"
        "   - norm: 傳統/規範描述\n"
        "   - origin: 起源\n"
        "   - context: 脈絡\n\n"
        "7. 重要人物（notable_figures）：對社群有重大影響的關鍵人物。\n"
        "   - name: 名字\n"
        "   - role: 角色/身分\n"
        "   - history: 在社群中的重要事蹟\n"
        "   - current_status: 目前狀態（活躍/淡出/離開/被禁等）\n\n"
        "【重要原則】\n"
        "- 你是在寫歷史，不是在寫現況報告——著重長期的、結構性的東西\n"
        "- 只記錄從訊息中能觀察到的東西，不要編造或過度推測\n"
        "- 衝突要寫清楚起因和演變——不要只寫「A跟B有恩怨」\n"
        "- 事件要寫清楚因果——為什麼發生、導致了什麼\n"
        "- 人物要寫清楚事蹟——不要只寫「A很重要」\n"
        "- 寫繁體中文\n"
        "- 每個欄位的文字不要超過 200 字\n"
        "- 如果某個維度沒有足夠資料判斷，就回空陣列\n"
        "- 【反幻覺鐵律】絕對不要自己判定「A 國家其實就是 B 國家的別名／不同稱呼」「A 跟 B 其實是"
        "同一人」這類等同關係，除非訊息中有人明確這樣說過（例如有人親口說「我們也叫做...」）。"
        "兩個名稱只是在對話中出現在附近，不代表它們有任何關聯，不要自己腦補出一個聽起來合理的"
        "身分等同故事。如果不確定兩者關係，就分別記錄為獨立條目，不要合併。\n\n"
    )

    if prev_summary:
        system_prompt += (
            f"以下是上一次編年史的內容（作為參考，請在此基礎上更新）：\n"
            f"{prev_summary}\n\n"
            "請以最新資料為準更新以上內容。如果某些關係或事件有了新進展，"
            "更新它們的狀態。如果發現新的歷史脈絡，補充進去。\n\n"
        )

    system_prompt += (
        "嚴格回覆以下 JSON 格式（不要加 markdown code block，不要加其他文字）：\n"
        '{"last_updated": "", "last_deep_scan": "", "major_alliances": [{"name": "", "members": [], "formed": "", "context": "", "status": ""}], "major_conflicts": [{"parties": [], "started": "", "cause": "", "status": "", "resolution": "", "current_state": ""}], "key_events": [{"date": "", "event": "", "participants": [], "consequences": "", "significance": ""}], "treaties_agreements": [{"name": "", "parties": [], "date": "", "terms": "", "status": ""}], "power_dynamics": [{"description": "", "context": "", "evolution": ""}], "cultural_traditions": [{"norm": "", "origin": "", "context": ""}], "notable_figures": [{"name": "", "role": "", "history": "", "current_status": ""}]}'
    )

    # Combine inputs — truncate to keep within token budget
    combined_input = ""
    if forum_digest:
        combined_input += f"=== 論壇歷史摘要 ===\n{forum_digest[:20000]}\n\n"
    if channel_history:
        combined_input += f"=== 頻道深層歷史 ===\n{channel_history[:30000]}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": combined_input[:55000]},
    ]

    try:
        result = await asyncio.wait_for(
            call_chat_api(messages, chat_ai_settings, max_tokens=4000, fallback_mode="disabled"), timeout=120
        )
    except Exception as e:
        print(f"📜 社群編年史：AI 分析失敗：{e}")
        return False

    raw = result.get("content", "")
    if not raw:
        tool_calls = result.get("tool_calls", [])
        if tool_calls:
            raw = tool_calls[0].get("function", {}).get("arguments", "")

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json_module.loads(raw)
    except Exception:
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                data = json_module.loads(match.group())
            except Exception:
                print(f"📜 社群編年史：JSON 解析失敗：{raw[:200]}")
                return False
        else:
            print(f"📜 社群編年史：無法解析 AI 回應：{raw[:200]}")
            return False

    if not isinstance(data, dict):
        return False

    now_str = datetime.now(GMT8).strftime("%Y-%m-%d %H:%M")
    data["last_updated"] = now_str
    data["last_deep_scan"] = now_str
    _community_chronicle = data
    _save_community_chronicle()

    n_alliances = len(data.get("major_alliances", []))
    n_conflicts = len(data.get("major_conflicts", []))
    n_events = len(data.get("key_events", []))
    n_treaties = len(data.get("treaties_agreements", []))
    n_power = len(data.get("power_dynamics", []))
    n_traditions = len(data.get("cultural_traditions", []))
    n_figures = len(data.get("notable_figures", []))
    print(f"📜 社群編年史已更新（{now_str}）：{n_alliances} 聯盟, {n_conflicts} 衝突, {n_events} 事件, {n_treaties} 條約, {n_power} 權力動態, {n_traditions} 傳統, {n_figures} 人物")

    return True


async def community_chronicle_loop():
    """Background task: deep scan community history every 24 hours."""
    global _chronicle_last_run
    await asyncio.sleep(300)  # Wait 5 min after startup before first deep scan
    while True:
        try:
            if not _community_awareness_settings.get("enabled"):
                await asyncio.sleep(60)
                continue

            if not chat_ai_settings.get("api_key"):
                await asyncio.sleep(60)
                continue

            guild_id = _community_awareness_settings.get("guild_id")
            if not guild_id:
                if bot.guilds:
                    _community_awareness_settings["guild_id"] = str(bot.guilds[0].id)
                    _save_awareness_settings()
                    guild_id = _community_awareness_settings["guild_id"]
                else:
                    await asyncio.sleep(60)
                    continue

            guild = bot.get_guild(int(guild_id))
            if not guild:
                await asyncio.sleep(60)
                continue

            now = _time.time()
            if _chronicle_last_run and (now - _chronicle_last_run) < _CHRONICLE_INTERVAL:
                await asyncio.sleep(60)
                continue

            _chronicle_last_run = now
            print(f"📜 社群編年史：開始深度歷史掃描 {guild.name}...")
            success = await _deep_scan_community(guild)
            if not success:
                _chronicle_last_run = now  # Count as attempted

        except Exception as e:
            print(f"⚠️ 社群編年史迴圈錯誤：{e}")

        await asyncio.sleep(60)


def _get_community_chronicle_context() -> str:
    """Render the community chronicle as a compact text block for
    injection into the AI system prompt. This gives the AI deep
    historical context — long-standing relationships, past events,
    treaties, and their current status."""
    ch = _community_chronicle
    if not ch.get("last_updated"):
        return ""

    lines = ["─── 社群編年史（僅供參考的次要背景資訊，非查證來源）───"]

    # Major alliances
    alliances = ch.get("major_alliances", [])
    if alliances:
        alliance_parts = []
        for a in alliances[:8]:
            members = ", ".join(a.get("members", []))
            name = a.get("name", "")
            formed = a.get("formed", "")
            context = a.get("context", "")
            status = a.get("status", "")
            name_str = f"「{name}」" if name else ""
            alliance_parts.append(f"  • {name_str}{members}（{formed}）— {context} [{status}]")
        lines.append("\n🤝 重大聯盟：\n" + "\n".join(alliance_parts))

    # Major conflicts
    conflicts = ch.get("major_conflicts", [])
    if conflicts:
        conflict_parts = []
        for c in conflicts[:8]:
            parties = " vs ".join(c.get("parties", []))
            started = c.get("started", "")
            cause = c.get("cause", "")
            status = c.get("status", "")
            current = c.get("current_state", "")
            resolution = c.get("resolution", "")
            detail = f"起因：{cause}" if cause else ""
            if resolution:
                detail += f" → 已解決：{resolution}"
            elif current:
                detail += f" → 目前：{current}"
            conflict_parts.append(f"  • {parties}（{started}）— {detail} [{status}]")
        lines.append("\n⚔️ 重大衝突：\n" + "\n".join(conflict_parts))

    # Key events
    events = ch.get("key_events", [])
    if events:
        event_parts = []
        for e in events[:10]:
            date = e.get("date", "")
            event = e.get("event", "")
            consequences = e.get("consequences", "")
            significance = e.get("significance", "")
            detail = f" — {consequences}" if consequences else ""
            if significance:
                detail += f"（{significance}）"
            event_parts.append(f"  • [{date}] {event}{detail}")
        lines.append("\n📜 關鍵歷史事件：\n" + "\n".join(event_parts))

    # Treaties
    treaties = ch.get("treaties_agreements", [])
    if treaties:
        treaty_parts = []
        for t in treaties[:8]:
            name = t.get("name", "?")
            parties = ", ".join(t.get("parties", []))
            date = t.get("date", "")
            terms = t.get("terms", "")
            status = t.get("status", "")
            treaty_parts.append(f"  • {name}（{parties}，{date}）— {terms} [{status}]")
        lines.append("\n📑 條約與協議：\n" + "\n".join(treaty_parts))

    # Power dynamics
    power = ch.get("power_dynamics", [])
    if power:
        power_parts = []
        for p in power[:5]:
            desc = p.get("description", "")
            evolution = p.get("evolution", "")
            power_parts.append(f"  • {desc} — 演變：{evolution}")
        lines.append("\n👑 權力動態：\n" + "\n".join(power_parts))

    # Cultural traditions
    traditions = ch.get("cultural_traditions", [])
    if traditions:
        trad_parts = []
        for ct in traditions[:5]:
            norm = ct.get("norm", "")
            origin = ct.get("origin", "")
            trad_parts.append(f"  • {norm} — 起源：{origin}")
        lines.append("\n🎭 文化傳統：\n" + "\n".join(trad_parts))

    # Notable figures
    figures = ch.get("notable_figures", [])
    if figures:
        figure_parts = []
        for f in figures[:10]:
            name = f.get("name", "?")
            role = f.get("role", "")
            history = f.get("history", "")
            current = f.get("current_status", "")
            figure_parts.append(f"  • {name}（{role}）— {history} [{current}]")
        lines.append("\n👤 重要人物：\n" + "\n".join(figure_parts))

    lines.append(
        "\n⚠️ 以上是 AI 分析社群歷史得到的編年史，涵蓋長期的聯盟、衝突、"
        "事件因果和人物動態。請自然運用這些歷史理解來回應使用者，"
        "表現得像一個了解社群過去的人。不要主動提及「編年史」這個詞。\n"
        "🚫 反幻覺鐵律：以上條目彼此是獨立的實體記錄，不要自己腦補或推論出"
        "「兩個國家/人物其實是同一個」「A 其實就是 B 的別名」這類等同關係，"
        "除非條目裡明確這樣寫。如果使用者問的細節不在上面資料中，"
        "誠實說不確定，不要編造合理但沒根據的關聯。"
    )

    return "\n".join(lines)



    @app_commands.command(name="clear_tool_cache", description="清除 AI 工具支援快取並重新探測（機器人擁有者限定）")
    async def clear_tool_cache(self, interaction: discord.Interaction):
        """Clear the tools_supported/tools_unsupported cache for the current
        chat AI endpoint and re-probe immediately. Use after switching models
        — the old model may not have supported function calling, but the new
        one might."""
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        global _tools_unsupported_apis, _tools_supported_apis

        _norm = chat_ai_settings.get("api_url", "").rstrip("/")
        if not _norm.endswith("/chat/completions"):
            if _norm.endswith("/v1") or _norm.endswith("/v2"):
                _norm += "/chat/completions"
            else:
                _norm += "/v1/chat/completions"

        was_unsupported = _norm in _tools_unsupported_apis
        was_supported = _norm in _tools_supported_apis

        _tools_unsupported_apis.discard(_norm)
        _tools_supported_apis.discard(_norm)
        save_tools_unsupported()
        save_tools_supported()

        msg = f"🧹 已清除工具快取：\n- 不支援名單：{'已移除' if was_unsupported else '原本就沒有'}\n- 支援名單：{'已移除' if was_supported else '原本就沒有'}\n\n⏳ 正在重新探測..."

        await interaction.response.send_message(msg, ephemeral=True)

        # Re-probe
        await _probe_tools_support(chat_ai_settings, _norm)

        # Report result
        if _norm in _tools_supported_apis:
            await interaction.edit_original_response(content=f"✅ 探測完成：`{_norm}` **支援** function calling！\n工具功能（web_search、search_micropedia 等）現在可以使用了。")
        elif _norm in _tools_unsupported_apis:
            await interaction.edit_original_response(content=f"❌ 探測完成：`{_norm}` **不支援** function calling。\n目前 model 可能不支援 tools 參數，請確認 model 設定。")
        else:
            await interaction.edit_original_response(content=f"⚠️ 探測結果未知（可能逾時或錯誤），請查看 Render 日誌。")


# ──────────────────────────────────────────────
# 社群感知指令
# ──────────────────────────────────────────────

