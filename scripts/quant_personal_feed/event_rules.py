"""Explainable private-news labels; no provider or model calls.

Rules identify phrases in the supplied text. They do not verify the report,
infer a price direction, or certify that a mentioned issuer has a security.
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime


RULE_VERSION = "technology_event_rules_v1_2_20261116"

# Canonical company entities are distinct from security tickers and ETFs.
ENTITY_RULES = {
    "AAPL": ("Apple", "COMPANY", ((r"\bAAPL\b", 0), (r"\bApple Inc\.?\b", re.I), ("苹果公司", 0))),
    "MSFT": ("Microsoft", "COMPANY", ((r"\bMSFT\b", 0), (r"\bMicrosoft\b", re.I), ("微软", 0))),
    "NVDA": ("NVIDIA", "COMPANY", ((r"\bNVDA\b", 0), (r"\bNVIDIA\b", re.I), ("英伟达", 0))),
    "AMZN": ("Amazon", "COMPANY", ((r"\bAMZN\b", 0), (r"\bAmazon\b", re.I), ("亚马逊", 0))),
    "GOOGL": ("Alphabet", "COMPANY", ((r"\bGOOGL?\b", 0), (r"\bAlphabet\b", re.I), (r"\bGoogle\b", re.I), ("谷歌", 0))),
    "META": ("Meta Platforms", "COMPANY", ((r"\bMETA\b", 0), (r"\bMeta Platforms\b", re.I), (r"\bFacebook\b", re.I), ("脸书", 0))),
    "TSLA": ("Tesla", "COMPANY", ((r"\bTSLA\b", 0), (r"\bTesla\b", re.I), ("特斯拉", 0))),
    "AMD": ("AMD", "COMPANY", ((r"\bAMD\b", 0), (r"\bAdvanced Micro Devices\b", re.I), ("超威半导体", 0))),
    "INTC": ("Intel", "COMPANY", ((r"\bINTC\b", 0), (r"\bIntel\b", re.I), ("英特尔", 0))),
    "CRWV": ("CoreWeave", "COMPANY", ((r"\bCRWV\b", 0), (r"\bCoreWeave\b", re.I))),
    "MU": ("Micron", "COMPANY", ((r"\bMU\b", 0), (r"\bMicron\b", re.I), ("美光", 0))),
    "SNDK": ("SanDisk", "COMPANY", ((r"\bSNDK\b", 0), (r"\bSanDisk\b", re.I), ("闪迪", 0))),
    "SKHYNIX": ("SK hynix", "COMPANY", ((r"\bSK[ -]?hynix\b", re.I), ("SK海力士", 0), ("SK 海力士", 0), ("海力士", 0))),
    "SAMSUNG_ELECTRONICS": ("Samsung Electronics", "COMPANY", ((r"\bSamsung Electronics\b", re.I), ("三星电子", 0))),
    "SPACEX": ("SpaceX", "COMPANY_NO_VERIFIED_LISTED_CODE", ((r"\bSpaceX\b", re.I), ("太空探索技术公司", 0))),
    "TQQQ": ("TQQQ", "ETF", ((r"\bTQQQ\b", 0),)),
    "QQQ": ("QQQ", "ETF", ((r"\bQQQ\b", 0),)),
    "SOXX": ("SOXX", "ETF", ((r"\bSOXX\b", 0),)),
    "SPY": ("SPY", "ETF", ((r"\bSPY\b", 0),)),
}

CATEGORY_RULES = {
    "EARNINGS_GUIDANCE": ("财报与业绩指引", 100, r"财报|业绩指引|营收|净利润|毛利率|每股收益|季度业绩|全年指引|\bearnings\b|\bguidance\b|\brevenue\b|\bnet income\b|\bEPS\b"),
    "AI_CAPEX": ("AI资本支出、数据中心与电力建设", 85, r"资本支出|资本开支|\bcapex\b|数据中心|算力中心|AI服务器|AI基础设施|电力建设|电网建设|\bdata cent(?:er|re)s?\b|\bpower grid\b"),
    "CHIPS_MEMORY": ("芯片与存储", 80, r"芯片|半导体|存储价格|存储芯片|\bHBM\b|\bDRAM\b|\bNAND\b|\bSSD\b|\bsemiconductor\b|\bmemory chip\b"),
    "MACRO_RATES": ("利率、通胀、就业与流动性", 95, r"利率|通胀|物价上涨|非农|就业数据|就业形势|失业率|流动性|美联储|\bCPI\b|\bPCE\b|\bFOMC\b|\binflation\b|\binterest rate\b|\bpayrolls\b|\bunemployment rate\b|\bFederal Reserve\b"),
    "POLICY": ("出口限制、关税、监管与政策", 75, r"出口限制|出口管制|关税|监管|制裁|科技政策|\bexport control\b|\btariff\b|\bsanction\b|\bregulat(?:ion|ory)\b"),
    "FINANCE_CREDIT": ("融资、债务、信用风险与并购", 70, r"融资|发债|债务|信用风险|信贷|利差|并购|收购|\bfinancing\b|\bdebt\b|\bcredit risk\b|\bmerger\b|\bacquisition\b"),
    "GOVERNANCE": ("管理层、治理与经营中断", 60, r"高管|首席执行官|董事会|管理层|经营中断|停产|罢工|裁员|\bCEO\b|\bexecutive\b|\bresignation\b|\boutage\b"),
    "GEO_ENERGY": ("地缘冲突、能源与航运", 55, r"地缘|战争|冲突|油价|石油|天然气|能源供应|航运|红海|霍尔木兹|\bOPEC\b|\bshipping\b|\bcrude oil\b"),
}

OTHER = {"id": "OTHER", "label": "其他", "phrase": None}
TECH_PRESET_ENTITIES = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "INTC", "CRWV", "MU", "SNDK", "SKHYNIX", "SAMSUNG_ELECTRONICS", "SPACEX")
TECH_PRESET_CATEGORIES = ("EARNINGS_GUIDANCE", "AI_CAPEX", "CHIPS_MEMORY", "POLICY", "FINANCE_CREDIT")

_APPLE_CONTEXT = re.compile(r"iPhone|iPad|MacBook|Apple Watch|Tim Cook|苹果手机|苹果电脑|科技公司|财报|股价|\bshares\b|\bstock\b|\bearnings\b|\bdevice\b", re.I)
_APPLE_FRUIT = re.compile(r"苹果树|苹果汁|苹果种植|苹果产量|苹果水果|\bapple orchard\b|\bapple fruit\b", re.I)
_PERIOD = re.compile(r"(?:FY\s?20\d{2}\s?Q[1-4]|20\d{2}\s?(?:年|财年|FY)?\s?(?:第?[一二三四1-4]季度|Q[1-4])|Q[1-4]\s?(?:FY)?20\d{2}|20\d{2}财年)", re.I)
_CLAIM_STAGES = (
    ("CORRECTION", re.compile(r"更正|修正公告|撤回|澄清|\bcorrection\b|\bcorrected\b|\bretraction\b", re.I)),
    ("DENIAL", re.compile(r"否认|辟谣|不实传闻|\bden(?:y|ies|ied)\b|\brefut(?:e|es|ed)\b", re.I)),
    ("RUMOR", re.compile(r"传闻|据传|消息人士|未经证实|\brumou?r\b|\breportedly\b", re.I)),
    ("GUIDANCE", re.compile(r"指引|预计下季度|预计全年|\bguidance\b|\boutlook\b", re.I)),
    ("ACTUAL", re.compile(r"财报|季度业绩|营收|净利润|\bearnings\b|\brevenue\b", re.I)),
)


def entity_choices() -> list[dict]:
    return [{"id": key, "label": label, "kind": kind} for key, (label,kind,_) in ENTITY_RULES.items()]


def category_choices() -> list[dict]:
    return [{"id": key, "label": label} for key, (label,_,_) in CATEGORY_RULES.items()]


def _first_entity_phrase(entity: str, text: str) -> str | None:
    for pattern, flags in ENTITY_RULES[entity][2]:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(0)
    if entity == "AAPL" and not _APPLE_FRUIT.search(text) and _APPLE_CONTEXT.search(text):
        match = re.search(r"\bApple\b|苹果", text)
        if match:
            return match.group(0)
    return None


def _utc_day(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.date().isoformat() if dt.tzinfo else None
    except ValueError:
        return None


def classify(item: dict) -> dict:
    title = str(item.get("title") or "")
    body = str(item.get("content") or "")
    text = html.unescape(title + "\n" + body)
    entities = []
    for key, (label,kind,_) in ENTITY_RULES.items():
        phrase = _first_entity_phrase(key, text)
        if phrase is not None:
            entities.append({"id": key, "label": label, "kind": kind, "phrase": phrase})
    categories = []
    for key, (label,_,pattern) in CATEGORY_RULES.items():
        match = re.search(pattern, text, re.I)
        if match:
            categories.append({"id": key, "label": label, "phrase": match.group(0)})
    if not categories:
        categories = [OTHER.copy()]
    claim_stage = next((key for key,pattern in _CLAIM_STAGES if pattern.search(text)), "GENERAL")
    periods = sorted({re.sub(r"\s+", "", match.group(0)).upper() for match in _PERIOD.finditer(text)})
    source_day = _utc_day(item.get("published_at_utc"))
    day_basis = "SOURCE_PUBLISHED_UTC" if source_day else "LOCAL_RECEIVED_UTC"
    day = source_day or _utc_day(item.get("received_at")) or "UNKNOWN"
    normalized_title = re.sub(r"\W+", "", title, flags=re.UNICODE).casefold()
    normalized_full = re.sub(r"\W+", "", text, flags=re.UNICODE).casefold()
    # Without an explicit fiscal period, an identical generic headline is not
    # sufficient to merge two different earnings events.
    scope = normalized_title if normalized_title and periods else normalized_full
    group_material = "|".join((day_basis, day, claim_stage, ",".join(periods), scope,
                               ",".join(sorted(x["id"] for x in entities))))
    group_id = hashlib.sha256(group_material.encode("utf-8")).hexdigest()[:20]
    return {"rule_version": RULE_VERSION, "entities": entities, "categories": categories,
            "claim_stage": claim_stage, "period_keys": periods, "event_group_id": group_id,
            "grouping_basis": "SAME_EXPLICIT_PERIOD_AND_TITLE" if normalized_title and periods else "EXACT_NORMALIZED_FULL_TEXT",
            "source_day": day, "day_basis": day_basis}


def focus(item: dict, labels: dict, interest_entities: set[str], interest_categories: set[str]) -> dict:
    matched_entities = [e for e in labels["entities"] if e["id"] in interest_entities]
    matched_categories = [c for c in labels["categories"] if c["id"] in interest_categories]
    material = [c for c in labels["categories"] if c["id"] != "OTHER"]
    eligible = bool((matched_entities and material) or matched_categories)
    primary = matched_categories[0] if matched_categories else material[0] if material else None
    if matched_entities and primary:
        reason = f"关注对象 {matched_entities[0]['label']}；原文出现“{primary['phrase']}”"
    elif primary and matched_categories:
        reason = f"关注主题 {primary['label']}；原文出现“{primary['phrase']}”"
    else:
        reason = "无明确重点规则命中"
    category_priority = max((CATEGORY_RULES[c["id"]][1] for c in material), default=0)
    return {"focus_eligible": eligible, "focus_reason": reason if eligible else None,
            "rule_priority": (1000 if matched_entities else 0) + category_priority,
            "rule_priority_label": "规则排序，非市场影响预测"}
