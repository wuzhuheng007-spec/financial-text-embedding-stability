"""Synthetic collector check; no vendor or private account calls."""
import threading
from unittest.mock import Mock, patch

from scripts.quant_personal_feed import collector


def test_identical_pushes_do_not_repeat_quote_writes_but_new_source_time_does():
    stop = threading.Event()
    store = Mock()
    store.selection.return_value = (1, ["TQQQ"])
    store.put_quote.side_effect = [True, True]

    def quote(source_at, received_at):
        return {"kind": "QUOTE", "quote": {
            "instrument_id": "US.TQQQ", "provider_time": source_at,
            "received_at": received_at,
            "payload": {"price": "10.00", "provider_data": {"pre_market": {"price": "10.00"}}},
        }}

    events = [
        quote("2026-11-17T20:00:00Z", "2026-11-17T20:00:01Z"),
        quote("2026-11-17T20:00:00Z", "2026-11-17T20:00:02Z"),
        quote("2026-11-17T20:00:01Z", "2026-11-17T20:00:03Z"),
    ]
    adapter = Mock()
    adapter._ever_acknowledged = [{"provider_code": "US.TQQQ"}]
    adapter._skipped = []
    adapter._market_state = {"US": 2}
    adapter.connection_state = "CONNECTED_DELAY_UNKNOWN"

    def next_message(**_kwargs):
        if events:
            return events.pop(0)
        stop.set()
        return None

    adapter.next_message.side_effect = next_message
    live = {"subscriptions": [{"requested_symbol": "TQQQ", "provider_code": "US.TQQQ"}]}
    config = {"cache_db": "ignored", "moomoo": {"max_reconnect_attempts": 1,
              "priority_symbols": ["TQQQ"], "status_path": "ignored",
              "reconnect_initial_seconds": 1, "reconnect_max_seconds": 1},
              "retention": {"quotes_per_symbol": 10}}
    with patch.object(collector, "PersonalFeedStore", return_value=store), \
         patch.object(collector, "verified_subscriptions", return_value=(live, [], {})), \
         patch.object(collector, "MoomooWebSocketPush", return_value=adapter), \
         patch.object(collector, "token_store", return_value=Mock()), \
         patch.object(collector, "parse_push_children", return_value=[{
             "session": "PREMARKET", "source_at": None}]):
        collector.run_moomoo(config, 1, stop)

    assert store.put_quote.call_count == 2
    assert store.put_session_quotes.call_count == 2
    assert any(call.kwargs.get("duplicate_quote_packets") == 1
               for call in store.provider_metric.call_args_list)
    assert any(call.kwargs.get("source_at") == "2026-11-17T20:00:01Z"
               for call in store.state.call_args_list)
