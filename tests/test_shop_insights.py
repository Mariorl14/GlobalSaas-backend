"""Unit tests for shop insights period helpers (no DB)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.shop_insights import (
    local_to_utc_naive,
    parse_goals,
    resolve_period,
    serialize_goals,
)


def test_resolve_period_today():
    now = datetime(2026, 7, 14, 15, 30, 0)
    start, end, prev_start, prev_end, label = resolve_period("today", None, None, now)
    assert label == "today"
    assert start == datetime(2026, 7, 14)
    assert end == datetime(2026, 7, 15)
    assert prev_start == datetime(2026, 7, 13)
    assert prev_end == datetime(2026, 7, 14)


def test_resolve_period_week_to_date_monday_based():
    now = datetime(2026, 7, 15, 10, 0, 0)  # Wednesday
    start, end, prev_start, prev_end, label = resolve_period("week", None, None, now)
    assert label == "week"
    assert start == datetime(2026, 7, 13)  # Monday
    assert end == datetime(2026, 7, 16)  # end of Wednesday (exclusive Thu)
    assert prev_start == datetime(2026, 7, 6)
    assert prev_end == datetime(2026, 7, 9)


def test_resolve_period_month_to_date():
    now = datetime(2026, 8, 16, 18, 0, 0)
    start, end, prev_start, prev_end, label = resolve_period("month", None, None, now)
    assert label == "month"
    assert start == datetime(2026, 8, 1)
    assert end == datetime(2026, 8, 17)
    assert prev_start == datetime(2026, 7, 1)
    assert prev_end == datetime(2026, 7, 17)


def test_resolve_period_month_to_date_clamps_feb():
    now = datetime(2026, 3, 31, 12, 0, 0)
    start, end, prev_start, prev_end, label = resolve_period("month", None, None, now)
    assert start == datetime(2026, 3, 1)
    assert end == datetime(2026, 4, 1)
    assert prev_start == datetime(2026, 2, 1)
    assert prev_end == datetime(2026, 3, 1)  # Feb has 28 days in 2026


def test_resolve_period_last_month_full():
    now = datetime(2026, 8, 16, 9, 0, 0)
    start, end, _, _, label = resolve_period("last_month", None, None, now)
    assert label == "last_month"
    assert start == datetime(2026, 7, 1)
    assert end == datetime(2026, 8, 1)


def test_resolve_period_year_to_date():
    now = datetime(2026, 8, 16, 9, 0, 0)
    start, end, prev_start, prev_end, label = resolve_period("year", None, None, now)
    assert label == "year"
    assert start == datetime(2026, 1, 1)
    assert end == datetime(2026, 8, 17)
    assert prev_start == datetime(2025, 1, 1)
    assert prev_end == datetime(2025, 8, 17)


def test_resolve_period_custom_inclusive_days():
    start, end, _, _, label = resolve_period(
        "custom",
        datetime(2026, 8, 1),
        datetime(2026, 8, 3),
        datetime(2026, 8, 16),
    )
    assert label == "custom"
    assert start == datetime(2026, 8, 1)
    assert end == datetime(2026, 8, 4)


def test_local_to_utc_naive_costa_rica():
    tz = ZoneInfo("America/Costa_Rica")
    local_midnight = datetime(2026, 8, 16, 0, 0, 0)
    utc = local_to_utc_naive(local_midnight, tz)
    assert utc == datetime(2026, 8, 16, 6, 0, 0)


def test_goals_roundtrip():
    raw = serialize_goals({"monthly_revenue": 1000, "monthly_appointments": 50})
    goals = parse_goals(raw)
    assert goals["monthly_revenue"] == 1000
    assert goals["monthly_appointments"] == 50
    assert "monthly_new_customers" in goals
