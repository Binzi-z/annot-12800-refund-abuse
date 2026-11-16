"""Versioned, parameterized refund-abuse rules.

Two cash-out shapes are covered:

* ``same_order_multi_refund``   — one order refunded several times, total
  refunds exceeding the order amount (a paid order can only be refunded once).
* ``near_full_refund_open_order`` — refund(s) covering almost the whole order
  amount while the order is still open (legitimate full refunds close the
  order via the after-sales flow).

Every change to thresholds or semantics bumps :data:`RULE_VERSION`. Each
alert stores the version and the exact parameter snapshot that fired it, so
old alerts stay explainable after the rules move on.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from typing import Any

# Semantic version of the whole rule set.
#   major: alert semantics changed (rule removed/renamed/condition meaning)
#   minor: new rule / new field
#   patch: threshold default tuning only
RULE_VERSION = "1.0.0"


@dataclass(frozen=True)
class RuleConfig:
    """All tunable parameters. Code paths never hardcode a threshold."""

    # same_order_multi_refund
    multi_refund_min_count: int = 2
    multi_refund_over_ratio: float = 1.0  # total refund > 100% of order amount

    # near_full_refund_open_order
    near_full_ratio: float = 0.90  # >= 90% of order amount
    near_full_min_count: int = 1

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any] | None) -> "RuleConfig":
        if not overrides:
            return cls()
        allowed = set(asdict(cls()).keys())
        unknown = set(overrides) - allowed
        if unknown:
            raise ValueError(f"unknown rule parameter(s): {sorted(unknown)}")
        return cls(**{k: overrides[k] for k in overrides if k in allowed})


@dataclass(frozen=True)
class RuleSpec:
    name: str
    version: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)


def rule_specs(cfg: RuleConfig) -> list[RuleSpec]:
    snap = cfg.snapshot()
    return [
        RuleSpec(
            name="same_order_multi_refund",
            version=RULE_VERSION,
            description=(
                "same order refunded >= multi_refund_min_count times and "
                "total refunds exceed multi_refund_over_ratio of the order "
                "amount; every refund must be linked to a support ticket"
            ),
            params={
                "multi_refund_min_count": snap["multi_refund_min_count"],
                "multi_refund_over_ratio": snap["multi_refund_over_ratio"],
            },
        ),
        RuleSpec(
            name="near_full_refund_open_order",
            version=RULE_VERSION,
            description=(
                "refund(s) covering >= near_full_ratio of the order amount "
                "while the order is not in a closed/after-sales status; "
                "every refund must be linked to a native support ticket, "
                "so ticket-less ordinary refunds never alert"
            ),
            params={
                "near_full_ratio": snap["near_full_ratio"],
                "near_full_min_count": snap["near_full_min_count"],
            },
        ),
    ]


def load_rule_override(path: str) -> tuple[RuleConfig, dict[str, Any]]:
    """Load a rule parameter override file (JSON).

    The file may pin ``rule_version``; a mismatch with the running engine is
    reported so an old parameter file is never silently applied to new rules.
    Returns the config and the raw override document (for the audit trail).
    """
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError("rule override file must contain a JSON object")
    overrides = doc.get("params", doc)
    cfg = RuleConfig.from_overrides(overrides)
    meta = {
        "source": path,
        "declared_rule_version": doc.get("rule_version"),
        "engine_rule_version": RULE_VERSION,
    }
    return cfg, meta


def compatible_versions(frozen: str, current: str = RULE_VERSION) -> bool:
    """Same major version => the stored alert remains interpretable."""
    return frozen.split(".", 1)[0] == current.split(".", 1)[0]


def default_config() -> RuleConfig:
    return RuleConfig()
