# ═══════════════════════════════════════════════════════════════════
# Module: 145_graceful_restart
# 重啟前保存遊戲狀態 + 重啟後無縫恢復 + 頻道公告
# 支援：海龜湯（完全恢復）、狼人殺（恢復到回合開頭）
# ═══════════════════════════════════════════════════════════════════

import time as _gr_time

GRACEFUL_RESTART_FILE = os.path.join(DATA_DIR, "graceful_restart.json")

# 重啟時保存的狀態
_restart_state = {
    "timestamp": 0,
    "turtle_soup": None,    # 保存的 _turtle_soup_state（若遊戲進行中）
    "werewolf": None,       # 保存的 _ww_state（若遊戲進行中）
    "notified_channels": [],  # 發過「重啟中」的頻道 ID 列表
}


def _save_restart_state():
    """儲存重啟狀態到磁碟。"""
    try:
        _save_json_file(GRACEFUL_RESTART_FILE, _restart_state)
    except Exception as e:
        print(f"⚠️ 重啟狀態存檔失敗：{e}")


def _load_restart_state():
    """從磁碟載入重啟狀態。"""
    global _restart_state
    try:
        if os.path.exists(GRACEFUL_RESTART_FILE):
            with open(GRACEFUL_RESTART_FILE, "r", encoding="utf-8") as f:
                data = json_module.load(f)
            if isinstance(data, dict):
                _restart_state = data
            print(f"🔄 重啟狀態已載入（時間戳：{data.get('timestamp', 0)}）")
    except Exception as e:
        print(f"⚠️ 重啟狀態載入失敗：{e}")


load_graceful_restart = _load_restart_state  # 別名，供主檔 setup_hook 呼叫


# ── 重啟前：保存遊戲狀態 + 發公告 ──

async def _send_pre_restart_notifications():
    """在所有受影響的頻道發送「重啟中」公告。"""
    notified = []

    # 海龜湯
    if _turtle_soup_state.get("active"):
        ch_id = _turtle_soup_state.get("channel_id")
        if ch_id:
            try:
                channel = bot.get_channel(int(ch_id))
                if channel:
                    embed = discord.Embed(
                        title="🔄 重啟中",
                        description=(
                            "⚠️ 機器人即將重啟，海龜湯遊戲暫停。\n"
                            "請稍後再操作，重啟完畢後可**直接繼續提問**。\n"
                            "你的提問次數和記錄都已保存。"
                        ),
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow(),
                    )
                    await channel.send(embed=embed)
                    notified.append(int(ch_id))
                    print(f"🔄 已在海龜湯頻道 {ch_id} 發送重啟公告")
            except Exception as e:
                print(f"⚠️ 海龜湯重啟公告發送失敗：{e}")

    # 狼人殺
    if _ww_state.get("phase") in ("signup", "playing"):
        ch_id = _ww_state.get("channel_id")
        if ch_id:
            try:
                channel = bot.get_channel(int(ch_id))
                if channel:
                    phase_name = "報名" if _ww_state["phase"] == "signup" else f"第{_ww_state['day']}天遊戲中"
                    embed = discord.Embed(
                        title="🔄 重啟中",
                        description=(
                            f"⚠️ 機器人即將重啟，狼人殺（{phase_name}）暫停。\n"
                            "請稍後再操作，重啟完畢後將提供「繼續遊戲」按鈕。\n"
                            "所有玩家身分和存活狀態已保存。"
                        ),
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow(),
                    )
                    await channel.send(embed=embed)
                    notified.append(int(ch_id))
                    print(f"🔄 已在狼人殺頻道 {ch_id} 發送重啟公告")
            except Exception as e:
                print(f"⚠️ 狼人殺重啟公告發送失敗：{e}")

    return notified


async def save_active_game_states():
    """重啟前呼叫：發公告 → 保存遊戲狀態 → 寫入磁碟。"""
    global _restart_state
    notified_channels = await _send_pre_restart_notifications()

    ts_save = None
    ww_save = None

    # 保存海龜湯（移除不可序列化的物件）
    if _turtle_soup_state.get("active"):
        ts_save = {k: v for k, v in _turtle_soup_state.items()}
        # queue 裡有 interaction 物件，無法序列化 — 清空，重啟後玩家需重新提問
        ts_save["queue"] = []
        ts_save["processing"] = False
        ts_save["hint_panel_active"] = False
        print(f"🍜 海龜湯狀態已保存（{ts_save['questions_used']}/{ts_save['max_questions']} 題）")

    # 保存狼人殺
    if _ww_state.get("phase") in ("signup", "playing"):
        ww_save = {k: v for k, v in _ww_state.items()}
        print(f"🐺 狼人殺狀態已保存（phase={ww_save['phase']}, day={ww_save['day']}, players={len(ww_save['players'])}）")

    _restart_state = {
        "timestamp": _gr_time.time(),
        "turtle_soup": ts_save,
        "werewolf": ww_save,
        "notified_channels": notified_channels,
    }
    _save_restart_state()


# ── 重啟後：恢復遊戲狀態 + 發公告 ──

class _WerewolfResumeView(discord.ui.View):
    """狼人殺恢復後的「繼續遊戲」按鈕面板。"""
    def __init__(self):
        super().__init__(timeout=None)  # 持久化 view（需 timeout=None）；按鈕靠遊戲狀態自我管控

    @discord.ui.button(label="繼續遊戲", style=discord.ButtonStyle.success, emoji="▶️", custom_id="ww_resume:continue")
    async def btn_continue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if _ww_state.get("phase") != "playing":
            await interaction.response.send_message("❌ 遊戲已不在進行中。", ephemeral=True)
            return

        channel = interaction.channel
        detail = _ww_state.get("phase_detail", "")
        if "night" in detail:
            await interaction.response.send_message("🔄 正在恢復夜晚階段...", ephemeral=True)
            await _ww_night_phase(channel)
        else:
            await interaction.response.send_message("🔄 正在恢復白天討論階段...", ephemeral=True)
            await _ww_day_phase(channel)

    @discord.ui.button(label="結束遊戲", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="ww_resume:end")
    async def btn_end(self, interaction: discord.Interaction, button: discord.ui.Button):
        if _ww_state.get("phase") not in ("playing", "signup"):
            await interaction.response.send_message("❌ 遊戲已不在進行中。", ephemeral=True)
            return
        channel = interaction.channel
        await interaction.response.send_message("🗑️ 正在結束遊戲...", ephemeral=True)
        await _ww_end_game(channel, "villagers")


async def restore_active_game_states():
    """重啟後呼叫：載入保存的狀態 → 恢復遊戲 → 發「重啟完畢」公告。"""
    global _restart_state, _turtle_soup_state, _ww_state

    ts_save = _restart_state.get("turtle_soup")
    ww_save = _restart_state.get("werewolf")

    # ── 恢復海龜湯 ──
    if ts_save and ts_save.get("active"):
        ch_id = ts_save.get("channel_id")
        if ch_id:
            try:
                channel = bot.get_channel(int(ch_id))
                if channel:
                    _turtle_soup_state = ts_save
                    _turtle_soup_state["processing"] = False
                    _turtle_soup_state["queue"] = []
                    _turtle_soup_state["hint_panel_active"] = False
                    print(f"🍜 海龜湯已恢復：{ts_save['questions_used']}/{ts_save['max_questions']} 題")

                    game_msg_id = ts_save.get("game_msg_id")
                    if game_msg_id:
                        try:
                            game_msg = await channel.fetch_message(int(game_msg_id))
                            remaining = ts_save["max_questions"] - ts_save["questions_used"]
                            embed = discord.Embed(
                                title="🍜 海龜湯進行中（已恢復）",
                                description=(
                                    f"**湯面：**{ts_save['surface']}\n\n"
                                    f"📖 提問次數：**{ts_save['questions_used']}/{ts_save['max_questions']}**（剩 {remaining} 次）\n"
                                    f"🎲 難度：{ts_save.get('difficulty', 'medium')}\n"
                                    f"📜 已問 {len(ts_save.get('qa_history', []))} 個問題\n\n"
                                    "直接在頻道發訊息提問（結尾加 ?）繼續遊戲！"
                                ),
                                color=discord.Color.green(),
                                timestamp=discord.utils.utcnow(),
                            )
                            embed.set_footer(text="✅ 重啟完畢，資料未遺失，請直接繼續提問")
                            await game_msg.edit(embed=embed)
                        except Exception as e:
                            print(f"⚠️ 海龜湯主訊息更新失敗：{e}")

                    embed = discord.Embed(
                        title="✅ 重啟完畢",
                        description=(
                            "資料未遺失，海龜湯遊戲已恢復！\n"
                            f"📖 進度：**{ts_save['questions_used']}/{ts_save['max_questions']}** 題\n"
                            "請直接繼續提問（結尾加 ?）即可！"
                        ),
                        color=discord.Color.green(),
                        timestamp=discord.utils.utcnow(),
                    )
                    await channel.send(embed=embed)
                    print(f"✅ 海龜湯恢復公告已發送至頻道 {ch_id}")
                else:
                    print(f"⚠️ 海龜湯頻道 {ch_id} 找不到，無法恢復")
            except Exception as e:
                print(f"⚠️ 海龜湯恢復失敗：{e}")

    # ── 恢復狼人殺 ──
    if ww_save and ww_save.get("phase") in ("signup", "playing"):
        ch_id = ww_save.get("channel_id")
        if ch_id:
            try:
                channel = bot.get_channel(int(ch_id))
                if channel:
                    _ww_state = ww_save
                    print(f"🐺 狼人殺已恢復：phase={ww_save['phase']}, day={ww_save['day']}, players={len(ww_save['players'])}")

                    if ww_save["phase"] == "playing":
                        alive_players = [p["name"] for p in ww_save["players"] if p.get("alive")]
                        dead_players = [p["name"] for p in ww_save["players"] if not p.get("alive")]
                        phase_detail = ww_save.get("phase_detail", "")
                        if "night" in phase_detail:
                            phase_desc = f"第 {ww_save['day']} 夜（夜晚行動）"
                        else:
                            phase_desc = f"第 {ww_save['day']} 天（白天討論）"

                        embed = discord.Embed(
                            title="✅ 重啟完畢",
                            description=(
                                "資料未遺失，狼人殺遊戲已恢復！\n\n"
                                f"📍 當前階段：**{phase_desc}**\n"
                                f"👥 存活玩家：{', '.join(alive_players)}\n"
                            ),
                            color=discord.Color.green(),
                            timestamp=discord.utils.utcnow(),
                        )
                        if dead_players:
                            embed.add_field(name="💀 已淘汰", value=", ".join(dead_players), inline=False)
                        embed.set_footer(text="點擊下方按鈕繼續遊戲（將從本回合開頭恢復）")

                        view = _WerewolfResumeView()
                        await channel.send(embed=embed, view=view)
                        print(f"✅ 狼人殺恢復公告已發送至頻道 {ch_id}（含繼續按鈕）")
                    else:
                        embed = discord.Embed(
                            title="✅ 重啟完畢",
                            description=(
                                "資料未遺失，狼人殺報名階段已恢復！\n"
                                f"👥 已報名：{len(ww_save['players'])} 人\n"
                                "報名面板將在稍後自動恢復。"
                            ),
                            color=discord.Color.green(),
                            timestamp=discord.utils.utcnow(),
                        )
                        await channel.send(embed=embed)
                        print(f"✅ 狼人殺報名恢復公告已發送至頻道 {ch_id}")
                else:
                    print(f"⚠️ 狼人殺頻道 {ch_id} 找不到，無法恢復")
            except Exception as e:
                print(f"⚠️ 狼人殺恢復失敗：{e}")

    # 清除重啟狀態檔（已恢復完畢）
    _restart_state = {
        "timestamp": 0,
        "turtle_soup": None,
        "werewolf": None,
        "notified_channels": [],
    }
    _save_restart_state()
