"""Business logo upload API (shop + super admin)."""

from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.logo_storage import minimal_png_bytes
from app.models import Business, User
from tests.conftest import create_tenant_bundle


def _shop_auth(user: User, business_id) -> dict:
    token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "role": user.role,
            "business_id": str(business_id),
            "employee_id": None,
        },
    )
    return {"Authorization": f"Bearer {token}"}


def test_shop_logo_upload_and_delete(app, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"logo-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"logo-admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()
        headers = _shop_auth(admin, bundle["business"].id)
        bid = bundle["business"].id

        res = client.post(
            "/api/shop/settings/logo",
            data={"logo": (BytesIO(minimal_png_bytes()), "brand.png")},
            content_type="multipart/form-data",
            headers=headers,
        )
        assert res.status_code == 200, res.get_json()
        data = res.get_json()
        assert data["logo_url"]
        assert str(bid) in data["logo_url"]
        assert data["logo_url"].startswith("/uploads/logos/")

        disk = Path(tmp_path) / "logos" / str(bid)
        assert any(disk.glob("logo.*"))

        path = data["logo_url"].split("?", 1)[0]
        served = client.get(path)
        assert served.status_code == 200
        assert served.data[:8] == b"\x89PNG\r\n\x1a\n"

        deleted = client.delete("/api/shop/settings/logo", headers=headers)
        assert deleted.status_code == 200
        assert deleted.get_json()["logo_url"] is None
        assert Business.query.get(bid).logo_url is None


def test_sa_logo_upload(app, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"logo-sa-{uuid.uuid4().hex[:8]}")
        bid = bundle["business"].id

        res = client.post(
            f"/api/business/{bid}/logo",
            data={"logo": (BytesIO(minimal_png_bytes()), "mark.png")},
            content_type="multipart/form-data",
        )
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["logo_url"]
        assert Business.query.get(bid).logo_url is not None
