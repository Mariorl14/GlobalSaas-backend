"""Display-name helpers for users and staff."""

from __future__ import annotations


def user_full_name(user) -> str | None:
    if user is None:
        return None
    parts = [
        p
        for p in [
            (getattr(user, "first_name", None) or "").strip(),
            (getattr(user, "last_name", None) or "").strip(),
        ]
        if p
    ]
    return " ".join(parts) if parts else None


def staff_display_label(employee, user=None) -> str:
    """Prefer employee.display_name, then user first/last name, then email local-part."""
    if employee is not None:
        dn = (getattr(employee, "display_name", None) or "").strip()
        if dn:
            return dn
        user = user or getattr(employee, "user", None)
    full = user_full_name(user)
    if full:
        return full
    email = getattr(user, "email", None) if user is not None else None
    if email and "@" in email:
        return email.split("@")[0]
    return "Staff"
