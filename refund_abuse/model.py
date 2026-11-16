"""Pairing model: events -> ticket-linked orders -> rule evaluation.

Two pairing modes coexist:

* **native mode** — events carry ``order_id``/``refund_id``/``ticket_id``
  (optionally ``order_status``). Refunds are grouped onto their real orders
  and linked to real customer-service tickets. This is the only mode in
  which cash-out alerts can fire.
* **derived mode** — the legacy six-field stream. Orders are reconstructed
  FIFO per (account, amount) and every refund gets a deterministic, clearly
  namespaced pseudo-id. Because the reconstruction has no ground truth, the
  rules never fire on derived objects (that would manufacture false
  positives); the pairing is still exposed so analysts can inspect it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from refund_abuse.ids import (
    CLOSED_STATUSES,
    TICKET_ACTIONS,
    derived_order_id,
    derived_refund_id,
    derived_ticket_id,
    native_order_id,
    native_refund_id,
    native_status,
    native_ticket_id,
)
from refund_abuse.rules import RuleConfig, RuleSpec, rule_specs


def _money(value: Any) -> Decimal | None:
    """Parse amounts as Decimal so ratio math never floats off a cent."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class RefundRecord:
    refund_id: str
    id_source: str  # "native" | "derived"
    ts: str
    amount: Decimal | None
    account: str
    line: int
    ticket_id: str | None = None
    ticket_source: str = "derived"  # "native" | "derived"
    order_id: str | None = None


@dataclass
class Order:
    order_id: str
    id_source: str  # "native" | "derived"
    account: str
    amount: Decimal | None
    opened_ts: str | None
    line: int
    status: str | None = None  # native status only; None = unknown/open
    refunds: list[RefundRecord] = field(default_factory=list)
    last_event_ts: str | None = None

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def refund_total(self) -> Decimal:
        return sum(
            (r.amount for r in self.refunds if r.amount is not None),
            Decimal("0"),
        )

    def native_ticket_ids(self) -> list[str]:
        return [
            r.ticket_id
            for r in self.refunds
            if r.ticket_id and r.ticket_source == "native"
        ]


@dataclass
class SupportStats:
    total_events: int = 0
    refund_events: int = 0
    pay_events: int = 0
    ticket_events: int = 0
    native_order_ids: int = 0
    native_refund_ids: int = 0
    native_ticket_ids: int = 0
    native_statuses: int = 0
    orders: int = 0
    native_orders: int = 0
    derived_orders: int = 0
    unlinked_refunds: int = 0  # refunds with no matching order at all

    @property
    def native_support(self) -> bool:
        """Cash-out rules only run when real order linkage exists."""
        return self.native_order_ids > 0 and self.native_refund_ids > 0


class Ledger:
    """Builds orders/refunds from an ordered event stream."""

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}
        self.unlinked_refunds: list[RefundRecord] = []
        self._open_pay_queues: dict[tuple[str, Decimal | None], deque[Order]] = {}
        self._pay_seq: dict[str, int] = {}
        self._pending_tickets: deque[tuple[str, str]] = deque()
        self.stats = SupportStats()

    # ------------------------------------------------------------------ ingest
    def ingest(self, events: list[dict[str, Any]]) -> None:
        for line, raw in enumerate(events, start=1):
            self.stats.total_events += 1
            action = str(raw.get("action", "")).lower()
            account = str(raw.get("account", ""))
            ts = str(raw.get("ts", ""))
            amount = _money(raw.get("amount"))

            if action == "pay":
                self.stats.pay_events += 1
                self._ingest_pay(raw, account, ts, amount, line)
            elif action == "refund":
                self.stats.refund_events += 1
                self._ingest_refund(raw, account, ts, amount, line)
            elif action in TICKET_ACTIONS:
                self.stats.ticket_events += 1
                tid = native_ticket_id(raw)
                if tid:
                    # Counts every event carrying a native ticket id
                    # (ticket events themselves; refunds referencing a
                    # ticket are counted on the refund path).
                    self.stats.native_ticket_ids += 1
                    self._pending_tickets.append((account, tid))

    def _ingest_pay(
        self,
        raw: dict[str, Any],
        account: str,
        ts: str,
        amount: Decimal | None,
        line: int,
    ) -> None:
        oid = native_order_id(raw)
        if oid is not None:
            self.stats.native_order_ids += 1
            order = self.orders.get(oid)
            if order is None:
                order = Order(
                    order_id=oid,
                    id_source="native",
                    account=account,
                    amount=amount,
                    opened_ts=ts,
                    line=line,
                )
                self.orders[oid] = order
            else:
                if order.amount is None and amount is not None:
                    order.amount = amount
                if order.opened_ts is None:
                    order.opened_ts = ts
            status = native_status(raw)
            if status is not None:
                self.stats.native_statuses += 1
                order.status = status
            order.last_event_ts = ts
            return

        # Derived: each pay is its own order, FIFO-claimable by refunds.
        seq = self._pay_seq.get(account, 0) + 1
        self._pay_seq[account] = seq
        oid = derived_order_id(account, amount, seq)
        order = Order(
            order_id=oid,
            id_source="derived",
            account=account,
            amount=amount,
            opened_ts=ts,
            line=line,
            last_event_ts=ts,
        )
        self.orders[oid] = order
        key = (account, amount)
        self._open_pay_queues.setdefault(key, deque()).append(order)

    def _ingest_refund(
        self,
        raw: dict[str, Any],
        account: str,
        ts: str,
        amount: Decimal | None,
        line: int,
    ) -> None:
        rid = native_refund_id(raw)
        if rid is not None:
            self.stats.native_refund_ids += 1
            record = RefundRecord(
                refund_id=rid,
                id_source="native",
                ts=ts,
                amount=amount,
                account=account,
                line=line,
            )
            oid = native_order_id(raw)
            if oid is None:
                # Refund without explicit order linkage stays unlinked; it
                # cannot be evaluated against any order.
                self.unlinked_refunds.append(record)
                self.stats.unlinked_refunds += 1
                return
            order = self.orders.get(oid)
            if order is None:
                order = Order(
                    order_id=oid,
                    id_source="native",
                    account=account,
                    amount=None,
                    opened_ts=None,
                    line=line,
                )
                self.orders[oid] = order
            status = native_status(raw)
            if status is not None:
                self.stats.native_statuses += 1
                order.status = status
            record.order_id = oid
            ticket = native_ticket_id(raw)
            if ticket is not None:
                self.stats.native_ticket_ids += 1
                record.ticket_id = ticket
                record.ticket_source = "native"
            else:
                record.ticket_id = self._claim_pending_ticket(account, raw)
            if record.ticket_id is None:
                record.ticket_id = derived_ticket_id(rid)
                record.ticket_source = "derived"
            order.refunds.append(record)
            order.last_event_ts = ts
            return

        # Derived: FIFO-match to the account's earliest open pay of the
        # same amount; when none remains, attach to the most recent one
        # (this is exactly the "one order, several refunds" shape, kept
        # visible but never alerted without native ids).
        record = RefundRecord(
            refund_id=derived_refund_id(line),
            id_source="derived",
            ts=ts,
            amount=amount,
            account=account,
            line=line,
        )
        key = (account, amount)
        queue = self._open_pay_queues.get(key)
        order: Order | None = None
        if queue:
            order = queue.popleft()
        else:
            same_key = [
                o
                for o in self.orders.values()
                if o.id_source == "derived"
                and o.account == account
                and o.amount == amount
            ]
            if same_key:
                order = same_key[-1]
            else:
                any_order = [
                    o
                    for o in self.orders.values()
                    if o.id_source == "derived" and o.account == account
                ]
                if any_order:
                    order = any_order[-1]
        if order is None:
            self.unlinked_refunds.append(record)
            self.stats.unlinked_refunds += 1
            return
        record.order_id = order.order_id
        record.ticket_id = derived_ticket_id(record.refund_id)
        record.ticket_source = "derived"
        order.refunds.append(record)
        order.last_event_ts = ts

    def _claim_pending_ticket(
        self, account: str, raw: dict[str, Any]
    ) -> str | None:
        explicit = native_ticket_id(raw)
        if explicit is not None:
            return explicit
        # Consume the account's earliest ticket event seen before this
        # refund; stale tickets for other accounts are left untouched.
        stale: deque[tuple[str, str]] = deque()
        found: str | None = None
        while self._pending_tickets:
            acc, tid = self._pending_tickets.popleft()
            if found is None and acc == account:
                found = tid
                break
            stale.append((acc, tid))
        self._pending_tickets.extendleft(reversed(stale))
        return found

    # ------------------------------------------------------------- bookkeeping
    def finalize_stats(self) -> SupportStats:
        stats = self.stats
        stats.orders = len(self.orders)
        stats.native_orders = sum(
            1 for o in self.orders.values() if o.id_source == "native"
        )
        stats.derived_orders = stats.orders - stats.native_orders
        return stats


# ---------------------------------------------------------------------- alerts
def _refund_payload(r: RefundRecord) -> dict[str, Any]:
    return {
        "refund_id": r.refund_id,
        "id_source": r.id_source,
        "ticket_id": r.ticket_id,
        "ticket_source": r.ticket_source,
        "ts": r.ts,
        "amount": None if r.amount is None else float(r.amount),
        "line": r.line,
    }


def _order_evidence(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "order_id_source": order.id_source,
        "account": order.account,
        "order_amount": None if order.amount is None else float(order.amount),
        "order_status": order.status,
        "order_closed": order.is_closed,
        "opened_ts": order.opened_ts,
        "last_event_ts": order.last_event_ts,
        "refund_count": len(order.refunds),
        "refund_total": float(order.refund_total),
        "refunds": [_refund_payload(r) for r in order.refunds],
    }


def _make_alert(
    rule: RuleSpec,
    order: Order,
    reason: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rule": rule.name,
        "rule_version": rule.version,
        "rule_params": copy_snapshot(rule.params),
        "account": order.account,
        "order_id": order.order_id,
        "order_id_source": order.id_source,
        "refund_ids": [r.refund_id for r in order.refunds],
        "ticket_ids": [
            r.ticket_id for r in order.refunds if r.ticket_id is not None
        ],
        "reason": reason,
        "metrics": metrics,
        "evidence": _order_evidence(order),
    }


def copy_snapshot(params: dict[str, Any]) -> dict[str, Any]:
    return dict(params)


def evaluate(ledger: Ledger, cfg: RuleConfig) -> list[dict[str, Any]]:
    """Run both rules over every *native* order.

    Derived orders are excluded by construction: without real order/refund
    serials there is no ground truth, so firing would create false positives
    against normal refunds (requirement: zero false positives).
    """
    specs = {s.name: s for s in rule_specs(cfg)}
    alerts: list[dict[str, Any]] = []
    for order in ledger.orders.values():
        if order.id_source != "native":
            continue
        if order.amount is None or order.amount <= 0:
            continue  # cannot compute amount-based ratios safely

        refund_total = order.refund_total
        ratio = (refund_total / order.amount) if order.amount else Decimal("0")
        n_refunds = len(order.refunds)
        all_ticketed = all(
            r.ticket_id and r.ticket_source == "native" for r in order.refunds
        )

        # Rule 1: same order, multiple refunds, money out exceeds the order.
        if (
            n_refunds >= cfg.multi_refund_min_count
            and ratio > Decimal(str(cfg.multi_refund_over_ratio))
            and all_ticketed
        ):
            alerts.append(
                _make_alert(
                    specs["same_order_multi_refund"],
                    order,
                    "one order received multiple ticket-linked refunds whose "
                    "total exceeds the order amount",
                    {
                        "refund_count": n_refunds,
                        "refund_total": float(refund_total),
                        "order_amount": float(order.amount),
                        "refund_to_order_ratio": float(ratio),
                        "over_ratio_threshold": cfg.multi_refund_over_ratio,
                    },
                )
            )

        # Rule 2: near-full refund while the order was never closed.
        # Closed/after-sales orders are exempt — that is the normal flow.
        # A native ticket linkage is required: refunds without any support
        # ticket cannot be distinguished from ordinary refunds, so they
        # never alert (zero-false-positive guard).
        if (
            not order.is_closed
            and n_refunds >= cfg.near_full_min_count
            and all_ticketed
            and ratio >= Decimal(str(cfg.near_full_ratio))
            and not (
                ratio > Decimal(str(cfg.multi_refund_over_ratio))
                and n_refunds >= cfg.multi_refund_min_count
                and all_ticketed
            )
        ):
            alerts.append(
                _make_alert(
                    specs["near_full_refund_open_order"],
                    order,
                    "refund(s) cover nearly the full order amount but the "
                    "order remains open (no after-sales closure)",
                    {
                        "refund_count": n_refunds,
                        "refund_total": float(refund_total),
                        "order_amount": float(order.amount),
                        "refund_to_order_ratio": float(ratio),
                        "near_full_threshold": cfg.near_full_ratio,
                    },
                )
            )
    alerts.sort(key=lambda a: (a["order_id"], a["rule"]))
    return alerts
