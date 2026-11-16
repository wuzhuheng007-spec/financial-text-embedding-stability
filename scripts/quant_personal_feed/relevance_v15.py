"""Deterministic screening of untrusted Jin10 text for the personal tech feed.

Phrase matches are relevance evidence, never verification of a reported fact.
The module has no provider, price, or model access.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .event_rules import classify

RULE_VERSION = "tech_macro_relevance_v15_2"
INDUSTRY = (
    ("AI_COMPUTE", re.compile(r"AI算力|人工智能算力|AI芯片|GPU集群|\bAI infrastructure\b|\bGPU cluster\b", re.I)),
    ("SEMICONDUCTOR", re.compile(r"半导体|芯片制造|晶圆|\bsemiconductor\b|\bfoundry\b", re.I)),
    ("MEMORY", re.compile(r"(?<![A-Za-z])HBM(?![A-Za-z])|(?<![A-Za-z])DRAM(?![A-Za-z])|(?<![A-Za-z])NAND(?![A-Za-z])|企业级SSD|存储芯片|存储价格|内存价格|\bmemory chip\b", re.I)),
    ("CLOUD_DATA_CENTER", re.compile(r"云计算|数据中心|算力中心|\bcloud computing\b|\bdata cent(?:er|re)\b", re.I)),
    ("POWER_OPTICAL", re.compile(r"电力建设|电网建设|光通信|光模块|\bpower grid\b|\boptical transceiver\b", re.I)),
)
MACRO = (
    ("RATES_INFLATION", re.compile(r"美联储|联邦公开市场委员会|利率决议|降息|加息|通胀数据|消费者物价指数|(?<![A-Za-z])FOMC(?![A-Za-z])|(?<![A-Za-z])CPI(?![A-Za-z])|(?<![A-Za-z])PCE(?![A-Za-z])|\bFederal Reserve\b|\brate decision\b", re.I)),
    ("JOBS_LIQUIDITY", re.compile(r"非农|失业率|就业报告|流动性|\bpayrolls\b|\bunemployment rate\b|\bliquidity\b", re.I)),
    ("POLICY", re.compile(r"出口管制|出口限制|关税|科技监管|制裁|\bexport control\b|\btariff\b|\bsanction\b", re.I)),
    ("ENERGY_GEO", re.compile(r"油价|原油价格|能源供应|地缘冲突|战争|霍尔木兹|红海航运|\bcrude oil\b|\bOPEC\b|\bshipping disruption\b", re.I)),
)
EVENT_ACTION = re.compile(r"发布|公布|宣布|计划|上调|下调|增加|减少|收购|并购|融资|裁员|停产|否认|澄清|更正|指引|财报|营收|利润|投资|扩建|限制|\breports?\b|\bannounc(?:e|ed|es)\b|\bguidance\b|\bearnings\b|\bacquir(?:e|ed|es)\b", re.I)


@dataclass(frozen=True)
class Screening:
    status: str  # INCLUDE, PENDING_REVIEW, EXCLUDE
    scope: str  # DIRECT_SUBJECT, INDUSTRY, MACRO, POSSIBLE_DIRECT, NONE
    reason_key: str
    evidence_phrase: str | None
    evidence_location: str | None
    entity_ids: tuple[str, ...]
    category_ids: tuple[str, ...]
    claim_stage: str
    event_group_id: str
    rule_version: str = RULE_VERSION

    def as_dict(self) -> dict:
        return {key: list(value) if isinstance(value, tuple) else value
                for key, value in self.__dict__.items()}


def _first(patterns: tuple[tuple[str, re.Pattern], ...], text: str):
    found = []
    for category, pattern in patterns:
        match = pattern.search(text)
        if match:
            found.append((match.start(), category, match.group(0)))
    return min(found) if found else None


def screen(item: dict) -> Screening:
    title = html.unescape(str(item.get("title") or ""))
    body = html.unescape(str(item.get("content") or ""))
    full = title + "\n" + body
    labels = classify(item)
    title_labels = classify({"title": title, "content": ""})
    title_entity = title_labels["entities"][0] if title_labels["entities"] else None
    # Jin10 flashes often omit a separate title. In that case only the first
    # complete lead clause may establish the subject; a later mention cannot.
    lead = re.split(r"[。.!?！？\n]", body, maxsplit=1)[0][:160] if not title else ""
    lead_labels = classify({"title": lead, "content": ""}) if lead else None
    lead_entity = lead_labels["entities"][0] if lead_labels and lead_labels["entities"] else None
    industry = _first(INDUSTRY, full)
    macro = _first(MACRO, full)
    entity_ids = tuple(x["id"] for x in labels["entities"])
    category_ids = tuple(x["id"] for x in labels["categories"] if x["id"] != "OTHER")
    common = dict(entity_ids=entity_ids, category_ids=category_ids,
                  claim_stage=labels["claim_stage"], event_group_id=labels["event_group_id"])
    title_material = any(x["id"] != "OTHER" for x in title_labels["categories"])
    if title_entity and (EVENT_ACTION.search(title) or title_material):
        return Screening("INCLUDE", "DIRECT_SUBJECT", "direct_title_event",
                         title_entity["phrase"], "title", **common)
    lead_material = bool(lead_labels and any(x["id"] != "OTHER" for x in lead_labels["categories"]))
    if lead_entity and (EVENT_ACTION.search(lead) or lead_material):
        return Screening("INCLUDE", "DIRECT_SUBJECT", "direct_lead_event",
                         lead_entity["phrase"], "body_lead", **common)
    if industry:
        location = "title" if industry[0] < len(title) else "body"
        return Screening("INCLUDE", "INDUSTRY", industry[1].lower(), industry[2], location, **common)
    if macro:
        location = "title" if macro[0] < len(title) else "body"
        return Screening("INCLUDE", "MACRO", macro[1].lower(), macro[2], location, **common)
    if entity_ids:
        phrase = labels["entities"][0]["phrase"]
        return Screening("PENDING_REVIEW", "POSSIBLE_DIRECT", "incidental_or_unclear_entity",
                         phrase, "body" if phrase not in title else "title", **common)
    if category_ids:
        phrase = next((x["phrase"] for x in labels["categories"] if x["id"] != "OTHER"), None)
        return Screening("PENDING_REVIEW", "NONE", "broad_category_without_scope",
                         phrase, "title" if phrase and phrase in title else "body", **common)
    return Screening("EXCLUDE", "NONE", "no_tech_macro_or_direct_evidence", None, None, **common)


def rank_reason(decision: Screening, *, published_at_utc: str | None, received_at: str,
                source_url: str | None, version: int, weights: dict, now: datetime | None = None) -> dict:
    """Product order only. Reprints do not add source confidence or votes."""
    if decision.status != "INCLUDE":
        raise ValueError("Only included events can enter Top 10")
    now = now or datetime.now(timezone.utc)
    factors = {}
    factors["direct_subject"] = weights["direct_subject"] if decision.scope == "DIRECT_SUBJECT" else 0
    factors["industry"] = weights["industry"] if decision.scope == "INDUSTRY" else 0
    factors["macro"] = weights["macro"] if decision.scope == "MACRO" else 0
    factors["material_category"] = weights["material_category"] if decision.category_ids else 0
    factors["source_url"] = 0
    # Every Jin10 item remains an uncorroborated supplier report; URL presence
    # helps navigation, but contributes no independent confirmation.
    factors["source_status_unverified"] = 0
    factors["correction"] = weights["correction"] if decision.claim_stage == "CORRECTION" else 0
    factors["denial"] = weights["denial"] if decision.claim_stage == "DENIAL" else 0
    factors["new_version"] = 7 if version > 1 else 0
    timestamp = published_at_utc or received_at
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        age_h = max(0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
    except (ValueError, TypeError):
        age_h = None
    factors["recency"] = 20 if age_h is not None and age_h <= 1 else 12 if age_h is not None and age_h <= 6 else 5 if age_h is not None and age_h <= 24 else 0
    return {"score": sum(factors.values()), "factors": factors,
            "method": "EXPLAINABLE_PRODUCT_ORDER_NOT_MARKET_PROBABILITY"}
