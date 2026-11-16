"""Field-name aliases and deterministic fallback identifiers.

The production event stream (``data/events.jsonl``) only carries
``ts/account/device/amount/action/ip_region``. Upstream systems *may* add the
fields below without changing the existing format; when they are absent the
engine derives stable, clearly-namespaced identifiers so that every internal
object still has an identity. Derived identifiers are never presented as
upstream serial numbers.
"""

from __future__ import annotations

from typing import Any

# Accepted native field names (first match wins).
ORDER_ID_KEYS = ("order_id", "order_no", "orderId", "order_serial")
REFUND_ID_KEYS = (
    "refund_id",
    "refund_no",
    "refundId",
    "refund_serial",
    "serial_no",
)
TICKET_ID_KEYS = (
    "ticket_id",
    "ticket_no",
    "ticketId",
    "work_order_id",
    "work_order_no",
)
ORDER_STATUS_KEYS = ("order_status", "status")

# Actions that open/describe a customer-service ticket.
TICKET_ACTIONS = frozenset(
    {"ticket", "work_order", "service_ticket", "customer_service", "cs_ticket"}
)

# Statuses under which an order is considered settled, i.e. a full refund on
# it is the normal after-sales flow and must not alert.
CLOSED_STATUSES = frozenset(
    {
        "closed",
        "finished",
        "completed",
        "refunded",
        "closed_refunded",
        "cancelled",
        "canceled",
    }
)


def first_present(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return None


def native_order_id(raw: dict[str, Any]) -> str | None:
    value = first_present(raw, ORDER_ID_KEYS)
    return None if value is None else str(value)


def native_refund_id(raw: dict[str, Any]) -> str | None:
    value = first_present(raw, REFUND_ID_KEYS)
    return None if value is None else str(value)


def native_ticket_id(raw: dict[str, Any]) -> str | None:
    value = first_present(raw, TICKET_ID_KEYS)
    return None if value is None else str(value)


def native_status(raw: dict[str, Any]) -> str | None:
    value = first_present(raw, ORDER_STATUS_KEYS)
    return None if value is None else str(value).strip().lower()


def _fmt_amount(amount: float) -> str:
    return f"{float(amount):.2f}".rstrip("0").rstrip(".")


def derived_order_id(account: str, amount: float | None, seq: int) -> str:
    """Stable id for an order reconstructed without a native order id."""
    amt = "NA" if amount is None else _fmt_amount(amount)
    return f"ORD-DER-{account}-{amt}-{seq:03d}"


def derived_refund_id(line: int) -> str:
    """A refund's stable identity is its line number in the event file."""
    return f"REF-DER-L{line}"


def derived_ticket_id(refund_id: str) -> str:
    return f"TKT-DER-{refund_id}"
