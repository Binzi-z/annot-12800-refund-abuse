"""Refund-abuse detection engine.

Rules live in :mod:`refund_abuse.rules` (versioned, parameterized), pairing
logic in :mod:`refund_abuse.model`, deterministic fallback identifiers in
:mod:`refund_abuse.ids`. The event file format is never modified and native
fields are always optional.
"""

from refund_abuse.engine import detect_events, explain_result, load_events
from refund_abuse.rules import RULE_VERSION, load_rule_override

__all__ = [
    "detect_events",
    "explain_result",
    "load_events",
    "load_rule_override",
    "RULE_VERSION",
]
