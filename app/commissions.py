"""Service commission helpers. Product sales are never commissioned."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_STAFF_COMMISSION = Decimal("50")
OWNER_ADMIN_COMMISSION = Decimal("0")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_CENT = Decimal("0.01")


class CommissionError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def default_commission_for_role(role: str | None) -> Decimal:
    """Owners/admins keep 100% unless they later set a percentage. Barbers default to 50%."""
    if (role or "").strip().lower() in {"owner", "admin"}:
        return OWNER_ADMIN_COMMISSION
    return DEFAULT_STAFF_COMMISSION


def parse_commission_percentage(value: Any) -> Decimal:
    if value is None or (isinstance(value, str) and not str(value).strip()):
        raise CommissionError("La comisión es obligatoria.")
    try:
        pct = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CommissionError("Comisión inválida. Usa un número entre 0 y 100.") from exc
    if pct < _ZERO or pct > _HUNDRED:
        raise CommissionError("La comisión debe estar entre 0% y 100%.")
    return pct.quantize(_CENT)


def split_service_line(
    line_total: Decimal, percentage: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (percentage, staff_earnings, business_earnings) from the final service line."""
    pct = parse_commission_percentage(percentage)
    base = (line_total or _ZERO).quantize(_CENT)
    if base < _ZERO:
        base = _ZERO
    staff = (base * pct / _HUNDRED).quantize(_CENT)
    if staff > base:
        staff = base
    business = (base - staff).quantize(_CENT)
    return pct, staff, business
