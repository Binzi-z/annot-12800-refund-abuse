"""Command-line entry point.

Backward-compatible usage::

    python engine.py data/events.jsonl alerts.json

Extra::

    python engine.py data/events.jsonl alerts.json --rules rules.json
    python engine.py --explain alerts.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow `python -I engine.py ...`: isolated mode may omit the script
# directory from sys.path in some Python builds.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from refund_abuse.engine import detect_events, explain_result, load_events
from refund_abuse.rules import RuleConfig, load_rule_override


def _write(result: dict, out: str) -> None:
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refund-abuse detection engine")
    parser.add_argument("src", nargs="?", help="JSONL event stream")
    parser.add_argument("out", nargs="?", default="alerts.json", help="output JSON")
    parser.add_argument(
        "--rules",
        help="rule parameter override JSON (no code release needed to tune)",
    )
    parser.add_argument(
        "--explain",
        metavar="ALERTS_JSON",
        help="explain an existing (possibly old) alert file against the "
        "current rule version and exit",
    )
    args = parser.parse_args(argv)

    if args.explain:
        with open(args.explain, encoding="utf-8") as fh:
            old = json.load(fh)
        report = explain_result(old)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not args.src:
        parser.error("an event stream path is required unless --explain is used")

    cfg = RuleConfig()
    rule_meta = None
    if args.rules:
        cfg, rule_meta = load_rule_override(args.rules)

    events = load_events(args.src)
    result = detect_events(
        events, cfg, rule_meta=rule_meta, source_name=args.src
    )
    _write(result, args.out)

    support = result["data_support"]
    print(
        f"{len(result['alerts'])} alerts -> {args.out} "
        f"(rule_version={result['engine_rule_version']}, "
        f"native_linkage={support['native_order_refund_linkage']}, "
        f"orders={support['counts']['orders_total']})"
    )
    if not support["native_order_refund_linkage"]:
        print(
            "NOTE: stream lacks native order/refund serials; pairing is "
            "derived FIFO and report-only, so no alerts are raised by design."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
