"""Inventory item kinds: retail merchandise vs operating supplies."""

from __future__ import annotations

ITEM_KIND_RETAIL = "RETAIL_PRODUCT"
ITEM_KIND_SUPPLY = "OPERATING_SUPPLY"
ITEM_KIND_UNCLASSIFIED = "UNCLASSIFIED"

ITEM_KINDS = frozenset(
    {ITEM_KIND_RETAIL, ITEM_KIND_SUPPLY, ITEM_KIND_UNCLASSIFIED}
)

# Products that may be sold through POS / sale movements.
SELLABLE_KINDS = frozenset({ITEM_KIND_RETAIL})

# Contribute to "potential inventory sales value".
POTENTIAL_SALES_KINDS = frozenset({ITEM_KIND_RETAIL})


def normalize_item_kind(raw) -> str | None:
    if raw is None:
        return None
    kind = str(raw).strip().upper()
    if kind not in ITEM_KINDS:
        return None
    return kind


def is_sellable_kind(kind: str | None) -> bool:
    return (kind or ITEM_KIND_UNCLASSIFIED) in SELLABLE_KINDS


def counts_toward_potential_sales(kind: str | None) -> bool:
    return (kind or ITEM_KIND_UNCLASSIFIED) in POTENTIAL_SALES_KINDS


def is_operating_supply(kind: str | None) -> bool:
    return (kind or "") == ITEM_KIND_SUPPLY
