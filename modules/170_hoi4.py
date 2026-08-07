# ════════════════════════════════════════════════════════════════════════════
# HOI4 文字版 — Discord 面板 + 網頁面板並行的簡化版鋼鐵雄心4
# 模組 170 — Discord 端持久面板、tick 結算、按鈕互動
# 網頁端（地圖/前線微操/師級編制）由 dashboard.html + /api/game/hoi4/* 提供
# ════════════════════════════════════════════════════════════════════════════

import os, json, asyncio, random, math, time, datetime as _dt
from collections import OrderedDict

# ── 常數 ──
GUILD_ID = 1425065927027720286
HOI4_PANEL_TITLE_MARKER = "🎭 戰略指揮部"
TICK_INTERVAL_SECONDS = 1800          # 30 分鐘 = 遊戲內 1 天
PANEL_REFRESH_SECONDS = 60            # 面板刷新間隔
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
HOI4_STATE_FILE = os.path.join(DATA_DIR, "hoi4_state.json")
HOI4_PANEL_FILE = os.path.join(DATA_DIR, "hoi4_panel.json")

# ── 預設國策樹 ──
_DEFAULT_FOCUS_TREE = [
    {"id": "f_industry_1", "name": "工業化基礎", "cost": 70, "x": 0, "y": 0, "prereq": [], "effects": {"civilian_factories": 2}, "desc": "建立基礎工業能力"},
    {"id": "f_industry_2", "name": "擴大生產", "cost": 70, "x": 0, "y": 1, "prereq": ["f_industry_1"], "effects": {"civilian_factories": 2, "military_factories": 1}, "desc": "擴充工廠規模"},
    {"id": "f_industry_3", "name": "戰時經濟", "cost": 70, "x": 0, "y": 2, "prereq": ["f_industry_2"], "effects": {"military_factories": 3}, "desc": "轉入戰時經濟體制"},
    {"id": "f_research_1", "name": "學術投資", "cost": 70, "x": 1, "y": 0, "prereq": [], "effects": {"research_slots": 1}, "desc": "增加一個研究槽"},
    {"id": "f_research_2", "name": "技術突破", "cost": 70, "x": 1, "y": 1, "prereq": ["f_research_1"], "effects": {"research_speed_bonus": 0.2}, "desc": "研究速度 +20%"},
    {"id": "f_military_1", "name": "陸軍擴編", "cost": 70, "x": 2, "y": 0, "prereq": [], "effects": {"manpower": 500}, "desc": "擴充兵員"},
    {"id": "f_military_2", "name": "裝甲學說", "cost": 70, "x": 2, "y": 1, "prereq": ["f_military_1"], "effects": {"armor_unlocked": True}, "desc": "解鎖裝甲師模板"},
    {"id": "f_military_3", "name": "空軍發展", "cost": 70, "x": 2, "y": 2, "prereq": ["f_military_1"], "effects": {"air_unlocked": True}, "desc": "解鎖空軍"},
    {"id": "f_political_1","name": "政治改革", "cost": 70, "x": 3, "y": 0, "prereq": [], "effects": {"political_power_per_tick": 0.5}, "desc": "政治點數產出 +0.5/tick"},
    {"id": "f_political_2","name": "穩定政府", "cost": 70, "x": 3, "y": 1, "prereq": ["f_political_1"], "effects": {"stability": 0.1}, "desc": "穩定度 +10%"},
    {"id": "f_diplo_1", "name": "外交擴張", "cost": 70, "x": 4, "y": 0, "prereq": [], "effects": {"extra_policies": 1}, "desc": "解鎖進階外交"},
    {"id": "f_diplo_2", "name": "同盟體系", "cost": 70, "x": 4, "y": 1, "prereq": ["f_diplo_1"], "effects": {"alliance_slots": 2}, "desc": "可建立同盟"},
]

_DEFAULT_TECH_TREE = {
    "industry": [
        {"id": "t_ind_1", "name": "工業基礎", "cost": 100, "effects": {"construction_speed": 0.1}},
        {"id": "t_ind_2", "name": "大量生產", "cost": 120, "prereq": "t_ind_1", "effects": {"production_speed": 0.15}},
        {"id": "t_ind_3", "name": "流水線", "cost": 150, "prereq": "t_ind_2", "effects": {"production_speed": 0.2}},
        {"id": "t_ind_4", "name": "原子能", "cost": 200, "prereq": "t_ind_3", "effects": {"research_speed_bonus": 0.1}},
    ],
    "army": [
        {"id": "t_arm_1", "name": "步兵裝備 I", "cost": 100, "effects": {"infantry_attack": 2}},
        {"id": "t_arm_2", "name": "步兵裝備 II","cost": 120, "prereq": "t_arm_1", "effects": {"infantry_attack": 3, "infantry_defense": 2}},
        {"id": "t_arm_3", "name": "支援裝備", "cost": 130, "prereq": "t_arm_1", "effects": {"support_unlocked": True}},
        {"id": "t_arm_4", "name": "裝甲戰車", "cost": 180, "prereq": "t_arm_2", "effects": {"armor_attack": 5}},
    ],
    "air": [
        {"id": "t_air_1", "name": "基礎戰機", "cost": 120, "effects": {"air_attack": 3}},
        {"id": "t_air_2", "name": "攔截機", "cost": 140, "prereq": "t_air_1", "effects": {"air_defense": 3}},
        {"id": "t_air_3", "name": "戰略轟炸", "cost": 180, "prereq": "t_air_2", "effects": {"strategic_bombing": True}},
    ],
    "naval": [
        {"id": "t_nav_1", "name": "海岸防禦", "cost": 100, "effects": {"naval_defense": 3}},
        {"id": "t_nav_2", "name": "驅逐艦", "cost": 130, "prereq": "t_nav_1", "effects": {"naval_attack": 2}},
    ],
}

_DEFAULT_DIVISION_TEMPLATES = [
    {"id": "tpl_inf", "name": "步兵師", "battalions": ["infantry","infantry","infantry"],
     "support": ["artillery","engineer"], "stats": {"attack": 8,"defense": 12,"hp": 30,"org": 60}, "cost": 100},
    {"id": "tpl_cav", "name": "騎兵師", "battalions": ["cavalry","cavalry","cavalry"],
     "support": [], "stats": {"attack": 6,"defense": 4,"hp": 20,"org": 40,"speed": 2}, "cost": 80},
]

def _generate_default_provinces():
    provinces = {}
    terrain_types = ["plains","mountains","forest","urban","plains","forest","plains","desert"]
    for i in range(20):
        pid = "p{:02d}".format(i)
        row, col = divmod(i, 5)
        provinces[pid] = {
            "id": pid, "name": "省份{}".format(i+1), "owner": None,
            "type": terrain_types[i % len(terrain_types)],
            "victory_points": random.randint(1,5) if i % 3 == 0 else 0,
            "fortifications": 0, "infrastructure": 1, "resources": {},
            "grid_x": col, "grid_y": row,
        }
    resource_types = ["steel","oil","rubber","tungsten","aluminum"]
    for pid in provinces:
        if random.random() < 0.4:
            res = random.choice(resource_types)
            provinces[pid]["resources"][res] = random.randint(2,8)
    return provinces

# ════════════════════════════════════════════════════════════════════════════
# 遊戲狀態管理
# ════════════════════════════════════════════════════════════════════════════

hoi4_state = {
    "game_active": False, "tick": 0, "last_tick_iso": None,
    "countries": {}, "provinces": {}, "wars": {},
    "focus_tree": _DEFAULT_FOCUS_TREE, "tech_tree": _DEFAULT_TECH_TREE, "log": [],
}
hoi4_panel = {"channel_id": None, "message_id": None}

def _load_hoi4_state():
    global hoi4_state
    try:
        if os.path.exists(HOI4_STATE_FILE):
            with open(HOI4_STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json_module.loads(f.read())
            if loaded.get("focus_tree"): hoi4_state["focus_tree"] = loaded["focus_tree"]
            if loaded.get("tech_tree"): hoi4_state["tech_tree"] = loaded["tech_tree"]
            hoi4_state.update({k: v for k, v in loaded.items() if k not in ("focus_tree","tech_tree")})
            print("HOI4 狀態已載入（tick={}, 國家數={}）".format(hoi4_state.get("tick",0), len(hoi4_state.get("countries",{}))))
    except Exception as e:
        print("HOI4 狀態載入失敗: {}".format(e))
    global hoi4_panel
    try:
        if os.path.exists(HOI4_PANEL_FILE):
            with open(HOI4_PANEL_FILE, "r", encoding="utf-8") as f:
                hoi4_panel.update(json_module.loads(f.read()))
    except Exception as e:
        print("HOI4 面板設定載入失敗: {}".format(e))

def _save_hoi4_state():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(HOI4_STATE_FILE, "w", encoding="utf-8") as f:
            json_module.dump(hoi4_state, f, ensure_ascii=False, indent=2)
        asyncio.ensure_future(_immediate_drive_upload("hoi4_state.json"))
    except Exception as e:
        print("HOI4 存檔失敗: {}".format(e))

def _save_hoi4_panel():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(HOI4_PANEL_FILE, "w", encoding="utf-8") as f:
            json_module.dump(hoi4_panel, f, ensure_ascii=False)
        asyncio.ensure_future(_immediate_drive_upload("hoi4_panel.json"))
    except Exception as e:
        print("HOI4 面板設定存檔失敗: {}".format(e))

def _now_gmt8_iso():
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)).isoformat()

def _now_gmt8_str(fmt="%Y-%m-%d %H:%M"):
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)).strftime(fmt)

# ════════════════════════════════════════════════════════════════════════════
# 遊戲初始化
# ════════════════════════════════════════════════════════════════════════════

def _create_country(name, owner_id, color, province_ids):
    return {
        "name": name, "owner": str(owner_id), "color": color,
        "political_power": 50, "political_power_per_tick": 1.0, "stability": 0.75,
        "manpower": 1000, "civilian_factories": 8, "military_factories": 4,
        "resources": {"steel": 20, "oil": 10, "rubber": 5, "tungsten": 3, "aluminum": 15},
        "research_slots": 3, "research_speed_bonus": 0.0, "research": [],
        "construction_queue": [], "production_queue": [],
        "completed_focuses": [], "current_focus": None, "focus_progress": 0,
        "division_templates": list(_DEFAULT_DIVISION_TEMPLATES), "divisions": [],
        "provinces_owned": list(province_ids), "at_war_with": [],
        "armor_unlocked": False, "air_unlocked": False, "support_unlocked": False,
        "extra_policies": 0, "alliance_slots": 0, "extra_research_slots": 0,
        "production_speed_bonus": 0.0, "construction_speed_bonus": 0.0,
    }

def _init_game(players):
    global hoi4_state
    provinces = _generate_default_provinces()
    pids = list(provinces.keys())
    random.shuffle(pids)
    countries = {}
    per_player = len(pids) // max(len(players), 1)
    for i, p in enumerate(players):
        if i < len(players) - 1:
            my_pids = pids[i*per_player:(i+1)*per_player]
        else:
            my_pids = pids[i*per_player:]
        cid = "c{}".format(i+1)
        countries[cid] = _create_country(p["name"], p["owner"], p.get("color","#5865f2"), my_pids)
        for pid in my_pids: provinces[pid]["owner"] = cid
        for j in range(3):
            countries[cid]["divisions"].append({
                "id": "{}_d{}".format(cid,j+1), "template_id": "tpl_inf",
                "province": my_pids[j % len(my_pids)], "strength": 1.0, "org": 60,
            })
    hoi4_state = {
        "game_active": True, "tick": 0, "last_tick_iso": _now_gmt8_iso(),
        "countries": countries, "provinces": provinces, "wars": {},
        "focus_tree": _DEFAULT_FOCUS_TREE, "tech_tree": _DEFAULT_TECH_TREE,
        "log": ["[{}] 遊戲開始 — {} 個國家參戰".format(_now_gmt8_str(), len(countries))],
    }
    _save_hoi4_state()

# ════════════════════════════════════════════════════════════════════════════
# Tick 結算
# ════════════════════════════════════════════════════════════════════════════

def _process_tick():
    if not hoi4_state.get("game_active"): return
    hoi4_state["tick"] += 1
    tick = hoi4_state["tick"]
    log_entries = []
    for cid, c in hoi4_state["countries"].items():
        # 政治點數
        c["political_power"] = round(c.get("political_power",0) + c.get("political_power_per_tick",1.0), 1)
        # 建設進度
        speed_bonus = 1.0 + c.get("construction_speed_bonus",0.0)
        for item in c.get("construction_queue",[]):
            item["progress"] += 10 * speed_bonus
            if item["progress"] >= item["cost"]:
                t = item["type"]
                if t == "civilian_factory": c["civilian_factories"] = c.get("civilian_factories",0) + 1
                elif t == "military_factory": c["military_factories"] = c.get("military_factories",0) + 1
                elif t == "fortification":
                    prov = hoi4_state["provinces"].get(item["province"])
                    if prov: prov["fortifications"] = min(prov.get("fortifications",0)+1, 5)
                elif t == "infrastructure":
                    prov = hoi4_state["provinces"].get(item["province"])
                    if prov: prov["infrastructure"] = min(prov.get("infrastructure",0)+1, 5)
                log_entries.append("{} 完成建設：{}".format(c["name"], t))
        c["construction_queue"] = [q for q in c["construction_queue"] if q["progress"] < q["cost"]]
        # 生產進度
        prod_speed = 1.0 + c.get("production_speed_bonus",0.0)
        for item in c.get("production_queue",[]):
            factories = item.get("factories",1)
            item["progress"] += 10 * factories * prod_speed
            if item["progress"] >= item["cost"]:
                tpl_id = item["template_id"]
                my_pids = c.get("provinces_owned",[])
                deploy_prov = my_pids[0] if my_pids else None
                if deploy_prov:
                    c["divisions"].append({
                        "id": "{}_d{}".format(cid, len(c["divisions"])+1),
                        "template_id": tpl_id, "province": deploy_prov,
                        "strength": 1.0, "org": 60,
                    })
                    log_entries.append("{} 新建師級單位（{}）".format(c["name"], tpl_id))
        c["production_queue"] = [q for q in c["production_queue"] if q["progress"] < q["cost"]]
        # 研究進度
        research_bonus = 1.0 + c.get("research_speed_bonus",0.0)
        for r in c.get("research",[]):
            r["progress"] += 10 * research_bonus
            if r["progress"] >= _get_tech_cost(r["category"], r["tech_id"]):
                _apply_tech_effects(cid, r["category"], r["tech_id"])
                log_entries.append("{} 完成研究：{}".format(c["name"], _get_tech_name(r["category"], r["tech_id"])))
        c["research"] = [r for r in c["research"] if r["progress"] < _get_tech_cost(r["category"], r["tech_id"])]
        # 國策進度
        if c.get("current_focus"):
            c["focus_progress"] = c.get("focus_progress",0) + 10
            focus = _get_focus(c["current_focus"])
            if focus and c["focus_progress"] >= focus["cost"]:
                _apply_focus_effects(cid, focus)
                c["completed_focuses"].append(focus["id"])
                c["current_focus"] = None
                c["focus_progress"] = 0
                log_entries.append("{} 完成國策：{}".format(c["name"], focus["name"]))
        # 資源產出
        for pid in c.get("provinces_owned",[]):
            prov = hoi4_state["provinces"].get(pid)
            if prov and prov.get("resources"):
                for res, amt in prov["resources"].items():
                    c["resources"][res] = c["resources"].get(res,0) + amt * 0.1
        # 兵力回復
        for div in c.get("divisions",[]):
            if div["strength"] < 1.0: div["strength"] = min(1.0, div["strength"] + 0.02)
            if div["org"] < 60: div["org"] = min(60, div["org"] + 2)
    # 戰爭結算
    for war_id, war in list(hoi4_state.get("wars",{}).items()):
        _resolve_war_tick(war_id, war, log_entries)
    # 日誌
    if log_entries:
        hoi4_state["log"].extend(["[Day {}] {}".format(tick, e) for e in log_entries])
        hoi4_state["log"] = hoi4_state["log"][-50:]
    hoi4_state["last_tick_iso"] = _now_gmt8_iso()
    _save_hoi4_state()

def _get_focus(focus_id):
    for f in hoi4_state.get("focus_tree", _DEFAULT_FOCUS_TREE):
        if f["id"] == focus_id: return f
    return None

def _get_tech_cost(category, tech_id):
    tree = hoi4_state.get("tech_tree", _DEFAULT_TECH_TREE)
    for t in tree.get(category, []):
        if t["id"] == tech_id: return t.get("cost", 100)
    return 100

def _get_tech_name(category, tech_id):
    tree = hoi4_state.get("tech_tree", _DEFAULT_TECH_TREE)
    for t in tree.get(category, []):
        if t["id"] == tech_id: return t.get("name", tech_id)
    return tech_id

def _apply_tech_effects(cid, category, tech_id):
    c = hoi4_state["countries"].get(cid)
    if not c: return
    tree = hoi4_state.get("tech_tree", _DEFAULT_TECH_TREE)
    tech = None
    for t in tree.get(category, []):
        if t["id"] == tech_id: tech = t; break
    if not tech: return
    for key, val in tech.get("effects", {}).items():
        if key == "construction_speed": c["construction_speed_bonus"] = c.get("construction_speed_bonus",0) + val
        elif key == "production_speed": c["production_speed_bonus"] = c.get("production_speed_bonus",0) + val
        elif key == "research_speed_bonus": c["research_speed_bonus"] = c.get("research_speed_bonus",0) + val
        elif key == "support_unlocked": c["support_unlocked"] = True

def _apply_focus_effects(cid, focus):
    c = hoi4_state["countries"].get(cid)
    if not c: return
    for key, val in focus.get("effects", {}).items():
        if key == "civilian_factories": c["civilian_factories"] = c.get("civilian_factories",0) + val
        elif key == "military_factories": c["military_factories"] = c.get("military_factories",0) + val
        elif key == "manpower": c["manpower"] = c.get("manpower",0) + val
        elif key == "research_slots": c["research_slots"] = c.get("research_slots",3) + val
        elif key == "research_speed_bonus": c["research_speed_bonus"] = c.get("research_speed_bonus",0) + val
        elif key == "political_power_per_tick": c["political_power_per_tick"] = c.get("political_power_per_tick",1.0) + val
        elif key == "stability": c["stability"] = min(1.0, c.get("stability",0.75) + val)
        elif key == "armor_unlocked": c["armor_unlocked"] = True
        elif key == "air_unlocked": c["air_unlocked"] = True
        elif key == "extra_policies": c["extra_policies"] = c.get("extra_policies",0) + val
        elif key == "alliance_slots": c["alliance_slots"] = c.get("alliance_slots",0) + val

def _resolve_war_tick(war_id, war, log_entries):
    attacker_id = war.get("attacker")
    defender_id = war.get("defender")
    if not attacker_id or not defender_id: return
    attacker = hoi4_state["countries"].get(attacker_id)
    defender = hoi4_state["countries"].get(defender_id)
    if not attacker or not defender: return
    contested = war.get("contested_provinces", [])
    if not contested: return
    for pid in list(contested):
        prov = hoi4_state["provinces"].get(pid)
        if not prov: continue
        atk_divs = [d for d in attacker["divisions"] if d["province"] == pid]
        def_divs = [d for d in defender["divisions"] if d["province"] == pid]
        if not atk_divs: continue
        atk_power = sum(d["strength"] * _get_div_attack(d, attacker) for d in atk_divs)
        terrain_def_bonus = {"mountains": 2.0, "forest": 1.5, "urban": 1.8, "plains": 1.0, "desert": 1.0}
        terrain_mult = terrain_def_bonus.get(prov.get("type","plains"), 1.0)
        fort_bonus = 1.0 + prov.get("fortifications",0) * 0.3
        def_power = sum(d["strength"] * _get_div_defense(d, defender) * terrain_mult * fort_bonus for d in def_divs) if def_divs else 5
        if atk_power > def_power * 0.8:
            damage_ratio = min(1.0, atk_power / max(def_power, 1))
            for d in def_divs:
                d["strength"] = max(0, d["strength"] - 0.15 * damage_ratio)
                d["org"] = max(0, d["org"] - 10 * damage_ratio)
            for d in atk_divs:
                d["strength"] = max(0, d["strength"] - 0.08)
                d["org"] = max(0, d["org"] - 5)
            if not def_divs or all(d["strength"] < 0.1 for d in def_divs):
                prov["owner"] = attacker_id
                if pid in defender["provinces_owned"]: defender["provinces_owned"].remove(pid)
                if pid not in attacker["provinces_owned"]: attacker["provinces_owned"].append(pid)
                log_entries.append("{} 佔領了 {}！".format(attacker["name"], prov["name"]))
                contested.remove(pid)
            else:
                log_entries.append("{} 在 {} 擊退守軍".format(attacker["name"], prov["name"]))
        else:
            for d in atk_divs:
                d["strength"] = max(0, d["strength"] - 0.12)
                d["org"] = max(0, d["org"] - 8)
            for d in def_divs:
                d["strength"] = max(0, d["strength"] - 0.05)
                d["org"] = max(0, d["org"] - 3)
            log_entries.append("{} 在 {} 擊退進攻".format(defender["name"], prov["name"]))

def _get_div_attack(div, country):
    tpl = next((t for t in country.get("division_templates",[]) if t["id"] == div["template_id"]), None)
    return tpl["stats"].get("attack", 5) if tpl else 5

def _get_div_defense(div, country):
    tpl = next((t for t in country.get("division_templates",[]) if t["id"] == div["template_id"]), None)
    return tpl["stats"].get("defense", 5) if tpl else 5

# ════════════════════════════════════════════════════════════════════════════
# Discord 持久面板
# ════════════════════════════════════════════════════════════════════════════

def _build_hoi4_embed():
    if not hoi4_state.get("game_active"):
        return discord.Embed(title=HOI4_PANEL_TITLE_MARKER,
            description="遊戲尚未開始\n\n使用 /hoi4 start 開始一局新遊戲\n使用 /hoi4 join 加入遊戲",
            color=discord.Color.dark_gold())
    countries = hoi4_state.get("countries", {})
    tick = hoi4_state.get("tick", 0)
    embed = discord.Embed(title=HOI4_PANEL_TITLE_MARKER,
        description="第 {} 天 | 最後結算：{}".format(tick, _fmt_tick_time()),
        color=discord.Color.dark_gold())
    for cid, c in list(countries.items())[:5]:
        total_divs = len(c.get("divisions",[]))
        avg_str = sum(d["strength"] for d in c.get("divisions",[])) / max(total_divs,1)
        focus_name = "無"
        focus_bar = ""
        if c.get("current_focus"):
            f = _get_focus(c["current_focus"])
            if f:
                focus_name = f["name"]
                pct = min(100, int(c.get("focus_progress",0) / f["cost"] * 100))
                filled = pct // 10
                focus_bar = " `{}{}` {}%".format("█"*filled, "░"*(10-filled), pct)
        research_lines = []
        for r in c.get("research",[]):
            cost = _get_tech_cost(r["category"], r["tech_id"])
            pct = min(100, int(r.get("progress",0) / cost * 100))
            research_lines.append("  {} {}%".format(_get_tech_name(r["category"], r["tech_id"]), pct))
        research_str = "\n".join(research_lines) if research_lines else "  無研究進行中"
        build_lines = []
        for q in c.get("construction_queue",[]):
            pct = min(100, int(q["progress"] / q["cost"] * 100))
            build_lines.append("  {} {}%".format(q["type"], pct))
        build_str = "\n".join(build_lines) if build_lines else "  無建設進行中"
        prod_lines = []
        for q in c.get("production_queue",[]):
            pct = min(100, int(q["progress"] / q["cost"] * 100))
            prod_lines.append("  {} ({}廠) {}%".format(q["template_id"], q.get("factories",1), pct))
        prod_str = "\n".join(prod_lines) if prod_lines else "  無生產進行中"
        value = (
            "政治：{:.0f} PP | 穩定度：{:.0f}%\n"
            "民工：{} | 軍工：{}\n"
            "兵員：{} | 師級單位：{}（平均戰力 {:.0f}%）\n"
            "國策：{}{}\n"
            "研究：\n{}\n"
            "建設：\n{}\n"
            "生產：\n{}\n"
            "領土：{} 省份"
        ).format(c.get("political_power",0), c.get("stability",0)*100,
            c.get("civilian_factories",0), c.get("military_factories",0),
            c.get("manpower",0), total_divs, avg_str*100,
            focus_name, focus_bar, research_str, build_str, prod_str,
            len(c.get("provinces_owned",[])))
        if c.get("at_war_with"):
            war_names = [hoi4_state["countries"].get(w,{}).get("name",w) for w in c["at_war_with"]]
            value += "\n戰爭中：{}".format(", ".join(war_names))
        embed.add_field(name="{} {}".format(c.get("color",""), c["name"]), value=value, inline=False)
    log = hoi4_state.get("log",[])
    if log:
        embed.add_field(name="最近戰報", value="\n".join(log[-5:]), inline=False)
    embed.set_footer(text="Tick 每 {} 分鐘 | /hoi4 start | /hoi4 join | /hoi4 focus | /hoi4 research | /hoi4 build | /hoi4 war".format(TICK_INTERVAL_SECONDS//60))
    return embed

def _fmt_tick_time():
    iso = hoi4_state.get("last_tick_iso")
    if not iso: return "—"
    try: return _dt.datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except Exception: return iso[:16]

def _get_hoi4_panel_channel():
    ch_id = hoi4_panel.get("channel_id")
    if not ch_id: return None
    for guild in bot.guilds:
        ch = guild.get_channel(int(ch_id))
        if ch: return ch
    return None

async def setup_hoi4_panel():
    channel = _get_hoi4_panel_channel()
    if not channel: return None
    old_msg_id = hoi4_panel.get("message_id")
    if old_msg_id:
        try:
            old_msg = await channel.fetch_message(int(old_msg_id))
            await old_msg.delete()
        except discord.NotFound: pass
        except Exception as e: print("HOI4 刪舊面板失敗: {}".format(e))
    try:
        async for msg in channel.history(limit=20):
            if msg.author.id == bot.user.id and msg.embeds:
                if msg.embeds[0].title and HOI4_PANEL_TITLE_MARKER in msg.embeds[0].title:
                    try: await msg.delete()
                    except Exception: pass
    except Exception: pass
    try:
        view = HOI4PanelView()
        new_msg = await channel.send(embed=_build_hoi4_embed(), view=view)
        hoi4_panel["message_id"] = new_msg.id
        _save_hoi4_panel()
        print("HOI4 面板已發送至 #{}（ID: {}）".format(channel.name, new_msg.id))
        return new_msg
    except Exception as e:
        print("HOI4 面板發送失敗: {}".format(e))
        return None

async def refresh_hoi4_panel():
    channel = _get_hoi4_panel_channel()
    if not channel: return
    msg_id = hoi4_panel.get("message_id")
    if not msg_id:
        await setup_hoi4_panel()
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=_build_hoi4_embed(), view=HOI4PanelView())
    except discord.NotFound:
        await setup_hoi4_panel()
    except Exception as e:
        print("HOI4 面板刷新失敗: {}".format(e))

async def hoi4_panel_loop():
    await bot.wait_until_ready()
    await asyncio.sleep(5)
    _load_hoi4_state()
    try: await setup_hoi4_panel()
    except Exception as e: print("HOI4 面板初始化失敗: {}".format(e))
    last_tick = time.time()
    while True:
        await asyncio.sleep(PANEL_REFRESH_SECONDS)
        now = time.time()
        try: await refresh_hoi4_panel()
        except Exception: pass
        if hoi4_state.get("game_active") and (now - last_tick) >= TICK_INTERVAL_SECONDS:
            try:
                _process_tick()
                last_tick = time.time()
                await refresh_hoi4_panel()
                print("HOI4 tick {} 結算完成".format(hoi4_state.get("tick",0)))
            except Exception as e:
                print("HOI4 tick 結算失敗: {}".format(e))

# ════════════════════════════════════════════════════════════════════════════
# Discord 按鈕 View
# ════════════════════════════════════════════════════════════════════════════

class HOI4PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="國策", style=discord.ButtonStyle.primary, custom_id="hoi4_btn_focus")
    async def btn_focus(self, interaction, button):
        await _hoi4_show_focus_menu(interaction)

    @discord.ui.button(label="研究", style=discord.ButtonStyle.primary, custom_id="hoi4_btn_research")
    async def btn_research(self, interaction, button):
        await _hoi4_show_research_menu(interaction)

    @discord.ui.button(label="建設", style=discord.ButtonStyle.secondary, custom_id="hoi4_btn_build")
    async def btn_build(self, interaction, button):
        await _hoi4_show_build_menu(interaction)

    @discord.ui.button(label="生產", style=discord.ButtonStyle.secondary, custom_id="hoi4_btn_prod")
    async def btn_prod(self, interaction, button):
        await _hoi4_show_production_menu(interaction)

    @discord.ui.button(label="戰爭", style=discord.ButtonStyle.danger, custom_id="hoi4_btn_war")
    async def btn_war(self, interaction, button):
        await _hoi4_show_war_menu(interaction)

    @discord.ui.button(label="地圖", style=discord.ButtonStyle.success, custom_id="hoi4_btn_map")
    async def btn_map(self, interaction, button):
        await _hoi4_show_map_link(interaction)

async def _get_player_country(interaction):
    uid = str(interaction.user.id)
    for cid, c in hoi4_state.get("countries",{}).items():
        if c.get("owner") == uid:
            return cid, c, None
    return None, None, "你還沒有加入遊戲，請先用 /hoi4 join 加入。"

async def _hoi4_show_focus_menu(interaction):
    cid, c, err = await _get_player_country(interaction)
    if err:
        await interaction.response.send_message(err, ephemeral=True); return
    available = []
    completed = set(c.get("completed_focuses",[]))
    for f in hoi4_state.get("focus_tree", _DEFAULT_FOCUS_TREE):
        if f["id"] in completed: continue
        if all(p in completed for p in f.get("prereq",[])): available.append(f)
    if c.get("current_focus"):
        cur = _get_focus(c["current_focus"])
        cur_name = cur["name"] if cur else c["current_focus"]
        pct = int(c.get("focus_progress",0) / (cur["cost"] if cur else 70) * 100) if cur else 0
        desc = "當前國策：**{}**（{}%）\n\n請先完成當前國策再選新的。".format(cur_name, pct)
    else:
        lines = []
        for i, f in enumerate(available):
            lines.append("[{}] {} — {} (需 {} PP)".format(i, f["name"], f.get("desc",""), f["cost"]))
        desc = "你的政治點數：{}\n\n{}".format(c.get("political_power",0), "\n".join(lines) if lines else "沒有可選的國策")
        if available: desc += "\n\n用 /hoi4 focus index:數字 選擇國策"
    embed = discord.Embed(title="選擇國策", description=desc, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def _hoi4_show_research_menu(interaction):
    cid, c, err = await _get_player_country(interaction)
    if err:
        await interaction.response.send_message(err, ephemeral=True); return
    slots_used = len(c.get("research",[]))
    slots_total = c.get("research_slots",3)
    lines = ["研究槽：{}/{}\n".format(slots_used, slots_total)]
    if slots_used < slots_total:
        lines.append("可研究科技：")
        tree = hoi4_state.get("tech_tree", _DEFAULT_TECH_TREE)
        in_progress = set(r.get("tech_id") for r in c.get("research",[]))
        idx = 0
        for cat, techs in tree.items():
            for t in techs:
                if t["id"] in in_progress: continue
                lines.append("[{}] [{}] {} (需 {} 研究點)".format(idx, cat, t["name"], t["cost"]))
                idx += 1
        lines.append("\n用 /hoi4 research category:類別 index:數字 開始研究")
    else:
        lines.append("所有研究槽已滿")
    embed = discord.Embed(title="研究所", description="\n".join(lines), color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def _hoi4_show_build_menu(interaction):
    cid, c, err = await _get_player_country(interaction)
    if err:
        await interaction.response.send_message(err, ephemeral=True); return
    lines = [
        "民工：{} | 軍工：{}".format(c.get("civilian_factories",0), c.get("military_factories",0)),
        "你的省份：{}{}".format(", ".join(c.get("provinces_owned",[])[:5]), "..." if len(c.get("provinces_owned",[]))>5 else ""),
        "",
        "可建設項目：",
        "[0] 民用工廠 (需 100 建設點)",
        "[1] 軍用工廠 (需 100 建設點)",
        "[2] 要塞 (需 80 建設點, 防禦+1)",
        "[3] 基礎建設 (需 60 建設點, 基建+1)",
        "",
        "用 /hoi4 build type:類型 province:省份ID 開始建設",
    ]
    embed = discord.Embed(title="建設部", description="\n".join(lines), color=discord.Color.orange())
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def _hoi4_show_production_menu(interaction):
    cid, c, err = await _get_player_country(interaction)
    if err:
        await interaction.response.send_message(err, ephemeral=True); return
    lines = ["軍工廠：{}\n".format(c.get("military_factories",0))]
    lines.append("可生產的師級模板：")
    for i, tpl in enumerate(c.get("division_templates",[])):
        stats = tpl.get("stats",{})
        lines.append("[{}] {} — 攻{} 防{} (需 {} 生產點)".format(i, tpl["name"], stats.get("attack","?"), stats.get("defense","?"), tpl.get("cost",100)))
    lines.append("\n用 /hoi4 produce template:模板ID factories:軍工數 開始生產")
    lines.append("\n目前生產佇列：{} 項".format(len(c.get("production_queue",[]))))
    embed = discord.Embed(title="生產部", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def _hoi4_show_war_menu(interaction):
    cid, c, err = await _get_player_country(interaction)
    if err:
        await interaction.response.send_message(err, ephemeral=True); return
    lines = []
    if c.get("at_war_with"):
        for wid in c["at_war_with"]:
            enemy = hoi4_state["countries"].get(wid,{})
            lines.append("vs {}".format(enemy.get("name",wid)))
    else:
        other = ["[{}] {}".format(i, oc["name"]) for i,(ocid,oc) in enumerate(hoi4_state["countries"].items()) if ocid != cid]
        lines.append("目前沒有戰爭。\n\n可宣戰的國家：")
        lines.extend(other)
        lines.append("\n用 /hoi4 war target:國家編號 宣戰")
    embed = discord.Embed(title="軍事指揮", description="\n".join(lines), color=discord.Color.red())
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def _hoi4_show_map_link(interaction):
    embed = discord.Embed(title="戰略地圖",
        description="請到 Dashboard 網頁面板查看互動式地圖、前線微操和師級編制設計。\n\n在 Dashboard 點擊上方導航列的「HOI4」分頁即可。",
        color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ════════════════════════════════════════════════════════════════════════════
# Slash 指令群組
# ════════════════════════════════════════════════════════════════════════════

class HOI4Group(app_commands.Group):
    def __init__(self):
        super().__init__(name="hoi4", description="鋼鐵雄心4 文字版遊戲")

    @app_commands.command(name="start", description="開始一局新遊戲（機器人擁有者限定）")
    @app_commands.describe(player2="第二位玩家", player3="第三位玩家")
    async def hoi4_start(self, interaction, player2=None, player3=None):
        if not is_owner(interaction):
            await interaction.response.send_message("此指令僅限機器人擁有者使用。", ephemeral=True); return
        if hoi4_state.get("game_active"):
            await interaction.response.send_message("遊戲已在進行中，請先用 /hoi4 end 結束。", ephemeral=True); return
        players = [{"name": interaction.user.display_name + "的國家", "owner": interaction.user.id, "color": "#5865f2"}]
        if player2: players.append({"name": player2.display_name + "的國家", "owner": player2.id, "color": "#ed4245"})
        if player3: players.append({"name": player3.display_name + "的國家", "owner": player3.id, "color": "#57f287"})
        _init_game(players)
        await refresh_hoi4_panel()
        await interaction.response.send_message("遊戲已開始！參戰國家：{}\n每 30 分鐘一個 tick。".format(", ".join(p["name"] for p in players)))

    @app_commands.command(name="join", description="加入現有遊戲")
    @app_commands.describe(name="你的國家名稱")
    async def hoi4_join(self, interaction, name: str):
        if not hoi4_state.get("game_active"):
            await interaction.response.send_message("遊戲尚未開始。", ephemeral=True); return
        uid = str(interaction.user.id)
        for cid, c in hoi4_state.get("countries",{}).items():
            if c.get("owner") == uid:
                await interaction.response.send_message("你已經在遊戲中（{}）。".format(c["name"]), ephemeral=True); return
        free_pids = [pid for pid, p in hoi4_state.get("provinces",{}).items() if p.get("owner") is None]
        if not free_pids:
            await interaction.response.send_message("沒有可分配的省份了。", ephemeral=True); return
        my_pids = free_pids[:max(1, len(free_pids)//4)]
        colors = ["#fee75c","#eb459e","#3ba55d","#7289da","#f47fff"]
        new_cid = "c{}".format(len(hoi4_state["countries"])+1)
        hoi4_state["countries"][new_cid] = _create_country(name, uid, random.choice(colors), my_pids)
        for pid in my_pids: hoi4_state["provinces"][pid]["owner"] = new_cid
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("你已加入遊戲！國名：{}，獲得 {} 個省份。".format(name, len(my_pids)))

    @app_commands.command(name="end", description="結束遊戲（機器人擁有者限定）")
    async def hoi4_end(self, interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("此指令僅限機器人擁有者使用。", ephemeral=True); return
        hoi4_state["game_active"] = False
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("遊戲已結束。", ephemeral=True)

    @app_commands.command(name="set_channel", description="設定 HOI4 面板頻道（機器人擁有者限定）")
    @app_commands.describe(channel="面板要顯示在哪個頻道")
    async def hoi4_set_channel(self, interaction, channel):
        if not is_owner(interaction):
            await interaction.response.send_message("此指令僅限機器人擁有者使用。", ephemeral=True); return
        hoi4_panel["channel_id"] = str(channel.id)
        _save_hoi4_panel()
        await setup_hoi4_panel()
        await interaction.response.send_message("HOI4 面板已設定到 {}".format(channel.mention), ephemeral=True)

    @app_commands.command(name="focus", description="選擇國策")
    @app_commands.describe(index="從面板按鈕查到的國策編號")
    async def hoi4_focus(self, interaction, index: int):
        cid, c, err = await _get_player_country(interaction)
        if err: await interaction.response.send_message(err, ephemeral=True); return
        if c.get("current_focus"):
            await interaction.response.send_message("你已有進行中的國策，請等完成後再選。", ephemeral=True); return
        completed = set(c.get("completed_focuses",[]))
        available = [f for f in hoi4_state.get("focus_tree", _DEFAULT_FOCUS_TREE) if f["id"] not in completed and all(p in completed for p in f.get("prereq",[]))]
        if index < 0 or index >= len(available):
            await interaction.response.send_message("無效的國策編號（0~{}）。".format(len(available)-1), ephemeral=True); return
        chosen = available[index]
        c["current_focus"] = chosen["id"]
        c["focus_progress"] = 0
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("已開始國策：**{}**\n{}".format(chosen["name"], chosen.get("desc","")), ephemeral=True)

    @app_commands.command(name="research", description="開始研究科技")
    @app_commands.describe(category="研究領域", index="科技編號")
    @app_commands.choices(category=[
        app_commands.Choice(name="工業", value="industry"),
        app_commands.Choice(name="陸軍", value="army"),
        app_commands.Choice(name="空軍", value="air"),
        app_commands.Choice(name="海軍", value="naval"),
    ])
    async def hoi4_research(self, interaction, category, index: int):
        cid, c, err = await _get_player_country(interaction)
        if err: await interaction.response.send_message(err, ephemeral=True); return
        slots_used = len(c.get("research",[]))
        if slots_used >= c.get("research_slots",3):
            await interaction.response.send_message("研究槽已滿。", ephemeral=True); return
        cat = category.value
        tree = hoi4_state.get("tech_tree", _DEFAULT_TECH_TREE)
        techs = tree.get(cat, [])
        in_progress = set(r["tech_id"] for r in c.get("research",[]))
        available = [t for t in techs if t["id"] not in in_progress]
        if index < 0 or index >= len(available):
            await interaction.response.send_message("無效的科技編號。", ephemeral=True); return
        chosen = available[index]
        c["research"].append({"tech_id": chosen["id"], "category": cat, "progress": 0})
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("已開始研究：**{}**（{}）".format(chosen["name"], cat), ephemeral=True)

    @app_commands.command(name="build", description="開始建設")
    @app_commands.describe(type="建設類型", province="省份ID (如 p01)")
    @app_commands.choices(type=[
        app_commands.Choice(name="民用工廠", value="civilian_factory"),
        app_commands.Choice(name="軍用工廠", value="military_factory"),
        app_commands.Choice(name="要塞", value="fortification"),
        app_commands.Choice(name="基礎建設", value="infrastructure"),
    ])
    async def hoi4_build(self, interaction, type, province: str):
        cid, c, err = await _get_player_country(interaction)
        if err: await interaction.response.send_message(err, ephemeral=True); return
        if province not in c.get("provinces_owned",[]):
            await interaction.response.send_message("{} 不是你的省份。".format(province), ephemeral=True); return
        costs = {"civilian_factory":100,"military_factory":100,"fortification":80,"infrastructure":60}
        c["construction_queue"].append({"type": type.value, "province": province, "progress": 0, "cost": costs.get(type.value,100)})
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("已開始建設：{} @ {}".format(type.name, province), ephemeral=True)

    @app_commands.command(name="produce", description="開始生產師級單位")
    @app_commands.describe(template="模板ID", factories="分配軍工數")
    async def hoi4_produce(self, interaction, template: str, factories: int = 1):
        cid, c, err = await _get_player_country(interaction)
        if err: await interaction.response.send_message(err, ephemeral=True); return
        tpl = next((t for t in c.get("division_templates",[]) if t["id"] == template), None)
        if not tpl:
            available = ", ".join(t["id"] for t in c.get("division_templates",[]))
            await interaction.response.send_message("找不到模板 {}。可用：{}".format(template, available), ephemeral=True); return
        if factories < 1 or factories > c.get("military_factories",0):
            await interaction.response.send_message("軍工數無效（你有 {} 個軍工）。".format(c.get("military_factories",0)), ephemeral=True); return
        c["production_queue"].append({"template_id": template, "factories": factories, "progress": 0, "cost": tpl.get("cost",100)})
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("已開始生產：{}（{} 個軍工）".format(tpl["name"], factories), ephemeral=True)

    @app_commands.command(name="war", description="向其他國家宣戰")
    @app_commands.describe(target="目標國家編號")
    async def hoi4_war(self, interaction, target: int):
        cid, c, err = await _get_player_country(interaction)
        if err: await interaction.response.send_message(err, ephemeral=True); return
        other_ids = [ocid for ocid in hoi4_state["countries"] if ocid != cid]
        if target < 0 or target >= len(other_ids):
            await interaction.response.send_message("無效的目標編號（0~{}）。".format(len(other_ids)-1), ephemeral=True); return
        target_id = other_ids[target]
        target_c = hoi4_state["countries"][target_id]
        if target_id in c.get("at_war_with",[]):
            await interaction.response.send_message("已經在跟 {} 戰爭了。".format(target_c["name"]), ephemeral=True); return
        my_pids = set(c.get("provinces_owned",[]))
        their_pids = set(target_c.get("provinces_owned",[]))
        contested = []
        for pid in my_pids:
            prov = hoi4_state["provinces"].get(pid)
            if prov:
                px, py = prov["grid_x"], prov["grid_y"]
                for tpid in their_pids:
                    tprov = hoi4_state["provinces"].get(tpid)
                    if tprov and abs(tprov["grid_x"]-px) + abs(tprov["grid_y"]-py) == 1:
                        contested.append(pid); break
        war_id = "war_{}_{}".format(cid, target_id)
        hoi4_state["wars"][war_id] = {"attacker": cid, "defender": target_id, "contested_provinces": contested, "start_tick": hoi4_state.get("tick",0)}
        c.setdefault("at_war_with",[]).append(target_id)
        target_c.setdefault("at_war_with",[]).append(cid)
        hoi4_state["log"].append("[Day {}] {} 向 {} 宣戰！".format(hoi4_state.get("tick",0), c["name"], target_c["name"]))
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("{} 向 {} 宣戰！爭議省份：{}".format(c["name"], target_c["name"], ", ".join(contested) if contested else "無交界"))

    @app_commands.command(name="status", description="查看你自己的國家詳情")
    async def hoi4_status(self, interaction):
        cid, c, err = await _get_player_country(interaction)
        if err: await interaction.response.send_message(err, ephemeral=True); return
        embed = discord.Embed(title="{} — 國家詳情".format(c["name"]), color=discord.Color.gold())
        embed.add_field(name="政治", value="PP: {:.0f}\n穩定度: {:.0f}%".format(c.get("political_power",0), c.get("stability",0)*100), inline=True)
        embed.add_field(name="工業", value="民工: {}\n軍工: {}".format(c.get("civilian_factories",0), c.get("military_factories",0)), inline=True)
        embed.add_field(name="軍事", value="兵員: {}\n師數: {}".format(c.get("manpower",0), len(c.get("divisions",[]))), inline=True)
        embed.add_field(name="研究槽", value="{}/{}".format(len(c.get("research",[])), c.get("research_slots",3)), inline=True)
        embed.add_field(name="領土", value="{} 省份".format(len(c.get("provinces_owned",[]))), inline=True)
        f = _get_focus(c["current_focus"]) if c.get("current_focus") else None
        embed.add_field(name="國策", value=f["name"] if f else "無", inline=True)
        res_str = "\n".join("{}: {:.0f}".format(k,v) for k,v in c.get("resources",{}).items())
        embed.add_field(name="資源", value=res_str or "無", inline=True)
        div_lines = []
        for d in c.get("divisions",[])[:10]:
            tpl = next((t for t in c.get("division_templates",[]) if t["id"]==d["template_id"]), None)
            tpl_name = tpl["name"] if tpl else d["template_id"]
            div_lines.append("  {} @ {} — 戰力{:.0f}% 組織度{}".format(tpl_name, d["province"], d["strength"]*100, d["org"]))
        embed.add_field(name="部隊", value="\n".join(div_lines) or "無", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="move", description="移動部隊到相鄰省份")
    @app_commands.describe(division="部隊編號（從 /hoi4 status 查看）", province="目標省份ID")
    async def hoi4_move(self, interaction, division: int, province: str):
        cid, c, err = await _get_player_country(interaction)
        if err: await interaction.response.send_message(err, ephemeral=True); return
        divs = c.get("divisions",[])
        if division < 0 or division >= len(divs):
            await interaction.response.send_message("無效的部隊編號（0~{}）。".format(len(divs)-1), ephemeral=True); return
        div = divs[division]
        cur = hoi4_state["provinces"].get(div["province"])
        tgt = hoi4_state["provinces"].get(province)
        if not tgt:
            await interaction.response.send_message("找不到省份 {}。".format(province), ephemeral=True); return
        if cur:
            dist = abs(cur["grid_x"]-tgt["grid_x"]) + abs(cur["grid_y"]-tgt["grid_y"])
            if dist != 1:
                await interaction.response.send_message("{} 不相鄰目前位置（距離 {}）。".format(province, dist), ephemeral=True); return
        if tgt["owner"] and tgt["owner"] != cid and tgt["owner"] not in c.get("at_war_with",[]):
            await interaction.response.send_message("{} 屬於非交戰國，不能移入。".format(province), ephemeral=True); return
        div["province"] = province
        div["org"] = max(0, div["org"] - 10)
        _save_hoi4_state()
        await refresh_hoi4_panel()
        await interaction.response.send_message("部隊已移動到 {}（組織度 -10）。".format(province), ephemeral=True)

# ════════════════════════════════════════════════════════════════════════════
# API 端點（供 Dashboard 網頁面板使用）
# ════════════════════════════════════════════════════════════════════════════

async def api_hoi4_state(request):
    try: return web.json_response(hoi4_state)
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def api_hoi4_province(request):
    try:
        pid = request.match_info.get("pid")
        body = await request.json()
        prov = hoi4_state.get("provinces",{}).get(pid)
        if not prov: return web.json_response({"error":"省份不存在"}, status=404)
        for key in ("fortifications","infrastructure"):
            if key in body: prov[key] = body[key]
        _save_hoi4_state()
        return web.json_response({"ok":True, "province": prov})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_hoi4_move_division(request):
    try:
        body = await request.json()
        cid = body.get("country_id")
        div_idx = body.get("division_index")
        target_province = body.get("province")
        c = hoi4_state.get("countries",{}).get(cid)
        if not c: return web.json_response({"error":"國家不存在"}, status=404)
        divs = c.get("divisions",[])
        if div_idx is None or div_idx < 0 or div_idx >= len(divs):
            return web.json_response({"error":"無效的部隊編號"}, status=400)
        div = divs[div_idx]
        cur = hoi4_state["provinces"].get(div["province"])
        tgt = hoi4_state["provinces"].get(target_province)
        if not tgt: return web.json_response({"error":"目標省份不存在"}, status=400)
        if cur:
            dist = abs(cur["grid_x"]-tgt["grid_x"]) + abs(cur["grid_y"]-tgt["grid_y"])
            if dist != 1: return web.json_response({"error":"不相鄰（距離 {}）".format(dist)}, status=400)
        div["province"] = target_province
        div["org"] = max(0, div["org"] - 10)
        _save_hoi4_state()
        await refresh_hoi4_panel()
        return web.json_response({"ok":True, "division": div})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_hoi4_division_template(request):
    try:
        body = await request.json()
        cid = body.get("country_id")
        c = hoi4_state.get("countries",{}).get(cid)
        if not c: return web.json_response({"error":"國家不存在"}, status=404)
        tpl = body.get("template")
        if not tpl or not tpl.get("name"):
            return web.json_response({"error":"模板需要 name"}, status=400)
        battalion_stats = {
            "infantry": {"attack":3,"defense":4,"hp":10,"org":20},
            "cavalry": {"attack":2,"defense":1,"hp":7,"org":15,"speed":2},
            "artillery": {"attack":6,"defense":1,"hp":5,"org":10},
            "armor": {"attack":8,"defense":6,"hp":15,"org":15,"speed":1},
            "support_artillery": {"attack":2,"defense":0,"hp":2,"org":5},
            "engineer": {"defense":2,"hp":2,"org":5},
            "recon": {"speed":1,"hp":1,"org":3},
        }
        total = {"attack":0,"defense":0,"hp":0,"org":0}
        for b in tpl.get("battalions",[]):
            bs = battalion_stats.get(b,{})
            for k,v in bs.items():
                if k in total: total[k] += v
        for s in tpl.get("support",[]):
            bs = battalion_stats.get(s,{})
            for k,v in bs.items():
                if k in total: total[k] += v
        tpl["stats"] = total
        tpl["cost"] = max(50, len(tpl.get("battalions",[]))*30 + len(tpl.get("support",[]))*20)
        tpl["id"] = tpl.get("id", "tpl_custom_{}".format(len(c.get("division_templates",[]))))
        existing = next((t for t in c.get("division_templates",[]) if t["id"] == tpl["id"]), None)
        if existing: existing.update(tpl)
        else: c.setdefault("division_templates",[]).append(tpl)
        _save_hoi4_state()
        return web.json_response({"ok":True, "template": tpl})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_hoi4_set_focus(request):
    try:
        body = await request.json()
        cid = body.get("country_id")
        focus_id = body.get("focus_id")
        c = hoi4_state.get("countries",{}).get(cid)
        if not c: return web.json_response({"error":"國家不存在"}, status=404)
        if c.get("current_focus"): return web.json_response({"error":"已有進行中的國策"}, status=400)
        focus = _get_focus(focus_id)
        if not focus: return web.json_response({"error":"國策不存在"}, status=404)
        c["current_focus"] = focus_id
        c["focus_progress"] = 0
        _save_hoi4_state()
        await refresh_hoi4_panel()
        return web.json_response({"ok":True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_hoi4_research(request):
    try:
        body = await request.json()
        cid = body.get("country_id")
        cat = body.get("category")
        tech_id = body.get("tech_id")
        c = hoi4_state.get("countries",{}).get(cid)
        if not c: return web.json_response({"error":"國家不存在"}, status=404)
        if len(c.get("research",[])) >= c.get("research_slots",3):
            return web.json_response({"error":"研究槽已滿"}, status=400)
        c["research"].append({"tech_id": tech_id, "category": cat, "progress": 0})
        _save_hoi4_state()
        await refresh_hoi4_panel()
        return web.json_response({"ok":True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_hoi4_build(request):
    try:
        body = await request.json()
        cid = body.get("country_id")
        build_type = body.get("type")
        province = body.get("province")
        c = hoi4_state.get("countries",{}).get(cid)
        if not c: return web.json_response({"error":"國家不存在"}, status=404)
        if province not in c.get("provinces_owned",[]):
            return web.json_response({"error":"不是你的省份"}, status=400)
        costs = {"civilian_factory":100,"military_factory":100,"fortification":80,"infrastructure":60}
        c["construction_queue"].append({"type": build_type, "province": province, "progress": 0, "cost": costs.get(build_type,100)})
        _save_hoi4_state()
        await refresh_hoi4_panel()
        return web.json_response({"ok":True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_hoi4_produce(request):
    try:
        body = await request.json()
        cid = body.get("country_id")
        template_id = body.get("template_id")
        factories = body.get("factories",1)
        c = hoi4_state.get("countries",{}).get(cid)
        if not c: return web.json_response({"error":"國家不存在"}, status=404)
        tpl = next((t for t in c.get("division_templates",[]) if t["id"] == template_id), None)
        if not tpl: return web.json_response({"error":"模板不存在"}, status=404)
        c["production_queue"].append({"template_id": template_id, "factories": factories, "progress": 0, "cost": tpl.get("cost",100)})
        _save_hoi4_state()
        await refresh_hoi4_panel()
        return web.json_response({"ok":True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

# ════════════════════════════════════════════════════════════════════════════
# 註冊到主程式
# ════════════════════════════════════════════════════════════════════════════

_load_hoi4_state()

HOI4_API_ROUTES = [
    ("/api/game/hoi4/state", "GET", api_hoi4_state),
    ("/api/game/hoi4/provinces/{pid}", "PUT", api_hoi4_province),
    ("/api/game/hoi4/move-division", "POST", api_hoi4_move_division),
    ("/api/game/hoi4/division-template", "POST", api_hoi4_division_template),
    ("/api/game/hoi4/set-focus", "POST", api_hoi4_set_focus),
    ("/api/game/hoi4/research", "POST", api_hoi4_research),
    ("/api/game/hoi4/build", "POST", api_hoi4_build),
    ("/api/game/hoi4/produce", "POST", api_hoi4_produce),
]

try: bot.tree.add_command(HOI4Group())
except Exception as e: print("HOI4 指令群組註冊失敗: {}".format(e))

try: bot.add_view(HOI4PanelView())
except Exception as e: print("HOI4 面板 View 註冊失敗: {}".format(e))

# hoi4_panel_loop 由 setup_hook() 在 discord_borda_poll.py 中啟動
# （與 economy_panel_loop、siege_loop 同模式）
