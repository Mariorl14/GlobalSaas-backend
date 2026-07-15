import re
import uuid

from app.models import Business


def slugify_base(name: str, max_len: int = 80) -> str:
    if not name or not str(name).strip():
        return "barberia"
    s = str(name).lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-") or "barberia"
    return s[:max_len]


def public_slug_for_business(name: str, business_id: uuid.UUID) -> str:
    base = slugify_base(name)
    suffix = str(business_id).replace("-", "").replace("{", "").replace("}", "")[:8]
    return f"{base}-{suffix}"[:120]


def regenerate_public_slug(name: str, exclude_business_id: uuid.UUID | None = None) -> str:
    base = slugify_base(name, max_len=70)
    for _ in range(80):
        suffix = str(uuid.uuid4()).replace("-", "")[:10]
        cand = f"{base}-{suffix}"[:120]
        q = Business.query.filter_by(public_slug=cand)
        if exclude_business_id is not None:
            q = q.filter(Business.id != exclude_business_id)
        if not q.first():
            return cand
    return f"{base}-{str(uuid.uuid4()).replace('-', '')}"[:120]


def is_valid_public_slug(slug: str) -> bool:
    if not slug or len(slug) > 120:
        return False
    return bool(re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug))
