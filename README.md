# Kalshi Weather Trading Daemon 🌤️⚡

An automated trading daemon that prices Kalshi daily-high-temperature prediction markets against an ensemble of weather forecasts (NOAA, GFS, ICON, ECMWF, GEM). It converts forecasts into calibrated win probabilities via normal-CDF strike geometry, blends them with the market in log-odds space, and sizes trades with quarter-Kelly behind a multi-tier risk framework.

> **Project status:** This is a paper-trading **logic harness and research tool**, not a profit-validated strategy. It has not been run long enough to demonstrate an edge, and the paper-trading simulator assumes frictionless fills. Treat any results as illustrative, not predictive. See the [Roadmap](#roadmap) for what's planned vs. implemented.

## ⚠️ Financial Risk Warning

**In live mode this daemon trades real money.** You can lose your entire account balance.

- ✅ **Paper trading is the default and is STRONGLY recommended** for testing and learning
- 🧪 `PAPER_TRADING=true` in your `.env` (the default) means no real orders are placed
- 💰 If you ever go live, start with trivial amounts ($10–50 max)
- 📚 This is educational software, not financial advice
- ⚖️ No warranty or liability for losses

**By using this software, you accept full responsibility for any financial outcomes.**

---

## Features

### Core Trading
- **11-city coverage:** PHX, SFO, SEA, DC, HOU, NOLA, DAL, BOS, OKC, ATL, MIN
- **5-provider ensemble forecasting:** NOAA NWS + Open-Meteo GFS, ICON, ECMWF, GEM
- **Calibrated probabilities:** normal-CDF over the strike geometry (`less` / `greater` / `between`) using per-city × per-season volatility priors and lead-time scaling
- **Bayesian market blend:** model and market probabilities combined in log-odds space (default 30% model / 70% market)
- **Risk management:**
  - Quarter-Kelly position sizing
  - Daily / weekly loss circuit breaker (includes worst-case open exposure)
  - Correlation-group limits
  - Per-city-per-date and per-ticker position deduplication
  - Telegram circuit-breaker / system alerts

### Analytics
- Per-city × per-season standard deviations
- Static per-(provider, city) bias correction **plus** dynamic accuracy-based reweighting (the ensemble learns from settlements)
- Lead-time accuracy scaling (same-day forecasts treated as ~2× more accurate)
- NOAA staleness detection (weight penalty when the NWS forecast is stale)
- Structured JSONL decision + settlement logs (the raw material for backtesting/calibration)

### Safety
- **Paper trading mode** (simulated fills, no order API calls) running the *identical* risk pipeline as live
- Edge sanity caps (prevents trading on stale, implausibly large edges)
- Spread / liquidity, strike-proximity, model-vs-market disagreement, and ratio filters

---

## Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements (only `requests` and `cryptography`)
pip install -r requirements.txt
```

Python 3.9+ is recommended.

### 2. Get Kalshi API Credentials

1. Sign up at [kalshi.com](https://kalshi.com)
2. Go to **Account → API Keys**
3. Generate a new API key
4. Download the private key file (e.g. `kalshi_private.pem`)
5. Copy your API Key ID

### 3. Configure Environment

```bash
# Copy example config
cp .env.example .env

# Edit .env with your credentials
nano .env
```

**Minimal .env:**
```bash
KALSHI_API_KEY_ID=your_api_key_id_here
KALSHI_PRIVATE_KEY_PATH=./kalshi_private.pem
PAPER_TRADING=true
```

> Note: even in paper mode the daemon fetches your **real** account balance for reference, so valid credentials are required to start.

### 4. Run Paper Trading (Safe Mode)

```bash
# Make sure PAPER_TRADING=true in .env
python kalshi_unified.py
```

You should see output like:
```
======================================================================
PAPER TRADING MODE ACTIVE — no real money at risk
Unified Kalshi Weather Daemon — 11 cities
Providers: 5 | Min edge: 10c | Min confidence: 0.5
Cities: ATL, BOS, DAL, DC, HOU, MIN, NOLA, OKC, PHX, SEA, SFO
```

---

## Configuration

> **What's read from `.env`:**
> Credentials and mode (`PAPER_TRADING`, the two `KALSHI_*`, the two `TELEGRAM_*`, and the optional `WEATHER_ACCURACY_PATH`), **plus all of the trading and risk knobs in the tables below** — each is overridable by an environment variable of the same name (see `kalshi/config.py`). An unset variable keeps the coded default shown here; an unparseable value is ignored with a warning rather than crashing the daemon.
>
> For the paper/live split, the **mode sets the default** and an explicit env var wins over it (e.g. `MIN_EDGE_CENTS=12` overrides whichever paper/live default would otherwise apply). Model-internal parameters (forecast σ, NOAA staleness penalty, etc.) are intentionally *not* env-overridable — tune those in code.

Several thresholds differ between **paper** and **live** mode (paper loosens filters for more opportunity volume).

### Trading Parameters (`kalshi/config.py`)

| Parameter | Paper | Live | Description |
|-----------|-------|------|-------------|
| `PAPER_TRADING` | `true` | `false` | Safe mode (no real orders); read from `.env` |
| `MAX_CONTRACTS` | 8 | 8 | Max contracts per trade |
| `MAX_COST_PER_TRADE` | 500¢ | 500¢ | Max cost per trade ($5) |
| `MAX_OPEN_POSITIONS` | 20 | 20 | Max simultaneous positions |
| `MAX_DAILY_TRADES` | 40 | 40 | Daily trade limit |
| `MIN_VOLUME` | 10 | 10 | Min market volume to consider |
| `MIN_EDGE_CENTS` | 10¢ | 15¢ | Minimum confidence-adjusted edge to trade |
| `MIN_CONFIDENCE_SCORE` | 0.5 | 0.6 | Minimum forecast confidence |
| `MODEL_WEIGHT` | 0.30 | 0.30 | Weight on the model in the log-odds blend |
| `MAX_DISAGREEMENT_CENTS` | 40¢ | 25¢ | Skip if model/market disagree by more |
| `MAX_FAIR_MARKET_RATIO` | 3.5× | 3.0× | Skip if fair/market ratio exceeds |
| `MIN_YES_PRICE` / `MIN_NO_PRICE` | 5¢ | 15¢ | Reject cheap tail bets below this |
| `MAX_EDGE_CENTS` | 60¢ | 60¢ | Edge sanity cap (likely stale → skip) |
| `MAX_SPREAD` | 30¢ | 30¢ | Skip illiquid markets above this spread |

**Polling interval is dynamic** (`get_poll_interval()` in `kalshi/forecast.py`): ~5 min around weather-model update hours, ~10 min shortly after, ~30 min during quiet hours. (The legacy `POLL_INTERVAL=900` constant is not used by the daemon.)

### Risk Limits (`kalshi/config.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_DAILY_LOSS_CENTS` | 500¢ ($5) | Daily stop-loss (circuit breaker) |
| `MAX_WEEKLY_LOSS_CENTS` | 1000¢ ($10) | Weekly stop-loss |
| `MAX_PER_GROUP` | 2 | Max positions per correlation group |
| `MAX_PER_CITY_DATE` | 1 | Max per city per settlement date |

---

## Going Live (Advanced)

### ⚠️ Read This First

Live trading is **high risk**. Only proceed if you:
- ✅ Understand prediction markets and weather forecasting
- ✅ Have run paper trading for at least 1–2 weeks
- ✅ Are comfortable losing your entire account balance
- ✅ Have read the entire codebase

### Pre-Flight Checklist (manual)

Before setting `PAPER_TRADING=false`, verify by hand:
- `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` are correct and the daemon prints a sane **real** balance on startup
- Network connectivity to `api.elections.kalshi.com`, `api.weather.gov`, and `api.open-meteo.com`
- The risk limits (`MAX_DAILY_LOSS_CENTS`, `MAX_CONTRACTS`, etc.) — set in `.env` or as constants in `kalshi/config.py` — match your risk tolerance
- You have a small balance funded ($10–20)

> An automated `preflight_checklist.py` is on the [Roadmap](#roadmap) but is **not** implemented yet.

### Enable Live Trading

1. Set `PAPER_TRADING=false` in `.env`
2. Start with minimal capital ($10–20)
3. Monitor closely for the first 24 hours
4. Watch the logs: `tail -f kalshi_unified_log.txt`

---

## File Structure

```
kalshi-weather-arbitrage-daemon/
├── kalshi_unified.py           # Main daemon — thin orchestration loop
├── kalshi/                     # Core package
│   ├── config.py               # Config, .env loading, city/series data, risk limits
│   ├── kalshi_api.py           # Kalshi REST client + RSA-PSS request signing
│   ├── forecast.py             # Ensemble interface, NOAA staleness, smart poll timing
│   ├── probability.py          # Normal CDF, log-odds blend, fair prob, Kelly sizing
│   ├── scanner.py              # Opportunity scanner + filter cascade
│   ├── execution.py            # Trade execution + risk management
│   ├── settlement.py           # Settlement, P&L, per-provider accuracy feedback loop
│   ├── state.py                # State + P&L persistence
│   ├── notifications.py        # Telegram notifications
│   └── logger.py               # Logging + JSONL writers
├── weather_providers.py        # 5-provider weather ensemble + accuracy reweighting
├── paper_trading_safety.py     # Paper-trading mock layer (balance / order stubs)
├── requirements.txt            # Python dependencies
├── .env.example                # Config template
├── LICENSE                     # MIT
├── README.md                   # This file
├── kalshi_private.pem          # YOUR private key (gitignored)
└── .env                        # YOUR config (gitignored)
```

### Generated Files (Gitignored)

```
kalshi_unified_log.txt        # Trading log
kalshi_unified_state.json     # Position / balance state
kalshi_pnl.json               # P&L tracking
kalshi_backtest_log.jsonl     # Per-decision scan log (trades AND skips)
kalshi_settlement_log.jsonl   # Settlement history (predicted vs. actual)
paper_trades.jsonl            # Paper-trade open/settle log
weather_accuracy.json         # Per-provider rolling accuracy (drives reweighting)
```

---

## Optional: Telegram Notifications

Get real-time trade alerts via Telegram.

### Setup

1. **Create a bot:**
   - Message [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` and follow the prompts
   - Copy the bot token

2. **Get your chat ID:**
   - Message your bot
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Find your `chat.id` in the JSON

3. **Add to .env:**
```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

> By default, paper-trade notifications are suppressed (`PAPER_TRADING_NOTIFICATIONS = False` in `kalshi/config.py`); system/circuit-breaker alerts still fire.

### Notification Types

- 📝 **Trade Opened** (paper/live)
- 🎯 **Position Settled** (win/loss)
- 🚨 **System Alerts** (circuit breaker, errors)
- 📅 **Daily Summary** — *(planned: the `notify_daily_summary` function exists but is not yet scheduled)*

---

## Strategy Overview

### Ensemble Forecasting

The bot aggregates daily-high forecasts from 5 free providers. **Base weights** (in `weather_providers.build_ensemble`):

1. **NOAA** (National Weather Service) — weight **1.2** (slight premium; US gold standard)
2. **GFS** (Global Forecast System, via Open-Meteo) — weight **1.0**
3. **ICON** (German DWD, via Open-Meteo) — weight **0.9**
4. **ECMWF** (European model, via Open-Meteo) — weight **1.0**
5. **GEM** (Canadian CMC, via Open-Meteo) — weight **0.8**

These base weights are then adjusted two ways:
- **Static bias correction:** per-(provider, city) offsets in `MODEL_BIAS` (`kalshi/config.py`) subtracted from each forecast.
- **Dynamic accuracy reweighting:** each provider's weight is scaled by its recent forecast error over a 30-day window (inverse-error, bounded to 0.25×–2.0×, requires ≥5 samples) — populated from settlements via the feedback loop.
- **NOAA staleness penalty:** if the NWS forecast is older than `NOAA_STALE_HOURS` (6h), NOAA's weight is halved.

### Edge Calculation

```
Model probability → log-odds (Bayesian) blend with market price → Edge
Edge = (Blended fair − executable price − half-spread haircut) × forecast confidence
```

- **Model weight:** 30% (default `MODEL_WEIGHT`); the blend assumes the market is mostly efficient
- **Confidence adjustment:** the raw edge is scaled by a 0–1 confidence score derived from provider agreement and count
- The **NO** side is correctly priced off `yes_bid` (buying NO ≡ selling YES at the bid)

### Filters (cheap → expensive)

1. **Volume / spread:** skip thin or illiquid markets (spread > 30¢)
2. **Strike proximity:** skip when the forecast is too close to a strike (paper 0.2°F / live 1.5°F)
3. **Provider spread:** skip if the providers disagree by > 6°F
4. **Model disagreement:** skip if model vs. market differ by more than `MAX_DISAGREEMENT_CENTS` (paper 40¢ / live 25¢)
5. **Ratio filter:** skip if fair/market ratio exceeds `MAX_FAIR_MARKET_RATIO` (paper 3.5× / live 3.0×)
6. **Edge floor/ceiling:** require ≥ `MIN_EDGE_CENTS`, reject implausible edges > `MAX_EDGE_CENTS` (60¢, likely stale)

### Position Sizing

**Quarter-Kelly criterion** for binary contracts:
```
f* = (p · b − q) / b           # full Kelly
size = f* · 0.25               # quarter-Kelly fraction of bankroll
```
Where `p` = fair probability, `q = 1 − p`, `b = payout/cost = (100 − price)/price`. The result is capped by `MAX_COST_PER_TRADE`, `MAX_CONTRACTS`, and a balance reserve.

### Settlement & Feedback Loop

When Kalshi marks a market settled, the daemon records win/loss and realized P&L, then closes the learning loop:

- **Actual high temperature** is read from NOAA station observations, windowed by the city's **Local Standard Time (LST) day** — the same NWS climatological day Kalshi settles on. LST is used year-round (not the DST-shifted civil day, and not a UTC calendar day), so a reading just after local-clock midnight during summer correctly belongs to the *previous* settlement day. See `kalshi/timeutils.py`.
- That observed high becomes the **label** that scores each provider's forecast for the day, feeding the dynamic accuracy reweighting described above (persisted to `weather_accuracy.json`).
- Each position's predicted win-probability and realized outcome are appended to `kalshi_settlement_log.jsonl`, which `analyze_calibration.py` consumes for the Brier-score / reliability / edge-realization report.

---

## Monitoring

### Logs

```bash
# Live tail
tail -f kalshi_unified_log.txt

# Trades and settlements
grep "TRADE:" kalshi_unified_log.txt
grep "SETTLED:" kalshi_unified_log.txt
```

### State Inspection

```bash
# Current positions
cat kalshi_unified_state.json | jq '.positions'

# P&L
cat kalshi_pnl.json | jq
```

### Inspecting Paper Trades

```bash
# Open paper positions and settlements are logged as JSONL
cat paper_trades.jsonl | jq -c '{ts: .timestamp, ticker, side, status, pnl_cents}'
```

> Run `python analyze_calibration.py` for a Brier-score / reliability / realized-vs-predicted-edge report over the settlement log (prints a friendly "no data yet" message on a fresh checkout). A `paper_summary.py` (win rate, ROI) summarizer is still on the [Roadmap](#roadmap).

---

## Troubleshooting

### "Invalid API credentials"
- Check `KALSHI_API_KEY_ID` is correct
- Ensure the file at `KALSHI_PRIVATE_KEY_PATH` exists and matches your account
- Try regenerating the API key from the Kalshi dashboard

### "NOAA stale (8.5h) — re-running ensemble with 0.5x weight penalty"
- NOAA/NWS updates roughly every ~6 hours
- NOAA's ensemble weight is automatically reduced while stale
- GFS/ICON/ECMWF/GEM continue to provide fresh forecasts

### "CIRCUIT BREAKER ... stopping trades"
- A daily or weekly loss limit (including worst-case open exposure) was reached
- Trading is paused for the period
- Adjust `MAX_DAILY_LOSS_CENTS` / `MAX_WEEKLY_LOSS_CENTS` (in `.env` or `kalshi/config.py`)

### No opportunities found
- Markets may be efficiently priced (the 30/70 blend deliberately shrinks toward the market)
- In paper mode the thresholds are already looser
- Check whether settlement dates are too far out

---

## Development

### Adding a New City

1. **Find NOAA grid coordinates** — visit [weather.gov](https://weather.gov), search the city, and note the office code + grid X/Y from the forecast URL.
2. **Find the NOAA observation station** — see the [NWS API docs](https://www.weather.gov/documentation/services-web-api).
3. **Add to `CITY_CONFIGS` in `weather_providers.py`:**
```python
'NYC': {
    'name': 'New York City',
    'lat': 40.7128, 'lon': -74.0060,
    'noaa_office': 'OKX', 'noaa_grid_x': 33, 'noaa_grid_y': 37,
    'timezone': 'America/New_York',
    'station': 'KNYC',
},
```
4. **Add to `SERIES` in `kalshi/config.py`:**
```python
'NYC': 'KXHIGHTNYC',
```
5. *(Optional)* Add a `CITY_STD_DEV['NYC']` entry and any `MODEL_BIAS` offsets in `kalshi/config.py`.

### Tests

```bash
# Unit tests over the pure math, .env parser, and settlement-window logic
pip install -r requirements-dev.txt
python -m pytest

# Exercise the weather ensemble across all cities (hits live forecast APIs)
python -c "from weather_providers import test_ensemble; test_ensemble()"

# End-to-end dry run (paper mode does not place real orders)
python kalshi_unified.py
```

---

## Roadmap

**Recently shipped:**

- **Test suite + CI** — `pytest` over the pure math (`normal_cdf`, `market_adjusted_fair`, `fair_probability`, `kelly_size`), the `.env` parser, and the settlement-window logic, on GitHub Actions (Python 3.9 / 3.11 / 3.12)
- **Calibration report** — `analyze_calibration.py` over `kalshi_settlement_log.jsonl`: Brier score, reliability diagram, realized-vs-predicted edge
- **Local-standard-time settlement window** — the actual-high lookup now uses the city's NWS climatological (LST) day, matching how Kalshi settles, instead of the UTC calendar day
- **`.env`-overridable limits** — every trading/risk knob in the tables above is overridable from the environment (env var name == constant name)

**Planned but not yet implemented:**

- **Backtest analyzer** — consumer for `kalshi_backtest_log.jsonl` (skip-reason histogram, edge distribution, threshold sweeps)
- **Paper summary** — `paper_summary.py` (win rate, ROI) and `preflight_checklist.py`
- **Web dashboard** — a read-only FastAPI + React view of positions, equity curve, and reliability metrics
- **Execution realism** — model basic slippage / partial fills in the paper simulator

---

## Contributing

Contributions welcome! Good first areas:
- A test suite and CI (see Roadmap)
- A calibration / backtest analysis layer over the existing JSONL logs
- Additional weather providers
- Portfolio optimization

Please open an issue before major changes.

---

## License

MIT License — see the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

- Weather data: NOAA/NWS, Open-Meteo, DWD (ICON), ECMWF, CMC (GEM)
- Market data: Kalshi API
- Inspired by statistical arbitrage and ensemble forecasting research

---

## Disclaimer

This software is provided "as-is" for educational purposes. No warranty, express or implied. Not financial advice. Use at your own risk. The author is not responsible for any financial losses incurred through use of this software.

Prediction markets involve substantial risk. Past performance does not guarantee future results.

---

## Contact

- **Issues:** [GitHub Issues](https://github.com/Tyler-Irving/kalshi-weather-arbitrage-daemon/issues)
- **Discussions:** [GitHub Discussions](https://github.com/Tyler-Irving/kalshi-weather-arbitrage-daemon/discussions)

---

**Happy (paper) trading! 🌦️📈**
