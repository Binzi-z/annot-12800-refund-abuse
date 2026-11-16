"""Orchestration: load events, run the ledger + rules, explain old alerts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from refund_abuse.model import Ledger, evaluate
from refund_abuse.rules import (
    RULE_VERSION,
    RuleConfig,
    compatible_versions,
    rule_specs,
)


def load_events(path: str) -> list[dict[str, Any]]:
    """Read the JSONL event stream without touching its format on disk."""
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_no}: {exc}") from exc
    return events


def _source_fingerprint(events: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for e in events:
        h.update(json.dumps(e, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def detect_events(
    events: list[dict[str, Any]],
    cfg: RuleConfig | None = None,
    *,
    rule_meta: dict[str, Any] | None = None,
    source_name: str = "events.jsonl",
) -> dict[str, Any]:
    """Full detection pass. Deterministic: same input -> identical output."""
    cfg = cfg or RuleConfig()
    ledger = Ledger()
    ledger.ingest(events)
    stats = ledger.finalize_stats()
    alerts = evaluate(ledger, cfg)

    pairing: list[dict[str, Any]] = []
    for order in sorted(ledger.orders.values(), key=lambda o: o.order_id):
        pairing.append(
            {
                "order_id": order.order_id,
                "id_source": order.id_source,
                "account": order.account,
                "order_amount": (
                    None if order.amount is None else float(order.amount)
                ),
                "status": order.status,
                "refund_ids": [r.refund_id for r in order.refunds],
                "ticket_ids": [
                    r.ticket_id
                    for r in order.refunds
                    if r.ticket_id is not None
                ],
            }
        )

    return {
        "schema_version": "1.0",
        "engine_rule_version": RULE_VERSION,
        "rule_config": cfg.snapshot(),
        "rule_meta": rule_meta or {},
        "rule_specs": [
            {
                "name": s.name,
                "version": s.version,
                "description": s.description,
                "params": s.params,
            }
            for s in rule_specs(cfg)
        ],
        "data_support": {
            "native_order_refund_linkage": stats.native_support,
            "alerts_enabled": stats.native_support,
            "note": (
                "alerts require native order_id/refund_id fields; derived "
                "FIFO pairing on the legacy stream is report-only to avoid "
                "false positives on legitimate refunds"
            )
            if not stats.native_support
            else "native order/refund identifiers present; rules active",
            "counts": {
                "events": stats.total_events,
                "pay_events": stats.pay_events,
                "refund_events": stats.refund_events,
                "ticket_events": stats.ticket_events,
                "native_order_ids": stats.native_order_ids,
                "native_refund_ids": stats.native_refund_ids,
                "native_ticket_ids": stats.native_ticket_ids,
                "native_statuses": stats.native_statuses,
                "orders_total": stats.orders,
                "orders_native": stats.native_orders,
                "orders_derived": stats.derived_orders,
                "unlinked_refunds": stats.unlinked_refunds,
            },
        },
        "source": {
            "name": source_name,
            "event_count": len(events),
            "sha256": _source_fingerprint(events),
        },
        "alerts": alerts,
        "pairing": pairing,
    }


def explain_result(result: dict[str, Any]) -> dict[str, Any]:
    """Explain a (possibly old) result document against the current engine.

    Each alert carries its own frozen version/params/evidence; this verifies
    whether the running engine can still interpret it and replays the
    triggering metrics.
    """
    current = RULE_VERSION
    explanations = []
    for alert in result.get("alerts", []):
        frozen = alert.get("rule_version", "unknown")
        explanations.append(
            {
                "rule": alert.get("rule"),
                "frozen_rule_version": frozen,
                "current_rule_version": current,
                "interpretable": (
                    frozen == "unknown"
                    or compatible_versions(frozen, current)
                ),
                "order_id": alert.get("order_id"),
                "refund_ids": alert.get("refund_ids", []),
                "ticket_ids": alert.get("ticket_ids", []),
                "frozen_params": alert.get("rule_params", {}),
                "reason": alert.get("reason"),
                "metrics": alert.get("metrics", {}),
                "has_window_evidence": bool(alert.get("evidence")),
            }
        )
    return {
        "engine_rule_version": current,
        "result_rule_version": result.get("engine_rule_version"),
        "alert_count": len(explanations),
        "all_interpretable": all(e["interpretable"] for e in explanations),
        "explanations": explanations,
    }
