# -*- coding: utf-8 -*-
"""
🍃 紫苏叶策略 V2.0 - 赛道情报层（sector_intel）
=============================================================
赛道壁垒 8 维里，6 维的「行业知识」（竞争格局、国产替代率、认证周期、
行业空间、政策、专利）来自赛道情报。本模块负责：

  1. 情报的存取：赛道数据库/intel/<赛道>.json（结构化，评分引擎直接读）
  2. 模板生成：给所有赛道生成空模板，缺的维度明确标 null + missing
  3. 从现有 md 迁移：把 赛道数据库/*.md 里人工研究的知识解析成 JSON
  4. 缺项报告：告诉你哪些赛道缺哪些维度（Pi 对话时按清单补）

工作方式：
  - 首次运行: python sector_intel.py --ensure            # 生成全部模板
  - 迁移已有: python sector_intel.py --from-md "赛道数据库/EDA工业软件.md" --sector "EDA/工业软件"
  - 查缺项:   python sector_intel.py --report
"""

import json
import os
import re
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INTEL_DIR = os.path.join(BASE_DIR, "赛道数据库", "intel")

# ============================================================
# 赛道别名映射：细分赛道名 → intel 文件名（不含.json）
# 支持关键词匹配，按顺序命中第一个
# ============================================================
SECTOR_ALIAS_MAP = [
    # EDA 相关
    ("eda", ["EDA", "工业软件", "良率", "全流程"]),
    # 科学仪器
    ("scientific_instruments", ["科学仪器", "分析仪器", "检测仪器"]),
    # 高温合金
    ("high_temp_alloy", ["高温合金", "航发材料"]),
    # 高端液压件
    ("high_end_hydraulic", ["液压件", "液压", "液压缸", "液压泵"]),
    # 半导体阀门
    ("semiconductor_valve", ["半导体阀门", "精密阀门", "半导体阀"]),
    # 光刻胶
    ("photoresist", ["光刻胶", "光刻材料"]),
    # 光学晶体
    ("optical_crystal", ["光学晶体", "非线性光学", "激光晶体"]),
    # 高端密封件
    ("high_end_seals", ["密封件", "机械密封", "干气密封"]),
    # 高端轴承
    ("high_end_bearings", ["高端轴承", "精密轴承", "轴承"]),
    # 电子特气
    ("electronic_gas", ["电子特气", "特种气体", "电子气体"]),
    # 溅射靶材
    ("sputtering_target", ["溅射靶材", "靶材"]),
    # 机器人核心部件
    ("robot_core", ["机器人", "空心杯", "丝杠", "谐波", "RV", "伺服"]),
    # 连接器
    ("connectors", ["连接器", "接插件"]),
    # 金属软磁粉芯（AI电感）
    ("metal_soft_magnetic", ["软磁", "粉芯", "电感材料", "磁粉"]),
    # 吸附分离材料
    ("adsorption_material", ["吸附", "分离材料", "提锂树脂"]),
    # MLCC(军工电子) - 2026-10-11 新增：鸿远电子 603267（原误命中钽电容情报）
    # 注意必须放在 tantalum_capacitor 之前，否则"军工电子"会被钽电容先匹配
    ("mlcc_military", ["MLCC", "瓷介电容", "高可靠电容"]),
    # 钽电容/军工电子
    ("tantalum_capacitor", ["钽电容", "军工电子", "军工元器件"]),
    # 电池精密模具（2026-09-25 新增：德新科技 603032，原走兜底分被低估）
    ("battery_precision_die", ["电池精密模具", "锂电模具", "裁切模具", "叠片模具",
                               "模切模具", "短切刀", "极片模具"]),
    # 航空刹车/起降系统 - 2026-10-11 新增：北摩高科 002985（情报文件已存在但未命中）
    ("aircraft_brake", ["航空刹车", "起落架", "刹车盘", "刹车"]),
    # 空港地面装备 - 2026-10-11 新增：威海广泰 002111（情报文件已存在但未命中）
    ("airport_ground_equipment", ["空港", "地面装备", "机场设备", "民航设备"]),
    # TPMS/空气悬架 - 2026-10-11 新增：保隆科技 603197（情报文件已存在但未命中）
    # 保隆赛道写为「汽车电子/智能传感器」，主业即TPMS+空气悬架，故含两个泛化词兜底
    ("tpms_suspension", ["TPMS", "空气悬架", "汽车电子", "智能传感器"]),
]

def _resolve_intel_key(sector: str) -> str:
    """把个股的赛道名解析为 intel 文件名键
    优先别名表（结构化有数据的大类文件），其次精确匹配中文名空模板
    """
    if not sector:
        return ""
    # 1. 别名关键词匹配（优先，对应有数据的英文 intel 文件）
    for key, keywords in SECTOR_ALIAS_MAP:
        for kw in keywords:
            if kw in sector:
                if os.path.exists(os.path.join(INTEL_DIR, key + ".json")):
                    return key
    # 2. 精确匹配（取 / 前的主名称，可能是空模板）
    main_name = sector.split("/")[0].strip()
    if os.path.exists(os.path.join(INTEL_DIR, _file_key(main_name) + ".json")):
        return _file_key(main_name)
    # 3. 都没匹配上，返回原名（load_intel 会找不到，返回 None）
    return _file_key(sector)

# 情报字段定义（评分引擎 barrier_engine 读取的键）
INTEL_FIELDS = {
    # 竞争格局
    "cr3":               "行业CR3(%)",
    "players_count":     "主要玩家数量",
    "global_players":    "全球能做的主要玩家数(技术壁垒证据)",
    # 国产替代
    "substitution_rate": "国产替代率(%)",
    "substitution_urgency": "替代紧迫度(high/medium/low)",
    # 客户认证
    "cert_cycle_months": "客户认证周期(月)",
    "cert_level":        "认证壁垒等级(high/medium/low)",
    # 行业空间
    "market_size_bn":    "行业规模(亿元)",
    "market_growth":     "行业增速(%)",
    # 政策
    "policy_level":      "政策支持等级(high/medium/low/none)",
    "policy_notes":      "政策说明",
    # 专利/技术
    "invent_patent_ratio": "发明专利占比(%)",
    "patent_year":       "年专利申请量(行业/龙头)",
    # 元信息
    "sources":           "数据来源列表",
    "updated":           "更新时间",
    "human_reviewed":    "是否人工复核过(true/false)",
}


def _file_key(sector: str) -> str:
    """赛道名 → 安全文件名（去特殊字符）"""
    safe = re.sub(r'[\\/:*?"<>|]', "-", sector.strip())
    return safe


def intel_path(sector: str) -> str:
    return os.path.join(INTEL_DIR, _file_key(_resolve_intel_key(sector)) + ".json")


# ===== 多赛道合并（2026-09-25 V2.4.1 新增）=====
# 问题：个股赛道名常写成「高端液压件/机器人丝杠」，原 _resolve_intel_key 命中第一个关键词
#       就返回 → 机器人丝杠（增速25%/政策high）的情报被整段丢弃，赛道壁垒被低估。
# 处理：拆开逐段取情报后合并。
_MULTI_SPLIT = re.compile(r"[／/、,，]+")
_LEVEL_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
# 合并方向：壁垒/格局类取「保守值」（不高估），成长/政策类取「较优值」（反映新赛道机会）
_MERGE_RULES = {
    "cr3": "min",                 # 集中度取低（保守）
    "players_count": "max",       # 玩家取多（保守）
    "global_players": "max",      # 全球玩家取多（保守）
    "cert_cycle_months": "min",   # 认证周期取短（保守）
    "cert_level": "min",          # 认证壁垒取低（保守）
    "substitution_rate": "max",   # 替代率取高（保守：剩余空间小）
    "market_size_bn": "max",      # 行业规模取大（机会）
    "market_growth": "max",       # 行业增速取优（机会）
    "policy_level": "max",        # 政策取优（机会）
    "substitution_urgency": "max",
}


def _load_intel_single(sector: str) -> dict:
    """读取单个赛道的情报文件"""
    path = intel_path(sector)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️ 读取赛道情报失败 {sector}: {e}")
    return None


def _compare(key, a, b):
    """按字段规则比较两个值，返回占优的那个"""
    if a is None:
        return b
    if b is None:
        return a
    mode = _MERGE_RULES.get(key, "min")
    try:
        if key.endswith("_level"):
            a, b = _LEVEL_ORDER.get(str(a).lower(), 0), _LEVEL_ORDER.get(str(b).lower(), 0)
            pick = max if mode == "max" else min
            return ("high", "medium", "low", "none")[3 - pick(a, b)]
        fa, fb = float(a), float(b)
        return fa if (max if mode == "max" else min)(fa, fb) == fa else fb
    except Exception:
        return a


def _merge_intels(intels, parts):
    """合并多个赛道情报：壁垒/格局取保守、成长/政策取优"""
    if not intels:
        return None
    if len(intels) == 1:
        return intels[0]
    keys = set()
    for it in intels:
        keys |= {k for k, v in it.items() if v is not None and not k.startswith("_")}
    merged = {}
    for k in keys:
        if k in ("sources", "updated", "human_reviewed", "sector", "policy_notes", "_merged_from", "_note"):
            continue
        val = None
        for it in intels:
            val = _compare(k, val, it.get(k))
        merged[k] = val
    merged["sector"] = " / ".join(parts)
    merged["sources"] = sorted({s for it in intels for s in (it.get("sources") or [])})
    merged["updated"] = max((it.get("updated") or "") for it in intels)
    merged["human_reviewed"] = all(bool(it.get("human_reviewed")) for it in intels)
    merged["policy_notes"] = " ｜ ".join(
        f"{p}:{it.get('policy_notes')}" for p, it in zip(parts, intels) if it.get("policy_notes"))
    merged["_merged_from"] = parts
    merged["_note"] = "多赛道合并：壁垒/格局/认证取保守值，成长/政策取较优值，未按业务占比加权 → 建议人工复核"
    return merged


# ===== 专属关键词优先（2026-10-11 新增）=====
# 问题：赛道名含 "/" 时 load_intel 会先拆段再合并，导致
#       「军工电子/MLCC瓷介电容」被拆成 钽电容 + MLCC 两份情报合并（赛道混淆）。
# 处理：整串含以下"专属词"时直接命中对应情报文件，不做拆分。
_EXCLUSIVE_MAP = [
    ("MLCC", "mlcc_military"), ("瓷介电容", "mlcc_military"),
    ("航空刹车", "aircraft_brake"), ("起落架", "aircraft_brake"),
    ("空港", "airport_ground_equipment"), ("地面装备", "airport_ground_equipment"),
    ("机场设备", "airport_ground_equipment"),
]


def load_intel(sector: str) -> dict:
    """读取赛道情报，没有返回 None

    支持两种情形：
      ① 单赛道（含别名匹配，细分赛道名自动匹配到大类 intel 文件）
      ② **多赛道**（用 / 、 等分隔，如「高端液压件/机器人丝杠」）→ 各段情报合并
    """
    if not sector:
        return None
    # 专属关键词优先：整串命中直接返回，不做多赛道拆分
    for kw, key in _EXCLUSIVE_MAP:
        if kw in sector:
            it = _load_intel_single(key)
            if it:
                return it
    parts = [x.strip() for x in _MULTI_SPLIT.split(sector) if x.strip()]
    if len(parts) > 1:
        intels, used = [], []
        for pt in parts:
            it = _load_intel_single(pt)
            if it:
                intels.append(it)
                used.append(pt)
        if intels:
            return _merge_intels(intels, used)
    return _load_intel_single(sector)


def save_intel(intel: dict, sector: str = None):
    """保存赛道情报"""
    sector = sector or intel.get("sector", "未命名赛道")
    os.makedirs(INTEL_DIR, exist_ok=True)
    intel["sector"] = sector
    intel["updated"] = datetime.now().strftime("%Y-%m-%d")
    path = intel_path(sector)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(intel, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 赛道情报已保存: {path}")


def make_template(sector: str) -> dict:
    """生成空模板（缺的维度全部 null + missing 标记）"""
    return {
        "sector": sector,
        "cr3": None,
        "players_count": None,
        "global_players": None,
        "substitution_rate": None,
        "substitution_urgency": None,
        "cert_cycle_months": None,
        "cert_level": None,
        "market_size_bn": None,
        "market_growth": None,
        "policy_level": None,
        "policy_notes": "",
        "invent_patent_ratio": None,
        "patent_year": None,
        "sources": [],
        "updated": "",
        "human_reviewed": False,
    }


def ensure_templates(sector_list: list):
    """为所有赛道生成缺失的模板文件"""
    os.makedirs(INTEL_DIR, exist_ok=True)
    created = 0
    for sector in sector_list:
        path = intel_path(sector)
        if not os.path.exists(path):
            save_intel(make_template(sector))
            created += 1
        else:
            print(f"  ℹ️ 已存在: {path}")
    print(f"\n✅ 模板检查完成：新建 {created} 个，其余已存在")


def parse_md_to_intel(md_text: str, sector: str, md_path: str = "") -> dict:
    """
    从赛道研究 md 解析出情报 JSON（规则抽取）
    针对现有「8维评估表 + 竞争格局表格」格式设计，抽不到就留 null
    """
    intel = make_template(sector)
    intel["sources"].append(os.path.basename(md_path) if md_path else "赛道研究md")

    # ---- 1. 8维评分表：| N | 维度 | 评分 | 说明 | ----
    dim_map = {
        "技术壁垒": ("tech", None),
        "国产替代": ("substitution", None),
        "客户认证": ("cert", "cert_level"),
        "上游议价": ("upstream", None),
        "行业空间": ("space", None),
        "竞争格局": ("competition", None),
        "产品迭代": ("iteration", None),
        "政策支持": ("policy", "policy_level"),
    }
    for m in re.finditer(r"\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", md_text):
        dim_name = m.group(1).strip()
        score_cell = m.group(2).strip()
        for kw, (_, field) in dim_map.items():
            if kw in dim_name:
                # 评分如 "⭐⭐⭐ 3/3" 或 "⭐ 0.5/1"
                frac = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", score_cell)
                if frac:
                    got, total = float(frac.group(1)), float(frac.group(2))
                    ratio = got / total if total else 0
                    if kw == "技术壁垒" and ratio >= 0.85:
                        if intel["global_players"] is None:
                            intel["global_players"] = 3  # 默认推断：技术壁垒极高≈全球极少玩家
                    if kw == "客户认证" and ratio >= 0.75:
                        intel["cert_level"] = "high"
                    elif kw == "客户认证" and ratio >= 0.5:
                        intel["cert_level"] = "medium"
                    elif kw == "客户认证":
                        intel["cert_level"] = "low"
                    if kw == "政策支持":
                        if ratio >= 0.75:
                            intel["policy_level"] = "high"
                        elif ratio >= 0.4:
                            intel["policy_level"] = "medium"
                        else:
                            intel["policy_level"] = "low"
                    if kw == "国产替代" and ratio >= 0.85:
                        intel["substitution_urgency"] = intel.get("substitution_urgency") or "high"
                break

    # ---- 2. CR3 ----
    m = re.search(r"CR3\s*[≈=~]?\s*(\d+(?:\.\d+)?)\s*%", md_text)
    if m:
        intel["cr3"] = float(m.group(1))

    # ---- 3. 国产替代率 ----
    m = re.search(r"(?:国产)?替代率\s*[<>≤≥]?\s*(\d+(?:\.\d+)?)\s*%", md_text)
    if m:
        intel["substitution_rate"] = float(m.group(1))
        rate = intel["substitution_rate"]
        if rate < 15:
            intel["substitution_urgency"] = "high"
        elif rate < 30:
            intel["substitution_urgency"] = "medium"

    # ---- 4. 紧迫度关键词（命中=真卡脖子，直接升级为 high）----
    if any(kw in md_text for kw in ("卡脖子", "禁运", "实体清单", "断供", "被封锁")):
        intel["substitution_urgency"] = "high"

    # ---- 5. 认证关键词 ----
    if any(kw in md_text for kw in ("替换成本极高", "粘性极强", "验证周期长")):
        intel["cert_level"] = intel.get("cert_level") or "high"

    # ---- 6. 行业规模（亿元）----
    for m in re.finditer(r"(?:国内|中国)\s*(?:市场)?\s*(?:规模|容量|空间)?\s*[约]?\s*(\d+(?:\.\d+)?)\s*亿", md_text):
        intel["market_size_bn"] = float(m.group(1))
        break
    if intel["market_size_bn"] is None:
        m = re.search(r"百亿人民币", md_text)
        if m:
            intel["market_size_bn"] = 100.0

    # ---- 7. 行业增速 ----
    m = re.search(r"增速\s*(\d+(?:\.\d+)?)\s*%", md_text)
    if m:
        intel["market_growth"] = float(m.group(1))

    # ---- 8. 政策 ----
    if any(kw in md_text for kw in ("国家级战略", "大基金", "十四五", "重点投", "政策支持")):
        intel["policy_level"] = intel.get("policy_level") or "high"
        # 8维表政策行：| 8 | 政策支持 | ⭐⭐ 2/2 | 国家级战略，大基金重点投 |
        m = re.search(r"政策支持\s*\|\s*[^|]+\|\s*([^|]+)", md_text)
        if m:
            note = m.group(1).strip()
            if len(note) > 2:
                intel["policy_notes"] = note

    # ---- 9. 竞争格局表格 → 玩家数 ----
    # 找"全球市场"表格行数（| 公司 | 市占率 | 说明 |）
    table_section = re.search(r"### 全球市场\n(.*?)(?:\n###|\n##|$)", md_text, re.S)
    if table_section:
        rows = [r for r in table_section.group(1).strip().split("\n")
                if r.startswith("| ") and "---" not in r and "公司" not in r]
        if rows:
            count = len(rows)
            intel["players_count"] = count
            if intel["global_players"] is None:
                intel["global_players"] = count

    intel["human_reviewed"] = False  # 迁移来的需要用户确认
    return intel


def report_missing(sector_list: list) -> list:
    """输出每个赛道缺失的情报维度（供 Pi 对话时按清单补齐）"""
    print("\n" + "=" * 60)
    print("📋 赛道情报缺项报告（按这个清单补，补完 human_reviewed=true）")
    print("=" * 60)
    result = []
    for sector in sector_list:
        intel = load_intel(sector)
        if intel is None:
            print(f"\n❌ {sector}: 无情报文件（先跑 --ensure）")
            result.append((sector, ["全部"]))
            continue
        missing = [k for k, v in intel.items()
                   if k not in ("sector", "sources", "updated", "human_reviewed")
                   and v in (None, "", [])]
        key_missing = [k for k in missing if k in (
            "cr3", "substitution_rate", "cert_cycle_months",
            "market_size_bn", "market_growth", "policy_level", "global_players")]
        status = "✅ 完整" if not missing else f"缺 {len(missing)} 项"
        print(f"\n{'✅' if not missing else '🟡'} {sector}: {status}"
              + ("  (关键: " + "、".join(key_missing) + ")" if key_missing else ""))
        if missing:
            for k in missing:
                print(f"    - {k}: {INTEL_FIELDS.get(k, k)}")
        result.append((sector, missing))
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="赛道情报层")
    parser.add_argument("--ensure", action="store_true", help="为所有赛道生成缺失模板")
    parser.add_argument("--from-md", type=str, help="从赛道研究md解析情报")
    parser.add_argument("--sector", type=str, help="赛道名（与SECTOR_PURITY一致）")
    parser.add_argument("--report", action="store_true", help="输出缺项报告")
    parser.add_argument("--sectors", type=str, default="",
                        help="逗号分隔的赛道清单（默认用 config.SECTOR_PURITY）")
    args = parser.parse_args()

    # 赛道清单
    if args.sectors:
        sector_list = [s.strip() for s in args.sectors.split(",") if s.strip()]
    else:
        try:
            from config import SECTOR_PURITY
            sector_list = [s["sector"] for s in SECTOR_PURITY]
        except ImportError:
            print("⚠️ 无法加载 config，请用 --sectors 指定赛道清单")
            sys.exit(1)

    if args.from_md:
        if not args.sector:
            print("❌ --from-md 需要 --sector 指定赛道名")
            sys.exit(1)
        with open(args.from_md, "r", encoding="utf-8") as f:
            text = f.read()
        intel = parse_md_to_intel(text, args.sector, args.from_md)
        save_intel(intel)
        print(f"\n📄 解析结果预览:")
        for k, v in intel.items():
            if k in ("sector", "updated", "human_reviewed"):
                continue
            mark = "  ✅" if v not in (None, "", []) else "  ❌缺"
            print(f"  {mark} {k}: {v if v not in (None, '', []) else '(空)'}")

    if args.ensure:
        ensure_templates(sector_list)

    if args.report:
        report_missing(sector_list)


if __name__ == "__main__":
    main()
