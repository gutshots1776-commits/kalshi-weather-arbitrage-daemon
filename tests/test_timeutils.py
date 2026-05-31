"""Tests for the settlement-window timezone helpers in kalshi.timeutils.

These pin the correctness fix for the actual-high-temperature lookup: NOAA
observations must be windowed by the city's **Local Standard Time** day (the
NWS/Kalshi climatological day), not the UTC calendar day the code used to use
and not the DST-aware civil day. The module is pure stdlib and imports without
credentials, so the whole settlement math is testable offline.
"""
from datetime import datetime, timedelta, timezone

import pytest

from kalshi.timeutils import (
    parse_iso_utc,
    standard_utc_offset,
    lst_day_utc_window,
    daily_high_f_from_observations,
)

UTC = timezone.utc


def _obs(timestamp, value, unit="wmoUnit:degC"):
    """Build a minimal NOAA observation feature."""
    return {"properties": {"timestamp": timestamp,
                            "temperature": {"unitCode": unit, "value": value}}}


# ── parse_iso_utc ────────────────────────────────────────────────────────

def test_parse_offset_form():
    assert parse_iso_utc("2026-05-31T16:00:00+00:00") == datetime(2026, 5, 31, 16, tzinfo=UTC)


def test_parse_z_suffix_uppercase_and_lower():
    # The whole point of the shim: 'Z' must work even on Python 3.9/3.10.
    expected = datetime(2026, 5, 31, 16, tzinfo=UTC)
    assert parse_iso_utc("2026-05-31T16:00:00Z") == expected
    assert parse_iso_utc("2026-05-31T16:00:00z") == expected


def test_parse_nonzero_offset_is_normalized_to_utc():
    assert parse_iso_utc("2026-05-31T08:00:00-08:00") == datetime(2026, 5, 31, 16, tzinfo=UTC)


def test_parse_naive_is_assumed_utc():
    assert parse_iso_utc("2026-05-31T16:00:00") == datetime(2026, 5, 31, 16, tzinfo=UTC)


def test_parse_garbage_and_non_string_returns_none():
    assert parse_iso_utc("not a timestamp") is None
    assert parse_iso_utc("") is None
    assert parse_iso_utc(None) is None
    assert parse_iso_utc(1717171717) is None


# ── standard_utc_offset ──────────────────────────────────────────────────

@pytest.mark.parametrize("zone, hours", [
    ("America/Los_Angeles", -8),   # PST
    ("America/Denver", -7),        # MST
    ("America/Chicago", -6),       # CST
    ("America/New_York", -5),      # EST
    ("America/Phoenix", -7),       # MST, never observes DST
])
def test_standard_offset_is_standard_time_not_dst(zone, hours):
    assert standard_utc_offset(zone) == timedelta(hours=hours)


def test_standard_offset_unknown_zone_is_none():
    assert standard_utc_offset("Not/ARealZone") is None
    assert standard_utc_offset(None) is None
    assert standard_utc_offset("") is None


# ── lst_day_utc_window ───────────────────────────────────────────────────

def test_window_uses_standard_offset_even_during_dst():
    # The crux: in July (PDT, civil offset -7) the LST window must still use the
    # STANDARD offset -8, so the day starts at 08:00 UTC, not 07:00 UTC.
    start, end = lst_day_utc_window("America/Los_Angeles", datetime(2026, 7, 15))
    assert start == datetime(2026, 7, 15, 8, tzinfo=UTC)
    assert end == datetime(2026, 7, 16, 8, tzinfo=UTC)


def test_window_is_exactly_24h_across_a_dst_transition():
    # A fixed offset means the day is always 24h, even on spring-forward day.
    start, end = lst_day_utc_window("America/New_York", datetime(2026, 3, 8))
    assert end - start == timedelta(hours=24)
    assert start == datetime(2026, 3, 8, 5, tzinfo=UTC)   # EST -5, applied year-round


def test_window_unknown_zone_is_none():
    assert lst_day_utc_window("Not/ARealZone", datetime(2026, 1, 1)) is None


# ── daily_high_f_from_observations ───────────────────────────────────────

def test_high_is_max_within_lst_day_excluding_adjacent_days():
    # Los Angeles, settlement day 2026-07-15. LST window = [07-15 08:00Z, 07-16 08:00Z).
    features = [
        # 07:30Z = 00:30 PDT clock on the 15th, but 23:30 LST on the 14th -> PREVIOUS
        # day. A UTC-calendar-day query (the old bug) would have wrongly counted this
        # 35C/95F prior-evening reading as the 15th's high. New code excludes it.
        _obs("2026-07-15T07:30:00+00:00", 35),
        # 23:00Z = 15:00 LST, the real afternoon high: 30C -> 86F. INCLUDED.
        _obs("2026-07-15T23:00:00+00:00", 30),
        # 07:30Z next day = 23:30 LST on the 15th -> still the target day. INCLUDED.
        _obs("2026-07-16T07:30:00+00:00", 20),
        # 08:00Z next day == window end (00:00 LST 07-16) -> next day, EXCLUDED (half-open).
        _obs("2026-07-16T08:00:00+00:00", 40),
    ]
    high = daily_high_f_from_observations(features, "America/Los_Angeles", datetime(2026, 7, 15))
    assert high == pytest.approx(86.0)   # not 95.0 (prior evening) and not 104.0 (next day)


def test_dst_midnight_boundary_assigned_to_previous_day():
    # A hot reading at local clock 00:30 during DST is 23:30 LST the prior day,
    # so it must NOT count toward today's high (Kalshi's documented LST rule).
    features = [_obs("2026-07-15T07:30:00+00:00", 40)]   # 00:30 PDT clock / 23:30 LST 07-14
    assert daily_high_f_from_observations(features, "America/Los_Angeles", datetime(2026, 7, 15)) is None
    # ...and it DOES count toward the previous settlement day.
    prev = daily_high_f_from_observations(features, "America/Los_Angeles", datetime(2026, 7, 14))
    assert prev == pytest.approx(104.0)


def test_null_and_missing_temperatures_are_skipped():
    features = [
        _obs("2026-01-15T20:00:00+00:00", None),                 # null value
        {"properties": {"timestamp": "2026-01-15T21:00:00+00:00"}},  # no temperature key
        _obs("2026-01-15T22:00:00+00:00", 10),                   # 10C -> 50F, the only usable one
    ]
    high = daily_high_f_from_observations(features, "America/New_York", datetime(2026, 1, 15))
    assert high == pytest.approx(50.0)


def test_celsius_conversion_and_fahrenheit_unit_tolerated():
    c = daily_high_f_from_observations(
        [_obs("2026-01-15T18:00:00+00:00", 0)], "America/New_York", datetime(2026, 1, 15))
    assert c == pytest.approx(32.0)
    f = daily_high_f_from_observations(
        [_obs("2026-01-15T18:00:00+00:00", 70, unit="wmoUnit:degF")],
        "America/New_York", datetime(2026, 1, 15))
    assert f == pytest.approx(70.0)


def test_empty_and_unknown_zone_return_none():
    assert daily_high_f_from_observations([], "America/New_York", datetime(2026, 1, 15)) is None
    assert daily_high_f_from_observations(
        [_obs("2026-01-15T18:00:00+00:00", 20)], "Not/AZone", datetime(2026, 1, 15)) is None


# ── regression guards (added after adversarial review) ───────────────────

@pytest.mark.parametrize("zone, before_utc", [
    ("America/New_York", "2026-07-15T04:30:00+00:00"),   # 00:30 EDT clock / 23:30 EST -> prev day
    ("America/Chicago",  "2026-07-15T05:30:00+00:00"),   # 00:30 CDT clock / 23:30 CST -> prev day
])
def test_eastern_central_zones_use_standard_offset_during_dst(zone, before_utc):
    # Mirror of the LA case for non-western zones: during DST a reading just after
    # local-clock midnight belongs to the PREVIOUS LST day, so it must not count
    # toward the 15th. Guards against a civil/DST-offset regression scoped to a
    # zone other than Los_Angeles (which the original suite only covered).
    features = [_obs(before_utc, 40)]
    assert daily_high_f_from_observations(features, zone, datetime(2026, 7, 15)) is None
    assert daily_high_f_from_observations(features, zone, datetime(2026, 7, 14)) == pytest.approx(104.0)


def test_window_start_boundary_is_inclusive():
    # An observation at exactly the LST-day start (08:00Z for LA) must be INCLUDED.
    # Pins the inclusive side of the half-open [start, end) window; a regression to
    # a strict `start < ts` would silently drop the first obs of the day.
    features = [_obs("2026-07-15T08:00:00+00:00", 20)]   # == window start, 20C -> 68F
    assert daily_high_f_from_observations(features, "America/Los_Angeles",
                                          datetime(2026, 7, 15)) == pytest.approx(68.0)


def test_z_suffixed_timestamp_flows_through_daily_high():
    # The Z-form is real NOAA output; ensure it survives the whole windowing path,
    # not just the parse_iso_utc unit test. NY LST window is [05:00Z, 05:00Z next).
    features = [_obs("2026-01-15T18:00:00Z", 10)]        # in-window, 10C -> 50F
    assert daily_high_f_from_observations(features, "America/New_York",
                                          datetime(2026, 1, 15)) == pytest.approx(50.0)


def test_all_in_window_temps_null_returns_none():
    # In-window observations exist but none has a usable temperature -> None.
    # (The only other null test mixes in a usable reading, masking this branch.)
    features = [
        _obs("2026-01-15T18:00:00+00:00", None),                  # null value, in window
        {"properties": {"timestamp": "2026-01-15T19:00:00+00:00"}},  # missing temperature, in window
    ]
    assert daily_high_f_from_observations(features, "America/New_York",
                                          datetime(2026, 1, 15)) is None


def test_unknown_unit_is_dropped_not_misconverted():
    # A non-degC/degF unit (e.g. Kelvin) must be dropped, not run through the
    # Celsius->F formula into a plausible-but-wrong number that poisons the max.
    features = [_obs("2026-01-15T18:00:00+00:00", 300, unit="wmoUnit:K")]
    assert daily_high_f_from_observations(features, "America/New_York",
                                          datetime(2026, 1, 15)) is None
