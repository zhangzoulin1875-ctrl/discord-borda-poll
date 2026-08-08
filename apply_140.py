import os

with open('modules/140_horse_racing.py', 'r', encoding='utf-8') as f:
    code = f.read()

# 1. Add try-import at top
code = code.replace(
    'import time as _hr_time\n',
    'import time as _hr_time\n\ntry:\n    from discord_borda_poll import is_admin, ICEA_GUILD_ID, get_channel_any\nexcept ImportError:\n    pass\n'
)

# 2. Add guild_channels to horse_racing_settings
code = code.replace(
    'horse_racing_settings = {"channel_id": None, "last_race_end_time": 0, "force_next": False}',
    'horse_racing_settings = {"channel_id": None, "guild_channels": {}, "last_race_end_time": 0, "force_next": False}'
)

# 3. Modify _get_horse_racing_channel and add _get_all_horse_channels
old_get_ch = '''def _get_horse_racing_channel():
    ch_id = horse_racing_settings.get("channel_id")
    if not ch_id:
        return None
    for guild in bot.guilds:
        ch = guild.get_channel(int(ch_id))
        if ch:
            return ch
    return None'''

new_get_ch = '''def _get_all_horse_channels():
    channels = []
    main_ch_id = horse_racing_settings.get("channel_id")
    if main_ch_id:
        ch = get_channel_any(int(main_ch_id))
        if ch and ch not in channels:
            channels.append(ch)

    guild_channels = horse_racing_settings.get("guild_channels", {})
    for g_id, ch_id in guild_channels.items():
        if ch_id:
            ch = get_channel_any(int(ch_id))
            if ch and ch not in channels:
                channels.append(ch)

    return channels


def _get_horse_racing_channel():
    return _get_all_horse_channels()'''

assert old_get_ch in code, 'old_get_ch not found'
code = code.replace(old_get_ch, new_get_ch)

# 4. Modify _start_new_race
old_start_race = '''async def _start_new_race(channel):
    global current_race
    race = _generate_race()
    race["channel_id"] = channel.id
    embed = _build_race_betting_embed(race)
    view = HorseBettingView(race["race_id"])
    try:
        msg = await channel.send(embed=embed, view=view)
        race["message_id"] = msg.id
    except Exception as e:
        print(f"⚠️ 賽馬公告發送失敗：{e}")
    current_race = race
    save_horse_racing()
    print(f"🏇 新賽事開始：{race['race_id']}，{len(race['horses'])} 匹馬參賽，投注截止於 {int(race['betting_end_time'])}")'''

new_start_race = '''async def _start_new_race(channels=None):
    global current_race
    if channels is None:
        channels = _get_all_horse_channels()
    elif isinstance(channels, discord.TextChannel):
        channels = [channels]

    race = _generate_race()
    embed = _build_race_betting_embed(race)
    view = HorseBettingView(race["race_id"])

    channel_ids = []
    messages = {}

    for channel in channels:
        try:
            msg = await channel.send(embed=embed, view=view)
            channel_ids.append(channel.id)
            messages[str(channel.id)] = msg.id
        except Exception as e:
            print(f"⚠️ 賽馬公告發送至頻道 {channel.id} 失敗：{e}")

    race["channel_ids"] = channel_ids
    race["messages"] = messages
    if channel_ids:
        race["channel_id"] = channel_ids[0]
        race["message_id"] = messages.get(str(channel_ids[0]))

    current_race = race
    save_horse_racing()
    print(f"🏇 新賽事開始：{race['race_id']}，{len(race['horses'])} 匹馬參賽，投注截止於 {int(race['betting_end_time'])}")'''

assert old_start_race in code, 'old_start_race not found'
code = code.replace(old_start_race, new_start_race)

# 5. Modify _reattach_betting_view
old_reattach = '''async def _reattach_betting_view():
    """機器人重啟後，若賽事仍在投注階段，重新掛載按鈕面板（舊面板按鈕在重啟後會失效）。"""
    if not current_race or current_race.get("status") != "betting":
        return
    ch_id = current_race.get("channel_id")
    msg_id = current_race.get("message_id")
    if not ch_id or not msg_id:
        return
    channel = None
    for guild in bot.guilds:
        ch = guild.get_channel(int(ch_id))
        if ch:
            channel = ch
            break
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        view = HorseBettingView(current_race["race_id"])
        await msg.edit(view=view)
        print(f"🔄 賽馬投注面板已在重啟後重新掛載：{current_race['race_id']}")
    except Exception as e:
        print(f"⚠️ 賽馬投注面板重新掛載失敗：{e}")'''

new_reattach = '''async def _reattach_betting_view():
    """機器人重啟後，若賽事仍在投注階段，重新掛載按鈕面板（舊面板按鈕在重啟後會失效）。"""
    if not current_race or current_race.get("status") != "betting":
        return

    msg_map = current_race.get("messages", {})
    if not msg_map and current_race.get("channel_id") and current_race.get("message_id"):
        msg_map = {str(current_race["channel_id"]): current_race["message_id"]}

    for ch_id_str, msg_id in msg_map.items():
        if not ch_id_str or not msg_id:
            continue
        channel = get_channel_any(int(ch_id_str))
        if not channel:
            continue
        try:
            msg = await channel.fetch_message(int(msg_id))
            view = HorseBettingView(current_race["race_id"])
            await msg.edit(view=view)
            print(f"🔄 賽馬投注面板已在頻道 {ch_id_str} 重新掛載：{current_race['race_id']}")
        except Exception as e:
            print(f"⚠️ 賽馬投注面板在頻道 {ch_id_str} 重新掛載失敗：{e}")'''

assert old_reattach in code, 'old_reattach not found'
code = code.replace(old_reattach, new_reattach)

# 6. Modify race settlement channel sending in _resolve_race
old_resolve_ch = '''    # 找公告頻道
    channel = None
    try:
        ch_id = race.get("channel_id") or horse_racing_settings.get("channel_id")
        if ch_id:
            for guild in bot.guilds:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    channel = ch
                    break
    except Exception:
        pass'''

new_resolve_ch = '''    # 找公告頻道
    target_channels = []
    ch_ids = race.get("channel_ids") or []
    if not ch_ids and race.get("channel_id"):
        ch_ids = [race["channel_id"]]

    if ch_ids:
        for cid in ch_ids:
            ch = get_channel_any(int(cid))
            if ch and ch not in target_channels:
                target_channels.append(ch)
    else:
        target_channels = _get_all_horse_channels()

    msg_map = race.get("messages", {})
    if not msg_map and race.get("channel_id") and race.get("message_id"):
        msg_map = {str(race["channel_id"]): race["message_id"]}'''

assert old_resolve_ch in code, 'old_resolve_ch not found'
code = code.replace(old_resolve_ch, new_resolve_ch)

old_send_result = '''    if channel:
        # 移除舊投注面板按鈕，避免結算後還能點
        msg_id = race.get("message_id")
        if msg_id:
            try:
                old_msg = await channel.fetch_message(int(msg_id))
                await old_msg.edit(view=None)
            except Exception:
                pass
        try:
            await channel.send(embed=result_embed)
        except Exception as e:
            print(f"⚠️ 賽馬結果發送失敗：{e}")'''

new_send_result = '''    for channel in target_channels:
        msg_id = msg_map.get(str(channel.id))
        if msg_id:
            try:
                old_msg = await channel.fetch_message(int(msg_id))
                await old_msg.edit(view=None)
            except Exception:
                pass
        try:
            await channel.send(embed=result_embed)
        except Exception as e:
            print(f"⚠️ 賽馬結果發送至頻道 {channel.id} 失敗：{e}")'''

assert old_send_result in code, 'old_send_result not found'
code = code.replace(old_send_result, new_send_result)

# 7. Modify horse_racing_loop
old_loop_ch = '''            if current_race is None:
                channel = _get_horse_racing_channel()
                if channel:
                    last_end = horse_racing_settings.get("last_race_end_time", 0)
                    force = horse_racing_settings.get("force_next", False)
                    if force or (now - last_end >= HORSE_RACE_INTERVAL_SEC):
                        horse_racing_settings["force_next"] = False
                        save_horse_racing()
                        await _start_new_race(channel)'''

new_loop_ch = '''            if current_race is None:
                channels = _get_all_horse_channels()
                if channels:
                    last_end = horse_racing_settings.get("last_race_end_time", 0)
                    force = horse_racing_settings.get("force_next", False)
                    if force or (now - last_end >= HORSE_RACE_INTERVAL_SEC):
                        horse_racing_settings["force_next"] = False
                        save_horse_racing()
                        await _start_new_race(channels)'''

assert old_loop_ch in code, 'old_loop_ch not found'
code = code.replace(old_loop_ch, new_loop_ch)

# 8. Modify commands set_channel, start_now, status
old_set_channel = '''    @app_commands.command(name="set_channel", description="設定賽馬新場次公告的頻道（僅擁有者）")
    @app_commands.describe(channel="要公告賽事的頻道")
    async def horse_set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        horse_racing_settings["channel_id"] = channel.id
        save_horse_racing()
        await interaction.response.send_message(
            f"✅ 賽馬公告頻道已設定為 {channel.mention}，將每 {HORSE_RACE_INTERVAL_MIN} 分鐘自動開一場賽事。",
            ephemeral=True
        )'''

new_set_channel = '''    @app_commands.command(name="set_channel", description="設定賽馬新場次公告的頻道（管理員限定）")
    @app_commands.describe(channel="要公告賽事的頻道")
    async def horse_set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return

        guild_id_str = str(interaction.guild.id) if interaction.guild else None
        if guild_id_str == ICEA_GUILD_ID:
            horse_racing_settings["channel_id"] = channel.id
        elif guild_id_str:
            horse_racing_settings.setdefault("guild_channels", {})[guild_id_str] = str(channel.id)

        save_horse_racing()
        await interaction.response.send_message(
            f"✅ 賽馬公告頻道已設定為 {channel.mention}，將每 {HORSE_RACE_INTERVAL_MIN} 分鐘自動開一場賽事。",
            ephemeral=True
        )'''

assert old_set_channel in code, 'old_set_channel not found'
code = code.replace(old_set_channel, new_set_channel)

old_start_now = '''    @app_commands.command(name="start_now", description="跳過冷卻，立即開始一場賽馬（僅擁有者）")
    async def horse_start_now(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if current_race:
            await interaction.response.send_message("❌ 目前已有一場賽事正在進行（投注中或結算中），無法開始新的一場。", ephemeral=True)
            return
        channel = _get_horse_racing_channel()
        if not channel:
            await interaction.response.send_message("❌ 尚未設定賽馬公告頻道，請先用 `/horse set_channel` 設定。", ephemeral=True)
            return

        await interaction.response.send_message(f"⏳ 正在於 {channel.mention} 開始新賽事…", ephemeral=True)
        await _start_new_race(channel)
        await interaction.followup.send(f"✅ 新賽事已在 {channel.mention} 開始！", ephemeral=True)'''

new_start_now = '''    @app_commands.command(name="start_now", description="跳過冷卻，立即開始一場賽馬（管理員限定）")
    async def horse_start_now(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 此指令僅限管理員使用。", ephemeral=True)
            return
        if current_race:
            await interaction.response.send_message("❌ 目前已有一場賽事正在進行（投注中或結算中），無法開始新的一場。", ephemeral=True)
            return
        channels = _get_all_horse_channels()
        if not channels:
            await interaction.response.send_message("❌ 尚未設定賽馬公告頻道，請先用 `/horse set_channel` 設定。", ephemeral=True)
            return

        await interaction.response.send_message("⏳ 正在開始新賽事…", ephemeral=True)
        await _start_new_race(channels)
        await interaction.followup.send("✅ 新賽事已開始！", ephemeral=True)'''

assert old_start_now in code, 'old_start_now not found'
code = code.replace(old_start_now, new_start_now)

old_status_cmd = '''        last_end = horse_racing_settings.get("last_race_end_time", 0)
        channel = _get_horse_racing_channel()
        if not channel:
            await interaction.response.send_message("❌ 尚未設定賽馬公告頻道。", ephemeral=True)
            return
        remaining = int(HORSE_RACE_INTERVAL_SEC - (_hr_time.time() - last_end))
        if remaining <= 0:
            desc = "下一場賽事即將開始（下個檢查週期內），請留意公告頻道。"
        else:
            desc = f"距離下一場賽事還有約 **{remaining // 60}** 分鐘，屆時將自動在 {channel.mention} 公告。"'''

new_status_cmd = '''        last_end = horse_racing_settings.get("last_race_end_time", 0)
        channels = _get_all_horse_channels()
        if not channels:
            await interaction.response.send_message("❌ 尚未設定賽馬公告頻道。", ephemeral=True)
            return
        remaining = int(HORSE_RACE_INTERVAL_SEC - (_hr_time.time() - last_end))
        if remaining <= 0:
            desc = "下一場賽事即將開始（下個檢查週期內），請留意公告頻道。"
        else:
            desc = f"距離下一場賽事還有約 **{remaining // 60}** 分鐘，屆時將自動在設定的頻道公告。"'''

assert old_status_cmd in code, 'old_status_cmd not found'
code = code.replace(old_status_cmd, new_status_cmd)

with open('modules/140_horse_racing.py', 'w', encoding='utf-8') as f:
    f.write(code)

print('140_horse_racing.py modified successfully')
