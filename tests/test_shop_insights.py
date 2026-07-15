"""Unit tests for shop insights period helpers (no DB)."""

from datetime import datetime

from app.shop_insights import parse_goals, resolve_period, serialize_goals


def test_resolve_period_today():
    now = datetime(2026, 7, 14, 15, 30, 0)
    start, end, prev_start, prev_end, label = resolve_period("today", None, None, now)
    assert label == "today"
    assert start == datetime(2026, 7, 14)
    assert end == datetime(2026, 7, 15)
    assert prev_start == datetime(2026, 7, 13)
    assert prev_end == datetime(2026, 7, 14)


def test_resolve_period_week_monday():
    now = datetime(2026, 7, 15, 10, 0, 0)  # Wednesday
    start, end, _, _, label = resolve_period("week", None, None, now)
    assert label == "week"
    assert start == datetime(2026, 7, 13)  # Monday
    assert end == datetime(2026, 7, 20)


def test_goals_roundtrip():
    raw = serialize_goals({"monthly_revenue": 1000, "monthly_appointments": 50})
    goals = parse_goals(raw)
    assert goals["monthly_revenue"] == 1000
    assert goals["monthly_appointments"] == 50
    assert "monthly_new_customers" in goals
