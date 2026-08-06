# ═════════════════════════════════════════════════════════════════
# Module: 40_awareness (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

class AwarenessGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="awareness", description="社群感知系統")

    @app_commands.command(name="status", description="查看社群感知系統狀態（管理員限定）")
    async def awareness_status(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        aw = _community_awareness
        settings = _community_awareness_settings
        enabled = settings.get("enabled", True)
        interval = settings.get("interval_minutes", 20)
        last_updated = aw.get("last_updated", "尚未分析")

        lines = ["🧠 **社群感知系統狀態**", ""]
        lines.append(f"啟用狀態：{'✅ 已啟用' if enabled else '❌ 已停用'}")
        lines.append(f"分析間隔：{interval} 分鐘")
        lines.append(f"最後更新：{last_updated}")

        sd = aw.get("social_dynamics", {})
        n_members = len(sd.get("active_members", []))
        n_rels = len(sd.get("relationships", []))
        n_events = len(aw.get("recent_events", []))
        n_topics = len(aw.get("current_topics", []))
        n_channels = len(aw.get("channel_cultures", {}))

        lines.append("")
        lines.append("**感知概況：**")
        lines.append(f"  👥 活躍成員：{n_members} 人")
        lines.append(f"  🔗 關係動態：{n_rels} 條")
        lines.append(f"  📅 近期事件：{n_events} 條")
        lines.append(f"  🔥 當前話題：{n_topics} 個")
        lines.append(f"  🎭 頻道文化：{n_channels} 個頻道")

        if n_members > 0:
            lines.append("")
            lines.append("**活躍成員：**")
            for m in sd.get("active_members", [])[:8]:
                lines.append(f"  • {m.get('name', '?')}：{m.get('activity', '')[:50]}")

        if n_rels > 0:
            lines.append("")
            lines.append("**關係動態：**")
            for r in sd.get("relationships", [])[:5]:
                lines.append(f"  • {r.get('a', '?')} ↔ {r.get('b', '?')}（{r.get('type', '?')}）：{r.get('context', '')[:50]}")

        if n_events > 0:
            lines.append("")
            lines.append("**近期事件：**")
            for e in aw.get("recent_events", [])[:5]:
                lines.append(f"  • [{e.get('date', '?')}] {e.get('summary', '')[:60]}")

        if n_topics > 0:
            lines.append("")
            lines.append("**當前話題：**")
            for t in aw.get("current_topics", [])[:5]:
                lines.append(f"  • {t.get('topic', '?')}：{t.get('summary', '')[:50]}")

        if aw.get("last_updated"):
            lines.append("")
            lines.append("💡 提示：社群感知資料會在聊天回覆時自動注入 AI 上下文，讓 AI 像社群成員一樣理解人事物。")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="toggle", description="開啟/關閉社群感知系統（機器人擁有者限定）")
    async def awareness_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        _community_awareness_settings["enabled"] = not _community_awareness_settings.get("enabled", True)
        _save_awareness_settings()
        status = "✅ 已啟用" if _community_awareness_settings["enabled"] else "❌ 已停用"
        await interaction.response.send_message(f"社群感知系統{status}", ephemeral=True)

    @app_commands.command(name="now", description="立即觸發社群感知分析（機器人擁有者限定）")
    async def awareness_now(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        guild_id = _community_awareness_settings.get("guild_id")
        if not guild_id and interaction.guild:
            guild_id = str(interaction.guild.id)
            _community_awareness_settings["guild_id"] = guild_id
            _save_awareness_settings()
        if not guild_id:
            await interaction.response.send_message("❌ 找不到伺服器", ephemeral=True)
            return
        guild = bot.get_guild(int(guild_id))
        if not guild:
            await interaction.response.send_message("❌ 找不到伺服器", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("🧠 正在分析社群動態...", ephemeral=True)
        success = await _analyze_community(guild)
        if success:
            await interaction.followup.send("✅ 社群感知已更新！用 /awareness status 查看", ephemeral=True)
        else:
            await interaction.followup.send("❌ 分析失敗，請檢查日誌", ephemeral=True)

    @app_commands.command(name="chronicle", description="查看社群編年史（深度歷史感知）")
    async def awareness_chronicle(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        ch = _community_chronicle
        last_updated = ch.get("last_updated", "尚未建立")

        lines = ["📜 **社群編年史**", ""]
        lines.append(f"最後更新：{last_updated}")
        lines.append(f"深度掃描：每 24 小時自動執行一次")
        lines.append("")

        n_alliances = len(ch.get("major_alliances", []))
        n_conflicts = len(ch.get("major_conflicts", []))
        n_events = len(ch.get("key_events", []))
        n_treaties = len(ch.get("treaties_agreements", []))
        n_power = len(ch.get("power_dynamics", []))
        n_traditions = len(ch.get("cultural_traditions", []))
        n_figures = len(ch.get("notable_figures", []))

        lines.append("**編年史概況：**")
        lines.append(f"  🤝 重大聯盟：{n_alliances}")
        lines.append(f"  ⚔️ 重大衝突：{n_conflicts}")
        lines.append(f"  📜 關鍵事件：{n_events}")
        lines.append(f"  📑 條約協議：{n_treaties}")
        lines.append(f"  👑 權力動態：{n_power}")
        lines.append(f"  🎭 文化傳統：{n_traditions}")
        lines.append(f"  👤 重要人物：{n_figures}")

        if n_alliances > 0:
            lines.append("")
            lines.append("**🤝 重大聯盟：**")
            for a in ch.get("major_alliances", [])[:8]:
                members = ", ".join(a.get("members", []))
                lines.append(f"  • {a.get('name', '')} — {members}（{a.get('formed', '')}）[{a.get('status', '')}]")

        if n_conflicts > 0:
            lines.append("")
            lines.append("**⚔️ 重大衝突：**")
            for c in ch.get("major_conflicts", [])[:8]:
                parties = " vs ".join(c.get("parties", []))
                lines.append(f"  • {parties}（{c.get('started', '')}）— {c.get('cause', '')[:60]} [{c.get('status', '')}]")

        if n_events > 0:
            lines.append("")
            lines.append("**📜 關鍵事件：**")
            for e in ch.get("key_events", [])[:10]:
                lines.append(f"  • [{e.get('date', '')}] {e.get('event', '')[:60]}")

        if n_treaties > 0:
            lines.append("")
            lines.append("**📑 條約與協議：**")
            for t in ch.get("treaties_agreements", [])[:8]:
                lines.append(f"  • {t.get('name', '?')} — {', '.join(t.get('parties', []))}（{t.get('date', '')}）[{t.get('status', '')}]")

        if n_figures > 0:
            lines.append("")
            lines.append("**👤 重要人物：**")
            for f in ch.get("notable_figures", [])[:10]:
                lines.append(f"  • {f.get('name', '?')}（{f.get('role', '')}）— {f.get('history', '')[:50]} [{f.get('current_status', '')}]")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="deep_scan", description="立即觸發深度歷史掃描，更新編年史（機器人擁有者限定）")
    async def awareness_deep_scan(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        guild_id = _community_awareness_settings.get("guild_id")
        if not guild_id and interaction.guild:
            guild_id = str(interaction.guild.id)
            _community_awareness_settings["guild_id"] = guild_id
            _save_awareness_settings()
        if not guild_id:
            await interaction.response.send_message("❌ 找不到伺服器", ephemeral=True)
            return
        guild = bot.get_guild(int(guild_id))
        if not guild:
            await interaction.response.send_message("❌ 找不到伺服器", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("📜 正在掃描深層歷史...（論壇全文 + 10 個頻道 × 100 則訊息，可能需要 1-2 分鐘）", ephemeral=True)
        success = await _deep_scan_community(guild)
        if success:
            await interaction.followup.send("✅ 社群編年史已更新！用 /awareness chronicle 查看", ephemeral=True)
        else:
            await interaction.followup.send("❌ 掃描失敗，請檢查日誌", ephemeral=True)




# ──────────────────────────────────────────────
# AI 聊天指令
# ──────────────────────────────────────────────

