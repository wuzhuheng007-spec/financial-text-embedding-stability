"""Synthetic, no-provider tests for the separate read-only WebAPI push path."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from scripts.market_observer_v1.moomoo_webapi_oauth import OAuthError, _check_scope
from scripts.market_observer_v1.moomoo_quota_probe import probe_subscription_quota
from scripts.market_observer_v1.moomoo_ws import MoomooWebSocketPush, validate_live_config
from scripts.market_observer_v1 import prepare_live_acceptance
from scripts.market_observer_v1.service import AuthorizationError, QuotaError
from scripts.market_observer_v1.store import Store
from scripts.market_observer_v1.watchlist import load_watchlist, requested_us_subscription_order


class FakeTokens:
    def access_token(self) -> str:
        return "synthetic-secret"


class FakeWebSocket:
    def __init__(self, *, quota_on: int | None = None, reject_auth: bool = False,
                 idless_subscription_ack: bool = False):
        self.sent: list[dict] = []
        self.responses: list[dict] = []
        self.quota_on = quota_on
        self.reject_auth = reject_auth
        self.idless_subscription_ack = idless_subscription_ack
        self.subscriptions = 0
        self.closed = False
        self.pings = 0

    def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        if message["action"] == "auth":
            self.responses.append({"id": message["id"], "code": 401} if self.reject_auth else
                                  {"id": message["id"], "session_id": "synthetic-session"})
        elif message["action"] == "subscribe":
            self.subscriptions += 1
            code = 4 if self.quota_on == self.subscriptions else 0
            self.responses.append(
                {"ret_code": code, "ret_msg": "synthetic", "data": {}}
                if self.idless_subscription_ack else {"id": message["id"], "code": code})
        elif message["action"] == "refresh":
            self.responses.append({"id": message["id"], "session_id": "synthetic-session"})
        elif message["action"] == "unsubscribe":
            self.responses.append({"id": message["id"], "code": 0})

    def recv(self) -> str:
        if not self.responses:
            raise RuntimeError("synthetic no response")
        return json.dumps(self.responses.pop(0))

    def settimeout(self, value: float) -> None:
        pass

    def ping(self) -> None:
        self.pings += 1

    def close(self) -> None:
        self.closed = True


def _live() -> dict:
    return {"provider": "MOOMOO_WEBAPI_WS",
            "endpoint": "wss://webapi-quote.moomoo.com/ws",
            "scope": "quote:read", "entitlement_state": "ACCOUNT_CONFIRMED",
            "entitlement_evidence": "private:synthetic", "subscription_key_budget": 2,
            "mapping_receipt_sha256": "synthetic-hash",
            "subscriptions": [
                {"provider_code": "US.NVDA", "instrument_id": "US.NVDA",
                 "market": "US", "kind": "QUOTE",
                 "verification_status": "VERIFIED_EXACT_PROVIDER_CODE_MARKET"},
                {"provider_code": "US.NVDA", "instrument_id": "US.NVDA",
                 "market": "US", "kind": "KLINE_1M",
                 "verification_status": "VERIFIED_EXACT_PROVIDER_CODE_MARKET"}]}


def test_scope_rejects_trade_or_write_even_when_quote_read_present() -> None:
    _check_scope({"scope": "quote:read", "token_type": "Bearer", "access_token": "synthetic"})
    _check_scope({"scope": "quote:read accid:synthetic-account", "token_type": "Bearer",
                  "access_token": "synthetic"})
    for scope in ("quote:read trade:write", "quote:read quote:write",
                  "quote:read trade:read", "trade:read", "quote:read account:read",
                  "quote:read trade:write accid:synthetic-account"):
        with pytest.raises(OAuthError, match="scope rejected"):
            _check_scope({"scope": scope, "token_type": "Bearer", "access_token": "synthetic"})
    with pytest.raises(OAuthError) as exc:
        _check_scope({"scope": "trade:write accid:private-id", "token_type": "Bearer",
                      "access_token": "synthetic"})
    assert "private-id" not in str(exc.value)


def test_live_config_requires_entitlement_budget_and_verified_mapping() -> None:
    validate_live_config(_live())
    for change in ({"entitlement_state": "UNVERIFIED"},
                   {"subscription_key_budget": 1},
                   {"scope": "trade:read"},
                   {"mapping_receipt_sha256": None}):
        with pytest.raises(ValueError):
            validate_live_config({**_live(), **change})
    bad = _live()
    bad["subscriptions"][0]["verification_status"] = "CANDIDATE"
    with pytest.raises(ValueError, match="exact official"):
        validate_live_config(bad)


def test_bounded_probe_has_exactly_one_key_and_checked_receipt(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"rows": [{"requested_symbol": "TQQQ",
                                           "provider_code": "US.TQQQ",
                                           "provider_market": "US",
                                           "identity_scope": "CURRENT_ONLY"}]}), encoding="utf-8")
    config = _live()
    config.update({"entitlement_state": "BOUNDED_PROBE", "entitlement_evidence": None,
                   "subscription_key_budget": 1,
                   "subscriptions": [{"requested_symbol": "TQQQ", "provider_code": "US.TQQQ",
                                      "instrument_id": "US.TQQQ", "market": "US", "kind": "QUOTE",
                                      "verification_status": "VERIFIED_EXACT_PROVIDER_CODE_MARKET"}],
                   "mapping_receipt_path": "mapping.json",
                   "mapping_receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    validate_live_config(config, repo_root=tmp_path)
    config["subscriptions"][0]["requested_symbol"] = "NOT_TQQQ"
    with pytest.raises(ValueError, match="does not match"):
        validate_live_config(config, repo_root=tmp_path)
    config["subscriptions"][0]["requested_symbol"] = "TQQQ"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash changed"):
        validate_live_config(config, repo_root=tmp_path)


def test_continuous_account_confirmed_requires_private_quota_receipt(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"rows": [{"requested_symbol": "NVDA",
                                            "provider_code": "US.NVDA",
                                            "provider_market": "US",
                                            "identity_scope": "CURRENT_ONLY"}]}), encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    quota = artifacts / "quota.json"
    quota.write_text(json.dumps({"quota_state": "ACCOUNT_REPORTED", "remaining": 2}),
                     encoding="utf-8")
    config = _live()
    config.update({"mapping_receipt_path": "mapping.json",
                   "mapping_receipt_sha256": hashlib.sha256(mapping.read_bytes()).hexdigest(),
                   "entitlement_evidence": "artifacts/quota.json"})
    for row in config["subscriptions"]:
        row["requested_symbol"] = "NVDA"
    validate_live_config(config, repo_root=tmp_path)
    quota.write_text(json.dumps({"quota_state": "UNKNOWN", "remaining": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="does not support"):
        validate_live_config(config, repo_root=tmp_path)


def test_authentication_precedes_subscription_and_receipt_excludes_token(tmp_path: Path) -> None:
    ws = FakeWebSocket()
    source = MoomooWebSocketPush(live=_live(), token_store=FakeTokens(),
                                 status_path=tmp_path / "state.json", ws_factory=lambda *a, **k: ws)
    source.connect()
    assert [m["action"] for m in ws.sent] == ["auth", "subscribe", "subscribe"]
    assert source.connection_state == "CONNECTED_DELAY_UNKNOWN"
    assert source._subscriptions == [{"provider_code": "US.NVDA", "kind": "QUOTE"},
                                     {"provider_code": "US.NVDA", "kind": "KLINE_1M"}]
    receipt = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "synthetic-secret" not in receipt and "last_price" not in receipt
    source.close()
    assert ws.closed
    assert source._release_state == "ACKNOWLEDGED"


def test_live_observed_idless_subscription_envelope_is_accepted(tmp_path: Path) -> None:
    ws = FakeWebSocket(idless_subscription_ack=True)
    source = MoomooWebSocketPush(live=_live(), token_store=FakeTokens(),
                                 status_path=tmp_path / "idless.json", ws_factory=lambda *a, **k: ws)
    source.connect()
    assert len(source._subscriptions) == 2
    receipt = json.loads((tmp_path / "idless.json").read_text(encoding="utf-8"))
    assert receipt["last_control_response"] == {"protocol": "IDLESS_RET_CODE", "code": 0}
    source.close()


def test_subscription_quota_stops_expansion_and_reports_skipped(tmp_path: Path) -> None:
    ws = FakeWebSocket(quota_on=2)
    source = MoomooWebSocketPush(live=_live(), token_store=FakeTokens(),
                                 status_path=tmp_path / "state.json", ws_factory=lambda *a, **k: ws)
    source.connect()
    assert ws.subscriptions == 2
    assert source._subscriptions == [{"provider_code": "US.NVDA", "kind": "QUOTE"}]
    assert source._skipped == [{"provider_code": "US.NVDA", "kind": "KLINE_1M",
                                "reason": "SERVER_QUOTA_EXCEEDED"}]
    source.close()
    denied = FakeWebSocket(quota_on=1)
    source = MoomooWebSocketPush(live=_live(), token_store=FakeTokens(),
                                 status_path=tmp_path / "denied.json", ws_factory=lambda *a, **k: denied)
    with pytest.raises(QuotaError):
        source.connect()
    assert denied.subscriptions == 1 and denied.closed


def test_auth_rejection_stops_without_subscribe(tmp_path: Path) -> None:
    ws = FakeWebSocket(reject_auth=True)
    source = MoomooWebSocketPush(live=_live(), token_store=FakeTokens(),
                                 status_path=tmp_path / "state.json", ws_factory=lambda *a, **k: ws)
    with pytest.raises(AuthorizationError):
        source.connect()
    assert [m["action"] for m in ws.sent] == ["auth"]
    assert ws.closed


def test_quote_time_market_closed_and_unbound_kline_are_not_live_labels(tmp_path: Path) -> None:
    ws = FakeWebSocket()
    source = MoomooWebSocketPush(live=_live(), token_store=FakeTokens(),
                                 status_path=tmp_path / "state.json", ws_factory=lambda *a, **k: ws)
    source.connect()
    source._enqueue_push({"type": "MARKET_STATE", "data": {"market": "US", "status": 6}})
    assert source.connection_state == "CONNECTED_MARKET_CLOSED"
    source._enqueue_push({"type": "MARKET_STATE", "data": {"market": "US", "status": 11}})
    # AFTER_HOURS_END says nothing about the separate overnight session.
    assert source.connection_state == "CONNECTED_DELAY_UNKNOWN"
    source._enqueue_push({"type": "MARKET_STATE", "data": {"market": "US", "status": 37}})
    assert source.connection_state == "CONNECTED_DELAY_UNKNOWN"
    source._enqueue_push({"type": "QUOTE", "symbol": "US.NVDA",
                          "data": {"data_time_ms": 1763154000000, "last_price": 100}})
    quote = source.next_message(1)
    assert quote["quote"]["provider_time"].startswith("2025-")
    assert quote["quote"]["delay_status"] == "UNKNOWN"
    assert quote["quote"]["market_session"] == "UNKNOWN"
    source._enqueue_push({"type": "KLINE", "symbol": "US.NVDA",
                          "data": {"period": "1m", "adjust": "none",
                                   "kl_list": [{"time_ms": 1763154000000,
                                                "close_price": 100}]}})
    kline = source.next_message(1)
    assert kline["quote"]["kind"] == "KLINE_1M_UNBOUND"
    assert kline["quote"]["payload"]["bar_boundary_status"] == "UNCONFIRMED_CURRENT_KLINE"
    source.close()


def test_quota_probe_requires_business_success_and_preserves_receipt(tmp_path: Path,
                                                                      monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return json.dumps({"ret_code": 0, "data": {"total": 5,
                                                        "used": 2, "remaining": 3}}).encode()

    count = {"calls": 0}

    def fake_urlopen(request, timeout):
        count["calls"] += 1
        assert request.full_url.endswith("/api/v1.0/quote/quotas/subscription")
        return Response()

    monkeypatch.setattr("scripts.market_observer_v1.moomoo_quota_probe.urlopen", fake_urlopen)
    path = tmp_path / "quota.json"
    receipt = probe_subscription_quota(FakeTokens(), path)
    assert receipt["quota_state"] == "ACCOUNT_REPORTED"
    assert (receipt["total"], receipt["used"], receipt["remaining"]) == (5, 2, 3)
    assert "synthetic-secret" not in path.read_text()
    with pytest.raises(FileExistsError):
        probe_subscription_quota(FakeTokens(), path)
    assert count["calls"] == 1


def test_quota_probe_does_not_turn_business_error_into_zero(tmp_path: Path, monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b'{"ret_code":-13,"data":null}'

    monkeypatch.setattr("scripts.market_observer_v1.moomoo_quota_probe.urlopen",
                        lambda *a, **k: Response())
    receipt = probe_subscription_quota(FakeTokens(), tmp_path / "quota.json")
    assert receipt["quota_state"] == "UNKNOWN"
    assert "remaining" not in receipt


def test_full_acceptance_selection_uses_priority_and_reports_unserved(tmp_path: Path,
                                                                        monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "configs").mkdir()
    (tmp_path / "results").mkdir()
    watchlist_path = repo / "configs/market_observer_watchlist_v2_20261115.json"
    (tmp_path / "configs" / watchlist_path.name).write_bytes(watchlist_path.read_bytes())
    template = json.loads((repo / "configs/market_observer_v1.live.template.json").read_text())
    template["db_path"] = "artifacts/test/observer.sqlite"
    template["watchlist_path"] = "configs/" + watchlist_path.name
    template_path = tmp_path / "configs/live.template.json"
    template_path.write_text(json.dumps(template))
    monkeypatch.setattr(prepare_live_acceptance, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(prepare_live_acceptance, "TEMPLATE", template_path)
    frozen_watchlist = load_watchlist(watchlist_path)
    requested = requested_us_subscription_order(frozen_watchlist)
    assert len(requested) == 19
    mapping = tmp_path / "results/combined.json"
    mapping.write_text(json.dumps({
        "schema": "market_observer_combined_current_mapping_v1",
        "watchlist_version": frozen_watchlist["version"],
        "watchlist_config_sha256": hashlib.sha256(watchlist_path.read_bytes()).hexdigest(),
        "rows": [{
            "security_id": security_id,
            "requested_symbol": frozen_watchlist["securities"][security_id]["requested_ticker"],
            "provider_code": item["provider_code"],
            "provider_market": item["provider_market"],
            "identity_scope": item["identity_scope"],
            "evidence_id": item["evidence_id"],
        } for security_id, item in frozen_watchlist["verified_current_mappings"].items()],
    }))
    db_path = tmp_path / template["db_path"]
    with Store(db_path) as store:
        for symbol in requested:
            store.upsert_instrument("US." + symbol, "SYNTHETIC", symbol,
                                    provider_code="US." + symbol, market="US",
                                    identity_status="VERIFIED")
    quota = tmp_path / "artifacts/quota.json"
    quota.write_text(json.dumps({"quota_state": "ACCOUNT_REPORTED", "remaining": 3}))
    output = tmp_path / "artifacts/full.json"
    result = prepare_live_acceptance.prepare(mapping_path=mapping, quota_path=quota,
                                             output_path=output)
    assert result["configured_count"] == 3 and result["unserved_count"] == 16
    assert result["configured_provider_codes"] == ["US." + symbol for symbol in requested[:3]]
    live = json.loads(output.read_text())["live_push"]
    assert all(x["reason"] == "ACCOUNT_REPORTED_REMAINING_CAP"
               for x in live["unserved_subscriptions"])
    with pytest.raises(FileExistsError):
        prepare_live_acceptance.prepare(mapping_path=mapping, quota_path=quota,
                                        output_path=output)
