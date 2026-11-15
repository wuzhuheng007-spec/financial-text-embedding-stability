"""Versioned issuer, ETF, security-candidate and display contract.

The configuration describes the user's observation scope. Provider mappings,
dated holdings and issuer-to-security evidence must be attached separately.
This module makes no provider or model calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .store import require_utc


DEFAULT_WATCHLIST = (Path(__file__).resolve().parents[2] /
                     "configs/market_observer_watchlist_v2_20261115.json")
REQUIRED_GROUPS = {"priority_etfs", "magnificent_seven", "memory", "space", "benchmark", "related"}
ENTITY_KINDS = {"COMPANY", "COMPANY_CANDIDATE", "ETF"}


def load_watchlist(path: str | Path = DEFAULT_WATCHLIST) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_watchlist(data)
    return data


def validate_watchlist(data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or data.get("schema") != "market_observer_watchlist_v2":
        raise ValueError("unsupported market observer watchlist schema")
    groups, entities = data.get("groups"), data.get("entities")
    securities, indices = data.get("securities"), data.get("indices")
    aliases = data.get("news_aliases")
    if not all(isinstance(value, dict) for value in (groups, entities, securities, indices, aliases)):
        raise ValueError("watchlist groups/entities/securities/indices/aliases must be objects")
    if set(groups) != REQUIRED_GROUPS:
        raise ValueError("watchlist group set differs from frozen user scope")
    order = data.get("display_order")
    if not isinstance(order, list) or set(order) != REQUIRED_GROUPS or len(order) != len(REQUIRED_GROUPS):
        raise ValueError("watchlist display order must cover every group once")
    if order[:2] != ["priority_etfs", "benchmark"]:
        raise ValueError("ETF priority and QQQ benchmark must appear first")
    if not all(isinstance(members, list) and members for members in groups.values()):
        raise ValueError("each watchlist group needs members")
    displayed = [entity_id for members in groups.values() for entity_id in members]
    if len(displayed) != len(set(displayed)) or set(displayed) != set(entities):
        raise ValueError("each observed entity must appear exactly once")
    if set(entities) & set(indices) or set(entities) & set(securities) or set(indices) & set(securities):
        raise ValueError("company/fund, index and security IDs must be distinct")
    if any(not isinstance(entity_id, str) or not entity_id for entity_id in displayed):
        raise ValueError("entity IDs must be nonempty strings")
    for entity_id, entity in entities.items():
        if not isinstance(entity, dict) or entity.get("kind") not in ENTITY_KINDS:
            raise ValueError(f"invalid entity kind for {entity_id}")
        if not isinstance(entity.get("name"), str) or not entity["name"]:
            raise ValueError(f"missing entity name for {entity_id}")
        security_ids = entity.get("security_ids")
        if not isinstance(security_ids, list) or len(security_ids) != len(set(security_ids)):
            raise ValueError(f"invalid security list for {entity_id}")
        for security_id in security_ids:
            if security_id not in securities or securities[security_id].get("entity_id") != entity_id:
                raise ValueError(f"security link mismatch for {entity_id}/{security_id}")
        if entity["kind"] == "ETF":
            if entity.get("reference_index_id") not in indices or \
                    entity.get("return_label_policy") != "OWN_ACTUAL_QUOTE_OR_BAR_ONLY":
                raise ValueError(f"ETF {entity_id} lacks separate index or own-price label rule")
        elif entity.get("reference_index_id") is not None:
            raise ValueError("non-ETF entity cannot inherit an ETF reference index")
    for security_id, security in securities.items():
        if not isinstance(security, dict) or security.get("entity_id") not in entities:
            raise ValueError(f"security {security_id} has no issuer/fund entity")
        if security_id not in entities[security["entity_id"]]["security_ids"]:
            raise ValueError(f"security {security_id} is not listed by its entity")
        if security.get("provider_code") is not None:
            raise ValueError("static watchlist must not guess or freeze a provider code")
        if not isinstance(security.get("requested_ticker"), str) or not security["requested_ticker"]:
            raise ValueError(f"security {security_id} lacks a requested ticker")
    for index_id, index in indices.items():
        if index.get("kind") != "INDEX" or index.get("provider_code") is not None:
            raise ValueError(f"index {index_id} must stay separate from an ETF quote")
    if data.get("news_event_policy") != \
            "ONE_EVENT_ID_PER_SOURCE_EVENT; ENTITY_AND_SECURITY_LINKS_SEPARATE; ALIAS_MATCH_IS_CANDIDATE_ONLY":
        raise ValueError("news event entity policy was changed")
    for alias, entity_id in aliases.items():
        if not isinstance(alias, str) or alias != alias.upper() or entity_id not in entities:
            raise ValueError("news alias must be uppercase and resolve to an entity")
    if aliases.get("GOOG") != aliases.get("GOOGL") or aliases.get("GOOGL") != "COMPANY:ALPHABET":
        raise ValueError("GOOG and GOOGL must resolve to one Alphabet entity")
    if aliases.get("TSLA") == aliases.get("SPACEX"):
        raise ValueError("Tesla and SpaceX must stay distinct companies")
    if entities["COMPANY:ALPHABET"].get("primary_watch_security_id") != "SEC:GOOGL:US":
        raise ValueError("GOOGL must be the selected Alphabet watch security")
    if securities["SEC:GOOG:US"].get("watch_enabled") is not False:
        raise ValueError("GOOG share-class alias cannot become a second watched company")
    if entities["COMPANY:SPACEX"].get("security_ids") != ["SEC:SPCX:US"] or \
            securities["SEC:SPCX:US"].get("entity_id") != "COMPANY:SPACEX" or \
            entities["COMPANY:SPACEX"].get("issuer_security_relation") != \
            "CURRENT_PROVIDER_AND_OFFICIAL_ISSUER_CROSSCHECK":
        raise ValueError("SpaceX security requires the current official issuer crosscheck")
    hynix = entities["COMPANY:SK_HYNIX"]
    if hynix.get("cross_market_price_policy") != "SEPARATE_SERIES_NO_CONCATENATION" or \
            {securities[x]["market_hint"] for x in hynix["security_ids"]} != \
            {"KR", "US"}:
        raise ValueError("SK hynix US ADR and Korean ordinary must remain separate")
    tqqq = entities["ETF:TQQQ"]
    if tqqq.get("daily_target_leverage") != 3 or \
            tqqq.get("return_label_policy") != "OWN_ACTUAL_QUOTE_OR_BAR_ONLY":
        raise ValueError("TQQQ must keep its daily objective separate from actual returns")
    if data.get("etf_holdings_policy") != \
            "DATED_OFFICIAL_WEIGHT_FILE_REQUIRED_FOR_WEIGHTED_EFFECT; OTHERWISE_QUALITATIVE_ONLY":
        raise ValueError("ETF weighted analysis must require dated official holdings")
    if data.get("missing_consensus_policy") != "UNKNOWN_NO_BEAT_CLAIM":
        raise ValueError("missing consensus cannot become a beat claim")
    if data.get("mapping_history_policy") != \
            "CURRENT_PROVIDER_AND_ISSUER_EVIDENCE_DOES_NOT_BACKFILL_HISTORICAL_IDENTITY":
        raise ValueError("current security mapping cannot backfill historical identity")
    evidence, mappings = data.get("mapping_evidence"), data.get("verified_current_mappings")
    if not isinstance(evidence, dict) or not isinstance(mappings, dict) or set(mappings) != set(securities):
        raise ValueError("each current security candidate needs its own source-bound mapping")
    for evidence_id, receipt in evidence.items():
        if not isinstance(receipt, dict) or not isinstance(receipt.get("public_receipt"), str) or \
                not isinstance(receipt.get("raw_sha256"), str) or len(receipt["raw_sha256"]) != 64 or \
                any(c not in "0123456789abcdef" for c in receipt["raw_sha256"].lower()):
            raise ValueError(f"invalid mapping evidence {evidence_id}")
    provider_codes: set[str] = set()
    for security_id, mapping in mappings.items():
        if not isinstance(mapping, dict) or mapping.get("evidence_id") not in evidence or \
                mapping.get("source_tool") != "quote_stock_basicinfo" or \
                mapping.get("identity_scope") != "CURRENT_ONLY" or \
                mapping.get("live_quote_entitlement") != "NOT_TESTED":
            raise ValueError(f"current mapping {security_id} lacks source or scope")
        code, market = mapping.get("provider_code"), mapping.get("provider_market")
        if not isinstance(code, str) or not code or code in provider_codes or \
                market != securities[security_id]["market_hint"] or not code.startswith(f"{market}."):
            raise ValueError(f"current mapping {security_id} has a code/market conflict")
        provider_codes.add(code)
        require_utc(mapping.get("recorded_at_utc"), f"mapping {security_id} recorded_at_utc")
    if mappings["SEC:SPCX:US"]["provider_code"] != "US.SPCX" or \
            mappings["SEC:SKHY:US_CANDIDATE"]["provider_code"] != "US.SKHY" or \
            mappings["SEC:000660:KR_CANDIDATE"]["provider_code"] != "KR.000660":
        raise ValueError("SpaceX and SK hynix current source mappings are missing")


def candidate_entity_for_news_alias(alias: str, watchlist: dict[str, Any]) -> str | None:
    """A matching alias suggests an issuer/fund only; it does not verify a report."""
    validate_watchlist(watchlist)
    return watchlist["news_aliases"].get(alias.strip().upper())


def event_entity_projection(event_id: str, aliases: Iterable[str],
                            watchlist: dict[str, Any]) -> dict[str, Any]:
    """One source event, unique entity candidates, and separate security links."""
    validate_watchlist(watchlist)
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id is required")
    entities = sorted({watchlist["news_aliases"][alias.strip().upper()]
                       for alias in aliases if alias.strip().upper() in watchlist["news_aliases"]})
    securities = {entity_id: list(watchlist["entities"][entity_id]["security_ids"])
                  for entity_id in entities}
    return {"event_id": event_id, "candidate_entity_ids": entities,
            "candidate_security_ids_by_entity": securities,
            "verification_status": "CANDIDATE_ALIAS_MATCH_ONLY"}


def requested_us_subscription_order(watchlist: dict[str, Any]) -> list[str]:
    """Requested US symbols in display order, before provider resolution/quota gates."""
    validate_watchlist(watchlist)
    out: list[str] = []
    for group in watchlist["display_order"]:
        for entity_id in watchlist["groups"][group]:
            for security_id in watchlist["entities"][entity_id]["security_ids"]:
                security = watchlist["securities"][security_id]
                if security.get("watch_enabled", True) and security["market_hint"] == "US":
                    ticker = security["requested_ticker"]
                    if ticker not in out:
                        out.append(ticker)
    return out
