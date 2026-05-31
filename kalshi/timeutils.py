"""Timezone helpers for windowing NOAA observations into a settlement day.

Why this exists
---------------
Kalshi settles its daily-high temperature markets on the National Weather
Service Daily Climate Report (the ``CLI`` product). The NWS climatological day
for daily max/min temperature runs **00:00–23:59 Local Standard Time (LST),
year-round** — it does *not* shift with Daylight Saving Time, and it is
emphatically not a UTC calendar day. Straight from Kalshi's help center:

    "The NWS Climate Reports use local standard time when reporting daily high
     temperatures. This means that during Daylight Saving Time, the high
     temperature will be recorded between 1:00 AM and 12:59 AM local time the
     following day - not based on the standard midnight-to-midnight range."
    — https://help.kalshi.com/markets/popular-markets/weather-markets

The old settlement code windowed observations by the **UTC** calendar day
(``...T00:00:00Z`` to ``...T23:59:59Z``). That window is offset from the city's
real settlement day by the local UTC offset (5–8 hours) and straddles two local
days, so it could pick up the *previous* evening's warmth or miss a late-
afternoon peak — corrupting both realized P&L and the per-provider accuracy
labels that train the ensemble reweighting.

This module computes the correct **LST day** window. We derive each city's
standard-time offset from its IANA zone (already stored in ``CITY_CONFIGS``) and
apply it as a fixed offset every day of the year, so DST never moves the
boundary.

Known approximation: the official CLI daily max comes from the ASOS max-temp
algorithm, whereas here we take ``max`` over the station's raw observation
``temperature`` values in the LST window. With ~5-minute reporting cadence this
is a close proxy, but it can differ from the official figure by a tenth or two.

Pure standard library; safe to import without credentials or network.
"""
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - zoneinfo is stdlib on Python 3.9+
    ZoneInfo = None

    class ZoneInfoNotFoundError(Exception):
        pass


_UTC = timezone.utc
_ZERO = timedelta(0)

# Year used to sample a zone's standard-time offset. Any year since the 2007
# US DST rule change works; callers may override with the settlement year so a
# historical tz change is honored.
_REF_YEAR = 2021


def parse_iso_utc(value):
    """Parse an ISO-8601 timestamp into an aware UTC ``datetime``, or ``None``.

    Normalizes a trailing ``Z``/``z`` to ``+00:00`` first: ``fromisoformat``
    only learned to accept the ``Z`` suffix in Python 3.11, and the daemon's CI
    still covers 3.9/3.10. A naive timestamp (no offset) is assumed to be UTC.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text[-1] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def standard_utc_offset(tz_name, year=_REF_YEAR):
    """Return an IANA zone's *standard-time* (non-DST) UTC offset, or ``None``.

    Samples mid-winter and mid-summer and returns the offset from whichever has
    no DST in effect (``dst() == 0``). This is correct in both hemispheres and
    for zones that never observe DST (e.g. America/Phoenix), where both samples
    already report the standard offset.
    """
    if ZoneInfo is None or not tz_name:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    for month in (1, 7):
        probe = datetime(year, month, 15, 12, tzinfo=tz)
        if probe.dst() == _ZERO:
            return probe.utcoffset()
    # No DST-free sample (not expected for real zones) — fall back to January.
    return datetime(year, 1, 15, 12, tzinfo=tz).utcoffset()


def lst_day_utc_window(tz_name, target_date):
    """``(start_utc, end_utc)`` bounding the LST settlement day, or ``None``.

    ``target_date`` is a ``date``/``datetime`` whose year/month/day name the
    settlement (LST) calendar day. The returned bounds are aware UTC datetimes
    forming a **half-open** ``[start, end)`` interval — matching both the NOAA
    API's server-side filtering and :func:`daily_high_f_from_observations`.

    The window is built in a *fixed* offset (the zone's standard offset), so it
    is exactly 24h every day and never moves with DST.
    """
    offset = standard_utc_offset(tz_name, getattr(target_date, "year", _REF_YEAR))
    if offset is None:
        return None
    fixed = timezone(offset)
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=fixed)
    end = start + timedelta(days=1)
    return start.astimezone(_UTC), end.astimezone(_UTC)


def _observation_temp_f(props):
    """Convert one observation's ``properties.temperature`` to °F, or ``None``.

    NOAA reports Celsius (``unitCode`` ``wmoUnit:degC``) and the value is
    nullable. A missing unit is assumed Celsius (the documented default); an
    explicit Fahrenheit unit is passed through. Any *other* unit (e.g. Kelvin)
    is dropped rather than silently mis-converted into a plausible-looking but
    wrong °F number that would poison the daily max.
    """
    temp = props.get("temperature") or {}
    value = temp.get("value")
    if not isinstance(value, (int, float)):
        return None
    unit = temp.get("unitCode") or "wmoUnit:degC"
    if "degC" in unit:
        return value * 9 / 5 + 32
    if "degF" in unit:
        return float(value)
    return None


def daily_high_f_from_observations(features, tz_name, target_date):
    """Max observed temperature (°F) over the LST settlement day, or ``None``.

    ``features`` is the ``features`` list from a NOAA observations GeoJSON
    response. Each observation is kept only if ``properties.timestamp`` falls in
    the half-open ``[start, end)`` LST-day window; observations with a missing
    or null temperature are skipped. Returns ``None`` if the timezone is unknown
    or no usable observation falls in the window.
    """
    window = lst_day_utc_window(tz_name, target_date)
    if window is None:
        return None
    start, end = window

    highs = []
    for feature in features or []:
        props = (feature or {}).get("properties") or {}
        ts = parse_iso_utc(props.get("timestamp"))
        if ts is None or not (start <= ts < end):
            continue
        temp_f = _observation_temp_f(props)
        if temp_f is not None:
            highs.append(temp_f)

    return max(highs) if highs else None
