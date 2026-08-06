# ═════════════════════════════════════════════════════════════════
# Module: 10_werewolf (auto-extracted from discord_borda_poll.py)
# This file is loaded via exec() in the main file's namespace.
# All shared globals/utilities from the main file are accessible.
# ═════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# AI 狼人殺（AI 主持版）
# ═══════════════════════════════════════════════════════════════════

import random as _ww_random

WEREWOLF_FILE = os.path.join(DATA_DIR, "werewolf_settings.json")

# ── 遊戲狀態 ──
_ww_state = {
    "phase": "idle",          # idle | signup | playing | ended
    "game_id": 0,
    "channel_id": None,       # 遊戲頻道 ID
    "guild_id": None,
    "role_id": None,          # 臨時身分組 ID
    "signup_msg_id": None,    # 報名面板訊息 ID
    "players": [],            # [{"id": str, "name": str, "is_ai": bool, "role": "", "alive": True, "dm_done": False}]
    "day": 0,
    "phase_detail": "",       # night_wolf | night_seer | day_discuss | day_vote | result
    "night_target": None,     # 被狼人殺的玩家 id
    "seer_target": None,      # 預言家查的玩家 id
    "seer_result": None,      # 預言家查驗結果
    "votes": {},              # {voter_id: target_id}
    "log": [],                # 遊戲事件記錄
    "winner": None,           # "wolves" | "villagers"
    "discussion_suspects": {},  # {player_id: 被指名懷疑次數}（每天討論階段重置）
}

_ww_invite_msg_id = None
_ww_webhook_cache = {}  # {channel_id: discord.Webhook} 快取，避免重複建立/查詢

async def _ww_get_webhook(channel):
    """取得（或建立）本頻道用於 AI 玩家發言的 Webhook，並快取起來。"""
    cached = _ww_webhook_cache.get(channel.id)
    if cached:
        return cached
    try:
        webhooks = await channel.webhooks()
        existing = next((w for w in webhooks if w.name == "ICEA 狼人殺 AI"), None)
        if existing:
            _ww_webhook_cache[channel.id] = existing
            return existing
        created = await channel.create_webhook(name="ICEA 狼人殺 AI", reason="狼人殺 AI 玩家發言用")
        _ww_webhook_cache[channel.id] = created
        return created
    except discord.Forbidden:
        print("⚠️ WW: 無 Manage Webhooks 權限，AI 發言將改用一般訊息格式")
        return None
    except Exception as e:
        print(f"⚠️ WW get_webhook failed: {e}")
        return None


def _ww_ai_avatar_url(name: str) -> str:
    """依名字產生一個穩定但看起來隨機的頭像（dicebear，同名字每次都一樣，換名字就換頭像）。"""
    seed = urllib.parse.quote(name)
    return f"https://api.dicebear.com/9.x/adventurer/png?seed={seed}&backgroundType=gradientLinear"


async def _ww_send_ai_message(channel, ai_player: dict, text: str):
    """用 Webhook 以 AI 玩家的身分（自訂名字+頭像）發送發言訊息，更有帶入感。
    若無法取得 Webhook（權限不足等），自動降級為一般訊息格式。"""
    webhook = await _ww_get_webhook(channel)
    if webhook:
        try:
            await webhook.send(
                content=text,
                username=ai_player["name"],
                avatar_url=_ww_ai_avatar_url(ai_player["name"]),
            )
            return
        except Exception as e:
            print(f"⚠️ WW webhook send failed, falling back: {e}")
    # 降級：一般訊息
    try:
        await channel.send(f"**{ai_player['name']}：** {text}")
    except Exception as e:
        print(f"⚠️ WW fallback message send failed: {e}")

def _save_ww_settings():
    settings = {
        "enabled": chat_ai_settings.get("werewolf_enabled", False),
        "channel_id": chat_ai_settings.get("werewolf_channel_id"),
    }
    _save_json_file(WEREWOLF_FILE, settings)

def _ww_log(msg: str):
    _ww_state["log"].append(f"[Day {_ww_state['day']}] {msg}")
    if len(_ww_state["log"]) > 100:
        _ww_state["log"] = _ww_state["log"][-50:]
    print(f"🐺 WW: {msg}")


# ── AI 主持人生成旁白 ──
_WW_NARRATOR_PROMPT = """你是一個狼人殺遊戲的主持人（旁白）。請用台灣繁體中文生成簡短、有氛圍感的旁白文字。要求：
- 50-100字以內
- 不要透露任何角色身分
- 有懸疑感、沉浸感
- 不要加 emoji 或格式符號
- 直接輸出旁白文字，不要有開場白

場景：{scene}
{extra}"""

async def _ww_narrate(scene: str, extra: str = "") -> str:
    """生成 AI 旁白文字。"""
    prompt = _WW_NARRATOR_PROMPT.format(scene=scene, extra=extra)
    settings = {
        "api_url": chat_ai_settings["api_url"],
        "api_key": chat_ai_settings["api_key"],
        "model": chat_ai_settings.get("werewolf_model") or chat_ai_settings["model"],
        "model_fallback_chain": chat_ai_settings.get("model_fallback_chain", ""),
    }
    if chat_ai_settings.get("fallback_enabled") and not _ai_circuit_breaker["tripped"]:
        settings["fallback_api_url"] = chat_ai_settings.get("fallback_api_url", "")
        settings["fallback_api_key"] = chat_ai_settings.get("fallback_api_key", "")
        settings["fallback_model"] = chat_ai_settings.get("fallback_model", "")

    messages = [{"role": "user", "content": prompt}]
    try:
        result = await call_chat_api(
            messages, settings,
            max_tokens=200,
            timeout_total=15,
            timeout_read=12,
            is_background=True,
            fallback_mode="full",
            fallback_user_id="werewolf",
            category="entertainment",
        )
        text = result.get("content", "").strip()
        return text or None
    except Exception as e:
        print(f"⚠️ WW narrate failed: {e}")
        return None


# ── 角色分配（6人局：2狼人 + 1預言家 + 3村民）──
_WW_ROLES_6P = ["狼人", "狼人", "預言家", "村民", "村民", "村民"]

_WW_ROLE_INFO = {
    "狼人": {
        "emoji": "🐺",
        "color": discord.Color.red(),
        "desc": "你是狼人。每晚與同伴選擇一名玩家擊殺。白天偽裝成好人，避免被投票淘汰。",
        "team": "wolves",
    },
    "預言家": {
        "emoji": "🔮",
        "color": discord.Color.blue(),
        "desc": "你是預言家。每晚可以查驗一名玩家的身分（好人/狼人）。白天可以利用你的資訊引導投票，但要小心被狼人針對。",
        "team": "villagers",
    },
    "村民": {
        "emoji": "👤",
        "color": discord.Color.green(),
        "desc": "你是普通村民。你沒有特殊能力，但要透過觀察和討論找出狼人，在白天投票淘汰他們。",
        "team": "villagers",
    },
}


def _ww_assign_roles(players: list):
    """隨機分配角色給所有玩家。"""
    roles = list(_WW_ROLES_6P[:len(players)])
    _ww_random.shuffle(roles)
    for i, p in enumerate(players):
        p["role"] = roles[i]
        p["alive"] = True
        p["dm_done"] = False


def _ww_alive_players():
    return [p for p in _ww_state["players"] if p["alive"]]

def _ww_wolves_alive():
    return [p for p in _ww_alive_players() if p["role"] == "狼人"]

def _ww_player_by_id(pid: str):
    for p in _ww_state["players"]:
        if p["id"] == pid:
            return p
    return None


def _ww_check_win():
    """檢查勝利條件。回傳 'wolves' / 'villagers' / None。"""
    wolves = _ww_wolves_alive()
    villagers = [p for p in _ww_alive_players() if p["role"] != "狼人"]
    if len(wolves) == 0:
        return "villagers"
    if len(wolves) >= len(villagers):
        return "wolves"
    return None


# ── 報名面板 View ──
class WerewolfSignupView(discord.ui.View):
    """持續存在的報名按鈕面板。"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🐺 報名參加本局狼人殺", style=discord.ButtonStyle.primary, custom_id="ww_signup")
    async def signup(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _ww_state

        if _ww_state["phase"] != "signup":
            await interaction.response.send_message("⚠️ 目前無法報名（遊戲已開始或尚未開放）。", ephemeral=True)
            return

        pid = str(interaction.user.id)

        # 已報名 → 重新測試私訊狀態（方便玩家開啟私訊後重測）
        for p in _ww_state["players"]:
            if p["id"] == pid and not p["is_ai"]:
                dm_warning = ""
                try:
                    await interaction.user.send("🔄 重新測試私訊連線成功！")
                    p["dm_ok"] = True
                except discord.Forbidden:
                    p["dm_ok"] = False
                    dm_warning = (
                        "\n\n⚠️ 私訊仍然是關閉的，請至 **伺服器設定 → 隱私設定 → "
                        "允許來自伺服器成員的私訊** 開啟後再重新點擊測試。"
                    )
                except Exception as e:
                    print(f"⚠️ WW re-signup DM test failed: {e}")
                await _ww_update_signup_embed(interaction.channel)
                status = "✅ 私訊正常" if p.get("dm_ok", True) else "❌ 私訊仍關閉"
                await interaction.response.send_message(
                    f"⚠️ 你已經報名了，已重新測試私訊：{status}{dm_warning}",
                    ephemeral=True,
                )
                return

        # 加入臨時身分組
        role_id = _ww_state.get("role_id")
        guild = interaction.guild
        if role_id:
            role = guild.get_role(int(role_id))
            if role:
                try:
                    member = interaction.user
                    if role not in member.roles:
                        await member.add_roles(role)
                except discord.Forbidden:
                    await interaction.response.send_message("⚠️ 機器人缺少管理身分組的權限。", ephemeral=True)
                    return
                except Exception as e:
                    print(f"⚠️ WW add role failed: {e}")

        # 加入玩家列表
        _ww_state["players"].append({
            "id": pid,
            "name": interaction.user.display_name,
            "is_ai": False,
            "role": "",
            "alive": True,
            "dm_done": False,
            "dm_ok": True,
        })

        _ww_log(f"{interaction.user.display_name} 報名（共 {len(_ww_state['players'])} 人）")

        # 立即測試私訊是否能送達（遊戲開始後角色分配靠 DM，DM 關閉會導致玩家收不到身分且無法行動）
        dm_warning = ""
        try:
            await interaction.user.send(
                "🐺 你已成功報名本局狼人殺！\n"
                "遊戲開始後，你的身分與夜晚行動都會透過**私訊**進行，請保持這個對話開啟。"
            )
        except discord.Forbidden:
            new_player = _ww_state["players"][-1]
            new_player["dm_ok"] = False
            dm_warning = (
                "\n\n⚠️ **偵測到你的私訊是關閉的！** 遊戲需要透過私訊分配身分、進行夜晚行動。\n"
                "請至 **伺服器設定 → 隱私設定 → 允許來自伺服器成員的私訊** 開啟後，"
                "重新點擊報名以便機器人重新測試。"
            )
        except Exception as e:
            print(f"⚠️ WW signup DM test failed: {e}")

        # 更新面板
        await _ww_update_signup_embed(interaction.channel)
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} 已報名！目前共 {len(_ww_state['players'])} 人。{dm_warning}",
            ephemeral=True,
        )

    @discord.ui.button(label="🗳️ 投票開始遊戲", style=discord.ButtonStyle.success, custom_id="ww_vote_start")
    async def vote_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _ww_state

        if _ww_state["phase"] != "signup":
            await interaction.response.send_message("⚠️ 目前無法投票（遊戲已開始或尚未開放）。", ephemeral=True)
            return

        pid = str(interaction.user.id)
        real_players = [p for p in _ww_state["players"] if not p["is_ai"]]

        # 確認已報名
        if not any(p["id"] == pid for p in real_players):
            await interaction.response.send_message("⚠️ 你必須先報名才能投票開始。", ephemeral=True)
            return

        # 最少 3 人才能發起
        if len(real_players) < 3:
            await interaction.response.send_message(
                f"⚠️ 至少需要 3 名真人玩家才能開始（目前 {len(real_players)} 人）。",
                ephemeral=True,
            )
            return

        # 立刻 ack
        await interaction.response.send_message("🗳️ 正在發起開始投票...", ephemeral=True)

        # 發起投票面板
        await _ww_start_vote(interaction.channel, pid)

    @discord.ui.button(label="❌ 取消報名", style=discord.ButtonStyle.secondary, custom_id="ww_cancel_signup")
    async def cancel_signup(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _ww_state

        if _ww_state["phase"] != "signup":
            await interaction.response.send_message("⚠️ 目前無法取消報名。", ephemeral=True)
            return

        pid = str(interaction.user.id)
        before = len(_ww_state["players"])
        _ww_state["players"] = [p for p in _ww_state["players"] if p["id"] != pid]

        if len(_ww_state["players"]) == before:
            await interaction.response.send_message("⚠️ 你沒有報名，無需取消。", ephemeral=True)
            return

        # 移除身分組
        role_id = _ww_state.get("role_id")
        if role_id:
            role = interaction.guild.get_role(int(role_id))
            if role:
                try:
                    await interaction.user.remove_roles(role)
                except Exception:
                    pass

        await _ww_update_signup_embed(interaction.channel)
        await interaction.followup.send(
            f"✅ {interaction.user.mention} 已取消報名。目前共 {len(_ww_state['players'])} 人。",
            ephemeral=True,
        )


async def _ww_update_signup_embed(channel):
    """更新報名面板的 Embed。"""
    global _ww_invite_msg_id

    if not _ww_invite_msg_id:
        return

    try:
        msg = await channel.fetch_message(_ww_invite_msg_id)
    except Exception:
        return

    players = _ww_state["players"]
    real = [p for p in players if not p["is_ai"]]
    player_list = "\n".join(
        f"• {p['name']}" + ("" if p.get('dm_ok', True) else " ⚠️私訊未開")
        for p in real
    ) or "（尚無人報名）"
    dm_closed_count = sum(1 for p in real if not p.get("dm_ok", True))

    embed = discord.Embed(
        title="🐺 AI 狼人殺 · 報名中",
        description=(
            "一場由 AI 主持的狼人殺遊戲！\n"
            "點擊 **報名** 按鈕加入，湊滿 3 人以上即可投票開始。\n\n"
            f"👥 **已報名（{len(real)} 人）：**\n{player_list}\n\n"
            "⚙️ **規則：**\n"
            "• 6 人局：2 狼人 + 1 預言家 + 3 村民\n"
            "• 不足 6 人時自動生成 AI 玩家補位\n"
            "• 報名後會獲得臨時身分組，僅此身分組可在本頻道發言\n"
            "• ⚠️ **請務必開啟「允許來自伺服器成員的私訊」**，角色分配與夜晚行動都透過私訊進行"
        ),
        color=discord.Color.dark_red(),
        timestamp=discord.utils.utcnow(),
    )
    if dm_closed_count > 0:
        embed.add_field(
            name="⚠️ 私訊警告",
            value=f"有 {dm_closed_count} 位玩家私訊關閉，開局前請開啟私訊並重新報名一次以通過檢測。",
            inline=False,
        )
    embed.set_footer(text="ICEA · AI 狼人殺 | 報名階段")

    view = WerewolfSignupView()
    if len(real) < 3:
        view.vote_start.disabled = True
    else:
        view.vote_start.disabled = False

    try:
        await msg.edit(embed=embed, view=view)
    except Exception as e:
        print(f"⚠️ WW update signup embed failed: {e}")


async def _ww_post_invite(channel):
    """發送報名邀請面板。"""
    global _ww_invite_msg_id

    if _ww_invite_msg_id:
        try:
            old = await channel.fetch_message(_ww_invite_msg_id)
            await old.delete()
        except Exception:
            pass

    embed = discord.Embed(
        title="🐺 AI 狼人殺 · 報名中",
        description=(
            "一場由 AI 主持的狼人殺遊戲！\n"
            "點擊 **報名** 按鈕加入，湊滿 3 人以上即可投票開始。\n\n"
            "👥 **已報名（0 人）：**\n（尚無人報名）\n\n"
            "⚙️ **規則：**\n"
            "• 6 人局：2 狼人 + 1 預言家 + 3 村民\n"
            "• 不足 6 人時自動生成 AI 玩家補位\n"
            "• 報名後會獲得臨時身分組，僅此身分組可在本頻道發言\n"
            "• ⚠️ **請務必開啟「允許來自伺服器成員的私訊」**，角色分配與夜晚行動都透過私訊進行"
        ),
        color=discord.Color.dark_red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="ICEA · AI 狼人殺 | 報名階段")

    view = WerewolfSignupView()
    view.vote_start.disabled = True  # 0 人時不能投票

    msg = await channel.send(embed=embed, view=view)
    _ww_invite_msg_id = msg.id
    _ww_log(f"Invite posted (msg_id={msg.id})")


# ── 開始投票面板 ──
class WerewolfStartVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self._votes = {}  # {user_id: True}
        self._voted_users = set()

    @discord.ui.button(label="✅ 同意開始", style=discord.ButtonStyle.success, custom_id="ww_approve_start")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        real_players = [p for p in _ww_state["players"] if not p["is_ai"]]
        if not any(p["id"] == uid for p in real_players):
            await interaction.response.send_message("⚠️ 只有已報名的玩家可以投票。", ephemeral=True)
            return
        if uid in self._voted_users:
            await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
            return
        self._voted_users.add(uid)
        self._votes[uid] = True
        count = len(self._votes)
        total = len(real_players)
        await interaction.response.send_message(
            f"✅ 已投下同意票（{count}/{total}）。", ephemeral=True,
        )

    @discord.ui.button(label="❌ 反對", style=discord.ButtonStyle.danger, custom_id="ww_reject_start")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        real_players = [p for p in _ww_state["players"] if not p["is_ai"]]
        if not any(p["id"] == uid for p in real_players):
            await interaction.response.send_message("⚠️ 只有已報名的玩家可以投票。", ephemeral=True)
            return
        if uid in self._voted_users:
            await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
            return
        self._voted_users.add(uid)
        self._votes[uid] = False
        count = sum(1 for v in self._votes.values() if v)
        total = len(real_players)
        await interaction.response.send_message(
            f"❌ 已投下反對票（同意 {count}/{total}）。", ephemeral=True,
        )


async def _ww_start_vote(channel, initiator_id: str):
    """發起開始遊戲投票。"""
    global _ww_state
    real_players = [p for p in _ww_state["players"] if not p["is_ai"]]
    total = len(real_players)

    vote_view = WerewolfStartVoteView()
    vote_msg = await channel.send(
        f"🗳️ **開始遊戲投票**\n"
        f"由 <@{initiator_id}> 發起。需要過半數（{total // 2 + 1} 票）同意才能開始。\n"
        f"⏱️ 投票時間 30 秒。",
        view=vote_view,
    )

    await asyncio.sleep(30)

    # 結算
    for child in vote_view.children:
        child.disabled = True
    try:
        await vote_msg.edit(view=vote_view)
    except Exception:
        pass

    if _ww_state["phase"] != "signup":
        return  # 已被取消或已開始

    yes = sum(1 for v in vote_view._votes.values() if v)
    needed = total // 2 + 1

    _ww_log(f"Start vote: {yes}/{total} yes, needed {needed}")

    if yes >= needed:
        await channel.send(f"✅ **投票通過**（{yes}/{total}），遊戲即將開始！")
        await _ww_begin_game(channel)
    else:
        await channel.send(f"❌ **投票未通過**（{yes}/{total}，需要 {needed} 票），繼續報名中。")


async def _ww_begin_game(channel):
    """遊戲正式開始：分配角色、發 DM、開始夜晚。"""
    global _ww_state

    # 遞增 game_id：讓舊局的殘留任務（等待中的 sleep、DM 面板等）在新局開始後立即失效
    _ww_state["game_id"] = _ww_state.get("game_id", 0) + 1
    _ww_log(f"Game starting, game_id={_ww_state['game_id']}")
    _ww_state["phase"] = "playing"

    # 鎖定報名按鈕
    if _ww_invite_msg_id:
        try:
            msg = await channel.fetch_message(_ww_invite_msg_id)
            view = WerewolfSignupView()
            for child in view.children:
                child.disabled = True
            await msg.edit(view=view)
        except Exception:
            pass

    # 開局前最後檢查：私訊未開的玩家公開提醒（他們仍會進入遊戲，但收不到身分與夜晚行動面板）
    real_players_check = [p for p in _ww_state["players"] if not p["is_ai"]]
    dm_closed = [p for p in real_players_check if not p.get("dm_ok", True)]
    if dm_closed:
        names = "、".join(p["name"] for p in dm_closed)
        await channel.send(
            f"⚠️ **注意：** {names} 的私訊仍為關閉狀態，遊戲開始後將無法收到身分或進行夜晚行動！\n"
            f"請盡快開啟「允許來自伺服器成員的私訊」，若收不到 DM 請私訊管理員協助。"
        )

    # 補 AI 玩家（以「目前總人數」為基準，避免測試模式已預先塞入的 AI 被重複疊加）
    real_players = [p for p in _ww_state["players"] if not p["is_ai"]]
    ai_needed = 6 - len(_ww_state["players"])
    if ai_needed > 0:
        ai_names = ["AI-老王", "AI-小美", "AI-阿哲", "AI-婷婷", "AI-大偉"]
        for i in range(ai_needed):
            name = ai_names[i] if i < len(ai_names) else f"AI-玩家{i+1}"
            _ww_state["players"].append({
                "id": f"ai_{i}",
                "name": name,
                "is_ai": True,
                "role": "",
                "alive": True,
                "dm_done": False,
            })
        _ww_log(f"Added {ai_needed} AI players. Total: {len(_ww_state['players'])}")

    # 分配角色
    _ww_assign_roles(_ww_state["players"])
    _ww_log(f"Roles assigned: " + ", ".join(f"{p['name']}={p['role']}" for p in _ww_state["players"]))

    # 歡迎訊息
    role_mention = ""
    if _ww_state.get("role_id"):
        role_mention = f"<@&{_ww_state['role_id']}>"

    narrate = await _ww_narrate("遊戲開始，所有人抵達村莊，夜幕即將降臨")
    narrate_text = f"\n\n> {narrate}" if narrate else ""

    ai_count = len([p for p in _ww_state["players"] if p["is_ai"]])
    embed = discord.Embed(
        title="🐺 狼人殺 · 遊戲開始！",
        description=(
            f"本局共 **{len(_ww_state['players'])} 人**"
            f"（真人 {len(real_players)} + AI {ai_count}）\n\n"
            f"🎭 角色配置：2 狼人 + 1 預言家 + 3 村民\n\n"
            f"每個人的身分已透過 **僅自己可見的訊息** 發送，請查看你的 DM。{narrate_text}"
        ),
        color=discord.Color.dark_red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="ICEA · AI 狼人殺")
    await channel.send(content=role_mention, embed=embed)

    # 發送角色 DM
    for p in _ww_state["players"]:
        if p["is_ai"]:
            p["dm_done"] = True
            continue
        try:
            member = channel.guild.get_member(int(p["id"]))
            if not member:
                continue
            info = _WW_ROLE_INFO[p["role"]]
            dm_embed = discord.Embed(
                title=f"{info['emoji']} 你的角色：{p['role']}",
                description=info["desc"],
                color=info["color"],
            )
            # 狼人知道同伴
            if p["role"] == "狼人":
                partners = [pp["name"] for pp in _ww_state["players"] if pp["role"] == "狼人" and pp["id"] != p["id"]]
                if partners:
                    dm_embed.add_field(name="你的同伴", value="、".join(partners), inline=False)
            dm_embed.set_footer(text="ICEA · AI 狼人殺 | 此訊息僅你可見")
            await member.send(embed=dm_embed)
            p["dm_done"] = True
            _ww_log(f"DM sent to {p['name']}: {p['role']}")
        except discord.Forbidden:
            print(f"⚠️ WW: Cannot DM {p['name']} (DM disabled)")
        except Exception as e:
            print(f"⚠️ WW DM failed for {p['name']}: {e}")

    await asyncio.sleep(3)
    await _ww_night_phase(channel)


# ── 夜晚階段 ──
async def _ww_night_phase(channel):
    """夜晚：狼人殺人 + 預言家查驗。"""
    global _ww_state

    # 綁定本輪遊戲 ID：所有長時間 await 之後都要驗證，避免舊局殘留任務污染新局
    my_game_id = _ww_state.get("game_id", 0)

    _ww_state["day"] += 1
    _ww_state["phase_detail"] = "night_wolf"
    _ww_state["night_target"] = None
    _ww_state["seer_target"] = None
    _ww_state["seer_result"] = None

    narrate = await _ww_narrate(f"第{_ww_state['day']}個夜晚降臨，村莊陷入寂靜")
    narrate_text = f"\n\n> {narrate}" if narrate else ""

    embed = discord.Embed(
        title=f"🌙 第 {_ww_state['day']} 夜",
        description=(
            f"夜幕降臨，所有人閉上眼睛...{narrate_text}\n\n"
            "🐺 狼人請選擇目標\n"
            "🔮 預言家請選擇查驗對象\n\n"
            "_請至你的 DM 進行操作_"
        ),
        color=discord.Color.dark_blue(),
    )
    await channel.send(embed=embed)

    # ── 狼人行動 ──
    wolves = [p for p in _ww_alive_players() if p["role"] == "狼人"]
    human_wolves = [w for w in wolves if not w["is_ai"]]
    ai_wolves = [w for w in wolves if w["is_ai"]]

    # AI 狼自動選目標（策略：優先殺預言家 > 隨機非狼人）
    if ai_wolves and not human_wolves:
        targets = [p for p in _ww_alive_players() if p["role"] != "狼人"]
        if targets:
            # 優先目標：預言家（如果活著）
            seer_targets = [t for t in targets if t["role"] == "預言家"]
            if seer_targets and _ww_random.random() < 0.6:
                target = _ww_random.choice(seer_targets)
            else:
                target = _ww_random.choice(targets)
            _ww_state["night_target"] = target["id"]
            _ww_log(f"AI wolf chose target: {target['name']} ({target['role']})")
    elif human_wolves:
        # 發 DM 給真人狼人投票
        await _ww_wolf_vote(channel, human_wolves)
    
    # 如果有 AI 狼但也有真人狼，AI 狼等待真人狼決策（不重複設目標）

    # ── 預言家行動 ──
    seer = next((p for p in _ww_alive_players() if p["role"] == "預言家"), None)
    if seer:
        if seer["is_ai"]:
            # AI 預言家自動查驗（策略：優先查未查過的玩家）
            checked = seer.get("seer_history", [])
            candidates = [p for p in _ww_alive_players() if p["id"] != seer["id"]]
            unchecked = [p for p in candidates if p["id"] not in checked]
            if unchecked:
                target = _ww_random.choice(unchecked)
            elif candidates:
                target = _ww_random.choice(candidates)
            else:
                target = None
            if target:
                _ww_state["seer_target"] = target["id"]
                _ww_state["seer_result"] = target["role"]
                if "seer_history" not in seer:
                    seer["seer_history"] = []
                seer["seer_history"].append(target["id"])
                _ww_log(f"AI seer checked {target['name']}: {target['role']}")
        else:
            await _ww_seer_check(channel, seer)

    # 等待行動完成
    deadline = _time.time() + 60  # 60 秒等待
    while _time.time() < deadline:
        if _ww_state.get("game_id") != my_game_id:
            return  # 舊局殘留任務，新局已開始，靜默中止
        wolf_done = _ww_state["night_target"] is not None
        seer_done = True
        if seer and not seer["is_ai"]:
            seer_done = _ww_state["seer_target"] is not None
        if wolf_done and seer_done:
            break
        await asyncio.sleep(2)

    if _ww_state.get("game_id") != my_game_id:
        return  # 本局已結束/被新局取代，靜默中止

    # ── 處理夜晚結果 ──
    killed_id = _ww_state.get("night_target")
    killed = _ww_player_by_id(killed_id) if killed_id else None

    if killed:
        killed["alive"] = False
        _ww_log(f"Night {_ww_state['day']}: {killed['name']} was killed")
        day_narrate = await _ww_narrate(
            f"第{_ww_state['day']}天清晨，{killed['name']}被發現死在床上",
            extra=f"死者身分：{killed['role']}"
        )
        narrate_text = f"\n\n> {day_narrate}" if day_narrate else ""
        embed = discord.Embed(
            title=f"☀️ 第 {_ww_state['day']} 天清晨",
            description=(
                f"天亮了...{narrate_text}\n\n"
                f"💀 **{killed['name']}** 在夜晚被殺害。\n"
                f"身分：{_WW_ROLE_INFO[killed['role']]['emoji']} {killed['role']}\n\n"
                "請大家開始討論，稍後將進行投票。"
            ),
            color=discord.Color.orange(),
        )
    else:
        embed = discord.Embed(
            title=f"☀️ 第 {_ww_state['day']} 天清晨",
            description="天亮了...昨夜風平浪靜，沒有人遇害。\n\n請大家開始討論，稍後將進行投票。",
            color=discord.Color.green(),
        )

    await channel.send(embed=embed)

    if _ww_state.get("game_id") != my_game_id:
        return  # 本局已結束/被新局取代，靜默中止

    # 檢查勝負
    winner = _ww_check_win()
    if winner:
        await _ww_end_game(channel, winner)
        return

    # 進入白天討論
    await _ww_day_phase(channel)


# ── 狼人投票（DM）──
class WerewolfNightActionView(discord.ui.View):
    """狼人夜晚選擇目標的按鈕面板（DM）。"""
    def __init__(self, targets):
        super().__init__(timeout=60)
        self._targets = targets
        self._vote = None
        self._voter_id = None
        self._game_id = _ww_state.get("game_id", 0)  # 綁定建立時的局號，防止跨局污染

    async def _handle(self, interaction: discord.Interaction, target_id: str):
        global _ww_state
        if _ww_state.get("game_id") != self._game_id:
            await interaction.response.send_message("⚠️ 此面板已失效（遊戲已結束或新的一局已開始）。", ephemeral=True)
            return
        if self._vote is not None:
            await interaction.response.send_message("⚠️ 你已經選好了。", ephemeral=True)
            return
        self._vote = target_id
        target = _ww_player_by_id(target_id)
        await interaction.response.send_message(
            f"✅ 你選擇了擊殺 **{target['name']}**。", ephemeral=True,
        )
        # 更新全局目標
        _ww_state["night_target"] = target_id


async def _ww_wolf_vote(channel, wolves):
    """發送 DM 給狼人玩家選擇目標。"""
    targets = [p for p in _ww_alive_players() if p["role"] != "狼人"]
    if not targets:
        return

    # 只讓第一個真人狼人操作（簡化：多狼時只取一人意見）
    wolf = wolves[0]
    try:
        member = channel.guild.get_member(int(wolf["id"]))
        if not member:
            return

        embed = discord.Embed(
            title="🐺 夜晚行動 · 狼人",
            description="請選擇今晚要擊殺的目標：",
            color=discord.Color.red(),
        )
        embed.set_footer(text="60 秒內做選擇")

        view = WerewolfNightActionView(targets)
        for t in targets:
            btn = discord.ui.Button(
                label=f"殺 {t['name']}", style=discord.ButtonStyle.danger,
                custom_id=f"ww_wolf_{t['id'][:12]}",
            )
            async def _cb(interaction, tid=t["id"]):
                await view._handle(interaction, tid)
            btn.callback = _cb
            view.add_item(btn)

        await member.send(embed=embed, view=view)

        # 如果有多個真人狼人，通知其他狼人等待
        for w in wolves[1:]:
            other = channel.guild.get_member(int(w["id"]))
            if other:
                await other.send("🐺 你的同伴正在選擇今晚的目標...")

    except Exception as e:
        print(f"⚠️ WW wolf vote DM failed: {e}")
        # 失敗時 AI 代選
        _ww_state["night_target"] = _ww_random.choice(targets)["id"]


# ── 預言家查驗（DM）──
class WerewolfSeerView(discord.ui.View):
    """預言家夜晚查驗的按鈕面板（DM）。"""
    def __init__(self, targets):
        super().__init__(timeout=60)
        self._targets = targets
        self._choice = None
        self._game_id = _ww_state.get("game_id", 0)  # 綁定建立時的局號，防止跨局污染

    async def _handle(self, interaction: discord.Interaction, target_id: str):
        global _ww_state
        if _ww_state.get("game_id") != self._game_id:
            await interaction.response.send_message("⚠️ 此面板已失效（遊戲已結束或新的一局已開始）。", ephemeral=True)
            return
        if self._choice is not None:
            await interaction.response.send_message("⚠️ 你已經查過了。", ephemeral=True)
            return
        self._choice = target_id
        target = _ww_player_by_id(target_id)
        is_wolf = target["role"] == "狼人"

        _ww_state["seer_target"] = target_id
        _ww_state["seer_result"] = target["role"]

        result_text = "🐺 狼人" if is_wolf else "👤 好人"
        await interaction.response.send_message(
            f"🔮 查驗結果：**{target['name']}** 是 **{result_text}**",
            ephemeral=True,
        )


async def _ww_seer_check(channel, seer):
    """發送 DM 給預言家選擇查驗對象。"""
    targets = [p for p in _ww_alive_players() if p["id"] != seer["id"]]
    if not targets:
        return

    try:
        member = channel.guild.get_member(int(seer["id"]))
        if not member:
            return

        embed = discord.Embed(
            title="🔮 夜晚行動 · 預言家",
            description="請選擇今晚要查驗的對象：",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="60 秒內做選擇")

        view = WerewolfSeerView(targets)
        for t in targets:
            btn = discord.ui.Button(
                label=f"查 {t['name']}", style=discord.ButtonStyle.primary,
                custom_id=f"ww_seer_{t['id'][:12]}",
            )
            async def _cb(interaction, tid=t["id"]):
                await view._handle(interaction, tid)
            btn.callback = _cb
            view.add_item(btn)

        await member.send(embed=embed, view=view)
    except Exception as e:
        print(f"⚠️ WW seer DM failed: {e}")


# ── 白天討論 + 投票 ──
class WerewolfDayVoteView(discord.ui.View):
    """白天投票淘汰面板。"""
    def __init__(self):
        super().__init__(timeout=60)
        self._votes = {}  # {voter_id: target_id}
        self._voters = set()

    @discord.ui.button(label="🗳️ 投票淘汰", style=discord.ButtonStyle.danger, custom_id="ww_day_vote")
    async def open_vote(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 這個按鈕打開一個選擇面板（用 select menu）
        pass


async def _ww_ai_discuss(ai_player: dict) -> tuple[str, str | None] | None:
    """讓 AI 玩家在白天討論時發言。根據角色產生不同視角的簡短發言。
    回傳 (發言內容, 被懷疑的玩家id或None) —— 懷疑對象由程式預先指定給AI寫評論，
    不讓AI自己自由選名字，避免（1）誤把自己的名字當懷疑對象、（2）提到遊戲外不存在的名字。"""
    role = ai_player["role"]
    my_name = ai_player["name"]
    alive = _ww_alive_players()
    others = [p for p in alive if p["id"] != ai_player["id"]]

    if not others:
        return None

    suspect_id = None

    if role == "狼人":
        # 狼人：假裝好人。70% 機率點名非狼人（偽裝合理），30% 隨機（含可能點隊友，製造煙霧彈）
        non_wolves = [p for p in others if p["role"] != "狼人"]
        if non_wolves and _ww_random.random() < 0.7:
            suspect = _ww_random.choice(non_wolves)
        else:
            suspect = _ww_random.choice(others)
        suspect_id = suspect["id"]
        prompt = (
            f"你是狼人殺遊戲中的玩家，你的名字是「{my_name}」。你的真實身分是狼人，但正在白天討論階段偽裝成好人，"
            f"絕對不能暴露自己是狼人，也絕對不要提到「{my_name}」這個名字（不要用第三人稱討論自己）。\n"
            f"請針對玩家「{suspect['name']}」說一句簡短的懷疑或分析（20-40字），語氣自然、像真人在推理，不要加emoji，不要提及「懷疑對象」「AI」等字眼。\n"
            f"直接輸出發言內容，不要加任何前綴或說明。"
        )
    elif role == "預言家":
        # 預言家：暗示有資訊但不直接跳身分
        checked = ai_player.get("seer_history", [])
        checked_wolf = next((cid for cid in checked if _ww_player_by_id(cid) and _ww_player_by_id(cid)["role"] == "狼人"), None)
        if checked_wolf:
            suspect = _ww_player_by_id(checked_wolf)
            suspect_id = suspect["id"]
            prompt = (
                f"你是狼人殺遊戲中的玩家，你的名字是「{my_name}」。你的真實身分是預言家，你已經查驗出「{suspect['name']}」是狼人，"
                f"但不要直接說「我是預言家」或「我查驗過」，絕對不要提到「{my_name}」這個名字（不要用第三人稱討論自己）。\n"
                f"請用比較有把握、堅定的語氣針對「{suspect['name']}」說一句簡短的懷疑或引導（20-40字），暗示你有依據但不要直接爆身分，不要加emoji。\n"
                f"直接輸出發言內容，不要加任何前綴或說明。"
            )
        elif checked:
            checked_names = [_ww_player_by_id(cid)["name"] for cid in checked if _ww_player_by_id(cid)]
            suspect = _ww_random.choice(others)
            suspect_id = suspect["id"]
            prompt = (
                f"你是狼人殺遊戲中的玩家，你的名字是「{my_name}」。你的真實身分是預言家，你已經查驗過「{'、'.join(checked_names)}」但他們都是好人，"
                f"絕對不要提到「{my_name}」這個名字（不要用第三人稱討論自己）。\n"
                f"請針對玩家「{suspect['name']}」說一句簡短的觀察或引導（20-40字），可以暗示你有一些資訊但不要直接爆身分，不要加emoji。\n"
                f"直接輸出發言內容，不要加任何前綴或說明。"
            )
        else:
            suspect = _ww_random.choice(others)
            suspect_id = suspect["id"]
            prompt = (
                f"你是狼人殺遊戲中的玩家，你的名字是「{my_name}」。絕對不要提到「{my_name}」這個名字（不要用第三人稱討論自己）。\n"
                f"請針對玩家「{suspect['name']}」說一句簡短的觀察或懷疑（20-40字），不要加emoji。\n"
                f"直接輸出發言內容，不要加任何前綴或說明。"
            )
    else:
        # 村民：隨機懷疑（沒有任何內幕消息，純粹瞎猜，符合真實遊玩體感）
        suspect = _ww_random.choice(others)
        suspect_id = suspect["id"]
        prompt = (
            f"你是狼人殺遊戲中的普通村民，你的名字是「{my_name}」。你沒有任何特殊資訊，只能憑直覺瞎猜。"
            f"絕對不要提到「{my_name}」這個名字（不要用第三人稱討論自己）。\n"
            f"請針對玩家「{suspect['name']}」說一句簡短的觀察或懷疑（20-40字），語氣自然、像真人隨口猜測，不要加emoji。\n"
            f"直接輸出發言內容，不要加任何前綴或說明。"
        )

    settings = {
        "api_url": chat_ai_settings["api_url"],
        "api_key": chat_ai_settings["api_key"],
        "model": chat_ai_settings.get("werewolf_model") or chat_ai_settings["model"],
        "model_fallback_chain": chat_ai_settings.get("model_fallback_chain", ""),
    }
    if chat_ai_settings.get("fallback_enabled") and not _ai_circuit_breaker["tripped"]:
        settings["fallback_api_url"] = chat_ai_settings.get("fallback_api_url", "")
        settings["fallback_api_key"] = chat_ai_settings.get("fallback_api_key", "")
        settings["fallback_model"] = chat_ai_settings.get("fallback_model", "")

    messages = [{"role": "user", "content": prompt}]
    try:
        result = await call_chat_api(
            messages, settings,
            max_tokens=100,
            timeout_total=10,
            timeout_read=8,
            is_background=True,
            fallback_mode="full",
            fallback_user_id="werewolf_ai",
            category="entertainment",
        )
        text = result.get("content", "").strip()
        # 清理：去掉引號、換行
        text = text.strip().strip("'").strip('"').replace("\n", " ")
        # 防禦性檢查：若AI仍不小心提到自己的名字，整句直接捨棄（避免蠢錯誤露出）
        if text and my_name in text:
            print(f"⚠️ WW AI discuss self-mention detected, discarding: {text[:50]}")
            return None
        return (text[:100], suspect_id) if text else None
    except Exception as e:
        print(f"⚠️ WW AI discuss failed: {e}")
        return None


async def _ww_day_phase(channel):
    """白天：討論 + 投票淘汰。"""
    global _ww_state

    # 綁定本輪遊戲 ID：所有長時間 await 之後都要驗證，避免舊局殘留任務污染新局
    my_game_id = _ww_state.get("game_id", 0)

    _ww_state["phase_detail"] = "day_discuss"
    _ww_state["discussion_suspects"] = {}

    # 提前計算存活玩家名單（討論發言與投票階段都需要用到）
    alive = _ww_alive_players()
    human_alive = [p for p in alive if not p["is_ai"]]
    ai_alive = [p for p in alive if p["is_ai"]]

    embed = discord.Embed(
        title=f"💬 第 {_ww_state['day']} 天 · 討論時間",
        description=(
            "請大家討論誰是狼人。\n"
            "⏱️ 討論時間 2 分鐘，之後自動進入投票。"
        ),
        color=discord.Color.gold(),
    )
    await channel.send(embed=embed)

    # AI 玩家發言（討論階段），並記錄被懷疑次數供投票階段參考
    for ai in ai_alive:
        try:
            result = await _ww_ai_discuss(ai)
            if _ww_state.get("game_id") != my_game_id:
                return  # 舊局殘留任務，新局已開始，靜默中止
            if result:
                msg, suspect_id = result
                await _ww_send_ai_message(channel, ai, msg)
                if suspect_id:
                    _ww_state["discussion_suspects"][suspect_id] = _ww_state["discussion_suspects"].get(suspect_id, 0) + 1
                await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ WW AI discuss failed for {ai['name']}: {e}")

    # 等待討論（剩餘時間，扣掉 AI 發言已用的時間）
    await asyncio.sleep(60)
    if _ww_state.get("game_id") != my_game_id:
        return  # 本局已結束/被新局取代，靜默中止，避免對新局頻道亂發訊息

    # 進入投票
    _ww_state["phase_detail"] = "day_vote"
    _ww_state["votes"] = {}
    suspects_tally = dict(_ww_state.get("discussion_suspects", {}))

    # 發送投票面板
    embed = discord.Embed(
        title=f"🗳️ 第 {_ww_state['day']} 天 · 投票淘汰",
        description=(
            "請選擇你要淘汰的玩家。\n"
            "⏱️ 投票時間 60 秒。"
        ),
        color=discord.Color.red(),
    )
    await channel.send(embed=embed)

    # 使用 select menu 投票
    options = []
    for p in alive:
        options.append(discord.SelectOption(
            label=p["name"], value=p["id"],
            description=f"{'AI 玩家' if p['is_ai'] else '真人玩家'}",
        ))

    vote_view = discord.ui.View(timeout=60)
    select = discord.ui.Select(
        placeholder="選擇要淘汰的玩家...",
        options=options,
        custom_id="ww_day_vote_select",
    )

    async def _vote_callback(interaction):
        if _ww_state.get("game_id") != my_game_id:
            await interaction.response.send_message("⚠️ 此投票面板已失效（新的一局已經開始）。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        voter = _ww_player_by_id(uid)
        if not voter or not voter["alive"]:
            await interaction.response.send_message("⚠️ 你無法投票。", ephemeral=True)
            return
        if uid in vote_view._voted:
            await interaction.response.send_message("⚠️ 你已經投過票了。", ephemeral=True)
            return
        vote_view._voted.add(uid)
        target_id = select.values[0]
        target = _ww_player_by_id(target_id)
        _ww_state["votes"][uid] = target_id
        await interaction.response.send_message(
            f"✅ 你投了 **{target['name']}** 一票。", ephemeral=True,
        )

    vote_view._voted = set()
    select.callback = _vote_callback
    vote_view.add_item(select)
    vote_msg = await channel.send(view=vote_view)

    # AI 玩家自動投票（策略性投票 + 討論階段風向影響）
    for ai in ai_alive:
        targets = [p for p in alive if p["id"] != ai["id"]]
        if not targets:
            continue
        if ai["role"] == "狼人":
            # AI 狼人：優先投預言家（如果已知且活著），否則投非狼人
            seer_targets = [t for t in targets if t["role"] == "預言家"]
            non_wolves = [t for t in targets if t["role"] != "狼人"]
            if seer_targets and _ww_random.random() < 0.5:
                vote_target = _ww_random.choice(seer_targets)
            elif non_wolves:
                vote_target = _ww_random.choice(non_wolves)
            else:
                vote_target = _ww_random.choice(targets)
        elif ai["role"] == "預言家":
            # AI 預言家：投已確認的狼人
            known_wolves = [t for t in targets if t["id"] in ai.get("seer_history", []) and t["role"] == "狼人"]
            if known_wolves:
                vote_target = _ww_random.choice(known_wolves)
            else:
                # 投已確認的非狼人之外的人（不投自己確認過的好人）
                confirmed_good = [t for t in targets if t["id"] in ai.get("seer_history", []) and t["role"] != "狼人"]
                unconfirmed = [t for t in targets if t not in confirmed_good]
                vote_target = _ww_random.choice(unconfirmed) if unconfirmed else _ww_random.choice(targets)
        else:
            # AI 村民：沒有內幕消息，主要跟討論階段的風向走（誰被點名越多次就越容易被投），
            # 但保留隨機噪聲，不是每次都精準命中真兇——這樣才像真實玩家會誤判、會被帶錯風向。
            weights = [1 + suspects_tally.get(t["id"], 0) * 2 for t in targets]
            vote_target = _ww_random.choices(targets, weights=weights, k=1)[0]
        _ww_state["votes"][ai["id"]] = vote_target["id"]
        _ww_log(f"AI {ai['name']} ({ai['role']}) voted for {vote_target['name']} ({vote_target['role']})")

    # 等待 60 秒
    await asyncio.sleep(60)
    if _ww_state.get("game_id") != my_game_id:
        return  # 本局已結束/被新局取代，靜默中止

    # 結算投票
    for child in vote_view.children:
        child.disabled = True
    try:
        await vote_msg.edit(view=vote_view)
    except Exception:
        pass

    # 計票
    vote_count = {}
    for voter_id, target_id in _ww_state["votes"].items():
        vote_count[target_id] = vote_count.get(target_id, 0) + 1

    if not vote_count:
        await channel.send("📊 本輪無人投票，跳過淘汰。")
    else:
        # 找出最高票
        max_votes = max(vote_count.values())
        top = [tid for tid, cnt in vote_count.items() if cnt == max_votes]
        if len(top) > 1:
            # 平票，隨機淘汰一人
            eliminated_id = _ww_random.choice(top)
            await channel.send(f"📊 平票！隨機淘汰一人。")
        else:
            eliminated_id = top[0]

        eliminated = _ww_player_by_id(eliminated_id)
        eliminated["alive"] = False
        _ww_log(f"Day vote: {eliminated['name']} eliminated ({eliminated['role']})")

        embed = discord.Embed(
            title="⚖️ 投票結果",
            description=(
                f"**{eliminated['name']}** 被淘汰！\n"
                f"身分：{_WW_ROLE_INFO[eliminated['role']]['emoji']} {eliminated['role']}\n\n"
                + "\n".join(
                    f"• {p['name']}：{vote_count.get(p['id'], 0)} 票"
                    for p in alive if p["id"] in vote_count or p["id"] == eliminated_id
                )
            ),
            color=discord.Color.dark_red(),
        )
        await channel.send(embed=embed)

    if _ww_state.get("game_id") != my_game_id:
        return  # 保險：結算後再檢查一次，避免對新局做出勝負判定

    # 檢查勝負
    winner = _ww_check_win()
    if winner:
        await _ww_end_game(channel, winner)
        return

    # 進入下一個夜晚
    await asyncio.sleep(3)
    if _ww_state.get("game_id") != my_game_id:
        return  # 等待期間本局已結束/被新局取代
    await _ww_night_phase(channel)


# ── 遊戲結束 + 清理 ──
async def _ww_end_game(channel, winner: str):
    """結束遊戲，公佈結果，清理身分組和權限。"""
    global _ww_state

    _ww_state["phase"] = "ended"
    _ww_state["winner"] = winner

    # 公佈所有身分
    role_reveal = "\n".join(
        f"• {p['name']}：{_WW_ROLE_INFO[p['role']]['emoji']} {p['role']} {'💀' if not p['alive'] else '✅'}"
        for p in _ww_state["players"]
    )

    win_text = "🐺 **狼人勝利！**" if winner == "wolves" else "👥 **好人勝利！**"

    narrate = await _ww_narrate(
        f"遊戲結束，{'狼人' if winner == 'wolves' else '好人'}獲勝",
        extra=f"存活玩家: {[p['name'] for p in _ww_alive_players()]}"
    )
    narrate_text = f"\n\n> {narrate}" if narrate else ""

    embed = discord.Embed(
        title="🐺 狼人殺 · 遊戲結束",
        description=f"{win_text}{narrate_text}\n\n**身分揭曉：**\n{role_reveal}",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="ICEA · AI 狼人殺 | 感謝遊玩！")
    await channel.send(embed=embed)

    # 清理：移除臨時身分組 + 頻道解鎖
    guild = channel.guild
    role_id = _ww_state.get("role_id")
    if role_id:
        role = guild.get_role(int(role_id))
        if role:
            # 移除所有成員的身分組
            try:
                for member in role.members:
                    try:
                        await member.remove_roles(role)
                        _ww_log(f"Removed role from {member.display_name}")
                    except Exception as e:
                        _ww_log(f"Failed to remove role from {member.display_name}: {e}")
                # 刪除身分組
                await role.delete(reason="狼人殺遊戲結束")
                _ww_log(f"Role {role.name} deleted")
            except discord.Forbidden:
                _ww_log("Cannot delete role (missing permissions)")
            except Exception as e:
                _ww_log(f"Role cleanup error: {e}")
        else:
            _ww_log(f"Role ID {role_id} not found in guild (already deleted?)")
    else:
        _ww_log("No role_id set, skipping role cleanup")

    # 重置頻道權限：移除 @everyone 的限制 + 移除身分組的覆寫
    try:
        # 移除 @everyone 的 overwrite（恢復預設=可發言）
        await channel.set_permissions(guild.default_role, overwrite=None)
        _ww_log("Channel @everyone permissions reset")
    except Exception as e:
        _ww_log(f"Channel @everyone permission reset failed: {e}")

    # 如果身分組還在（刪除失敗），也移除身分組的頻道覆寫
    if role_id:
        try:
            role_obj = guild.get_role(int(role_id))
            if role_obj:
                await channel.set_permissions(role_obj, overwrite=None)
                _ww_log("Channel role overwrite reset")
        except Exception as e:
            _ww_log(f"Channel role overwrite reset failed: {e}")

    # 確保所有真人玩家也能在頻道發言（移除任何個別限制）
    for p in _ww_state["players"]:
        if not p["is_ai"]:
            try:
                member = guild.get_member(int(p["id"]))
                if member:
                    # 移除針對個別成員的 overwrite（如果有）
                    await channel.set_permissions(member, overwrite=None)
            except Exception:
                pass

    _ww_log("Cleanup complete: channel unlocked, roles removed")

    # 重置狀態
    _ww_state = {
        "phase": "idle",
        "game_id": _ww_state.get("game_id", 0) + 1,
        "channel_id": None,
        "guild_id": None,
        "role_id": None,
        "signup_msg_id": None,
        "players": [],
        "day": 0,
        "phase_detail": "",
        "night_target": None,
        "seer_target": None,
        "seer_result": None,
        "votes": {},
        "log": [],
        "winner": None,
        "discussion_suspects": {},
    }
    _ww_invite_msg_id = None
    _ww_webhook_cache.clear()  # 清除 webhook 快取，下局會重新建立

    # 重新發送報名面板
    await asyncio.sleep(5)
    await _ww_post_invite(channel)


# ── 狼人殺背景循環 ──
async def werewolf_loop():
    """管理狼人殺報名面板。"""
    global _ww_invite_msg_id
    await asyncio.sleep(35)  # 等待 bot 就緒
    while True:
        try:
            if not chat_ai_settings.get("werewolf_enabled"):
                await asyncio.sleep(15)
                continue

            channel_id = chat_ai_settings.get("werewolf_channel_id")
            if not channel_id:
                await asyncio.sleep(15)
                continue

            channel = bot.get_channel(int(channel_id))
            if not channel:
                await asyncio.sleep(15)
                continue

            # 只有 idle 或 signup 階段才確保有面板
            if _ww_state["phase"] in ("idle", "signup"):
                needs_post = True
                if _ww_invite_msg_id:
                    try:
                        await channel.fetch_message(_ww_invite_msg_id)
                        needs_post = False
                    except discord.NotFound:
                        _ww_invite_msg_id = None
                    except Exception:
                        _ww_invite_msg_id = None

                if needs_post:
                    # idle 階段：初次開局，需建立臨時身分組 + 轉入 signup
                    if _ww_state["phase"] == "idle":
                        if not _ww_state.get("role_id"):
                            await _ww_setup_role_and_perms(channel)
                        _ww_state["phase"] = "signup"
                    # signup 階段但面板遺失（例如重啟後恢復到 signup 卻無面板）：直接重發
                    await _ww_post_invite(channel)
                    if _ww_state["players"]:
                        # 重發後若已有報名玩家（如重啟恢復），立即更新面板反映正確人數
                        await _ww_update_signup_embed(channel)

            await asyncio.sleep(30)
        except Exception as e:
            print(f"⚠️ Werewolf loop error: {e}")
            await asyncio.sleep(30)


async def _ww_setup_role_and_perms(channel):
    """建立臨時身分組並設定頻道權限。"""
    global _ww_state

    guild = channel.guild
    # 建立身分組
    try:
        role = await guild.create_role(
            name=f"狼人殺玩家_本場",
            color=discord.Color.dark_red(),
            reason="狼人殺遊戲身分組",
        )
        _ww_state["role_id"] = str(role.id)
        _ww_state["guild_id"] = str(guild.id)
        _ww_state["channel_id"] = str(channel.id)
        _ww_log(f"Created role: {role.name} ({role.id})")
    except discord.Forbidden:
        print("⚠️ WW: Cannot create role (missing permissions)")
        return
    except Exception as e:
        print(f"⚠️ WW create role failed: {e}")
        return

    # 設定頻道權限：身分組可發言，其他人只能看
    try:
        # @everyone 只能看
        await channel.set_permissions(
            guild.default_role,
            send_messages=False,
            read_messages=True,
            view_channel=True,
        )
        # 身分組可以發言
        await channel.set_permissions(
            role,
            send_messages=True,
            read_messages=True,
            view_channel=True,
        )
        _ww_log("Channel permissions set")
    except Exception as e:
        print(f"⚠️ WW set permissions failed: {e}")


# ── Slash Command Group ──
class WerewolfGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ww", description="AI 狼人殺遊戲")

    @app_commands.command(name="toggle", description="開啟/關閉 AI 狼人殺功能（機器人擁有者限定）")
    async def ww_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["werewolf_enabled"] = not chat_ai_settings.get("werewolf_enabled", False)
        _save_ww_settings()
        status = "開啟" if chat_ai_settings["werewolf_enabled"] else "關閉"
        await interaction.response.send_message(f"✅ AI 狼人殺已{status}。", ephemeral=True)

    @app_commands.command(name="channel", description="設定狼人殺頻道（機器人擁有者限定）")
    @app_commands.describe(channel="要設為狼人殺頻道的頻道")
    async def ww_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        chat_ai_settings["werewolf_channel_id"] = str(channel.id)
        _save_ww_settings()
        await interaction.response.send_message(
            f"✅ 狼人殺頻道已設為 {channel.mention}。\n"
            f"啟用後會自動發送報名面板。",
            ephemeral=True,
        )

    @app_commands.command(name="end", description="強制結束當前狼人殺遊戲（機器人擁有者限定）")
    async def ww_end(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        if _ww_state["phase"] not in ("playing", "signup"):
            await interaction.response.send_message("⚠️ 目前沒有進行中的狼人殺遊戲。", ephemeral=True)
            return
        await _ww_end_game(interaction.channel, "villagers")
        await interaction.response.send_message("✅ 狼人殺遊戲已強制結束。", ephemeral=True)

    @app_commands.command(name="test", description="測試模式：用 6 個 AI 玩家直接開始遊戲（機器人擁有者限定）")
    async def ww_test(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ 此指令僅限機器人擁有者使用。", ephemeral=True)
            return
        global _ww_state, _ww_invite_msg_id

        if _ww_state["phase"] not in ("idle", "signup"):
            await interaction.response.send_message("⚠️ 目前有遊戲進行中，請先 /ww end 再測試。", ephemeral=True)
            return

        channel = interaction.channel
        if not channel:
            await interaction.response.send_message("⚠️ 無法取得頻道。", ephemeral=True)
            return

        await interaction.response.send_message("🧪 正在啟動測試遊戲（6 AI 玩家）...", ephemeral=True)

        # 清除現有狀態，直接設定 6 個 AI 玩家
        _ww_state = {
            "phase": "signup",
            "game_id": _ww_state.get("game_id", 0),
            "channel_id": str(channel.id),
            "guild_id": str(channel.guild.id),
            "role_id": None,
            "signup_msg_id": None,
            "players": [],
            "day": 0,
            "phase_detail": "",
            "night_target": None,
            "seer_target": None,
            "seer_result": None,
            "votes": {},
            "log": [],
            "winner": None,
            "discussion_suspects": {},
        }
        _ww_invite_msg_id = None

        # 不建立身分組、不設頻道權限（測試模式）
        ai_names = ["AI-老王", "AI-小美", "AI-阿哲", "AI-婷婷", "AI-大偉", "AI-小黑"]
        for name in ai_names:
            _ww_state["players"].append({
                "id": f"ai_{len(_ww_state['players'])}",
                "name": name,
                "is_ai": True,
                "role": "",
                "alive": True,
                "dm_done": True,
                "dm_ok": True,
            })

        await channel.send("🧪 **測試模式啟動** — 6 個 AI 玩家，無身分組、無頻道鎖定。")
        await _ww_begin_game(channel)

    @app_commands.command(name="status", description="查看狼人殺遊戲狀態")
    async def ww_status(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🐺 AI 狼人殺狀態", color=discord.Color.dark_red())
        embed.add_field(name="功能狀態", value="開啟" if chat_ai_settings.get("werewolf_enabled") else "關閉", inline=True)
        ch_id = chat_ai_settings.get("werewolf_channel_id")
        embed.add_field(name="頻道", value=f"<#{ch_id}>" if ch_id else "未設定", inline=True)

        phase_names = {
            "idle": "空閒", "signup": "報名中", "playing": "遊戲中", "ended": "已結束",
        }
        embed.add_field(name="階段", value=phase_names.get(_ww_state["phase"], _ww_state["phase"]), inline=True)

        if _ww_state["phase"] in ("playing", "signup"):
            players = _ww_state["players"]
            real = [p for p in players if not p["is_ai"]]
            ai = [p for p in players if p["is_ai"]]
            embed.add_field(name="真人玩家", value=str(len(real)), inline=True)
            embed.add_field(name="AI 玩家", value=str(len(ai)), inline=True)
            embed.add_field(name="第幾天", value=str(_ww_state["day"]), inline=True)
            if players:
                plist = ", ".join(p["name"] for p in players)
                embed.add_field(name="玩家列表", value=plist[:1024], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


