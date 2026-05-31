"""
Settlement checker.

Polls Kalshi for settled markets, computes P&L, fetches actual observed
temperatures for the feedback loop, and records everything to JSONL logs.
"""
import json
import re
from datetime import datetime, timezone

import requests

from weather_providers import CITY_CONFIGS

from kalshi.config import SETTLEMENT_LOG_PATH
from kalshi.kalshi_api import kalshi_request
from kalshi.forecast import weather_ensemble
from kalshi.probability import MONTH_MAP
from kalshi.state import record_pnl
from kalshi.logger import log, log_paper_trade
from kalshi.notifications import notify_settlement
from kalshi.timeutils import lst_day_utc_window, daily_high_f_from_observations


# ── NOAA observations for actual high temp ───────────────────────────────

def fetch_actual_high_temp(city, target_date):
    """Fetch the observed high temperature from NOAA for a city/date.

    The observation window is the city's Local Standard Time day — the same
    climatological day the NWS (and therefore Kalshi) uses to settle these
    markets — not the UTC calendar day. See ``kalshi.timeutils`` for why.

    Returns temperature in °F, or None on failure.
    """
    try:
        city_cfg = CITY_CONFIGS.get(city)
        if not city_cfg or 'station' not in city_cfg:
            log(f"  No station configured for {city}")
            return None

        station = city_cfg['station']
        tz_name = city_cfg.get('timezone')
        date_str = target_date.strftime("%Y-%m-%d")

        window = lst_day_utc_window(tz_name, target_date)
        if window is None:
            log(f"  No usable timezone for {city} ({tz_name!r}); cannot window observations")
            return None
        start_utc, end_utc = window

        url = f"https://api.weather.gov/stations/{station}/observations"
        # Server-side filter to the LST day (UTC bounds); limit covers a full
        # day of ~5-minute ASOS observations. The window is half-open [start, end).
        params = {'start': start_utc.isoformat(), 'end': end_utc.isoformat(), 'limit': 500}
        headers = {'User-Agent': 'KaelWeatherBot/2.0'}

        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()

        features = r.json().get('features', [])
        if len(features) >= 500:
            # Hit the API's max page size. Observations come newest-first, so any
            # dropped rows are the oldest (overnight) ones — never the mid-afternoon
            # high — but log it so a silently-capped day is at least visible.
            log(f"  Note: {station} hit the 500-observation cap for {date_str}"
                f" (LST day); using the most recent 500 — daily high is unaffected")

        actual_high = daily_high_f_from_observations(features, tz_name, target_date)

        if actual_high is not None:
            log(f"  Actual high for {city} on {date_str} (LST day): {actual_high:.1f}°F")
            return actual_high

        log(f"  No valid observations for {station} on {date_str}")
        return None
    except Exception as e:
        log(f"  Error fetching actual temp for {city}: {e}")
        return None


# ── Settlement processing ────────────────────────────────────────────────

def check_settled(state):
    """Walk open positions and resolve any that Kalshi has settled."""
    new_positions = []

    for pos in state.get('positions', []):
        try:
            data = kalshi_request('GET', f'/trade-api/v2/markets/{pos["ticker"]}')
            m = data.get('market', {})
            result = m.get('result')

            if not result:
                new_positions.append(pos)
                continue

            is_paper = pos.get('paper_trade', False)
            won = (result == pos['side'])
            pnl = (100 - pos['price']) * pos['count'] if won else -(pos['price'] * pos['count'])
            state['total_pnl_cents'] = state.get('total_pnl_cents', 0) + pnl

            actual_temp = _fetch_and_record_accuracy(pos)

            _log_settlement(pos, result, won, pnl, actual_temp, is_paper)
            _log_paper_settlement(pos, result, won, pnl, actual_temp, is_paper)

            trade_type = "PAPER " if is_paper else ""
            actual_str = f" | Actual: {actual_temp:.1f}°F" if actual_temp else ""
            log(f"{trade_type}SETTLED: {pos['ticker']} -> {'WIN' if won else 'LOSS'}"
                f" ${pnl/100:.2f} (total: ${state['total_pnl_cents']/100:.2f}){actual_str}")

            notify_settlement({
                'ticker': pos['ticker'],
                'won': won,
                'pnl_cents': pnl,
                'total_pnl_cents': state['total_pnl_cents'],
                'actual_temp': actual_temp,
                'forecast': pos.get('forecast'),
                'is_paper': is_paper,
            })

            record_pnl(pnl, pos['ticker'])

        except Exception as e:
            log(f"ERROR checking settlement for {pos.get('ticker', '?')}: {e}")
            new_positions.append(pos)

    state['positions'] = new_positions


# ── Internal helpers ─────────────────────────────────────────────────────

def _parse_settlement_date(pos):
    """Extract the settlement date from a position record.

    Tries the ticker regex first, then falls back to the stored target_date
    field so settlement still works if Kalshi changes their ticker format.
    """
    ticker = pos.get('ticker', '')
    date_match = re.search(
        r'-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})-',
        ticker, re.IGNORECASE,
    )
    if date_match:
        month_str = date_match.group(2).lower()[:3]
        month = MONTH_MAP.get(month_str)
        if month is not None:
            yy = int(date_match.group(1))
            dd = int(date_match.group(3))
            try:
                return datetime(2000 + yy, month, dd)
            except ValueError:
                pass

    # Fallback: use the target_date stored at trade time
    target_date_str = pos.get('target_date')
    if target_date_str:
        try:
            return datetime.strptime(target_date_str, "%Y-%m-%d")
        except ValueError:
            pass

    log(f"  Could not determine settlement date for {ticker}")
    return None


def _fetch_and_record_accuracy(pos):
    """Try to fetch actual temp and record per-provider accuracy. Returns temp or None."""
    city = pos.get('city')
    if not city or 'ticker' not in pos:
        return None

    try:
        settlement_date = _parse_settlement_date(pos)
        if settlement_date is None:
            return None

        actual_temp = fetch_actual_high_temp(city, settlement_date)

        if actual_temp is not None:
            individual = pos.get('ensemble_details', {}).get('individual_forecasts', {})
            for provider_name, forecast_value in individual.items():
                weather_ensemble.record_accuracy(provider_name, forecast_value, actual_temp)
                log(f"  Recorded {provider_name}: predicted={forecast_value:.1f}°F actual={actual_temp:.1f}°F")

        return actual_temp
    except Exception as e:
        log(f"  Error in feedback loop for {pos.get('ticker', '?')}: {e}")
        return None


def _log_settlement(pos, result, won, pnl, actual_temp, is_paper):
    """Write a JSONL entry to the settlement log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": pos["ticker"],
        "city": pos.get("city"),
        "side": pos["side"],
        "count": pos["count"],
        "price_cents": pos["price"],
        "cost_cents": pos["price"] * pos["count"],
        "result": result,
        "won": won,
        "pnl_cents": pnl,
        "forecast": pos.get("forecast"),
        "fair_cents": pos.get("fair_cents"),
        # Side-specific blended fair in cents == model's P(this position wins) * 100.
        # Logged explicitly so calibration analysis (analyze_calibration.py) has a
        # clean predicted-probability column to score against the realized outcome.
        "predicted_fair_cents": pos.get("fair"),
        "raw_edge": pos.get("raw_edge"),
        "adjusted_edge": pos.get("adjusted_edge"),
        "confidence": pos.get("confidence"),
        "ensemble_details": pos.get("ensemble_details"),
        "trade_time": pos.get("trade_time"),
        "actual_temp": actual_temp,
        "paper_trade": is_paper,
    }
    try:
        with open(SETTLEMENT_LOG_PATH, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        log(f"  Error writing settlement log: {e}")


def _log_paper_settlement(pos, result, won, pnl, actual_temp, is_paper):
    """Write a paper-trade settlement entry if applicable."""
    if not is_paper:
        return
    log_paper_trade({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": pos["ticker"],
        "side": pos["side"],
        "price": pos["price"],
        "count": pos["count"],
        "cost": pos["price"] * pos["count"],
        "forecast": pos.get("forecast"),
        "fair_cents": pos.get("fair_cents"),
        "edge": pos.get("adjusted_edge"),
        "confidence": pos.get("confidence"),
        "reason": "settlement",
        "settlement_date": pos.get("target_date"),
        "city": pos.get("city"),
        "status": "settled",
        "result": result,
        "won": won,
        "pnl_cents": pnl,
        "actual_temp": actual_temp,
    })
