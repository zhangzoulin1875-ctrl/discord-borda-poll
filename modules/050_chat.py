# ═════════════════════════════════════════════════════════════════
# Module: 50_chat (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

class ChatGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="chat", description="AI 聊天設定")



    @app_commands.command(name="toggle", description="開啟/關閉 AI 聊天功能（機器人擁有者限定）")
    async def chat_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["enabled"] = not chat_ai_settings["enabled"]
        save_chat_ai_settings()
        status = "✅ 開啟" if chat_ai_settings["enabled"] else "❌ 關閉"
        await interaction.response.send_message(f"AI 聊天功能已{status}", ephemeral=True)

    @app_commands.command(name="model", description="設定 AI 聊天模型（機器人擁有者限定）")
    @app_commands.describe(model="模型名稱（例如：gpt-4o-mini, gemini-1.5-flash）")
    async def chat_model(self, interaction: discord.Interaction, model: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["model"] = model
        save_chat_ai_settings()
        await interaction.response.send_message(f"✅ AI 聊天模型已設為 `{model}`", ephemeral=True)

    @app_commands.command(name="vision_model", description="設定/關閉視覺模型（用於識圖，留空=停用）（機器人擁有者限定）")
    @app_commands.describe(model="視覺模型名稱（例如：gpt-4o, gemini-1.5-flash），留空=停用識圖功能")
    async def chat_vision_model(self, interaction: discord.Interaction, model: str = ""):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        model = model.strip()
        chat_ai_settings["vision_model"] = model
        save_chat_ai_settings()
        if model:
            await interaction.response.send_message(
                f"✅ 視覺模型已設為 `{model}`\n"
                f"使用者傳送圖片時，AI 會先用此模型識圖再回答。\n"
                f"使用同一個 API URL/Key，只是模型名不同。", ephemeral=True
            )
        else:
            await interaction.response.send_message("✅ 視覺模型已停用（不會再識圖）。", ephemeral=True)

    @app_commands.command(name="prompt", description="設定 AI 聊天人設（機器人擁有者限定）")
    @app_commands.describe(prompt="系統提示詞（人設描述）")
    async def chat_prompt(self, interaction: discord.Interaction, prompt: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["system_prompt"] = prompt
        save_chat_ai_settings()
        await interaction.response.send_message("✅ AI 聊天人設已更新", ephemeral=True)

    @app_commands.command(name="cooldown", description="設定 AI 聊天冷卻時間（機器人擁有者限定）")
    @app_commands.describe(seconds="冷卻秒數（自動回覆間隔，@提及不受限）")
    async def chat_cooldown(self, interaction: discord.Interaction, seconds: int):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["cooldown_seconds"] = max(0, seconds)
        save_chat_ai_settings()
        await interaction.response.send_message(f"✅ 冷卻時間已設為 {seconds} 秒", ephemeral=True)

    @app_commands.command(name="channel", description="新增/移除頻道白名單（機器人擁有者限定）")
    @app_commands.describe(action="新增或移除", channel="要設定的頻道")
    @app_commands.choices(action=[
        app_commands.Choice(name="新增", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="清空（所有頻道）", value="clear"),
    ])
    async def chat_channel(self, interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        wl = chat_ai_settings.get("channels_whitelist", [])
        act = action.value
        if act == "clear":
            chat_ai_settings["channels_whitelist"] = []
            save_chat_ai_settings()
            await interaction.response.send_message("✅ 頻道白名單已清空（AI 聊天在所有頻道啟用）", ephemeral=True)
        elif channel:
            if act == "add" and channel.id not in wl:
                wl.append(channel.id)
                chat_ai_settings["channels_whitelist"] = wl
                save_chat_ai_settings()
                await interaction.response.send_message(f"✅ 已新增 {channel.mention} 到白名單", ephemeral=True)
            elif act == "remove" and channel.id in wl:
                wl.remove(channel.id)
                chat_ai_settings["channels_whitelist"] = wl
                save_chat_ai_settings()
                await interaction.response.send_message(f"✅ 已從白名單移除 {channel.mention}", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ 頻道已在/不在白名單中", ephemeral=True)

    @app_commands.command(name="filter", description="設定垃圾話過濾強度（機器人擁有者限定）")
    @app_commands.describe(level="過濾強度等級")
    @app_commands.choices(level=[
        app_commands.Choice(name="僅@提及和回覆（推薦）", value="mention"),
        app_commands.Choice(name="關閉（回覆所有訊息）", value="off"),
        app_commands.Choice(name="低（只擋打招呼/連結/emoji）", value="low"),
        app_commands.Choice(name="中（需有實質內容或問題）", value="medium"),
        app_commands.Choice(name="高（嚴格，只回問題和關鍵字）", value="high"),
    ])
    async def chat_filter(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["filter_strength"] = level.value
        save_chat_ai_settings()
        descs = {
            "mention": "僅@提及和回覆：AI 只會在被 @到或被回覆時才說話（不會主動亂接話）",
            "off": "關閉：AI 會回覆所有非空訊息（最自然，但最耗 token）",
            "low": "低：只擋純打招呼、連結、emoji、極短訊息（適合活躍群組）",
            "medium": "中：需要問題、關鍵字、或 15 字以上才回（平衡）",
            "high": "高：只回覆問題和關鍵字（最省 token，但會擋掉很多正常對話）",
        }
        await interaction.response.send_message(
            f"✅ 過濾強度已設為**{level.name}**\n{descs.get(level.value, '')}",
            ephemeral=True
        )

    @app_commands.command(name="server_info", description="查看/更新伺服器結構快取（機器人擁有者限定）")
    async def chat_server_info(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # Force refresh
        await _refresh_server_context(interaction.guild)
        cached = _server_context_cache.get(interaction.guild.id, {})
        d = cached.get("data", {})
        if not d:
            await interaction.followup.send("❌ 無法取得伺服器結構。", ephemeral=True)
            return

        embed = discord.Embed(title=f"🏗️ 伺服器結構：{d['guild_name']}", color=discord.Color.blue())

        ch_list = []
        current_cat = None
        ch_names = []
        for ch in d["channels"]:
            if ch["category"] != current_cat:
                if ch_names:
                    ch_list.append(f"[{current_cat}] {' '.join(ch_names)}")
                    ch_names = []
                current_cat = ch["category"]
            ch_names.append(f"#{ch['name']}")
        if ch_names:
            ch_list.append(f"[{current_cat}] {' '.join(ch_names)}")
        embed.add_field(name=f"📁 頻道（{len(d['channels'])}）", value="\n".join(ch_list)[:1024] or "無", inline=False)

        roles_str = ", ".join(f"{r['name']}({r['member_count']})" for r in d["roles"][:20])
        embed.add_field(name=f"🏷️ 身分組（{len(d['roles'])}）", value=roles_str[:1024] or "無", inline=False)

        emoji_str = " ".join(f":{e['name']}:" for e in d["emojis"][:30])
        embed.add_field(name=f"😀 Emoji（{len(d['emojis'])}）", value=emoji_str[:1024] or "無", inline=False)

        embed.add_field(name="👥 成員", value=f"快取 {len(d['members'])} / {d['member_count']} 總成員", inline=True)
        embed.add_field(name="最後更新", value=f"<t:{int(cached.get('updated', 0))}:R>", inline=True)
        embed.set_footer(text="每 10 分鐘自動更新。此指令可手動刷新。")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="abuse_toggle", description="開關濫用偵測系統（機器人擁有者限定）")
    async def chat_abuse_toggle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        chat_ai_settings["abuse_detection_enabled"] = not chat_ai_settings.get("abuse_detection_enabled", False)
        save_chat_ai_settings()
        status = "✅ 開啟" if chat_ai_settings["abuse_detection_enabled"] else "❌ 關閉"
        await interaction.response.send_message(f"🛡️ 濫用偵測系統已{status}", ephemeral=True)

    @app_commands.command(name="abuse_level", description="設定濫用偵測嚴格度（機器人擁有者限定）")
    @app_commands.describe(level="偵測嚴格度等級")
    @app_commands.choices(level=[
        app_commands.Choice(name="低（寬容，嚴重違規才禁言）", value="low"),
        app_commands.Choice(name="中（標準，刷屏+辱罵都禁）", value="medium"),
        app_commands.Choice(name="高（嚴格，輕微挑釁也禁）", value="high"),
    ])
    async def chat_abuse_level(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        chat_ai_settings["abuse_detection_strictness"] = level.value
        save_chat_ai_settings()
        await interaction.response.send_message(f"✅ 濫用偵測嚴格度已設為**{level.name}**", ephemeral=True)

    @app_commands.command(name="abuse_admins", description="設定是否允許禁言管理員（機器人擁有者限定）")
    @app_commands.describe(enabled="True=可以禁言管理員, False=跳過管理員")
    async def chat_abuse_admins(self, interaction: discord.Interaction, enabled: bool):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        chat_ai_settings["abuse_mute_admins"] = enabled
        save_chat_ai_settings()
        await interaction.response.send_message(
            f"✅ 禁言管理員：{'開啟（管理員也會被禁言）' if enabled else '關閉（管理員不受影響）'}",
            ephemeral=True
        )

    @app_commands.command(name="abuse_log", description="查看最近的禁言記錄（機器人擁有者限定）")
    async def chat_abuse_log(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if not mod_action_log:
            await interaction.response.send_message("📋 目前沒有任何禁言記錄。", ephemeral=True)
            return
        lines = ["📋 **最近禁言記錄**\n"]
        for entry in reversed(mod_action_log[-15:]):
            ts = datetime.datetime.fromtimestamp(entry["timestamp"]).strftime("%m/%d %H:%M")
            mins = entry["duration"] // 60
            lines.append(f"• `{ts}` **{entry['user_name']}** — {mins}分鐘\n  原因：{entry['reason']}（#{entry['channel']}）")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="abuse_unmute", description="手動解除禁言（機器人擁有者限定）")
    @app_commands.describe(user="要解除禁言的用戶")
    async def chat_abuse_unmute(self, interaction: discord.Interaction, user: discord.Member):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        try:
            await user.timeout(None, reason=f"由 {interaction.user.display_name} 手動解除禁言")
            # Reset abuse tracker so they don't get escalated next time
            target_id = str(user.id)
            if target_id in abuse_tracker:
                abuse_tracker[target_id]["message_times"] = []
                abuse_tracker[target_id]["warnings"] = 0
                abuse_tracker[target_id]["total_mutes"] = 0
            await interaction.response.send_message(f"✅ 已解除 {user.mention} 的禁言，並重置濫用追蹤紀錄。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 解除禁言失敗：{e}", ephemeral=True)

    @app_commands.command(name="log_channel", description="設定/清除 AI 紀錄頻道（禁言+對話紀錄，伺服器擁有者限定）")
    @app_commands.describe(action="設定或清除", channel="要設為 log 頻道的頻道（清除時不填）")
    @app_commands.choices(action=[
        app_commands.Choice(name="設定", value="set"),
        app_commands.Choice(name="清除", value="clear"),
    ])
    async def chat_log_channel(self, interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if action.value == "clear":
            chat_ai_settings["log_channel_id"] = None
            save_chat_ai_settings()
            await interaction.response.send_message("✅ AI 紀錄頻道已清除。", ephemeral=True)
        elif action.value == "set" and channel:
            chat_ai_settings["log_channel_id"] = channel.id
            save_chat_ai_settings()
            await interaction.response.send_message(
                f"✅ AI 紀錄頻道已設為 {channel.mention}\n"
                f"AI 對話紀錄 + 自動禁言紀錄將發送到此頻道。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("⚠️ 請選擇動作和頻道。", ephemeral=True)

    @app_commands.command(name="log_debug", description="查看對話紀錄發送統計 + 用真實流程即時測試（機器人擁有者限定）")
    async def chat_log_debug(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        stats = _log_send_stats
        embed = discord.Embed(
            title="🔍 對話紀錄發送診斷",
            description="這個統計數字是**本次啟動以來**累積的（重啟會歸零），"
                        "讓你不需要 Render log 存取權限就能知道 ai-log 是否正常運作。",
            color=discord.Color.blue(),
        )
        embed.add_field(name="總嘗試次數", value=str(stats["attempts"]), inline=True)
        embed.add_field(name="✅ 成功", value=str(stats["successes"]), inline=True)
        embed.add_field(name="❌ 失敗", value=str(stats["failures"]), inline=True)
        embed.add_field(name="⏭️ 跳過（未設定/私訊）", value=str(stats["skips"]), inline=True)
        embed.add_field(name="最後成功時間", value=stats["last_success_at"] or "（尚無）", inline=False)
        if stats["failures"] > 0:
            embed.add_field(name="最後失敗時間", value=stats["last_failure_at"] or "?", inline=True)
            embed.add_field(name="最後失敗原因", value=f"```{stats['last_error'][:500]}```", inline=False)
        if stats["skips"] > 0:
            embed.add_field(name="最後跳過原因", value=f"{stats['last_skip_reason']}（{stats['last_skip_at']}）", inline=False)

        # ── 即時測試：直接呼叫真正對話紀錄會用的 _send_chat_log 函式 ──
        # 跟 /chat log_test 不同：log_test 只是自己組一個 embed 直接送，
        # 不會經過 _resolve_log_channel + _send_chat_log 的完整邏輯，
        # 這裡改成呼叫「真實對話會走的那條路徑」，確保診斷結果跟實際情況一致。
        class _FakeMsg:
            pass
        fake = _FakeMsg()
        fake.channel = interaction.channel
        fake.author = interaction.user
        fake.content = "（/chat log_debug 即時測試）"
        fake.guild = interaction.guild
        fake.attachments = []

        _before_success = stats["successes"]
        _before_fail = stats["failures"]
        try:
            await _send_chat_log(
                fake,
                "這是 /chat log_debug 的即時測試訊息",
                "如果你在 ai-log 頻道看到這則紀錄，代表對話紀錄功能運作正常。",
                model_info={"model": "log_debug_test", "fallback": False, "diag": []},
            )
        except Exception as e:
            embed.add_field(name="⚠️ 即時測試例外", value=f"```{e}```", inline=False)

        if stats["successes"] > _before_success:
            embed.add_field(name="🧪 即時測試結果", value="✅ 成功（用真實流程走完整個發送邏輯）", inline=False)
            embed.color = discord.Color.green()
        elif stats["failures"] > _before_fail:
            embed.add_field(name="🧪 即時測試結果", value=f"❌ 失敗：{stats['last_error'][:300]}", inline=False)
            embed.color = discord.Color.red()
        else:
            embed.add_field(name="🧪 即時測試結果", value="⏭️ 跳過（可能未設定 log_channel_id）", inline=False)
            embed.color = discord.Color.orange()

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="log_test", description="發送測試訊息到 AI 紀錄頻道，驗證設定是否正常（機器人擁有者限定）")
    async def chat_log_test(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        log_ch_id = chat_ai_settings.get("log_channel_id")
        if not log_ch_id:
            await interaction.response.send_message(
                "❌ 尚未設定 AI 紀錄頻道。請先用 `/chat log_channel` 設定。",
                ephemeral=True
            )
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        log_ch, err = await _resolve_log_channel(interaction.guild)
        if not log_ch:
            await interaction.followup.send(f"❌ 找不到紀錄頻道：{err}", ephemeral=True)
            return
        try:
            test_embed = discord.Embed(
                title="🧪 測試訊息",
                description=f"這是 `/chat log_test` 發送的測試訊息，確認 <#{log_ch_id}> 設定正常。",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            test_embed.set_footer(text=f"由 {interaction.user.display_name} 觸發")
            await log_ch.send(embed=test_embed)
            await interaction.followup.send(f"✅ 測試訊息已成功發送到 {log_ch.mention}！", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Bot 沒有在 {log_ch.mention} 發送訊息的權限。\n"
                f"請到該頻道 → 頻道設定 → 權限，確認 Bot 有「查看頻道」「發送訊息」「嵌入連結」權限。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 發送失敗：{e}", ephemeral=True)

    @app_commands.command(name="test", description="測試 AI 聊天回覆（機器人擁有者限定）")
    @app_commands.describe(message="要測試的訊息")
    async def chat_test(self, interaction: discord.Interaction, message: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if not chat_ai_settings.get("api_key"):
            await interaction.response.send_message("❌ 尚未設定 AI 聊天 API Key。請到 Dashboard 設定。", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        try:
            # Use generate_chat_reply for full memory integration
            class FakeMsg:
                pass
            fake = FakeMsg()
            fake.channel = interaction.channel
            fake.author = interaction.user
            fake.content = message
            fake.guild = interaction.guild  # needed by generate_chat_reply's server-awareness lookup
            reply, new_facts, mod_action, _ = await generate_chat_reply(fake, chat_ai_settings)
            # Strip [MEMORY:] from test reply
            if "[MEMORY:" in reply:
                reply = reply.rsplit("[MEMORY:", 1)[0].strip()
            if "[MOD:" in reply:
                reply = reply.rsplit("[MOD:", 1)[0].strip()
            result = f"✅ AI 回覆：\n{reply}"
            if new_facts:
                result += f"\n\n🧠 記憶更新：{', '.join(new_facts)}"
            await interaction.followup.send(result, ephemeral=True)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"❌ /chat test 失敗，完整 traceback:\n{tb}")
            short_tb = tb.strip().split(chr(10))[-1][:200]
            await interaction.followup.send(f"❌ AI 聊天測試失敗：{type(e).__name__}: {e}\n```{short_tb}```", ephemeral=True)

    @app_commands.command(name="memory", description="查看 AI 對你的記憶")
    async def chat_memory(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        mem = user_memories.get(user_id, {})
        facts = mem.get("facts", [])
        count = mem.get("interaction_count", 0)
        if not facts:
            await interaction.response.send_message("🧠 AI 目前對你沒有任何記憶。多聊天就會開始記住你了！", ephemeral=True)
            return
        lines = [f"🧠 AI 對 **{interaction.user.display_name}** 的記憶（{len(facts)} 條 / {count} 次互動）："]
        for i, f in enumerate(facts, 1):
            lines.append(f"{i}. {f}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="memory_clear", description="清除 AI 對你的記憶（擁有者可清除指定用戶）")
    @app_commands.describe(user="要清除記憶的用戶（不填則清除自己的）")
    async def chat_memory_clear(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        if user and not is_owner(interaction):
            await interaction.response.send_message("❌ 只有管理員能清除他人的記憶。", ephemeral=True)
            return
        target_id = str(target.id)
        if target_id in user_memories:
            old_count = len(user_memories[target_id].get("facts", []))
            del user_memories[target_id]
            save_user_memories()
            await interaction.response.send_message(
                f"✅ 已清除 AI 對 {target.mention} 的記憶（原本有 {old_count} 條）",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(f"ℹ️ {target.mention} 沒有任何記憶。", ephemeral=True)

    @app_commands.command(name="debug", description="診斷 AI 聊天問題（機器人擁有者限定）")
    async def chat_debug(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        lines = []
        lines.append(f"**AI 聊天診斷**")
        lines.append(f"")
        lines.append(f"**1. 功能狀態**")
        lines.append(f"  enabled: {'✅ 開啟' if chat_ai_settings.get('enabled') else '❌ 關閉'}")
        lines.append(f"  → 如果關閉，請執行 `/chat toggle`")
        lines.append(f"")
        lines.append(f"**2. API 設定**")
        lines.append(f"  API Key: {'✅ 已設定' if chat_ai_settings.get('api_key') else '❌ 未設定'}")
        lines.append(f"  API URL: `{chat_ai_settings.get('api_url', '未設定')}`")
        lines.append(f"  Model: `{chat_ai_settings.get('model', '未設定')}`")
        lines.append(f"")
        lines.append(f"**3. Message Content Intent**")
        has_intent = bot.intents.message_content
        lines.append(f"  程式碼: {'✅ 已啟用' if has_intent else '❌ 未啟用'}")
        lines.append(f"  → 如果上面是 ✅ 但 bot 仍不回覆，請到 Discord Developer Portal")
        lines.append(f"  → Bot → Privileged Gateway Intents → 開啟 MESSAGE CONTENT INTENT")
        lines.append(f"")
        lines.append(f"**4. 頻道白名單**")
        wl = chat_ai_settings.get("channels_whitelist", [])
        if wl:
            lines.append(f"  {'、'.join(f'<#{cid}>' for cid in wl)}")
            lines.append(f"  → 只有以上頻道會回覆，其他頻道被忽略")
        else:
            lines.append(f"  所有頻道（無限制）")
        lines.append(f"")
        lines.append(f"**5. 冷卻時間**")
        lines.append(f"  {chat_ai_settings.get('cooldown_seconds', 60)} 秒（@提及不受限）")
        _min_int = chat_ai_settings.get("min_response_interval", 0)
        lines.append(f"  全域最短間隔：{'關閉' if _min_int == 0 else f'{_min_int} 秒'}")
        lines.append(f"")
        filter_str = chat_ai_settings.get("filter_strength", "mention")
        filter_descs = {
            "off": "關閉：回覆所有非空訊息",
            "low": "低：只擋打招呼/連結/emoji/極短",
            "medium": "中：需問題/關鍵字/15字以上",
            "high": "高：只回問題和關鍵字",
        }
        lines.append(f"**6. 過濾強度**")
        lines.append(f"  目前：{filter_str} — {filter_descs.get(filter_str, '')}")
        lines.append(f"  → 用 `/chat filter` 調整")
        lines.append(f"")
        lines.append(f"**7. 伺服器結構感知**")
        if _server_context_cache:
            for gid, cache in _server_context_cache.items():
                d = cache.get("data", {})
                age = int(_time.time() - cache.get("updated", 0))
                lines.append(f"  Guild {gid}: {d.get('guild_name', '?')} — {len(d.get('channels', []))} 頻道, {len(d.get('members', []))} 成員快取 ({age}s ago)")
        else:
            lines.append(f"  ❌ 尚未建立快取")
        lines.append(f"  → 用 `/chat server_info` 手動刷新")
        lines.append(f"")
        lines.append(f"**8. 濫用偵測**")
        abuse_on = chat_ai_settings.get("abuse_detection_enabled", False)
        abuse_strict = chat_ai_settings.get("abuse_detection_strictness", "medium")
        abuse_admins = chat_ai_settings.get("abuse_mute_admins", False)
        lines.append(f"  狀態：{'✅ 開啟' if abuse_on else '❌ 關閉'}")
        lines.append(f"  嚴格度：{abuse_strict}")
        lines.append(f"  禁言管理員：{'是' if abuse_admins else '否'}")
        if mod_action_log:
            lines.append(f"  累計禁言次數：{len(mod_action_log)}")
        lines.append(f"  → `/chat abuse_toggle` 開關 | `/chat abuse_level` 調整 | `/chat abuse_log` 查看記錄")
        lines.append(f"")
        lines.append(f"**9. AI 紀錄頻道**")
        log_ch_id = chat_ai_settings.get("log_channel_id")
        if log_ch_id:
            lines.append(f"  ✅ 已設定：<#{log_ch_id}>")
            lines.append(f"  對話紀錄 + 禁言紀錄都會發送到此頻道")
        else:
            lines.append(f"  ❌ 未設定")
            lines.append(f"  → 用 `/chat log_channel` 設定")
        lines.append(f"")
        lines.append(f"**7. 測試**")
        lines.append(f"  請在這個頻道發一則 >15 字的訊息，然後查看 Render logs")
        lines.append(f"  應該能看到 `📩 on_message: ...` 的日誌")
        lines.append(f"")
        embed = discord.Embed(title="🔍 AI 聊天診斷", description="\n".join(lines), color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status", description="查看 AI 聊天設定")
    async def chat_status(self, interaction: discord.Interaction):
        enabled = "✅ 開啟" if chat_ai_settings["enabled"] else "❌ 關閉"
        model = chat_ai_settings.get("model", "gpt-4o-mini")
        cooldown = chat_ai_settings.get("cooldown_seconds", 60)
        wl = chat_ai_settings.get("channels_whitelist", [])
        if wl:
            channels = ", ".join(f"<#{cid}>" for cid in wl)
        else:
            channels = "所有頻道"
        key_set = "✅ 已設定" if chat_ai_settings.get("api_key") else "❌ 未設定"

        embed = discord.Embed(title="🤖 AI 聊天設定", color=discord.Color.green())
        embed.add_field(name="狀態", value=enabled, inline=True)
        embed.add_field(name="API Key", value=key_set, inline=True)
        embed.add_field(name="模型", value=f"`{model}`", inline=True)
        embed.add_field(name="冷卻時間", value=f"{cooldown} 秒", inline=True)
        _min_int = chat_ai_settings.get("min_response_interval", 0)
        embed.add_field(name="全域最短間隔", value=f"{_min_int} 秒" if _min_int > 0 else "關閉", inline=True)
        filter_str = chat_ai_settings.get("filter_strength", "mention")
        filter_names = {"off": "關閉", "low": "低", "medium": "中", "high": "高"}
        embed.add_field(name="過濾強度", value=filter_names.get(filter_str, filter_str), inline=True)
        embed.add_field(name="頻道白名單", value=channels, inline=False)
        mem_count = len(user_memories)
        embed.add_field(name="用戶記憶", value=f"已記住 {mem_count} 位使用者", inline=True)
        abuse_on = chat_ai_settings.get("abuse_detection_enabled", False)
        abuse_strict = chat_ai_settings.get("abuse_detection_strictness", "medium")
        embed.add_field(name="濫用偵測", value=f"{'✅' if abuse_on else '❌'} {abuse_strict}", inline=True)
        log_ch_id = chat_ai_settings.get("log_channel_id")
        log_ch_val = f"<#{log_ch_id}>" if log_ch_id else "未設定"
        embed.add_field(name="紀錄頻道", value=log_ch_val, inline=True)
        _drive_ok = globals().get("_drive_load_succeeded", None)
        _drive_status = "✅ 本次啟動成功" if _drive_ok else ("⚠️ 本次啟動失敗（設定可能是硬編碼預設值）" if _drive_ok is False else "❓ 未知")
        embed.add_field(name="本次啟動 Drive 設定載入", value=_drive_status, inline=False)
        # Micropedia status
        micro_on = chat_ai_settings.get("micropedia_enabled", True)
        micro_max = chat_ai_settings.get("micropedia_max_results", 5)
        embed.add_field(name="微國家百科", value=f"{'✅' if micro_on else '❌'} (最多{micro_max}篇)", inline=True)
        vm = chat_ai_settings.get("vision_model", "")
        embed.add_field(name="視覺模型（識圖）", value=f"`{vm}`" if vm else "❌ 未設定", inline=True)
        embed.set_footer(text="/chat toggle | /chat filter | /chat abuse_toggle | /chat log_channel | /chat log_debug | /chat memory | /chat micropedia | /chat vision_model | /chat debug")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="micropedia", description="開關微國家百科查詢功能（機器人擁有者限定）")
    @app_commands.describe(action="開啟或關閉", max_results="每次查詢最多抓取幾篇文章（1-10）")
    @app_commands.choices(action=[
        app_commands.Choice(name="開啟", value="on"),
        app_commands.Choice(name="關閉", value="off"),
    ])
    async def chat_micropedia(self, interaction: discord.Interaction, action: app_commands.Choice[str] = None, max_results: int = None):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if action:
            chat_ai_settings["micropedia_enabled"] = (action.value == "on")
            save_chat_ai_settings()
        if max_results is not None:
            chat_ai_settings["micropedia_max_results"] = max(1, min(10, max_results))
            save_chat_ai_settings()
        status = "✅ 開啟" if chat_ai_settings.get("micropedia_enabled", True) else "❌ 關閉"
        max_r = chat_ai_settings.get("micropedia_max_results", 5)
        await interaction.response.send_message(
            f"📚 微國家百科查詢：{status}\n每次查詢最多抓取：{max_r} 篇文章\n"
            f"來源：https://www.micropedia.site/",
            ephemeral=True
        )

    @app_commands.command(name="micropedia_test", description="測試微國家百科查詢（機器人擁有者限定）")
    @app_commands.describe(query="要搜尋的關鍵字")
    async def chat_micropedia_test(self, interaction: discord.Interaction, query: str):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        max_r = chat_ai_settings.get("micropedia_max_results", 5)
        result = await _fetch_micropedia(query, max_r)
        if not result:
            await interaction.followup.send(f"📚 搜尋「{query}」沒有找到結果。", ephemeral=True)
        else:
            # Truncate for Discord (2000 char limit)
            display = result[:1900]
            if len(result) > 1900:
                display += "..."
            await interaction.followup.send(f"📚 搜尋「{query}」的結果：\n\n{display}", ephemeral=True)


    @app_commands.command(name="emoji_alias", description="設定表情符號的別名，讓 AI 看懂含義（機器人擁有者限定）")
    @app_commands.describe(
        emoji="要設定別名的表情符號（直接貼上表情或輸入名稱）",
        alias="人類可讀的別名（例如：偉廷微笑）。留空則清除該表情的別名",
    )
    async def system_emoji_alias(self, interaction: discord.Interaction, emoji: str, alias: str = ""):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        # Parse emoji: user may type the raw <:name:id> or just the name
        m = re.match(r"<a?:(\w+):(\d+)>", emoji)
        if m:
            emoji_name = m.group(1)
            emoji_id = m.group(2)
        else:
            emoji_name = emoji.strip().strip(":")
            emoji_id = None
            for e in interaction.guild.emojis:
                if e.name == emoji_name:
                    emoji_id = str(e.id)
                    break
            if not emoji_id:
                await interaction.response.send_message(
                    f"❌ 找不到名為「{emoji}」的表情符號。\n請直接從 Discord 表情選擇器貼上完整的表情，或輸入表情名稱。",
                    ephemeral=True
                )
                return

        emoji_obj = None
        for e in interaction.guild.emojis:
            if str(e.id) == emoji_id:
                emoji_obj = e
                break

        if not alias:
            if emoji_name in emoji_aliases:
                del emoji_aliases[emoji_name]
                save_emoji_aliases()
                await interaction.response.send_message(f"✅ 已清除表情 `{emoji_name}` 的別名。", ephemeral=True)
            else:
                await interaction.response.send_message(f"ℹ️ 表情 `{emoji_name}` 本來就沒有設定別名。", ephemeral=True)
        else:
            emoji_aliases[emoji_name] = {
                "alias": alias,
                "emoji_id": emoji_id,
                "animated": bool(emoji_obj and emoji_obj.animated),
            }
            save_emoji_aliases()
            prefix = "a" if emoji_obj and emoji_obj.animated else ""
            await interaction.response.send_message(
                f"✅ 表情別名已設定：\n"
                f"  表情：<{prefix}:{emoji_name}:{emoji_id}>\n"
                f"  別名：{alias}\n"
                f"  AI 現在會知道這個表情代表「{alias}」，並在合適的時機使用。\n"
                f"  （AI 也可以用 `:{alias}:` 來表示，系統會自動轉換）",
                ephemeral=True
            )

    @app_commands.command(name="emoji_list", description="查看所有已設定別名的表情符號")
    async def system_emoji_list(self, interaction: discord.Interaction):
        if not emoji_aliases:
            await interaction.response.send_message(
                "ℹ️ 目前沒有設定任何表情別名。\n"
                "用 `/system emoji_alias` 來設定，讓 AI 看懂自訂表情的含義。",
                ephemeral=True
            )
            return

        lines = ["🧩 已設定的表情別名："]
        for name, data in emoji_aliases.items():
            prefix = "a" if data.get("animated") else ""
            eid = data.get("emoji_id", "")
            alias_label = data.get("alias", "")
            lines.append(f"<{prefix}:{name}:{eid}> = {alias_label}")

        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n..."
        await interaction.response.send_message(text, ephemeral=True)





# ──────────────────────────────────────────────
# 專屬 AI 聊天室指令群組
# ──────────────────────────────────────────────

class ChatRoomGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="room", description="專屬 AI 聊天室設定")

    @app_commands.command(name="setup", description="設定專屬 AI 聊天室面板頻道（機器人擁有者限定）")
    @app_commands.describe(channel="要放置「開啟聊天室」按鈕面板的頻道")
    async def chat_room_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # If the panel is moving to a different channel, try to clean up the
        # old panel message in the OLD channel first (it won't be found by
        # the helper below, which only scans the NEW target channel).
        old_channel_id = ai_chat_rooms.get("panel_channel_id")
        old_msg_id = ai_chat_rooms.get("panel_message_id")
        if old_channel_id and int(old_channel_id) != channel.id and old_msg_id:
            old_ch = interaction.guild.get_channel(int(old_channel_id)) if interaction.guild else None
            if old_ch:
                try:
                    old_msg = await old_ch.fetch_message(int(old_msg_id))
                    await old_msg.delete()
                    print(f"🧹 已刪除舊頻道 #{old_ch.name} 中的聊天室面板")
                except Exception:
                    pass

        ai_chat_rooms["panel_channel_id"] = channel.id
        save_ai_chat_rooms()

        # Use the shared helper — also cleans up any previous panel message
        # in this channel (or the old configured channel, if different).
        sent = await _repost_chat_room_panel(channel)

        if sent:
            await interaction.followup.send(
                f"✅ AI 聊天室面板已設定在 {channel.mention}\n"
                f"用戶現在可以點擊按鈕建立自己的聊天室。\n"
                f"記得用 `/room category` 設定聊天室分類頻道。\n"
                f"往後每次重新部署，面板會自動偵測舊按鈕並重新發送，不需要手動重跑此指令。",
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ 面板發送失敗，請查看日誌。", ephemeral=True)

    @app_commands.command(name="category", description="設定 AI 聊天室建立的分類頻道（機器人擁有者限定）")
    @app_commands.describe(category="新建聊天室會建立在這個分類下")
    async def chat_room_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        ai_chat_rooms["category_id"] = category.id
        save_ai_chat_rooms()
        await interaction.response.send_message(
            f"✅ AI 聊天室分類已設為「{category.name}」\n"
            f"新建的聊天室頻道會出現在這個分類下。",
            ephemeral=True
        )

    @app_commands.command(name="list", description="列出所有活躍的 AI 聊天室（機器人擁有者限定）")
    async def chat_room_list(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        rooms = ai_chat_rooms.get("rooms", {})
        if not rooms:
            await interaction.response.send_message("目前沒有活躍的 AI 聊天室。", ephemeral=True)
            return
        lines = [f"📋 活躍 AI 聊天室（共 {len(rooms)} 間）："]
        for ch_id, room in rooms.items():
            user_name = room.get("user_name", "?")
            msg_count = room.get("message_count", 0)
            created = room.get("created_at", 0)
            age_min = int((_time.time() - created) / 60) if created else 0
            lines.append(f"• <#{ch_id}> — {user_name}（{msg_count} 則訊息，{age_min} 分鐘前建立）")
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n..."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="max_rooms", description="設定 AI 聊天室數量上限（機器人擁有者限定）")
    @app_commands.describe(max_rooms="最大聊天室數量（預設 50）")
    async def chat_room_max(self, interaction: discord.Interaction, max_rooms: int):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if max_rooms < 1:
            max_rooms = 1
        if max_rooms > 500:
            max_rooms = 500
        ai_chat_rooms["max_rooms"] = max_rooms
        save_ai_chat_rooms()
        await interaction.response.send_message(f"✅ AI 聊天室數量上限已設為 {max_rooms}", ephemeral=True)

    @app_commands.command(name="history", description="設定 AI 聊天室歷史訊息數量（機器人擁有者限定）")
    @app_commands.describe(messages="抓取最近幾則訊息作為 AI 上下文（預設 50，建議 20-100）")
    async def chat_room_history(self, interaction: discord.Interaction, messages: int):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if messages < 5:
            messages = 5
        if messages > 200:
            messages = 200
        ai_chat_rooms["max_history_messages"] = messages
        save_ai_chat_rooms()
        await interaction.response.send_message(
            f"✅ AI 聊天室歷史訊息數量已設為 {messages}\n"
            f"較多訊息 = AI 記得更多對話，但每次回覆會較慢、消耗較多 token。",
            ephemeral=True
        )

    @app_commands.command(name="toggle", description="開啟/關閉 AI 聊天室功能（機器人擁有者限定）")
    async def chat_room_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        ai_chat_rooms["enabled"] = not ai_chat_rooms.get("enabled", True)
        save_ai_chat_rooms()
        status = "✅ 開啟" if ai_chat_rooms["enabled"] else "❌ 關閉"
        await interaction.response.send_message(f"AI 聊天室功能已{status}", ephemeral=True)


# ──────────────────────────────────────────────
# 會議指令群組
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# 獨立文生圖指令（/draw）— 放在 ChatGroup 外部是因為
# app_commands.Group 有 25 個子指令上限，ChatGroup 已滿額。
# ──────────────────────────────────────────────

@app_commands.command(name="draw", description="文生圖 — 根據文字描述生成圖片")
@app_commands.describe(prompt="要生成的圖片描述（越詳細越好）")
async def draw_command(interaction: discord.Interaction, prompt: str):
    import aiohttp as _aiohttp

    if not chat_ai_settings.get("t2i_enabled"):
        await interaction.response.send_message("❌ 文生圖功能未啟用。請到 Dashboard → AI 聊天設定 → 文生圖區塊開啟。", ephemeral=True)
        return

    if not chat_ai_settings.get("t2i_api_url") or not chat_ai_settings.get("t2i_model"):
        await interaction.response.send_message("❌ 文生圖 API 未設定完整（需要 URL、Key 和模型名稱）。請到 Dashboard 設定。", ephemeral=True)
        return

    # Rate limit check
    _uid = str(interaction.user.id)
    _allowed, _reason = _check_t2i_rate_limit(_uid, chat_ai_settings)
    if not _allowed:
        await interaction.response.send_message(_reason, ephemeral=True)
        return

    # Acknowledge immediately (Discord requires response within 3s)
    await interaction.response.send_message(f"🎨 正在生成圖片，請稍候...\n**提示詞：** {prompt[:200]}", ephemeral=False)

    # Generate image
    result = await _generate_image(prompt, chat_ai_settings)

    # 不管成功或失敗都記錄到 ai-log 頻道（fire-and-forget，不阻塞回覆）
    try:
        asyncio.ensure_future(_send_t2i_log(interaction.guild, interaction.user, prompt, result))
    except Exception as _log_e:
        print(f"⚠️ T2I 紀錄排程失敗: {_log_e}")

    if result.get("success"):
        _record_t2i_usage(_uid)
        image_url = result.get("image_url")
        image_path = result.get("image_path")
        revised_prompt = result.get("revised_prompt")

        channel = result.get("channel", "")
        channel_tag = " ✨高級通道" if channel == "premium" else ""
        text_parts = [f"🎨 **{interaction.user.display_name}** 生成的圖片！{channel_tag}"]
        if revised_prompt and revised_prompt != prompt:
            text_parts.append(f"（AI 優化後的提示詞：{revised_prompt[:100]}）")

        try:
            if image_path:
                import os as _os
                file = discord.File(image_path, filename="generated.png")
                await interaction.edit_original_response(content="\n".join(text_parts), attachments=[file])
                try:
                    _os.remove(image_path)
                except Exception:
                    pass
            elif image_url:
                # Try to download and send as file
                try:
                    async with _aiohttp.ClientSession(timeout=_aiohttp.ClientTimeout(total=30)) as sess:
                        async with sess.get(image_url) as img_resp:
                            if img_resp.status == 200:
                                import io as _io
                                img_bytes = await img_resp.read()
                                ct = img_resp.headers.get("Content-Type", "image/png")
                                ext = "png"
                                if "jpeg" in ct or "jpg" in ct:
                                    ext = "jpg"
                                elif "webp" in ct:
                                    ext = "webp"
                                file = discord.File(_io.BytesIO(img_bytes), filename=f"generated.{ext}")
                                await interaction.edit_original_response(content="\n".join(text_parts), attachments=[file])
                            else:
                                embed = discord.Embed(title="🎨 生成圖片", color=0x5865f2)
                                embed.set_image(url=image_url)
                                embed.set_footer(text=f"提示詞: {prompt[:200]}")
                                await interaction.edit_original_response(content="\n".join(text_parts), embeds=[embed])
                except Exception:
                    embed = discord.Embed(title="🎨 生成圖片", color=0x5865f2)
                    embed.set_image(url=image_url)
                    embed.set_footer(text=f"提示詞: {prompt[:200]}")
                    await interaction.edit_original_response(content="\n".join(text_parts), embeds=[embed])
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ 圖片發送失敗: {e}")
    else:
        await interaction.edit_original_response(content=f"❌ 文生圖失敗：{result.get('error', '未知錯誤')}")

bot.tree.add_command(draw_command)
