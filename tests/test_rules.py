"""Rule-behavior tests on native (order/refund/ticket) events.

The fixture encodes the ground truth:

fraud (must alert, all of them):
  ORD-9001 same_order_multi_refund      500 order, two refunds 300+260
  ORD-9002 near_full_refund_open_order  800 order, 780 refund, still open
  ORD-9007 near_full_refund_open_order  300 order, 270 (90%) refund, open

benign (must never alert):
  ORD-9003 full refund, order closed (normal after-sales)
  ORD-9004 one 20% partial refund
  ORD-9005 two refunds but only 45% total
  ORD-9006 near-full refund but order closed
  ORD-9008 two refunds, 73.3% total, open but below near-full threshold
"""

from __future__ import annotations

import json
import os

from refund_abuse.engine import detect_events, explain_result, load_events
from refund_abuse.rules import RULE_VERSION, RuleConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "native_events.jsonl")

FRAUD_ORDERS = {"ORD-9001", "ORD-9002", "ORD-9007"}
BENIGN_ORDERS = {"ORD-9003", "ORD-9004", "ORD-9005", "ORD-9006", "ORD-9008"}


def run(cfg: RuleConfig | None = None):
    events = load_events(FIXTURE)
    return detect_events(events, cfg, source_name="native_events.jsonl")


def test_data_support_native():
    result = run()
    support = result["data_support"]
    assert support["native_order_refund_linkage"] is True
    assert support["alerts_enabled"] is True
    counts = support["counts"]
    assert counts["native_order_ids"] >= len(FRAUD_ORDERS | BENIGN_ORDERS)
    assert counts["native_refund_ids"] == 11
    assert counts["native_ticket_ids"] == 14  # 11 on refunds + 3 ticket events


def test_all_labeled_fraud_hit_no_misses():
    result = run()
    alerted_orders = {a["order_id"] for a in result["alerts"]}
    assert FRAUD_ORDERS <= alerted_orders, FRAUD_ORDERS - alerted_orders


def test_zero_false_positives_on_benign_refunds():
    result = run()
    for alert in result["alerts"]:
        assert alert["order_id"] not in BENIGN_ORDERS
    # exactly the three fraud orders, no more
    assert {a["order_id"] for a in result["alerts"]} == FRAUD_ORDERS


def test_rule_names_and_no_duplicate_firing():
    result = run()
    by_order: dict[str, list[str]] = {}
    for a in result["alerts"]:
        by_order.setdefault(a["order_id"], []).append(a["rule"])
    assert by_order["ORD-9001"] == ["same_order_multi_refund"]
    assert by_order["ORD-9002"] == ["near_full_refund_open_order"]
    assert by_order["ORD-9007"] == ["near_full_refund_open_order"]
    # each order fires at most once per rule
    for rules in by_order.values():
        assert len(rules) == len(set(rules))


def test_every_alert_carries_order_refund_and_ticket_ids():
    result = run()
    assert result["alerts"], "fixture should produce alerts"
    for alert in result["alerts"]:
        assert alert["order_id"]
        assert alert["refund_ids"], "refund serials required"
        assert alert["ticket_ids"], "ticket ids required"
        assert len(alert["refund_ids"]) == len(alert["ticket_ids"])
        # evidence window must contain the same refund/ticket linkage
        ev_refunds = alert["evidence"]["refunds"]
        assert [r["refund_id"] for r in ev_refunds] == alert["refund_ids"]
        assert all(r["ticket_id"] for r in ev_refunds)


def test_multi_refund_alert_details():
    result = run()
    alert = next(
        a for a in result["alerts"] if a["order_id"] == "ORD-9001"
    )
    assert alert["refund_ids"] == ["REF-01", "REF-02"]
    assert alert["ticket_ids"] == ["TKT-01", "TKT-02"]
    assert alert["metrics"]["refund_count"] == 2
    assert alert["metrics"]["refund_total"] == 560.0
    assert alert["metrics"]["order_amount"] == 500.0
    assert alert["metrics"]["refund_to_order_ratio"] == 1.12


def test_near_full_open_order_alert_details():
    result = run()
    alert = next(
        a for a in result["alerts"] if a["order_id"] == "ORD-9002"
    )
    assert alert["refund_ids"] == ["REF-03"]
    assert alert["ticket_ids"] == ["TKT-03"]
    assert alert["evidence"]["order_closed"] is False
    assert alert["metrics"]["refund_to_order_ratio"] == 0.975


def test_closed_orders_never_alert_even_when_fully_refunded():
    result = run()
    alerted = {a["order_id"] for a in result["alerts"]}
    assert "ORD-9003" not in alerted  # full refund, closed_refunded
    assert "ORD-9006" not in alerted  # 95% refund, closed


def test_partial_and_sub_threshold_refunds_never_alert():
    result = run()
    alerted = {a["order_id"] for a in result["alerts"]}
    assert "ORD-9004" not in alerted  # 20% single refund
    assert "ORD-9005" not in alerted  # two refunds, 45% total
    assert "ORD-9008" not in alerted  # two refunds, 73.3% total


def test_alerts_are_versioned_with_param_snapshot():
    result = run()
    for alert in result["alerts"]:
        assert alert["rule_version"] == RULE_VERSION
        assert alert["rule_params"], "frozen params required for replay"
        assert alert["evidence"]["order_id"] == alert["order_id"]


def test_explain_replays_old_alerts():
    result = run()
    report = explain_result(result)
    assert report["all_interpretable"] is True
    assert report["alert_count"] == len(result["alerts"])
    for explanation in report["explanations"]:
        assert explanation["has_window_evidence"] is True
        assert explanation["frozen_params"]
        assert explanation["order_id"] in FRAUD_ORDERS


def test_parameter_override_changes_detection_without_code_change():
    # Tightening near-full to 80% pulls the benign 73.3% order in? No —
    # 73.3% stays below. Instead verify ORD-9008 (73.3%) fires at 0.7.
    cfg = RuleConfig(near_full_ratio=0.7)
    result = run(cfg)
    alerted = {a["order_id"] for a in result["alerts"]}
    assert "ORD-9008" in alerted
    # the fired alert remembers the overridden threshold, not the default
    alert = next(
        a
        for a in result["alerts"]
        if a["order_id"] == "ORD-9008"
        and a["rule"] == "near_full_refund_open_order"
    )
    assert alert["rule_params"]["near_full_ratio"] == 0.7
    assert result["rule_config"]["near_full_ratio"] == 0.7


def test_old_alert_remains_explainable_after_threshold_change():
    old = run()  # produced under 1.0.0 defaults
    new = run(RuleConfig(near_full_ratio=0.7))
    # old document is untouched and still fully explainable
    report = explain_result(old)
    assert report["all_interpretable"] is True
    old_ids = {a["order_id"] for a in old["alerts"]}
    new_ids = {a["order_id"] for a in new["alerts"]}
    assert old_ids != new_ids  # rule change changes new output only
    assert old_ids == FRAUD_ORDERS  # historical alerts stay as they were


def test_unticketed_near_full_refund_does_not_alert():
    # A near-full refund without any native support ticket is
    # indistinguishable from an ordinary refund -> no alert.
    events = load_events(FIXTURE) + [
        {
            "ts": "2026-10-05T18:00:00",
            "account": "u2009",
            "device": "u2009-d1",
            "amount": 100.0,
            "action": "pay",
            "ip_region": "110000",
            "order_id": "ORD-9009",
            "order_status": "open",
        },
        {
            "ts": "2026-10-05T18:05:00",
            "account": "u2009",
            "device": "u2009-d1",
            "amount": 95.0,
            "action": "refund",
            "ip_region": "110000",
            "order_id": "ORD-9009",
            "refund_id": "REF-12",
            "order_status": "open",
        },
    ]
    result = detect_events(events)
    assert "ORD-9009" not in {a["order_id"] for a in result["alerts"]}
    # existing fraud coverage is unaffected
    assert {a["order_id"] for a in result["alerts"]} == FRAUD_ORDERS


def test_unknown_parameter_rejected():
    import pytest

    with pytest.raises(ValueError, match="unknown rule parameter"):
        RuleConfig.from_overrides({"no_such_threshold": 1})


def test_explain_flags_foreign_major_version():
    old = run()
    # Simulate a historical document produced by an older major rule line.
    for alert in old["alerts"]:
        alert["rule_version"] = "0.9.0"
    report = explain_result(old)
    assert report["all_interpretable"] is False
    assert all(e["interpretable"] is False for e in report["explanations"])
    # evidence is still carried, so a human can reconstruct what fired
    assert all(e["has_window_evidence"] for e in report["explanations"])


def test_output_is_deterministic():
    first = json.dumps(run(), sort_keys=True, ensure_ascii=False)
    second = json.dumps(run(), sort_keys=True, ensure_ascii=False)
    assert first == second
