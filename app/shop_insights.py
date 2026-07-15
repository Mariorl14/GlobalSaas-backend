"""
Shop Business Insights — derived analytics from existing tenant data.

Service revenue prefers POS SaleItem rows (including tickets created when an
appointment is marked completed). Completed appointments without a linked sale
still contribute an estimate from the current ServiceType.price.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    Appointment,
    Business,
    Client,
    Employee,
    InventoryProduct,
    ServiceType,
)
from app.models.inventory_movement import InventoryMovement
from app.models.sale import Sale, SaleItem
from app.shop_sales import appointment_sale_idempotency_key, is_appointment_sale_key

COMPLETED = "completed"
NON_REVENUE = frozenset({"canceled", "cancelled", "no_show"})

DEFAULT_GOALS = {
    "monthly_revenue": 50000,
    "monthly_appointments": 200,
    "monthly_product_sales": 0,
    "monthly_new_customers": 30,
}


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _pct(part: float, whole: float) -> float | None:
    if whole <= 0:
        return None
    return round((part / whole) * 100, 1)


def _delta_pct(current: float, previous: float) -> float | None:
    if previous == 0:
        return 100.0 if current > 0 else (0.0 if current == 0 else None)
    return round(((current - previous) / abs(previous)) * 100, 1)


def parse_goals(raw: str | None) -> dict:
    goals = dict(DEFAULT_GOALS)
    if not raw:
        return goals
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            for key in DEFAULT_GOALS:
                if key in data and data[key] is not None:
                    goals[key] = max(0, float(data[key]))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return goals


def serialize_goals(goals: dict) -> str:
    clean = {}
    for key in DEFAULT_GOALS:
        clean[key] = max(0, float(goals.get(key, DEFAULT_GOALS[key])))
    return json.dumps(clean)


def resolve_period(
    range_key: str,
    from_raw: datetime | None,
    to_raw: datetime | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime, datetime, datetime, str]:
    """
    Returns (period_start, period_end, prev_start, prev_end, label).
    period_end is exclusive.
    """
    now = now or datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    key = (range_key or "today").strip().lower()

    if key == "custom" and from_raw and to_raw:
        start = from_raw.replace(hour=0, minute=0, second=0, microsecond=0)
        end = to_raw.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        if end <= start:
            end = start + timedelta(days=1)
        length = end - start
        return start, end, start - length, start, "custom"

    if key == "yesterday":
        start = today - timedelta(days=1)
        end = today
        return start, end, start - timedelta(days=1), start, "yesterday"

    if key in {"week", "this_week"}:
        # Monday-based week
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        return start, end, start - timedelta(days=7), start, "week"

    if key in {"month", "this_month"}:
        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        prev_end = start
        if start.month == 1:
            prev_start = start.replace(year=start.year - 1, month=12)
        else:
            prev_start = start.replace(month=start.month - 1)
        return start, end, prev_start, prev_end, "month"

    if key == "last_month":
        this_month = today.replace(day=1)
        if this_month.month == 1:
            start = this_month.replace(year=this_month.year - 1, month=12)
        else:
            start = this_month.replace(month=this_month.month - 1)
        end = this_month
        length = end - start
        return start, end, start - length, start, "last_month"

    if key == "year":
        start = today.replace(month=1, day=1)
        end = start.replace(year=start.year + 1)
        prev_start = start.replace(year=start.year - 1)
        return start, end, prev_start, start, "year"

    # today (default)
    start = today
    end = today + timedelta(days=1)
    return start, end, start - timedelta(days=1), start, "today"


def _status_norm(status: str | None) -> str:
    s = (status or "scheduled").strip().lower()
    if s == "cancelled":
        return "canceled"
    if s == "pending":
        return "scheduled"
    return s


def _appt_price(appt: Appointment, price_map: dict[UUID, float]) -> float:
    return price_map.get(appt.service_type_id, 0.0)


def _empty_insights_payload(
    *,
    start: datetime,
    end: datetime,
    label: str,
    now: datetime,
    goals: dict,
    employees: list[Employee],
    products: list[InventoryProduct],
) -> dict:
    """Lightweight response for brand-new shops (no appts/clients/sales yet)."""
    active_products = [p for p in products if p.is_active]
    cost_value = 0.0
    retail_value = 0.0
    low_stock: list[dict] = []
    out_of_stock: list[dict] = []
    for p in active_products:
        stock = int(p.stock or 0)
        price = _money(p.price)
        cost = _money(p.unit_cost) if p.unit_cost is not None else 0.0
        retail_value += stock * price
        cost_value += stock * cost
        if stock <= 0:
            out_of_stock.append(
                {"id": str(p.id), "name": p.name, "stock": stock, "min_stock": p.min_stock}
            )
        elif stock <= int(p.min_stock or 0):
            low_stock.append(
                {"id": str(p.id), "name": p.name, "stock": stock, "min_stock": p.min_stock}
            )

    remaining_units = sum(int(p.stock or 0) for p in active_products)
    day_count = max(1, (end - start).days)
    series = []
    cursor = start
    while cursor < end and len(series) < 62:
        nxt = cursor + timedelta(days=1)
        series.append(
            {
                "date": cursor.strftime("%Y-%m-%d"),
                "label": cursor.strftime("%d/%m"),
                "revenue": 0.0,
                "appointments": 0,
                "average_ticket": 0.0,
            }
        )
        cursor = nxt

    staff_rows = []
    for i, e in enumerate(employees):
        if not e.is_active:
            continue
        staff_rows.append(
            {
                "employee_id": str(e.id),
                "display_name": e.display_name or "Staff",
                "revenue": 0.0,
                "appointments_completed": 0,
                "appointments_total": 0,
                "average_ticket": 0.0,
                "average_review": None,
                "occupancy": 0.0,
                "completion_rate": None,
                "rank": i + 1,
            }
        )

    goals_progress = {
        "monthly_revenue": {
            "target": goals["monthly_revenue"],
            "current": 0.0,
            "pct": 0.0,
        },
        "monthly_appointments": {
            "target": goals["monthly_appointments"],
            "current": 0.0,
            "pct": 0.0,
        },
        "monthly_product_sales": {
            "target": goals["monthly_product_sales"],
            "current": 0.0,
            "pct": 0.0,
            "available": goals["monthly_product_sales"] > 0,
        },
        "monthly_new_customers": {
            "target": goals["monthly_new_customers"],
            "current": 0.0,
            "pct": 0.0,
        },
    }

    return {
        "period": {
            "range": label,
            "from": start.isoformat() + "Z",
            "to": (end - timedelta(microseconds=1)).isoformat() + "Z",
            "from_exclusive_end": end.isoformat() + "Z",
        },
        "meta": {
            "currency_note": (
                "Ingresos netos = servicios (POS + citas sin ticket) + productos "
                "− descuentos + impuestos."
            ),
            "unavailable": ["tips", "reviews", "actual_payments"],
            "generated_at": now.isoformat() + "Z",
        },
        "snapshot": {
            "revenue": 0.0,
            "revenue_delta_pct": None,
            "service_revenue": 0.0,
            "product_revenue": 0.0,
            "pos_service_revenue": 0.0,
            "discount_total": 0.0,
            "tax_total": 0.0,
            "appointments": 0,
            "appointments_delta_pct": None,
            "customers_served": 0,
            "customers_served_delta_pct": None,
            "products_sold": 0,
            "products_sold_delta_pct": None,
            "services_sold": 0,
            "average_ticket": 0.0,
            "average_ticket_delta_pct": None,
            "occupancy_rate": None,
            "occupancy_delta_pct": None,
        },
        "series": series or [
            {
                "date": start.strftime("%Y-%m-%d"),
                "label": start.strftime("%d/%m"),
                "revenue": 0.0,
                "appointments": 0,
                "average_ticket": 0.0,
            }
        ],
        "revenue_breakdown": [
            {
                "key": "services",
                "label": "Servicios",
                "amount": 0.0,
                "pct": 0.0,
                "available": True,
            },
            {
                "key": "products",
                "label": "Productos",
                "amount": 0.0,
                "pct": 0.0,
                "available": True,
            },
            {
                "key": "tips",
                "label": "Propinas",
                "amount": 0.0,
                "pct": 0.0,
                "available": False,
                "note": "Aún no se registran propinas",
            },
        ],
        "top_services": [],
        "staff_performance": staff_rows[:12],
        "customers": {
            "total": 0,
            "new": 0,
            "returning": 0,
            "retention_pct": None,
            "avg_visit_frequency": 0.0,
            "inactive_30": 0,
            "inactive_60": 0,
            "inactive_90": 0,
            "highest_spending": None,
            "most_loyal": None,
            "average_customer_value": 0.0,
        },
        "inventory": {
            "inventory_cost": round(cost_value, 2),
            "potential_revenue": round(retail_value, 2),
            "projected_gross_profit": round(retail_value - cost_value, 2),
            "products_remaining": remaining_units,
            "sku_count": len(active_products),
            "products_sold": 0,
            "product_revenue": 0.0,
            "product_gross_profit": 0.0,
            "avg_product_sale_value": 0.0,
            "sell_through_rate": None,
            "best_selling_product": None,
            "slowest_selling_product": None,
            "projected_product_revenue_month": 0.0,
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
            "note": "Valores de inventario basados en stock y precios actuales.",
        },
        "projections": {
            "today": 0.0,
            "week": 0.0,
            "month": 0.0,
            "year": 0.0,
            "is_estimate": True,
            "note": f"Sin actividad aún · periodo de {day_count} día(s).",
        },
        "goals": goals,
        "goals_progress": goals_progress,
        "health": {
            "score": 50,
            "label": "Nuevo",
            "observations": [
                {
                    "tone": "info",
                    "text": "Agenda la primera cita para empezar a medir el pulso del negocio.",
                }
            ],
        },
        "insights": [
            "Agenda más citas y completa servicios para desbloquear insights de crecimiento."
        ],
        "upcoming_appointments": [],
        "empty": True,
    }


def build_insights(
    business_id: UUID,
    *,
    range_key: str = "today",
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> dict:
    now = datetime.utcnow()
    start, end, prev_start, prev_end, label = resolve_period(range_key, from_dt, to_dt, now)

    business = Business.query.get(business_id)
    goals = parse_goals(getattr(business, "insights_goals_json", None) if business else None)

    services = ServiceType.query.filter_by(business_id=business_id).all()
    price_map = {s.id: _money(s.price) for s in services}
    duration_map = {s.id: int(s.duration or 0) for s in services}
    name_map = {s.id: s.name for s in services}

    employees = Employee.query.filter_by(business_id=business_id).all()
    emp_name = {e.id: e.display_name for e in employees}

    products = InventoryProduct.query.filter_by(business_id=business_id).all()

    # Brand-new shops: skip the heavy analytics path (many POS/sale queries).
    has_appointment = (
        db.session.query(Appointment.id)
        .filter(Appointment.business_id == business_id)
        .first()
        is not None
    )
    has_client = (
        db.session.query(Client.id).filter(Client.business_id == business_id).first()
        is not None
    )
    has_sale = False
    try:
        has_sale = (
            db.session.query(Sale.id).filter(Sale.business_id == business_id).first()
            is not None
        )
    except Exception:
        db.session.rollback()
        has_sale = False

    if not has_appointment and not has_client and not has_sale:
        return _empty_insights_payload(
            start=start,
            end=end,
            label=label,
            now=now,
            goals=goals,
            employees=employees,
            products=products,
        )

    # Always cover calendar month + hist window so goals/projections are not truncated
    # when the Insights range is "today" / a short custom range.
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hist_floor = now - timedelta(days=32)
    load_from = min(prev_start - timedelta(days=1), month_start - timedelta(days=1), hist_floor)

    # Pull a wide window once (prev period / month / hist → future for projections)
    horizon_end = max(end, now + timedelta(days=31))
    appts = (
        Appointment.query.filter(
            Appointment.business_id == business_id,
            Appointment.start_time >= load_from,
            Appointment.start_time < horizon_end,
        )
        .all()
    )

    # Also need older appts for inactivity / loyalty / first visit heuristics
    all_client_appts = (
        db.session.query(
            Appointment.client_id,
            func.min(Appointment.start_time).label("first_visit"),
            func.max(Appointment.start_time).label("last_visit"),
            func.count(Appointment.id).label("visit_count"),
        )
        .filter(Appointment.business_id == business_id)
        .group_by(Appointment.client_id)
        .all()
    )
    client_stats = {
        row.client_id: {
            "first_visit": row.first_visit,
            "last_visit": row.last_visit,
            "visit_count": int(row.visit_count),
        }
        for row in all_client_appts
    }

    def in_range(a: Appointment, a0: datetime, a1: datetime) -> bool:
        return a.start_time is not None and a0 <= a.start_time < a1

    period_appts = [a for a in appts if in_range(a, start, end)]
    prev_appts = [a for a in appts if in_range(a, prev_start, prev_end)]

    # Appointments that already have a POS ticket (avoid double-counting service revenue)
    appt_sale_keys = {
        s.idempotency_key
        for s in Sale.query.filter(
            Sale.business_id == business_id,
            Sale.status == "completed",
            Sale.idempotency_key.like("appointment:%"),
        ).all()
        if is_appointment_sale_key(s.idempotency_key)
    }

    def has_appointment_sale(a: Appointment) -> bool:
        return appointment_sale_idempotency_key(a.id) in appt_sale_keys

    def metrics_for(items: list[Appointment]) -> dict:
        by_status: dict[str, int] = defaultdict(int)
        revenue = 0.0
        completed = 0
        served_clients: set[UUID] = set()
        tickets: list[float] = []
        booked_minutes = 0
        for a in items:
            st = _status_norm(a.status)
            by_status[st] += 1
            if st == COMPLETED:
                completed += 1
                # Estimate only when no POS ticket exists (legacy / seed data).
                if not has_appointment_sale(a):
                    p = _appt_price(a, price_map)
                    revenue += p
                    tickets.append(p)
                if a.client_id:
                    served_clients.add(a.client_id)
            if st not in NON_REVENUE:
                if a.end_time and a.start_time:
                    booked_minutes += max(
                        0, int((a.end_time - a.start_time).total_seconds() // 60)
                    )
                else:
                    booked_minutes += duration_map.get(a.service_type_id, 0)
        avg_ticket = round(sum(tickets) / len(tickets), 2) if tickets else 0.0
        cancel = by_status.get("canceled", 0)
        no_show = by_status.get("no_show", 0)
        total = len(items)
        return {
            "total": total,
            "completed": completed,
            "by_status": dict(by_status),
            "revenue": round(revenue, 2),
            "customers_served": len(served_clients),
            "average_ticket": avg_ticket,
            "booked_minutes": booked_minutes,
            "cancel_rate": _pct(cancel, total),
            "no_show_rate": _pct(no_show, total),
            "completion_rate": _pct(completed, total),
        }

    cur = metrics_for(period_appts)
    prev = metrics_for(prev_appts)

    # Product sales ledger (only movement_type = sale)
    sale_movements = InventoryMovement.query.filter(
        InventoryMovement.business_id == business_id,
        InventoryMovement.movement_type == "sale",
        InventoryMovement.created_at >= start,
        InventoryMovement.created_at < end,
    ).all()
    prev_sale_movements = InventoryMovement.query.filter(
        InventoryMovement.business_id == business_id,
        InventoryMovement.movement_type == "sale",
        InventoryMovement.created_at >= prev_start,
        InventoryMovement.created_at < prev_end,
    ).all()
    product_units_sold = sum(int(m.quantity or 0) for m in sale_movements)
    product_revenue = round(sum(float(m.total_revenue or 0) for m in sale_movements), 2)
    product_cogs = round(sum(float(m.total_cost or 0) for m in sale_movements), 2)
    product_gross_profit = round(product_revenue - product_cogs, 2)
    prev_product_units = sum(int(m.quantity or 0) for m in prev_sale_movements)
    avg_product_sale = (
        round(product_revenue / product_units_sold, 2) if product_units_sold else 0.0
    )

    # POS service lines (SaleItem) — avoids relying only on completed appointments
    pos_service_rows = (
        db.session.query(SaleItem, Sale)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(
            Sale.business_id == business_id,
            Sale.status == "completed",
            SaleItem.item_type == "service",
            Sale.created_at >= start,
            Sale.created_at < end,
        )
        .all()
    )
    pos_service_revenue = round(
        sum(float(item.line_total or 0) for item, _ in pos_service_rows), 2
    )
    pos_services_sold = sum(int(item.quantity or 0) for item, _ in pos_service_rows)

    prev_pos_service_rows = (
        db.session.query(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(
            Sale.business_id == business_id,
            Sale.status == "completed",
            SaleItem.item_type == "service",
            Sale.created_at >= prev_start,
            Sale.created_at < prev_end,
        )
        .all()
    )
    prev_pos_service_revenue = sum(float(i.line_total or 0) for i in prev_pos_service_rows)

    # Sale headers for net cash (discounts / tax) and ticket counts
    period_sales = Sale.query.filter(
        Sale.business_id == business_id,
        Sale.status == "completed",
        Sale.created_at >= start,
        Sale.created_at < end,
    ).all()
    prev_sales = Sale.query.filter(
        Sale.business_id == business_id,
        Sale.status == "completed",
        Sale.created_at >= prev_start,
        Sale.created_at < prev_end,
    ).all()
    period_discount = round(sum(float(s.discount or 0) for s in period_sales), 2)
    period_tax = round(sum(float(s.tax or 0) for s in period_sales), 2)
    prev_discount = round(sum(float(s.discount or 0) for s in prev_sales), 2)
    prev_tax = round(sum(float(s.tax or 0) for s in prev_sales), 2)

    # Occupancy: booked minutes / assumed open capacity in period
    day_count = max(1, (end - start).days)
    prev_day_count = max(1, (prev_end - prev_start).days)
    # Assume 8 working hours × active staff (heuristic)
    active_staff = max(1, sum(1 for e in employees if e.is_active))
    capacity_minutes = day_count * 8 * 60 * active_staff
    prev_capacity_minutes = prev_day_count * 8 * 60 * active_staff
    occupancy = _pct(cur["booked_minutes"], capacity_minutes)
    prev_occupancy = _pct(prev["booked_minutes"], prev_capacity_minutes)

    # Combined: legacy appt estimates + POS service lines + product movements − discount + tax
    legacy_services = sum(
        1
        for a in period_appts
        if _status_norm(a.status) == COMPLETED and not has_appointment_sale(a)
    )
    prev_legacy_services = sum(
        1
        for a in prev_appts
        if _status_norm(a.status) == COMPLETED and not has_appointment_sale(a)
    )
    gross_service_revenue = round(cur["revenue"] + pos_service_revenue, 2)
    gross_combined = cur["revenue"] + product_revenue + pos_service_revenue
    combined_revenue = round(gross_combined - period_discount + period_tax, 2)
    prev_gross = (
        prev["revenue"]
        + sum(float(m.total_revenue or 0) for m in prev_sale_movements)
        + prev_pos_service_revenue
    )
    prev_combined = round(prev_gross - prev_discount + prev_tax, 2)

    # Ticket = transactions (legacy appts without sale + completed POS tickets)
    ticket_count = legacy_services + len(period_sales)
    prev_ticket_count = prev_legacy_services + len(prev_sales)
    combined_avg = (
        round(combined_revenue / ticket_count, 2) if ticket_count else 0.0
    )
    prev_combined_avg = (
        round(prev_combined / prev_ticket_count, 2) if prev_ticket_count else 0.0
    )

    def unique_customers_served(
        appts_in_range: list[Appointment], sales_in_range: list[Sale]
    ) -> int:
        """
        Unique clients served in the period.

        Union of:
        - completed appointments whose start_time is in range (legacy)
        - completed sales in range (walk-ins + appointments completed now,
          even when start_time falls on another calendar day / UTC bucket)
        """
        ids: set[UUID] = set()
        for a in appts_in_range:
            if _status_norm(a.status) == COMPLETED and a.client_id:
                ids.add(a.client_id)
        for s in sales_in_range:
            if s.client_id:
                ids.add(s.client_id)
        return len(ids)

    customers_served = unique_customers_served(period_appts, period_sales)
    prev_customers_served = unique_customers_served(prev_appts, prev_sales)

    snapshot = {
        "revenue": combined_revenue,
        "revenue_delta_pct": _delta_pct(combined_revenue, prev_combined),
        "service_revenue": gross_service_revenue,
        "product_revenue": product_revenue,
        "pos_service_revenue": pos_service_revenue,
        "discount_total": period_discount,
        "tax_total": period_tax,
        "appointments": cur["total"],
        "appointments_delta_pct": _delta_pct(float(cur["total"]), float(prev["total"])),
        "customers_served": customers_served,
        "customers_served_delta_pct": _delta_pct(
            float(customers_served), float(prev_customers_served)
        ),
        "products_sold": product_units_sold,
        "products_sold_delta_pct": _delta_pct(
            float(product_units_sold), float(prev_product_units)
        ),
        "services_sold": legacy_services + pos_services_sold,
        "average_ticket": combined_avg,
        "average_ticket_delta_pct": _delta_pct(combined_avg, prev_combined_avg),
        "occupancy_rate": occupancy,
        "occupancy_delta_pct": _delta_pct(
            occupancy or 0.0, prev_occupancy or 0.0
        ),
    }

    def ledger_revenue(bucket_start: datetime, bucket_end: datetime) -> float:
        pos = sum(
            float(item.line_total or 0)
            for item, sale in pos_service_rows
            if sale.created_at is not None
            and bucket_start <= sale.created_at < bucket_end
        )
        prods = sum(
            float(m.total_revenue or 0)
            for m in sale_movements
            if m.created_at is not None and bucket_start <= m.created_at < bucket_end
        )
        disc = sum(
            float(s.discount or 0)
            for s in period_sales
            if s.created_at is not None and bucket_start <= s.created_at < bucket_end
        )
        tax = sum(
            float(s.tax or 0)
            for s in period_sales
            if s.created_at is not None and bucket_start <= s.created_at < bucket_end
        )
        return pos + prods - disc + tax

    def bucket_ticket_avg(
        m: dict, bucket_start: datetime, bucket_end: datetime, rev: float
    ) -> float:
        legacy_n = sum(
            1
            for a in period_appts
            if a.start_time is not None
            and bucket_start <= a.start_time < bucket_end
            and _status_norm(a.status) == COMPLETED
            and not has_appointment_sale(a)
        )
        sales_n = sum(
            1
            for s in period_sales
            if s.created_at is not None and bucket_start <= s.created_at < bucket_end
        )
        n = legacy_n + sales_n
        return round(rev / n, 2) if n else 0.0

    # Series for charts (daily buckets inside period; monthly if year)
    series: list[dict] = []
    use_monthly = label == "year" or day_count > 45
    if use_monthly:
        cursor = start.replace(day=1)
        while cursor < end:
            if cursor.month == 12:
                nxt = cursor.replace(year=cursor.year + 1, month=1)
            else:
                nxt = cursor.replace(month=cursor.month + 1)
            bucket = [a for a in period_appts if cursor <= a.start_time < nxt]
            m = metrics_for(bucket)
            rev = round(m["revenue"] + ledger_revenue(cursor, nxt), 2)
            series.append(
                {
                    "date": cursor.strftime("%Y-%m"),
                    "label": cursor.strftime("%b"),
                    "revenue": rev,
                    "appointments": m["total"],
                    "average_ticket": bucket_ticket_avg(m, cursor, nxt, rev),
                }
            )
            cursor = nxt
    else:
        cursor = start
        while cursor < end:
            nxt = cursor + timedelta(days=1)
            bucket = [a for a in period_appts if cursor <= a.start_time < nxt]
            m = metrics_for(bucket)
            rev = round(m["revenue"] + ledger_revenue(cursor, nxt), 2)
            series.append(
                {
                    "date": cursor.strftime("%Y-%m-%d"),
                    "label": cursor.strftime("%d/%m"),
                    "revenue": rev,
                    "appointments": m["total"],
                    "average_ticket": bucket_ticket_avg(m, cursor, nxt, rev),
                }
            )
            cursor = nxt

    # Revenue breakdown — gross services/products + discount line so amounts reconcile
    service_rev = gross_service_revenue
    net_after_adj = round(service_rev + product_revenue - period_discount + period_tax, 2)
    revenue_breakdown = [
        {
            "key": "services",
            "label": "Servicios",
            "amount": service_rev,
            "pct": _pct(service_rev, max(net_after_adj, gross_combined)) or 0.0,
            "available": True,
        },
        {
            "key": "products",
            "label": "Productos",
            "amount": product_revenue,
            "pct": _pct(product_revenue, max(net_after_adj, gross_combined)) or 0.0,
            "available": True,
        },
    ]
    if period_discount > 0:
        revenue_breakdown.append(
            {
                "key": "discounts",
                "label": "Descuentos",
                "amount": -period_discount,
                "pct": _pct(period_discount, max(net_after_adj, gross_combined)) or 0.0,
                "available": True,
            }
        )
    if period_tax > 0:
        revenue_breakdown.append(
            {
                "key": "tax",
                "label": "Impuestos",
                "amount": period_tax,
                "pct": _pct(period_tax, max(net_after_adj, gross_combined)) or 0.0,
                "available": True,
            }
        )
    revenue_breakdown.append(
        {
            "key": "tips",
            "label": "Propinas",
            "amount": 0.0,
            "pct": 0.0,
            "available": False,
            "note": "Aún no se registran propinas",
        }
    )

    # Top services in period — prefer POS line amounts; catalog only for legacy appts
    svc_agg: dict[UUID, dict] = defaultdict(
        lambda: {"count": 0, "revenue": 0.0, "duration_sum": 0}
    )
    for a in period_appts:
        if _status_norm(a.status) in NON_REVENUE:
            continue
        sid = a.service_type_id
        svc_agg[sid]["count"] += 1
        if _status_norm(a.status) == COMPLETED and not has_appointment_sale(a):
            svc_agg[sid]["revenue"] += _appt_price(a, price_map)
        svc_agg[sid]["duration_sum"] += duration_map.get(sid, 0)

    for item, sale in pos_service_rows:
        sid = item.service_type_id
        if not sid:
            continue
        svc_agg[sid]["revenue"] += float(item.line_total or 0)
        if not is_appointment_sale_key(sale.idempotency_key):
            svc_agg[sid]["count"] += int(item.quantity or 0)
            svc_agg[sid]["duration_sum"] += duration_map.get(sid, 0) * int(
                item.quantity or 0
            )

    prev_svc: dict[UUID, int] = defaultdict(int)
    for a in prev_appts:
        if _status_norm(a.status) not in NON_REVENUE:
            prev_svc[a.service_type_id] += 1

    top_services = []
    for sid, agg in sorted(svc_agg.items(), key=lambda x: x[1]["revenue"], reverse=True)[:8]:
        cnt = agg["count"]
        top_services.append(
            {
                "service_type_id": str(sid),
                "name": name_map.get(sid, "Servicio"),
                "bookings": cnt,
                "revenue": round(agg["revenue"], 2),
                "avg_duration": round(agg["duration_sum"] / cnt) if cnt else 0,
                "trend_pct": _delta_pct(float(cnt), float(prev_svc.get(sid, 0))),
            }
        )

    # Staff performance — POS service lines + legacy appts without sale
    staff_agg: dict[UUID, dict] = defaultdict(
        lambda: {"total": 0, "completed": 0, "revenue": 0.0, "minutes": 0}
    )
    for a in period_appts:
        eid = a.employee_id
        if not eid:
            continue
        st = _status_norm(a.status)
        staff_agg[eid]["total"] += 1
        if st == COMPLETED:
            staff_agg[eid]["completed"] += 1
            if not has_appointment_sale(a):
                staff_agg[eid]["revenue"] += _appt_price(a, price_map)
        if st not in NON_REVENUE:
            staff_agg[eid]["minutes"] += duration_map.get(a.service_type_id, 0)

    for item, sale in pos_service_rows:
        eid = sale.employee_id
        if not eid:
            continue
        staff_agg[eid]["revenue"] += float(item.line_total or 0)
        if not is_appointment_sale_key(sale.idempotency_key):
            staff_agg[eid]["completed"] += int(item.quantity or 0)
            staff_agg[eid]["total"] += int(item.quantity or 0)

    staff_capacity = max(1, day_count * 8 * 60)
    staff_rows = []
    for eid, agg in staff_agg.items():
        completed = agg["completed"]
        revenue = round(agg["revenue"], 2)
        staff_rows.append(
            {
                "employee_id": str(eid),
                "display_name": emp_name.get(eid, "Barbero"),
                "revenue": revenue,
                "appointments_completed": completed,
                "appointments_total": agg["total"],
                "average_ticket": round(revenue / completed, 2) if completed else 0.0,
                "average_review": None,
                "occupancy": _pct(agg["minutes"], staff_capacity),
                "completion_rate": _pct(completed, agg["total"]),
            }
        )
    staff_rows.sort(key=lambda r: r["revenue"], reverse=True)
    for i, row in enumerate(staff_rows, start=1):
        row["rank"] = i

    # Include active staff with zero activity
    seen = {r["employee_id"] for r in staff_rows}
    for e in employees:
        if e.is_active and str(e.id) not in seen:
            staff_rows.append(
                {
                    "employee_id": str(e.id),
                    "display_name": e.display_name,
                    "revenue": 0.0,
                    "appointments_completed": 0,
                    "appointments_total": 0,
                    "average_ticket": 0.0,
                    "average_review": None,
                    "occupancy": 0.0,
                    "completion_rate": None,
                    "rank": len(staff_rows) + 1,
                }
            )

    # Customer insights
    appt_client_scope = (
        db.session.query(Appointment.client_id)
        .filter(Appointment.business_id == business_id)
        .distinct()
    )
    total_clients = Client.query.filter(
        or_(Client.business_id == business_id, Client.id.in_(appt_client_scope))
    ).count()

    new_customers = 0
    returning_customers = 0
    period_client_ids = {a.client_id for a in period_appts if a.client_id}
    for cid in period_client_ids:
        stats = client_stats.get(cid)
        if not stats:
            continue
        if stats["first_visit"] and start <= stats["first_visit"] < end:
            new_customers += 1
        elif stats["visit_count"] >= 2:
            returning_customers += 1

    inactive_30 = inactive_60 = inactive_90 = 0
    for cid, stats in client_stats.items():
        last = stats["last_visit"]
        if not last:
            continue
        days = (now - last).days
        if days >= 90:
            inactive_90 += 1
        elif days >= 60:
            inactive_60 += 1
        elif days >= 30:
            inactive_30 += 1

    # Highest spending / most loyal from completed appointments (all time, limited window)
    spend: dict[UUID, float] = defaultdict(float)
    loyalty: dict[UUID, int] = defaultdict(int)
    hist = (
        Appointment.query.filter(
            Appointment.business_id == business_id,
            Appointment.status == COMPLETED,
        )
        .all()
    )
    for a in hist:
        if not a.client_id:
            continue
        spend[a.client_id] += _appt_price(a, price_map)
        loyalty[a.client_id] += 1

    spender_ids = list(spend.keys())
    clients_by_id = (
        {c.id: c for c in Client.query.filter(Client.id.in_(spender_ids)).all()}
        if spender_ids
        else {}
    )

    def client_label(cid: UUID) -> str:
        c = clients_by_id.get(cid)
        if not c:
            return "Cliente"
        return f"{c.first_name} {c.last_name}".strip()

    top_spender_id = max(spend, key=spend.get) if spend else None
    top_loyal_id = max(loyalty, key=loyalty.get) if loyalty else None
    avg_customer_value = (
        round(sum(spend.values()) / len(spend), 2) if spend else 0.0
    )
    avg_frequency = (
        round(sum(loyalty.values()) / len(loyalty), 2) if loyalty else 0.0
    )
    retention = _pct(float(returning_customers), float(max(1, len(period_client_ids))))

    customers = {
        "total": total_clients,
        "new": new_customers,
        "returning": returning_customers,
        "retention_pct": retention,
        "avg_visit_frequency": avg_frequency,
        "inactive_30": inactive_30,
        "inactive_60": inactive_60,
        "inactive_90": inactive_90,
        "highest_spending": (
            {
                "client_id": str(top_spender_id),
                "name": client_label(top_spender_id),
                "amount": round(spend[top_spender_id], 2),
            }
            if top_spender_id
            else None
        ),
        "most_loyal": (
            {
                "client_id": str(top_loyal_id),
                "name": client_label(top_loyal_id),
                "visits": loyalty[top_loyal_id],
            }
            if top_loyal_id
            else None
        ),
        "average_customer_value": avg_customer_value,
    }

    # Inventory analytics + product sales from movement ledger
    active_products = [p for p in products if p.is_active]
    cost_value = 0.0
    retail_value = 0.0
    low_stock = []
    out_of_stock = []
    for p in active_products:
        stock = int(p.stock or 0)
        price = _money(p.price)
        cost = _money(p.unit_cost) if p.unit_cost is not None else 0.0
        retail_value += stock * price
        cost_value += stock * cost
        if stock <= 0:
            out_of_stock.append(
                {"id": str(p.id), "name": p.name, "stock": stock, "min_stock": p.min_stock}
            )
        elif stock <= int(p.min_stock or 0):
            low_stock.append(
                {"id": str(p.id), "name": p.name, "stock": stock, "min_stock": p.min_stock}
            )

    sold_by_product: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0, "name": ""})
    product_name_by_id = {p.id: p.name for p in products}
    for m in sale_movements:
        sold_by_product[m.product_id]["units"] += int(m.quantity or 0)
        sold_by_product[m.product_id]["revenue"] += float(m.total_revenue or 0)
        sold_by_product[m.product_id]["name"] = product_name_by_id.get(
            m.product_id, "Producto"
        )

    best_selling = None
    slowest_selling = None
    if sold_by_product:
        ranked = sorted(
            sold_by_product.items(),
            key=lambda x: (x[1]["units"], x[1]["revenue"]),
            reverse=True,
        )
        best_id, best_agg = ranked[0]
        best_selling = {
            "id": str(best_id),
            "name": best_agg["name"],
            "units": best_agg["units"],
            "revenue": round(best_agg["revenue"], 2),
        }
        slow_id, slow_agg = ranked[-1]
        slowest_selling = {
            "id": str(slow_id),
            "name": slow_agg["name"],
            "units": slow_agg["units"],
            "revenue": round(slow_agg["revenue"], 2),
        }

    remaining_units = sum(int(p.stock or 0) for p in active_products)
    sell_through = _pct(float(product_units_sold), float(product_units_sold + remaining_units))

    hist_product_start = now - timedelta(days=28)
    hist_product_sales = InventoryMovement.query.filter(
        InventoryMovement.business_id == business_id,
        InventoryMovement.movement_type == "sale",
        InventoryMovement.created_at >= hist_product_start,
        InventoryMovement.created_at < now,
    ).all()
    hist_product_by_day: dict[str, float] = defaultdict(float)
    for m in hist_product_sales:
        if m.created_at:
            hist_product_by_day[m.created_at.strftime("%Y-%m-%d")] += float(
                m.total_revenue or 0
            )
    product_daily_avg = (
        sum(hist_product_by_day.values()) / max(1, len(hist_product_by_day))
        if hist_product_by_day
        else 0.0
    )

    inventory = {
        "inventory_cost": round(cost_value, 2),
        "potential_revenue": round(retail_value, 2),
        "projected_gross_profit": round(retail_value - cost_value, 2),
        "products_remaining": remaining_units,
        "sku_count": len(active_products),
        "products_sold": product_units_sold,
        "product_revenue": product_revenue,
        "product_cogs": product_cogs,
        "product_gross_profit": product_gross_profit,
        "avg_product_sale_value": avg_product_sale,
        "sell_through_rate": sell_through,
        "low_stock": low_stock[:10],
        "out_of_stock": out_of_stock[:10],
        "best_selling_product": best_selling,
        "slowest_selling_product": slowest_selling,
        "projected_product_revenue_month": 0.0,  # filled after days_in_month known
        "note": "Ventas de producto = movimientos tipo sale. Valor en anaquel = stock × precio/costo.",
    }

    # Projections + goals use the same combined revenue definition as the snapshot KPI
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    month_legacy_rev = sum(
        _appt_price(a, price_map)
        for a in appts
        if a.start_time
        and month_start <= a.start_time < now
        and _status_norm(a.status) == COMPLETED
        and not has_appointment_sale(a)
    )
    month_pos_service_rev = round(
        sum(
            float(i.line_total or 0)
            for i in (
                db.session.query(SaleItem)
                .join(Sale, Sale.id == SaleItem.sale_id)
                .filter(
                    Sale.business_id == business_id,
                    Sale.status == "completed",
                    SaleItem.item_type == "service",
                    Sale.created_at >= month_start,
                    Sale.created_at < now,
                )
                .all()
            )
        ),
        2,
    )
    month_product_rev = round(
        sum(
            float(m.total_revenue or 0)
            for m in InventoryMovement.query.filter(
                InventoryMovement.business_id == business_id,
                InventoryMovement.movement_type == "sale",
                InventoryMovement.created_at >= month_start,
                InventoryMovement.created_at < now,
            ).all()
        ),
        2,
    )
    month_sales_headers = Sale.query.filter(
        Sale.business_id == business_id,
        Sale.status == "completed",
        Sale.created_at >= month_start,
        Sale.created_at < now,
    ).all()
    month_discount = round(sum(float(s.discount or 0) for s in month_sales_headers), 2)
    month_tax = round(sum(float(s.tax or 0) for s in month_sales_headers), 2)
    month_rev_so_far = round(
        month_legacy_rev
        + month_pos_service_rev
        + month_product_rev
        - month_discount
        + month_tax,
        2,
    )
    days_elapsed = max(1, (now.date() - month_start.date()).days + 1)
    days_in_month = (month_end - month_start).days
    daily_pace = month_rev_so_far / days_elapsed
    inventory["projected_product_revenue_month"] = round(
        min(product_daily_avg * days_in_month, retail_value + product_revenue),
        2,
    )

    upcoming_booked = [
        a
        for a in appts
        if a.start_time
        and a.start_time >= now
        and _status_norm(a.status) in {"scheduled", "confirmed"}
    ]

    def upcoming_revenue(until: datetime) -> float:
        return round(
            sum(
                price_map.get(a.service_type_id, 0.0)
                for a in upcoming_booked
                if a.start_time < until
            ),
            2,
        )

    today_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    week_end = today_end + timedelta(days=6)
    today_start = today_end - timedelta(days=1)

    def realized_revenue(a0: datetime, a1: datetime) -> float:
        legacy = sum(
            _appt_price(a, price_map)
            for a in appts
            if in_range(a, a0, a1)
            and _status_norm(a.status) == COMPLETED
            and not has_appointment_sale(a)
        )
        pos = sum(
            float(item.line_total or 0)
            for item, sale in (
                db.session.query(SaleItem, Sale)
                .join(Sale, Sale.id == SaleItem.sale_id)
                .filter(
                    Sale.business_id == business_id,
                    Sale.status == "completed",
                    SaleItem.item_type == "service",
                    Sale.created_at >= a0,
                    Sale.created_at < a1,
                )
                .all()
            )
        )
        prods = sum(
            float(m.total_revenue or 0)
            for m in InventoryMovement.query.filter(
                InventoryMovement.business_id == business_id,
                InventoryMovement.movement_type == "sale",
                InventoryMovement.created_at >= a0,
                InventoryMovement.created_at < a1,
            ).all()
        )
        sales = Sale.query.filter(
            Sale.business_id == business_id,
            Sale.status == "completed",
            Sale.created_at >= a0,
            Sale.created_at < a1,
        ).all()
        disc = sum(float(s.discount or 0) for s in sales)
        tax = sum(float(s.tax or 0) for s in sales)
        return round(legacy + pos + prods - disc + tax, 2)

    # Historical average daily revenue (last 28 completed days) — combined definition
    hist_start = now - timedelta(days=28)
    hist_by_day: dict[str, float] = defaultdict(float)
    for a in appts:
        if (
            a.start_time
            and hist_start <= a.start_time < now
            and _status_norm(a.status) == COMPLETED
            and not has_appointment_sale(a)
        ):
            hist_by_day[a.start_time.strftime("%Y-%m-%d")] += _appt_price(a, price_map)
    for item, sale in (
        db.session.query(SaleItem, Sale)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(
            Sale.business_id == business_id,
            Sale.status == "completed",
            SaleItem.item_type == "service",
            Sale.created_at >= hist_start,
            Sale.created_at < now,
        )
        .all()
    ):
        if sale.created_at:
            hist_by_day[sale.created_at.strftime("%Y-%m-%d")] += float(item.line_total or 0)
    for m in InventoryMovement.query.filter(
        InventoryMovement.business_id == business_id,
        InventoryMovement.movement_type == "sale",
        InventoryMovement.created_at >= hist_start,
        InventoryMovement.created_at < now,
    ).all():
        if m.created_at:
            hist_by_day[m.created_at.strftime("%Y-%m-%d")] += float(m.total_revenue or 0)
    for s in Sale.query.filter(
        Sale.business_id == business_id,
        Sale.status == "completed",
        Sale.created_at >= hist_start,
        Sale.created_at < now,
    ).all():
        if s.created_at:
            day = s.created_at.strftime("%Y-%m-%d")
            hist_by_day[day] -= float(s.discount or 0)
            hist_by_day[day] += float(s.tax or 0)

    hist_avg = (
        sum(hist_by_day.values()) / max(1, len(hist_by_day)) if hist_by_day else daily_pace
    )

    projections = {
        "today": round(realized_revenue(today_start, today_end) + upcoming_revenue(today_end), 2),
        "week": round(realized_revenue(today_start, week_end) + upcoming_revenue(week_end), 2),
        "month": round(month_rev_so_far + daily_pace * max(0, days_in_month - days_elapsed), 2),
        "year": round((daily_pace if daily_pace > 0 else hist_avg) * 365, 2),
        "booked_pipeline_today": upcoming_revenue(today_end),
        "booked_pipeline_week": upcoming_revenue(week_end),
        "booked_pipeline_month": upcoming_revenue(month_end),
        "is_estimate": True,
        "note": (
            "Estimaciones = ingresos realizados (citas/POS/productos) "
            "+ agenda futura confirmada."
        ),
    }

    # Goals progress (calendar month) — same net formula as KPI revenue
    month_appts_count = sum(
        1
        for a in appts
        if a.start_time and month_start <= a.start_time < month_end
        and _status_norm(a.status) not in NON_REVENUE
    )
    month_new_customers = sum(
        1
        for cid, stats in client_stats.items()
        if stats["first_visit"] and month_start <= stats["first_visit"] < month_end
    )
    month_product_rev_goal = round(
        sum(
            float(m.total_revenue or 0)
            for m in InventoryMovement.query.filter(
                InventoryMovement.business_id == business_id,
                InventoryMovement.movement_type == "sale",
                InventoryMovement.created_at >= month_start,
                InventoryMovement.created_at < month_end,
            ).all()
        ),
        2,
    )
    goals_progress = {
        "monthly_revenue": {
            "target": goals["monthly_revenue"],
            "current": month_rev_so_far,
            "pct": _pct(month_rev_so_far, goals["monthly_revenue"]) or 0.0,
        },
        "monthly_appointments": {
            "target": goals["monthly_appointments"],
            "current": month_appts_count,
            "pct": _pct(float(month_appts_count), goals["monthly_appointments"]) or 0.0,
        },
        "monthly_product_sales": {
            "target": goals["monthly_product_sales"],
            "current": month_product_rev_goal,
            "pct": _pct(month_product_rev_goal, goals["monthly_product_sales"]) or 0.0,
            "available": True,
        },
        "monthly_new_customers": {
            "target": goals["monthly_new_customers"],
            "current": month_new_customers,
            "pct": _pct(float(month_new_customers), goals["monthly_new_customers"])
            or 0.0,
        },
    }

    # Business health score (0-100)
    observations: list[dict] = []
    score = 70

    if (snapshot["revenue_delta_pct"] or 0) > 5:
        score += 8
        observations.append(
            {"tone": "positive", "text": "Los ingresos están subiendo respecto al periodo anterior."}
        )
    elif (snapshot["revenue_delta_pct"] or 0) < -10:
        score -= 10
        observations.append(
            {"tone": "warning", "text": "Los ingresos bajaron frente al periodo anterior."}
        )

    if (customers["retention_pct"] or 0) >= 40:
        score += 6
        observations.append(
            {"tone": "positive", "text": "Buena retención: muchos clientes están regresando."}
        )
    elif total_clients > 5 and (customers["retention_pct"] or 0) < 20:
        score -= 6
        observations.append(
            {"tone": "warning", "text": "La retención de clientes es baja en este periodo."}
        )

    if not low_stock and not out_of_stock:
        score += 5
        observations.append({"tone": "positive", "text": "Inventario en niveles saludables."})
    elif out_of_stock:
        score -= 8
        observations.append(
            {
                "tone": "danger",
                "text": f"{len(out_of_stock)} producto(s) sin stock.",
            }
        )
    elif low_stock:
        score -= 4
        observations.append(
            {
                "tone": "warning",
                "text": f"{len(low_stock)} producto(s) con stock bajo.",
            }
        )

    if new_customers > 0:
        score += 4
        observations.append(
            {"tone": "positive", "text": f"+{new_customers} clientes nuevos en el periodo."}
        )

    if inactive_90 >= 5:
        score -= 5
        observations.append(
            {
                "tone": "warning",
                "text": f"{inactive_90} clientes inactivos hace 90+ días.",
            }
        )

    if staff_rows and staff_rows[0]["revenue"] > 0 and snapshot["service_revenue"] > 0:
        share = _pct(staff_rows[0]["revenue"], snapshot["service_revenue"]) or 0
        if share >= 40:
            observations.append(
                {
                    "tone": "info",
                    "text": f"{staff_rows[0]['display_name']} genera ~{share}% de los ingresos del periodo.",
                }
            )

    # Busy day insight from series
    if series:
        busiest = max(series, key=lambda s: s["appointments"])
        if busiest["appointments"] > 0:
            observations.append(
                {
                    "tone": "info",
                    "text": f"Pico de agenda: {busiest['label']} con {busiest['appointments']} citas.",
                }
            )

    score = max(0, min(100, score))
    if score >= 85:
        health_label = "Excelente"
    elif score >= 70:
        health_label = "Bueno"
    elif score >= 55:
        health_label = "Regular"
    else:
        health_label = "Necesita atención"

    health = {
        "score": score,
        "label": health_label,
        "observations": observations[:8],
    }

    # Auto insights
    insights: list[str] = []
    if snapshot["revenue_delta_pct"] is not None:
        direction = "aumentaron" if snapshot["revenue_delta_pct"] >= 0 else "bajaron"
        insights.append(
            f"Los ingresos {direction} {abs(snapshot['revenue_delta_pct'])}% vs el periodo anterior."
        )
    if top_services:
        insights.append(
            f"{top_services[0]['name']} es tu servicio más rentable del periodo "
            f"(${top_services[0]['revenue']:,.2f})."
        )
    if snapshot["average_ticket_delta_pct"] is not None and abs(snapshot["average_ticket_delta_pct"]) >= 1:
        sign = "+" if snapshot["average_ticket_delta_pct"] > 0 else ""
        insights.append(
            f"Ticket promedio {sign}{snapshot['average_ticket_delta_pct']}% "
            f"(${snapshot['average_ticket']:,.2f})."
        )
    if inactive_90:
        insights.append(
            f"{inactive_90} clientes no regresan desde hace 90 días o más."
        )
    if low_stock:
        insights.append(
            f"Revisa inventario: {low_stock[0]['name']} está bajo ({low_stock[0]['stock']} uds)."
        )
    if best_selling:
        insights.append(
            f"{best_selling['name']} es tu producto más vendido "
            f"({best_selling['units']} uds, ${best_selling['revenue']:,.2f})."
        )
    if inventory["projected_gross_profit"] > 0:
        insights.append(
            f"Tu inventario proyecta ~${inventory['projected_gross_profit']:,.2f} de margen bruto potencial."
        )
    if not insights:
        insights.append(
            "Agenda más citas y completa servicios para desbloquear insights de crecimiento."
        )

    upcoming = (
        Appointment.query.filter(
            Appointment.business_id == business_id,
            Appointment.start_time >= now,
        )
        .order_by(Appointment.start_time.asc())
        .limit(6)
        .all()
    )

    return {
        "period": {
            "range": label,
            "from": start.isoformat() + "Z",
            "to": (end - timedelta(microseconds=1)).isoformat() + "Z",
            "from_exclusive_end": end.isoformat() + "Z",
        },
        "meta": {
            "currency_note": (
                "Ingresos netos = servicios (POS + citas sin ticket) + productos "
                "− descuentos + impuestos."
            ),
            "unavailable": ["tips", "reviews", "actual_payments"],
            "generated_at": now.isoformat() + "Z",
        },
        "snapshot": snapshot,
        "series": series,
        "revenue_breakdown": revenue_breakdown,
        "top_services": top_services,
        "staff_performance": staff_rows[:12],
        "customers": customers,
        "inventory": inventory,
        "projections": projections,
        "goals": goals,
        "goals_progress": goals_progress,
        "health": health,
        "insights": insights,
        "upcoming_appointments": [
            {
                "id": str(a.id),
                "client_name": a.client_name,
                "start_time": a.start_time.isoformat() if a.start_time else None,
                "status": _status_norm(a.status),
                "service_name": name_map.get(a.service_type_id),
            }
            for a in upcoming
        ],
        "empty": cur["total"] == 0 and total_clients == 0,
    }
