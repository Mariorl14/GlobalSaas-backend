"""
Seed realistic dummy analytics data for RPM Studio (development only).
Run: python scripts/seed_rpm_insights.py
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (
    Appointment,
    Business,
    Client,
    Employee,
    InventoryProduct,
    ServiceType,
    User,
)

BUSINESS_NAME = "RPM Studio"
SEED_TAG = "[seed-insights]"
RNG = random.Random(42)

EXTRA_SERVICES = [
    ("Fade moderno", "Fade con detalle", 5500, 40),
    ("Afeitado clásico", "Toalla caliente", 3500, 30),
    ("Diseño / line up", "Diseño lateral", 2500, 20),
]

EXTRA_INVENTORY = [
    ("Pomada matte", "Styling", 8500, 2800, 18, 8),
    ("Shampoo barba", "Cuidado", 4200, 1500, 6, 10),  # low stock
    ("Aceite de barba", "Cuidado", 5000, 1800, 0, 5),  # out
    ("Aftershave", "Cuidado", 3800, 1200, 25, 8),
    ("Cepillo fade", "Herramientas", 3200, 900, 12, 4),
]

CLIENT_NAMES = [
    ("Carlos", "Méndez"),
    ("Luis", "Vargas"),
    ("Diego", "Ramírez"),
    ("Andrés", "Soto"),
    ("Pedro", "Castillo"),
    ("Jorge", "Navarro"),
    ("Miguel", "Rojas"),
    ("Fernando", "Cruz"),
    ("Ricardo", "Vega"),
    ("Sebastián", "Morales"),
    ("Héctor", "Luna"),
    ("Ángel", "Paredes"),
    ("Iván", "Salas"),
    ("Mauricio", "Ortega"),
    ("Esteban", "Campos"),
    ("Óscar", "Delgado"),
    ("Bruno", "Silva"),
    ("Nicolás", "Herrera"),
    ("Gabriel", "Muñoz"),
    ("Tomás", "Reyes"),
    ("Mateo", "Aguilar"),
    ("Emilio", "Peña"),
    ("Rafael", "Guerrero"),
    ("Samuel", "Molina"),
]

STAFF = [
    ("sofia.staff.seed@rpm.local", "Sofía Ruiz"),
    ("marco.staff.seed@rpm.local", "Marco Díaz"),
]


def _ensure_services(bid: uuid.UUID) -> list[ServiceType]:
    existing = {s.name: s for s in ServiceType.query.filter_by(business_id=bid).all()}
    for name, desc, price, duration in EXTRA_SERVICES:
        if name in existing:
            continue
        s = ServiceType(
            id=uuid.uuid4(),
            business_id=bid,
            name=name,
            description=f"{SEED_TAG} {desc}",
            duration=duration,
            price=price,
            is_active=True,
        )
        db.session.add(s)
        existing[name] = s
    db.session.flush()
    return list(existing.values())


def _ensure_inventory(bid: uuid.UUID) -> None:
    existing = {p.name for p in InventoryProduct.query.filter_by(business_id=bid).all()}
    for name, category, price, cost, stock, min_stock in EXTRA_INVENTORY:
        if name in existing:
            continue
        db.session.add(
            InventoryProduct(
                id=uuid.uuid4(),
                business_id=bid,
                name=name,
                category=category,
                price=price,
                unit_cost=cost,
                supplier="Seed Supplier",
                stock=stock,
                min_stock=min_stock,
                is_active=True,
            )
        )


def _ensure_staff(bid: uuid.UUID) -> list[Employee]:
    staff = list(Employee.query.filter_by(business_id=bid, is_active=True).all())
    for email, display in STAFF:
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                id=uuid.uuid4(),
                business_id=bid,
                email=email,
                encrypted_password=generate_password_hash("SeedStaff123!"),
                role="employee",
                is_active=True,
            )
            db.session.add(user)
            db.session.flush()
        emp = Employee.query.filter_by(user_id=user.id).first()
        if not emp:
            emp = Employee(
                id=uuid.uuid4(),
                user_id=user.id,
                business_id=bid,
                display_name=display,
                phone=f"8888{RNG.randint(1000, 9999)}",
                is_active=True,
            )
            db.session.add(emp)
            db.session.flush()
            staff.append(emp)
        elif emp not in staff:
            staff.append(emp)
    return staff


def _ensure_clients(bid: uuid.UUID) -> list[Client]:
    clients = list(Client.query.filter_by(business_id=bid).all())
    existing_names = {(c.first_name, c.last_name) for c in clients}
    for i, (first, last) in enumerate(CLIENT_NAMES):
        if (first, last) in existing_names:
            continue
        c = Client(
            id=uuid.uuid4(),
            business_id=bid,
            first_name=first,
            last_name=last,
            phone=f"7000{1000 + i:04d}",
            email=f"{first.lower()}.{last.lower()}.seed@example.com",
            notes=SEED_TAG,
            appointments_amount=0,
        )
        db.session.add(c)
        clients.append(c)
    db.session.flush()
    return clients


def _clear_previous_seed_appointments(bid: uuid.UUID) -> int:
    q = Appointment.query.filter(
        Appointment.business_id == bid,
        Appointment.notes == SEED_TAG,
    )
    count = q.count()
    q.delete(synchronize_session=False)
    return count


def _build_appointments(
    bid: uuid.UUID,
    services: list[ServiceType],
    staff: list[Employee],
    clients: list[Client],
) -> int:
    now = datetime.utcnow()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    created = 0

    # Past ~70 days of completed / mix + some cancels/no-shows
    for day_offset in range(70, 0, -1):
        day = day0 - timedelta(days=day_offset)
        if day.weekday() == 6:  # Sunday quieter
            n = RNG.randint(0, 2)
        elif day.weekday() == 5:  # Saturday busy
            n = RNG.randint(5, 9)
        else:
            n = RNG.randint(3, 7)

        used_hours: set[int] = set()
        for _ in range(n):
            hour = RNG.choice([9, 10, 11, 12, 14, 15, 16, 17, 18])
            if hour in used_hours and len(used_hours) < 8:
                continue
            used_hours.add(hour)
            client = RNG.choice(clients)
            service = RNG.choice(services)
            employee = RNG.choice(staff)
            start = day.replace(hour=hour, minute=RNG.choice([0, 15, 30]))
            end = start + timedelta(minutes=int(service.duration or 30))

            roll = RNG.random()
            if roll < 0.78:
                status = "completed"
            elif roll < 0.88:
                status = "confirmed" if day_offset <= 1 else "canceled"
            elif roll < 0.94:
                status = "no_show"
            else:
                status = "canceled"

            # For past days force completed/canceled/no_show primarily
            if day_offset > 1 and status == "confirmed":
                status = "completed"

            db.session.add(
                Appointment(
                    id=uuid.uuid4(),
                    business_id=bid,
                    client_id=client.id,
                    service_type_id=service.id,
                    employee_id=employee.id,
                    client_name=f"{client.first_name} {client.last_name}",
                    client_email=client.email or f"{client.first_name.lower()}@example.com",
                    client_phone=client.phone,
                    start_time=start,
                    end_time=end,
                    status=status,
                    notes=SEED_TAG,
                )
            )
            created += 1

    # Upcoming scheduled / confirmed (today + next 10 days)
    for day_offset in range(0, 11):
        day = day0 + timedelta(days=day_offset)
        n = RNG.randint(2, 6) if day.weekday() != 6 else RNG.randint(0, 2)
        for i in range(n):
            hour = 9 + (i % 8)
            if hour == 13:
                hour = 14
            client = RNG.choice(clients)
            service = RNG.choice(services)
            employee = RNG.choice(staff)
            start = day.replace(hour=hour, minute=RNG.choice([0, 30]))
            if start < now and day_offset == 0:
                start = now + timedelta(hours=1 + i)
            end = start + timedelta(minutes=int(service.duration or 30))
            status = "confirmed" if RNG.random() < 0.6 else "scheduled"
            db.session.add(
                Appointment(
                    id=uuid.uuid4(),
                    business_id=bid,
                    client_id=client.id,
                    service_type_id=service.id,
                    employee_id=employee.id,
                    client_name=f"{client.first_name} {client.last_name}",
                    client_email=client.email or f"{client.first_name.lower()}@example.com",
                    client_phone=client.phone,
                    start_time=start,
                    end_time=end,
                    status=status,
                    notes=SEED_TAG,
                )
            )
            created += 1

    return created


def _refresh_client_counts(bid: uuid.UUID) -> None:
    clients = Client.query.filter_by(business_id=bid).all()
    for c in clients:
        c.appointments_amount = Appointment.query.filter_by(client_id=c.id).count()


def main() -> None:
    app = create_app()
    with app.app_context():
        business = Business.query.filter(Business.name.ilike(BUSINESS_NAME)).first()
        if not business:
            raise SystemExit(f'Business "{BUSINESS_NAME}" not found.')

        bid = business.id
        removed = _clear_previous_seed_appointments(bid)
        services = _ensure_services(bid)
        _ensure_inventory(bid)
        staff = _ensure_staff(bid)
        clients = _ensure_clients(bid)
        created = _build_appointments(bid, services, staff, clients)
        _refresh_client_counts(bid)

        # Nice goals for Insights demo
        business.insights_goals_json = (
            '{"monthly_revenue": 350000, "monthly_appointments": 180, '
            '"monthly_product_sales": 50000, "monthly_new_customers": 25}'
        )

        db.session.commit()
        print(
            f"Seeded Insights data for {business.name}: "
            f"removed {removed} old seed appts, created {created} appointments, "
            f"{len(services)} services, {len(staff)} staff, {len(clients)} clients."
        )


if __name__ == "__main__":
    main()
