"""
WW1 首相模擬器 — 多人回合制政治軍事模擬遊戲
================================================
玩家扮演一戰時期的首相/總統，透過每回合的前線報告（AI 生成）
做決策，管理各方滿意度，面對皇帝/議會的否決。
"""

import json, os, sys, traceback, asyncio, aiohttp
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ═════════════════════════════════════════════════════════════════
# 資料與持久化
# ═════════════════════════════════════════════════════════════════

_PERSIST_FILES.add("ww1_game.json")

DATA_PATH = str(DATA_DIR / "ww1_game.json")

DEFAULT_API_TIER1 = {
    "url": "https://api.ltzy.top/v1",
    "key": "sk-52dd603dab7d705016bcd06daa116b0f1830f42b2ecc914c69d7835169c0f430",
    "model": "deepseek-ai/deepseek-v4-flash-0731",
}

ww1_settings = {
    "api_tier_1": dict(DEFAULT_API_TIER1),
    "api_tier_2": {"url": "", "key": "", "model": ""},
    "api_tier_3": {"url": "", "key": "", "model": ""},
}

ww1_state = {
    "phase": "none",
    "turn": 0,
    "owner": None,
    "channel_id": None,
    "players": {},
    "global_events": [],
    "game_log": [],
}


def _load_ww1_data():
    global ww1_state, ww1_settings
    try:
        raw = load_json("ww1_game.json")
        if raw:
            ww1_state.update(raw.get("state", {}))
            for k in ("api_tier_1", "api_tier_2", "api_tier_3"):
                if k in raw.get("settings", {}):
                    ww1_settings[k] = raw["settings"][k]
    except Exception as e:
        print(f"⚠️ WW1 資料載入失敗：{e}")


def _save_ww1_data():
    try:
        save_json("ww1_game.json", {"state": ww1_state, "settings": ww1_settings})
    except Exception as e:
        print(f"⚠️ WW1 資料儲存失敗：{e}")


_load_ww1_data()

# ═════════════════════════════════════════════════════════════════
# 國家預設
# ═════════════════════════════════════════════════════════════════

COUNTRIES = {
    "德意志帝國": {
        "government": "monarchy", "ruler": "皇帝威廉二世",
        "ruler_title": "皇帝", "ruler_personality": "衝動、軍國主義、渴望榮耀、對威望極度敏感",
        "stats": {"military": 120, "economy": 110, "manpower": 100, "stability": 80, "prestige": 75, "war_exhaustion": 0},
        "satisfaction": {"emperor": 75, "military": 70, "nobility": 70, "civilian": 60, "church": 55},
    },
    "奧匈帝國": {
        "government": "monarchy", "ruler": "皇帝法蘭茨·約瑟夫",
        "ruler_title": "皇帝", "ruler_personality": "保守、謹慎、重視傳統與穩定，對改革持懷疑態度",
        "stats": {"military": 85, "economy": 75, "manpower": 90, "stability": 55, "prestige": 60, "war_exhaustion": 0},
        "satisfaction": {"emperor": 70, "military": 60, "nobility": 75, "civilian": 50, "church": 65},
    },
    "奧斯曼帝國": {
        "government": "monarchy", "ruler": "蘇丹穆罕默德五世",
        "ruler_title": "蘇丹", "ruler_personality": "宗教虔誠、受人擺佈、在意伊斯蘭傳統",
        "stats": {"military": 60, "economy": 50, "manpower": 70, "stability": 45, "prestige": 40, "war_exhaustion": 0},
        "satisfaction": {"emperor": 65, "military": 55, "nobility": 60, "civilian": 45, "church": 80},
    },
    "俄羅斯帝國": {
        "government": "monarchy", "ruler": "沙皇尼古拉二世",
        "ruler_title": "沙皇", "ruler_personality": "優柔寡斷、家庭至上、宗教虔誠、對改革多疑",
        "stats": {"military": 95, "economy": 70, "manpower": 130, "stability": 50, "prestige": 65, "war_exhaustion": 0},
        "satisfaction": {"emperor": 65, "military": 55, "nobility": 70, "civilian": 40, "church": 75},
    },
    "法蘭西共和國": {
        "government": "republic", "ruler": "總統普恩加萊",
        "ruler_title": "總統", "ruler_personality": "共和主義、反君主、重視自由與民族榮譽",
        "stats": {"military": 100, "economy": 105, "manpower": 90, "stability": 70, "prestige": 70, "war_exhaustion": 0},
        "satisfaction": {"military": 65, "civilian": 60, "church": 45},
    },
    "大英帝國": {
        "government": "monarchy", "ruler": "國王喬治五世",
        "ruler_title": "國王", "ruler_personality": "立憲、克制、盡責、支持議會",
        "stats": {"military": 110, "economy": 130, "manpower": 80, "stability": 85, "prestige": 90, "war_exhaustion": 0},
        "satisfaction": {"emperor": 80, "military": 75, "nobility": 65, "civilian": 70, "church": 60},
    },
    "義大利王國": {
        "government": "monarchy", "ruler": "國王維托里奧·埃馬努埃萊三世",
        "ruler_title": "國王", "ruler_personality": "軟弱、優柔寡斷、受議會擺佈",
        "stats": {"military": 75, "economy": 80, "manpower": 75, "stability": 60, "prestige": 55, "war_exhaustion": 0},
        "satisfaction": {"emperor": 60, "military": 55, "nobility": 60, "civilian": 55, "church": 70},
    },
    "美利堅合眾國": {
        "government": "republic", "ruler": "總統威爾遜",
        "ruler_title": "總統", "ruler_personality": "理想主義、孤立主義傾向、重視憲法與民主",
        "stats": {"military": 90, "economy": 140, "manpower": 110, "stability": 85, "prestige": 80, "war_exhaustion": 0},
        "satisfaction": {"military": 60, "civilian": 75, "church": 65},
    },
}


def _get_country_list():
    return list(COUNTRIES.keys())


# ═════════════════════════════════════════════════════════════════
# AI 呼叫（三層降級鏈）
# ═════════════════════════════════════════════════════════════════

async def _ww1_ai_call(messages, system_prompt=None, max_tokens=4096, temperature=0.8):
    """三層 API 降級鏈。成功回傳文字，全部失敗回傳 None。"""
    for tier in [1, 2, 3]:
        cfg = ww1_settings.get(f"api_tier_{tier}")
        if not cfg or not cfg.get("url"):
            continue
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": cfg["model"],
                    "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                headers = {
                    "Authorization": f"Bearer {cfg['key']}",
                    "Content-Type": "application/json",
                }
                async with session.post(
                    f"{cfg['url'].rstrip('/')}/chat/completions",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=90),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        err = await resp.text()
                        print(f"⚠️ WW1 AI tier {tier} HTTP {resp.status}: {err[:200]}")
        except Exception as e:
            print(f"⚠️ WW1 AI tier {tier} failed: {e}")
            continue
    return None


def _sat_keys(gov_type):
    if gov_type == "monarchy":
        return ["emperor", "military", "nobility", "civilian", "church"]
    else:
        return ["military", "civilian", "church"]


SAT_LABELS = {
    "emperor": "皇帝滿意度", "military": "軍方滿意度",
    "nobility": "貴族滿意度", "civilian": "平民滿意度",
    "church": "教會滿意度",
}

SAT_ICONS = {
    "emperor": "👑", "military": "⚔️", "nobility": "🎩",
    "civilian": "👥", "church": "✝️",
}


# ═════════════════════════════════════════════════════════════════
# 遊戲邏輯
# ═════════════════════════════════════════════════════════════════

def _init_player(discord_id, name, country_name):
    preset = COUNTRIES[country_name]
    gov = preset["government"]
    sat = {k: v for k, v in preset["satisfaction"].items()}
    if gov == "republic":
        sat.pop("emperor", None)
        sat.pop("nobility", None)
    return {
        "id": str(discord_id),
        "name": name,
        "country": country_name,
        "government": gov,
        "ruler_name": preset["ruler"],
        "ruler_title": preset["ruler_title"],
        "ruler_personality": preset["ruler_personality"],
        "stats": dict(preset["stats"]),
        "satisfaction": sat,
        "emperor_chats_this_turn": 0,
        "pending_decisions": [],
        "reports": [],
        "emperor_dialogues": [],
        "vetoes": [],
        "alive": True,
        "defeat_reason": None,
    }


def _build_report_prompt(player, all_summary):
    gov = player["government"]
    sat_desc = "、".join(f"{SAT_LABELS.get(k, k)} {player['satisfaction'][k]}" for k in _sat_keys(gov))
    stats = player["stats"]
    return f"""你是第一次世界大戰的戰地記者與情報分析官。你正在為{player['country']}的首相/總統撰寫一份機密的前線報告。

當前國家狀態（機密，報告中不能直接寫出數值）：
- 軍事力量：{stats['military']}、經濟：{stats['economy']}、人力：{stats['manpower']}
- 穩定：{stats['stability']}、威望：{stats['prestige']}、戰爭疲勞：{stats['war_exhaustion']}
- {sat_desc}

其他國家概況：{all_summary}
政體：{'帝制/王國' if gov == 'monarchy' else '共和國'}
統治者：{player['ruler_name']}（{player['ruler_personality']}）

要求：
1. 戰時電報語氣，約 400-600 字
2. 描述軍事前線、經濟、政治、外交
3. 滿意度只能暗示（如「軍方將領對近期決策不滿」），絕不能寫數值
4. 滿意度低於 30 要強烈暗示危機，高於 70 暗示良好
5. 提到統治者/議會態度
6. 不要列出任何數字或百分比"""


def _build_turn_prompt(player, all_summary, turn):
    gov = player["government"]
    sat_desc = "、".join(f"{SAT_LABELS.get(k, k)} {player['satisfaction'][k]}" for k in _sat_keys(gov))
    stats = player["stats"]
    decisions = "\n".join(f"- [{d['type']}] {d['action']}" for d in player["pending_decisions"]) or "（本回合無決策）"
    veto_entity = f"{player['ruler_name']}（{player['ruler_personality']}）" if gov == "monarchy" else "議會"
    return f"""你是第一次世界大戰的戰爭模擬引擎。處理{player['country']}第 {turn} 回合。

當前狀態：
- 軍事：{stats['military']}、經濟：{stats['economy']}、人力：{stats['manpower']}
- 穩定：{stats['stability']}、威望：{stats['prestige']}、戰爭疲勞：{stats['war_exhaustion']}
- {sat_desc}

政體：{'帝制/王國' if gov == 'monarchy' else '共和國'}
否決權人：{veto_entity}

本回合決策：
{decisions}

其他國家：{all_summary}

以 JSON 回覆：
{{
  "stat_changes": {{"military": 0, "economy": 0, "manpower": 0, "stability": 0, "prestige": 0, "war_exhaustion": 0}},
  "satisfaction_changes": {{<滿意度key>: <變化值>}},
  "vetoes": [{{"decision": "決策內容", "reason": "否決理由"}}],
  "report_text": "前線報告400-600字，不能寫數值",
  "global_event": "國際事件1-2句或空字串"
}}

規則：stat 變化 -20~+20，satisfaction 變化 -15~+15。否決權人滿意度低更可能否決。戰爭疲勞只增不減。"""


def _build_emperor_prompt(player):
    return f"""你是{player['ruler_name']}，{player['country']}的{player['ruler_title']}。
性格：{player['ruler_personality']}
當前對首相的滿意度：{player['satisfaction'].get('emperor', 50)}/100

與首相對話，保持歷史人物語氣。滿意度低(<30)嚴厲威脅，中(30-70)冷淡，高(>70)友善支持。100-200字。"""


def _deterministic_fallback(player, turn):
    stats = player["stats"]
    changes = {"military": 0, "economy": 0, "manpower": 0, "stability": 0, "prestige": 0, "war_exhaustion": 0}
    sat_changes = {k: 0 for k in _sat_keys(player["government"])}
    for d in player["pending_decisions"]:
        if d["type"] == "軍事":
            changes["military"] += 5; changes["manpower"] -= 5; changes["war_exhaustion"] += 3
            sat_changes["military"] = sat_changes.get("military", 0) + 5
            sat_changes["civilian"] = sat_changes.get("civilian", 0) - 3
        elif d["type"] == "經濟":
            changes["economy"] += 8; changes["stability"] += 2
            sat_changes["civilian"] = sat_changes.get("civilian", 0) + 3
        elif d["type"] == "外交":
            changes["prestige"] += 5
            sat_changes["military"] = sat_changes.get("military", 0) + 2
        elif d["type"] == "內政":
            changes["stability"] += 5
            sat_changes["civilian"] = sat_changes.get("civilian", 0) + 5
            if player["government"] == "monarchy":
                sat_changes["emperor"] = sat_changes.get("emperor", 0) - 2
                sat_changes["nobility"] = sat_changes.get("nobility", 0) - 3
    changes["war_exhaustion"] += 2
    sat_changes["civilian"] = sat_changes.get("civilian", 0) - 2
    report = f"【第 {turn} 回合前線報告】\n戰事持續，前線尚在控制中。\n"
    if changes["military"] > 0: report += "軍方近期行動有成效，將領士氣穩定。\n"
    if changes["economy"] > 0: report += "經濟措施略有起色，後方生產維持。\n"
    if changes["war_exhaustion"] > 3: report += "戰爭疲勞累積，民間已有倦怠之聲。\n"
    report += "請審慎評估下一步。"
    return {"stat_changes": changes, "satisfaction_changes": sat_changes, "vetoes": [], "report_text": report, "global_event": ""}


async def _process_turn():
    turn = ww1_state["turn"]
    alive = [p for p in ww1_state["players"].values() if p["alive"]]
    all_summary = "、".join(f"{p['country']}（{'穩定' if p['stats']['stability'] > 50 else '動盪'}）" for p in alive)

    async def process_one(player):
        sys_prompt = _build_turn_prompt(player, all_summary, turn)
        raw = await _ww1_ai_call([{"role": "user", "content": "請處理本回合決策並回傳 JSON。"}], system_prompt=sys_prompt, max_tokens=4096, temperature=0.7)
        if raw:
            import re
            jm = re.search(r'\{[\s\S]*\}', raw) or re.search(r'\[[\s\S]*\]', raw)
            if jm:
                try: return json.loads(jm.group())
                except: pass
        return _deterministic_fallback(player, turn)

    results = await asyncio.gather(*[process_one(p) for p in alive], return_exceptions=True)
    global_events = []
    for player, result in zip(alive, results):
        if isinstance(result, Exception):
            print(f"⚠️ {player['name']} 回合處理失敗：{result}")
            result = _deterministic_fallback(player, turn)
        for k, v in result.get("stat_changes", {}).items():
            if k in player["stats"]: player["stats"][k] = max(0, min(200, player["stats"][k] + v))
        for k, v in result.get("satisfaction_changes", {}).items():
            if k in player["satisfaction"]: player["satisfaction"][k] = max(0, min(100, player["satisfaction"][k] + v))
        for veto in result.get("vetoes", []):
            player["vetoes"].append({"turn": turn, **veto})
        report = result.get("report_text", "（報告生成失敗）")
        player["reports"].append({"turn": turn, "text": report, "timestamp": now_str()})
        ge = result.get("global_event", "")
        if ge: global_events.append({"turn": turn, "country": player["country"], "event": ge})
        player["pending_decisions"] = []
        player["emperor_chats_this_turn"] = 0
        for sk in _sat_keys(player["government"]):
            if player["satisfaction"][sk] <= 0:
                player["alive"] = False
                player["defeat_reason"] = f"{SAT_LABELS.get(sk, sk)}歸零，政府垮台"
                break
    ww1_state["global_events"].extend(global_events)
    if len(ww1_state["global_events"]) > 50: ww1_state["global_events"] = ww1_state["global_events"][-50:]
    ww1_state["turn"] = turn + 1
    ww1_state["game_log"].append({"turn": turn, "timestamp": now_str(), "event": "回合處理完成"})
    _save_ww1_data()


async def _generate_initial_reports():
    alive = [p for p in ww1_state["players"].values() if p["alive"]]
    all_summary = "、".join(p["country"] for p in alive)
    async def gen_one(player):
        sp = _build_report_prompt(player, all_summary)
        raw = await _ww1_ai_call([{"role": "user", "content": "請撰寫戰爭爆發初期的第一份前線報告。"}], system_prompt=sp, max_tokens=2048, temperature=0.8)
        if not raw: raw = f"戰爭已爆發。{player['country']}全國進入戰時狀態，軍隊正在動員，前線局勢尚不明朗。"
        player["reports"].append({"turn": 0, "text": raw, "timestamp": now_str()})
    await asyncio.gather(*[gen_one(p) for p in alive], return_exceptions=True)
    _save_ww1_data()


# ═════════════════════════════════════════════════════════════════
# Discord 指令
# ═════════════════════════════════════════════════════════════════

ww1_group = app_commands.Group(name="ww1", description="WW1 首相模擬器")


@ww1_group.command(name="start", description="建立新遊戲大廳（僅限擁有者）")
async def ww1_start(interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 僅限機器人擁有者使用。", ephemeral=True); return
    try:
        if ww1_state["phase"] == "active":
            await interaction.response.send_message("❉ 遊戲進行中，請先用 /ww1 end 結束。", ephemeral=True); return
        ww1_state.clear()
        ww1_state.update({"phase": "lobby", "turn": 0, "owner": str(interaction.user.id), "channel_id": str(interaction.channel_id), "players": {}, "global_events": [], "game_log": []})
        _save_ww1_data()
        embed = discord.Embed(title="⚔️ 第一次世界大戰 — 首相模擬器", description=f"遊戲大廳已建立！\n\n可用國家：{', '.join(_get_country_list())}\n\n使用 /ww1 join <國家> 加入。\n擁有者用 /ww1 begin 開始（至少 2 人）。", color=discord.Color.dark_red())
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 啟動失敗：{e}", ephemeral=True)


@ww1_group.command(name="join", description="加入遊戲（選擇國家）")
@app_commands.autocomplete(country=lambda interaction, current: [app_commands.Choice(name=c, value=c) for c in _get_country_list() if current.lower() in c.lower()][:25])
async def ww1_join(interaction, country: str):
    try:
        if ww1_state["phase"] != "lobby":
            await interaction.response.send_message("❉ 目前無法加入。", ephemeral=True); return
        if country not in COUNTRIES:
            await interaction.response.send_message(f"❉ 無效國家。可用：{', '.join(_get_country_list())}", ephemeral=True); return
        pid = str(interaction.user.id)
        if pid in ww1_state["players"]:
            await interaction.response.send_message("❉ 你已加入了。", ephemeral=True); return
        for p in ww1_state["players"].values():
            if p["country"] == country:
                await interaction.response.send_message(f"❉ {country} 已被選擇。", ephemeral=True); return
        player = _init_player(interaction.user.id, interaction.user.display_name, country)
        ww1_state["players"][pid] = player
        _save_ww1_data()
        gov = "帝制/王國" if player["government"] == "monarchy" else "共和國"
        await interaction.response.send_message(f"✅ {interaction.user.display_name} 加入，扮演 **{country}** 首相（{gov}）。", ephemeral=True)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 加入失敗：{e}", ephemeral=True)


@ww1_group.command(name="begin", description="開始遊戲（僅限擁有者）")
async def ww1_begin(interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 僅限擁有者。", ephemeral=True); return
    try:
        if ww1_state["phase"] != "lobby":
            await interaction.response.send_message("❉ 遊戲不在大廳階段。", ephemeral=True); return
        if len(ww1_state["players"]) < 2:
            await interaction.response.send_message("❉ 至少需 2 名玩家。", ephemeral=True); return
        ww1_state["phase"] = "active"; ww1_state["turn"] = 1
        _save_ww1_data()
        await interaction.response.send_message("⚔️ 遊戲開始！正在生成初始前線報告...")
        await _generate_initial_reports()
        plist = "\n".join(f"• {p['name']} — {p['country']}（{'帝制' if p['government'] == 'monarchy' else '共和'}）" for p in ww1_state["players"].values())
        embed = discord.Embed(title="⚔️ 戰爭已爆發！", description=f"第 1 回合開始。\n\n參戰國：\n{plist}\n\n用 /ww1 report 查看報告\n用 /ww1 policy 提交決策\n帝制國可用 /ww1 emperor 與統治者對話（3次/回合）\n擁有者用 /ww1 advance 推進回合", color=discord.Color.dark_red())
        await interaction.followup.send(embed=embed)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 開始失敗：{e}", ephemeral=True)


@ww1_group.command(name="report", description="查看最新前線報告")
async def ww1_report(interaction):
    try:
        pid = str(interaction.user.id)
        player = ww1_state["players"].get(pid)
        if not player:
            await interaction.response.send_message("❉ 你未加入遊戲。", ephemeral=True); return
        if not player["alive"]:
            await interaction.response.send_message(f"☠️ 你的政府已垮台：{player['defeat_reason']}", ephemeral=True); return
        if not player["reports"]:
            await interaction.response.send_message("❉ 尚無報告。", ephemeral=True); return
        latest = player["reports"][-1]
        embed = discord.Embed(title=f"📡 {player['country']} — 第 {latest['turn']} 回合前線報告", description=latest["text"], color=discord.Color.dark_green())
        embed.set_footer(text=f"{latest['timestamp']} | /ww1 policy 提交決策")
        if player["pending_decisions"]:
            embed.add_field(name="已提交決策", value="\n".join(f"• [{d['type']}] {d['action']}" for d in player["pending_decisions"]), inline=False)
        if player["government"] == "monarchy":
            embed.add_field(name="皇帝對話剩餘", value=f"{3 - player['emperor_chats_this_turn']}/3", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 查看失敗：{e}", ephemeral=True)


@ww1_group.command(name="policy", description="提交政策決策")
@app_commands.choices(policy_type=[
    app_commands.Choice(name="軍事", value="軍事"),
    app_commands.Choice(name="經濟", value="經濟"),
    app_commands.Choice(name="外交", value="外交"),
    app_commands.Choice(name="內政", value="內政"),
])
async def ww1_policy(interaction, policy_type: str, action: str):
    try:
        pid = str(interaction.user.id)
        player = ww1_state["players"].get(pid)
        if not player: await interaction.response.send_message("❉ 未加入遊戲。", ephemeral=True); return
        if not player["alive"]: await interaction.response.send_message("☠️ 政府已垮台。", ephemeral=True); return
        if ww1_state["phase"] != "active": await interaction.response.send_message("❉ 遊戲未進行中。", ephemeral=True); return
        player["pending_decisions"].append({"type": policy_type, "action": action, "timestamp": now_str()})
        _save_ww1_data()
        await interaction.response.send_message(f"✅ 已提交 [{policy_type}] {action}\n本回合 {len(player['pending_decisions'])} 項決策。", ephemeral=True)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 提交失敗：{e}", ephemeral=True)


@ww1_group.command(name="emperor", description="與皇帝/國王對話（帝制，每回合 3 次）")
async def ww1_emperor(interaction, message: str):
    try:
        pid = str(interaction.user.id)
        player = ww1_state["players"].get(pid)
        if not player: await interaction.response.send_message("❉ 未加入遊戲。", ephemeral=True); return
        if player["government"] != "monarchy": await interaction.response.send_message("❉ 共和國無皇帝。", ephemeral=True); return
        if not player["alive"]: await interaction.response.send_message("☠️ 政府已垮台。", ephemeral=True); return
        if player["emperor_chats_this_turn"] >= 3: await interaction.response.send_message("❉ 本回合對話已用完。", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        sp = _build_emperor_prompt(player)
        reply = await _ww1_ai_call([{"role": "user", "content": message}], system_prompt=sp, max_tokens=1024, temperature=0.8)
        if not reply: reply = f"（{player['ruler_name']} 此刻無暇回應。）"
        player["emperor_chats_this_turn"] += 1
        player["emperor_dialogues"].append({"turn": ww1_state["turn"], "player_msg": message, "emperor_reply": reply, "timestamp": now_str()})
        _save_ww1_data()
        embed = discord.Embed(title=f"👑 {player['ruler_name']} 的回應", description=reply, color=discord.Color.gold())
        embed.set_footer(text=f"第 {ww1_state['turn']} 回合 | 剩餘 {3 - player['emperor_chats_this_turn']}/3 次")
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 對話失敗：{e}", ephemeral=True)


@ww1_group.command(name="status", description="查看遊戲狀態")
async def ww1_status(interaction):
    try:
        if ww1_state["phase"] == "none":
            await interaction.response.send_message("❉ 目前沒有遊戲。", ephemeral=True); return
        lines = []
        for p in ww1_state["players"].values():
            s = "☠️ 垮台" if not p["alive"] else "✅"
            g = "👑" if p["government"] == "monarchy" else "🏛️"
            lines.append(f"{g} {p['name']} — {p['country']} {s}")
        embed = discord.Embed(title="⚔️ WW1 首相模擬器", color=discord.Color.dark_red())
        embed.add_field(name="階段", value=ww1_state["phase"], inline=True)
        embed.add_field(name="回合", value=str(ww1_state["turn"]), inline=True)
        embed.add_field(name="玩家數", value=str(len(ww1_state["players"])), inline=True)
        embed.add_field(name="參戰國", value="\n".join(lines) or "無", inline=False)
        if ww1_state["global_events"]:
            recent = ww1_state["global_events"][-3:]
            embed.add_field(name="近期事件", value="\n".join(f"[T{e['turn']}] {e['country']}: {e['event']}" for e in recent), inline=False)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 查詢失敗：{e}", ephemeral=True)


@ww1_group.command(name="advance", description="推進回合（僅限擁有者）")
async def ww1_advance(interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 僅限擁有者。", ephemeral=True); return
    try:
        if ww1_state["phase"] != "active":
            await interaction.response.send_message("❉ 遊戲未進行中。", ephemeral=True); return
        alive_count = sum(1 for p in ww1_state["players"].values() if p["alive"])
        if alive_count < 2:
            await interaction.response.send_message("❉ 存活玩家少於 2 人。", ephemeral=True); return
        await interaction.response.defer()
        await interaction.followup.send(f"⏳ 處理第 {ww1_state['turn']} 回合...")
        await _process_turn()
        alive = [p for p in ww1_state["players"].values() if p["alive"]]
        if len(alive) <= 1:
            if len(alive) == 1:
                w = alive[0]; ww1_state["phase"] = "ended"; _save_ww1_data()
                await interaction.followup.send(embed=discord.Embed(title="🏁 戰爭結束！", description=f"**{w['country']}** 的 **{w['name']}** 贏得勝利！", color=discord.Color.gold()))
            else:
                ww1_state["phase"] = "ended"; _save_ww1_data()
                await interaction.followup.send("🏁 全面崩潰。")
            return
        embed = discord.Embed(title=f"✅ 第 {ww1_state['turn'] - 1} 回合完成", description=f"現在是第 {ww1_state['turn']} 回合。查看報告並提交決策。", color=discord.Color.green())
        veto_lines = []
        for p in ww1_state["players"].values():
            if not p["alive"]: continue
            for v in p["vetoes"]:
                if v["turn"] == ww1_state["turn"] - 1:
                    ve = p["ruler_name"] if p["government"] == "monarchy" else "議會"
                    veto_lines.append(f"**{p['country']}**：{ve} 否決「{v.get('decision', '?')}」— {v.get('reason', '')}")
        if veto_lines: embed.add_field(name="🚫 否決通知", value="\n".join(veto_lines), inline=False)
        defeated = [p for p in ww1_state["players"].values() if not p["alive"] and p.get("defeat_reason")]
        if defeated: embed.add_field(name="☠️ 垮台", value="\n".join(f"**{p['country']}**：{p['defeat_reason']}" for p in defeated), inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        traceback.print_exc()
        await interaction.followup.send(f"❌ 推進失敗：{e}")


@ww1_group.command(name="end", description="結束遊戲（僅限擁有者）")
async def ww1_end(interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 僅限擁有者。", ephemeral=True); return
    try:
        if ww1_state["phase"] == "none":
            await interaction.response.send_message("❉ 沒有遊戲。", ephemeral=True); return
        alive = [p for p in ww1_state["players"].values() if p["alive"]]
        if alive:
            alive.sort(key=lambda p: p["stats"]["prestige"], reverse=True)
            w = alive[0]
            embed = discord.Embed(title="🏁 遊戲結束", description=f"勝利者：**{w['country']}** 的 **{w['name']}**（威望 {w['stats']['prestige']}）", color=discord.Color.gold())
        else:
            embed = discord.Embed(title="🏁 遊戲結束", description="所有政府垮台。", color=discord.Color.dark_gray())
        ww1_state["phase"] = "ended"
        _save_ww1_data()
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 結束失敗：{e}", ephemeral=True)


class WW1SettingsModal(discord.ui.Modal, title="WW1 AI API 三層降級鏈設定"):
    def __init__(self):
        super().__init__()
        for i in [1, 2, 3]:
            cfg = ww1_settings.get(f"api_tier_{i}", {})
            self.add_item(discord.ui.TextInput(label=f"Tier {i} — API URL", default=cfg.get("url", ""), required=(i == 1), placeholder="https://api.example.com/v1", custom_id=f"t{i}_url"))
            self.add_item(discord.ui.TextInput(label=f"Tier {i} — API Key", default=cfg.get("key", ""), required=False, placeholder="sk-...", custom_id=f"t{i}_key"))
            self.add_item(discord.ui.TextInput(label=f"Tier {i} — Model", default=cfg.get("model", ""), required=False, placeholder="model name", custom_id=f"t{i}_model"))

    async def on_submit(self, interaction):
        try:
            for i in [1, 2, 3]:
                url = self.get_item(f"t{i}_url").value.strip()
                key = self.get_item(f"t{i}_key").value.strip()
                model = self.get_item(f"t{i}_model").value.strip()
                if url: ww1_settings[f"api_tier_{i}"] = {"url": url, "key": key, "model": model}
            _save_ww1_data()
            await interaction.response.send_message("✅ WW1 API 設定已儲存。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 儲存失敗：{e}", ephemeral=True)


@ww1_group.command(name="settings", description="設定 AI API 三層降級鏈（僅限擁有者）")
async def ww1_settings_cmd(interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ 僅限擁有者。", ephemeral=True); return
    try:
        await interaction.response.send_modal(WW1SettingsModal())
    except Exception as e:
        traceback.print_exc()
        await interaction.response.send_message(f"❌ 開啟失敗：{e}", ephemeral=True)


# ═════════════════════════════════════════════════════════════════
# Web 路由
# ═════════════════════════════════════════════════════════════════

def _check_ww1_auth(request):
    return request.headers.get("X-Auth") == str(OWNER_ID)

def _sanitize_state(requester_id):
    pid = str(requester_id)
    result = {"phase": ww1_state["phase"], "turn": ww1_state["turn"], "players": {}, "global_events": ww1_state["global_events"][-10:]}
    for opid, p in ww1_state["players"].items():
        if opid == pid: result["players"][opid] = dict(p)
        else: result["players"][opid] = {"name": p["name"], "country": p["country"], "government": p["government"], "ruler_name": p["ruler_name"], "alive": p["alive"], "defeat_reason": p.get("defeat_reason")}
    return result

async def _handle_ww1_page(request):
    hp = Path(__file__).parent / "ww1.html"
    if not hp.exists(): return web.Response(text="<h1>WW1</h1><p>前端頁面尚未建立。</p>", content_type="text/html")
    return web.FileResponse(str(hp))

async def _handle_ww1_state_api(request):
    pid = request.query.get("pid", request.headers.get("X-Auth", ""))
    if not pid: return web.json_response({"error": "需要 pid"}, status=400)
    return web.json_response(_sanitize_state(pid))

async def _handle_ww1_settings_get(request):
    if not _check_ww1_auth(request): return web.json_response({"error": "未授權"}, status=401)
    safe = {}
    for i in [1, 2, 3]:
        cfg = ww1_settings.get(f"api_tier_{i}", {})
        safe[f"api_tier_{i}"] = {"url": cfg.get("url", ""), "key": (cfg.get("key", "")[:8] + "***") if cfg.get("key") else "", "model": cfg.get("model", "")}
    return web.json_response(safe)

async def _handle_ww1_settings_put(request):
    if not _check_ww1_auth(request): return web.json_response({"error": "未授權"}, status=401)
    try:
        data = await request.json()
        for i in [1, 2, 3]:
            k = f"api_tier_{i}"
            if k in data:
                nc = data[k]; old = ww1_settings.get(k, {})
                if nc.get("key", "").endswith("***"): nc["key"] = old.get("key", "")
                ww1_settings[k] = nc
        _save_ww1_data()
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def _handle_ww1_policy_api(request):
    pid = request.headers.get("X-Auth", "")
    if not pid or pid not in ww1_state["players"]: return web.json_response({"error": "未加入遊戲"}, status=400)
    try:
        data = await request.json()
        player = ww1_state["players"][pid]
        if not player["alive"]: return web.json_response({"error": "政府已垮台"}, status=400)
        player["pending_decisions"].append({"type": data["type"], "action": data["action"], "timestamp": now_str()})
        _save_ww1_data()
        return web.json_response({"ok": True, "pending": len(player["pending_decisions"])})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def _handle_ww1_emperor_api(request):
    pid = request.headers.get("X-Auth", "")
    if not pid or pid not in ww1_state["players"]: return web.json_response({"error": "未加入"}, status=400)
    try:
        player = ww1_state["players"][pid]
        if player["government"] != "monarchy": return web.json_response({"error": "共和國無皇帝"}, status=400)
        if player["emperor_chats_this_turn"] >= 3: return web.json_response({"error": "對話已用完"}, status=400)
        data = await request.json()
        msg = data.get("message", "")
        sp = _build_emperor_prompt(player)
        reply = await _ww1_ai_call([{"role": "user", "content": msg}], system_prompt=sp, max_tokens=1024, temperature=0.8)
        if not reply: reply = f"（{player['ruler_name']} 無暇回應。）"
        player["emperor_chats_this_turn"] += 1
        player["emperor_dialogues"].append({"turn": ww1_state["turn"], "player_msg": msg, "emperor_reply": reply, "timestamp": now_str()})
        _save_ww1_data()
        return web.json_response({"reply": reply, "remaining": 3 - player["emperor_chats_this_turn"]})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def _handle_ww1_advance_api(request):
    if not _check_ww1_auth(request): return web.json_response({"error": "未授權"}, status=401)
    try:
        if ww1_state["phase"] != "active": return web.json_response({"error": "遊戲未進行中"}, status=400)
        await _process_turn()
        return web.json_response({"ok": True, "turn": ww1_state["turn"]})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def _handle_ww1_join_api(request):
    pid = request.headers.get("X-Auth", "")
    if not pid: return web.json_response({"error": "未授權"}, status=401)
    try:
        if ww1_state["phase"] != "lobby": return web.json_response({"error": "不在大廳階段"}, status=400)
        data = await request.json()
        country = data.get("country", "")
        if country not in COUNTRIES: return web.json_response({"error": "無效國家"}, status=400)
        if pid in ww1_state["players"]: return web.json_response({"error": "已加入"}, status=400)
        for p in ww1_state["players"].values():
            if p["country"] == country: return web.json_response({"error": f"{country} 已被選"}, status=400)
        player = _init_player(int(pid), data.get("name", f"Player"), country)
        ww1_state["players"][pid] = player
        _save_ww1_data()
        return web.json_response({"ok": True, "country": country})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)


def setup_ww1_routes(app):
    app.router.add_get("/ww1", _handle_ww1_page)
    app.router.add_get("/api/ww1/state", _handle_ww1_state_api)
    app.router.add_get("/api/ww1/settings", _handle_ww1_settings_get)
    app.router.add_put("/api/ww1/settings", _handle_ww1_settings_put)
    app.router.add_post("/api/ww1/policy", _handle_ww1_policy_api)
    app.router.add_post("/api/ww1/emperor", _handle_ww1_emperor_api)
    app.router.add_post("/api/ww1/advance", _handle_ww1_advance_api)
    app.router.add_post("/api/ww1/join", _handle_ww1_join_api)
    print("⚔️ WW1 首相模擬器路由已註冊：/ww1 + /api/ww1/*")


print("⚔️ WW1 首相模擬器已載入")
