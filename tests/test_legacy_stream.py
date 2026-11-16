"""Legacy six-field stream: derived pairing, report-only, zero false alerts."""

from __future__ import annotations

import json
import os

from refund_abuse.engine import detect_events, load_events
from refund_abuse.rules import RuleConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STREAM = os.path.join(ROOT, "data", "events.jsonl")


def test_legacy_stream_produces_no_alerts():
    # The stream has no native order/refund/ticket ids: any alert here
    # would be a false positive against normal refunds.
    events = load_events(STREAM)
    result = detect_events(events, RuleConfig(), source_name="events.jsonl")
    assert result["alerts"] == []
    assert result["data_support"]["native_order_refund_linkage"] is False
    assert result["data_support"]["alerts_enabled"] is False


def test_legacy_stream_gets_deterministic_derived_pairing():
    events = load_events(STREAM)
    result = detect_events(events)
    pairing = result["pairing"]
    assert pairing, "FIFO pairing should still be reported"
    for entry in pairing:
        assert entry["id_source"] == "derived"
        assert entry["order_id"].startswith("ORD-DER-")
        for rid in entry["refund_ids"]:
            assert rid.startswith("REF-DER-L")
        for tid in entry["ticket_ids"]:
            assert tid.startswith("TKT-DER-")

    # determinism: rerun yields identical ids
    rerun = detect_events(load_events(STREAM))
    assert [p["order_id"] for p in rerun["pairing"]] == [
        p["order_id"] for p in pairing
    ]


def test_legacy_refund_accounting_matches_stream():
    events = load_events(STREAM)
    result = detect_events(events)
    counts = result["data_support"]["counts"]
    assert counts["events"] == len(events)
    assert counts["pay_events"] == 769
    assert counts["refund_events"] == 772
    linked = sum(len(p["refund_ids"]) for p in result["pairing"])
    assert linked + counts["unlinked_refunds"] == 772


def test_tuning_thresholds_never_manufactures_alerts_without_native_ids():
    # Even maximally aggressive parameters must not fire on derived data.
    cfg = RuleConfig(
        multi_refund_min_count=1,
        multi_refund_over_ratio=0.0,
        near_full_ratio=0.0,
        near_full_min_count=1,
    )
    result = detect_events(load_events(STREAM), cfg)
    assert result["alerts"] == []
