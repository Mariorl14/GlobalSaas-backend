"""
Create or reset a superadmin user for /api/auth/signin (only superadmin can sign in).

Usage (from barber-backend, venv active):
  python scripts/create_superadmin_user.py

Optional env overrides:
  SUPERADMIN_EMAIL    default: admin@barber.local
  SUPERADMIN_PASSWORD default: BarberAdmin123!
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import User


def main() -> None:
    email = os.environ.get("SUPERADMIN_EMAIL", "admin@barber.local").strip()
    password = os.environ.get("SUPERADMIN_PASSWORD", "BarberAdmin123!")

    if not email or not password:
        print("SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD must be non-empty.", file=sys.stderr)
        sys.exit(1)

    app = create_app()
    with app.app_context():
        existing = User.query.filter_by(email=email).first()
        if existing:
            if existing.role != "superadmin":
                print(
                    f"Email {email!r} is already used by a {existing.role!r} user. "
                    "Pick another SUPERADMIN_EMAIL.",
                    file=sys.stderr,
                )
                sys.exit(1)
            existing.encrypted_password = generate_password_hash(password)
            existing.is_active = True
            db.session.commit()
            print(f"Updated password for existing superadmin: {email}")
        else:
            user = User(
                business_id=None,
                email=email,
                encrypted_password=generate_password_hash(password),
                role="superadmin",
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            print(f"Created superadmin: {email}")

        print(f"Password: {password}")
        print("Use these credentials in the app login (signin).")


if __name__ == "__main__":
    main()
