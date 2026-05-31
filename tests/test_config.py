"""Tests for configuration helpers and the hardened .env parser.

The .env parsing tests pin the safety fix: an inline `# comment` after a value
must not leak into the value (which previously could flip PAPER_TRADING off and
silently enable live trading).
"""
import importlib
import os
from datetime import datetime

import pytest

import kalshi.config as config
from kalshi.config import (
    _strip_inline_comment,
    _parse_env_line,
    _load_env,
    _env_int,
    _env_float,
    get_season,
    get_city_std_dev,
    get_correlation_group,
    FORECAST_STD_DEV,
    CITY_STD_DEV,
)


# ── inline-comment stripping (the safety fix) ────────────────────────────

def test_strip_inline_comment_removes_trailing_comment():
    assert _strip_inline_comment("true  # set to false") == "true  "


def test_strip_inline_comment_no_comment_is_identity():
    assert _strip_inline_comment("true") == "true"


def test_strip_preserves_hash_inside_quotes():
    assert _strip_inline_comment('"a # b"') == '"a # b"'


def test_strip_preserves_hash_without_leading_whitespace():
    # A '#' that is part of a token (not preceded by space) is kept.
    assert _strip_inline_comment("abc#123") == "abc#123"


# ── full-line parsing ─────────────────────────────────────────────────────

def test_parse_paper_trading_inline_comment_is_safe():
    # The footgun: must parse to exactly "true", not "true  # ...".
    assert _parse_env_line("PAPER_TRADING=true  # set to 'false' for live") == (
        "PAPER_TRADING", "true",
    )


def test_parse_strips_export_prefix():
    assert _parse_env_line("export KALSHI_API_KEY_ID=abc123") == (
        "KALSHI_API_KEY_ID", "abc123",
    )


def test_parse_strips_surrounding_quotes():
    assert _parse_env_line('TOKEN="abc:def"') == ("TOKEN", "abc:def")
    assert _parse_env_line("TOKEN='xyz'") == ("TOKEN", "xyz")


def test_parse_preserves_hash_in_quoted_value():
    assert _parse_env_line('SECRET="a#b#c"') == ("SECRET", "a#b#c")


def test_parse_value_with_embedded_equals():
    assert _parse_env_line("URL=https://x/y?a=b") == ("URL", "https://x/y?a=b")


def test_parse_skips_blanks_and_comments():
    assert _parse_env_line("") is None
    assert _parse_env_line("   ") is None
    assert _parse_env_line("# a comment") is None
    assert _parse_env_line("no_equals_here") is None


# ── _load_env integration (temp file) ────────────────────────────────────

def test_load_env_reads_and_does_not_override(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n"
        "FOO_TEST_UNIQUE=bar  # inline note\n"
        "export BAZ_TEST_UNIQUE='q u x'\n"
        "PREEXISTING_TEST_UNIQUE=fromfile\n"
    )
    os.environ["PREEXISTING_TEST_UNIQUE"] = "preset"
    try:
        _load_env(env_file)
        assert os.environ["FOO_TEST_UNIQUE"] == "bar"
        assert os.environ["BAZ_TEST_UNIQUE"] == "q u x"
        # setdefault must not clobber an already-set variable
        assert os.environ["PREEXISTING_TEST_UNIQUE"] == "preset"
    finally:
        for k in ("FOO_TEST_UNIQUE", "BAZ_TEST_UNIQUE", "PREEXISTING_TEST_UNIQUE"):
            os.environ.pop(k, None)


def test_load_env_missing_file_is_noop(tmp_path):
    _load_env(tmp_path / "does_not_exist.env")  # must not raise


# ── season / std-dev / correlation helpers ───────────────────────────────

def test_get_season_boundaries():
    assert get_season(datetime(2026, 1, 15)) == "winter"
    assert get_season(datetime(2026, 4, 15)) == "spring"
    assert get_season(datetime(2026, 7, 15)) == "summer"
    assert get_season(datetime(2026, 10, 15)) == "fall"


def test_get_city_std_dev_known_city():
    assert get_city_std_dev("MIN", datetime(2026, 1, 15)) == CITY_STD_DEV["MIN"]["winter"]


def test_get_city_std_dev_unknown_city_falls_back():
    assert get_city_std_dev("ZZZ", datetime(2026, 1, 15)) == FORECAST_STD_DEV


def test_get_correlation_group_known_and_unknown():
    assert get_correlation_group("HOU") == "gulf_south"
    assert get_correlation_group("ZZZ") == "ZZZ"   # unconfigured city maps to itself


# ── typed env readers (_env_int / _env_float) ────────────────────────────

_SENTINEL = "ENV_READER_TEST_UNIQUE"


def test_env_int_unset_blank_returns_default(monkeypatch):
    monkeypatch.delenv(_SENTINEL, raising=False)
    assert _env_int(_SENTINEL, 8) == 8
    monkeypatch.setenv(_SENTINEL, "   ")
    assert _env_int(_SENTINEL, 8) == 8


def test_env_int_parses_value_and_trims_whitespace(monkeypatch):
    monkeypatch.setenv(_SENTINEL, "  3 ")
    assert _env_int(_SENTINEL, 8) == 3


def test_env_int_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_SENTINEL, "not-an-int")
    assert _env_int(_SENTINEL, 8) == 8


def test_env_float_parses_and_falls_back(monkeypatch):
    monkeypatch.setenv(_SENTINEL, "0.42")
    assert _env_float(_SENTINEL, 0.3) == pytest.approx(0.42)
    monkeypatch.setenv(_SENTINEL, "garbage")
    assert _env_float(_SENTINEL, 0.3) == pytest.approx(0.3)
    monkeypatch.delenv(_SENTINEL, raising=False)
    assert _env_float(_SENTINEL, 0.3) == pytest.approx(0.3)


def test_env_int_out_of_range_falls_back(monkeypatch):
    monkeypatch.setenv(_SENTINEL, "-5")
    assert _env_int(_SENTINEL, 8, minimum=0) == 8           # below min -> default
    monkeypatch.setenv(_SENTINEL, "5")
    assert _env_int(_SENTINEL, 8, minimum=0, maximum=10) == 5   # in range -> used


def test_env_float_rejects_non_finite(monkeypatch):
    # float() accepts these without raising — they must still be rejected so they
    # can't silently defeat a money-path check (e.g. MAX_FAIR_MARKET_RATIO=inf).
    for bad in ("inf", "-inf", "nan", "Infinity"):
        monkeypatch.setenv(_SENTINEL, bad)
        assert _env_float(_SENTINEL, 0.3) == pytest.approx(0.3)


def test_env_float_out_of_range_falls_back(monkeypatch):
    monkeypatch.setenv(_SENTINEL, "5.0")
    assert _env_float(_SENTINEL, 0.3, minimum=0.0, maximum=1.0) == pytest.approx(0.3)
    monkeypatch.setenv(_SENTINEL, "-0.1")
    assert _env_float(_SENTINEL, 0.3, minimum=0.0, maximum=1.0) == pytest.approx(0.3)
    monkeypatch.setenv(_SENTINEL, "0.8")
    assert _env_float(_SENTINEL, 0.3, minimum=0.0, maximum=1.0) == pytest.approx(0.8)


# ── end-to-end: env overrides reach the module-level constants ───────────

def test_env_overrides_apply_to_constants_on_import(monkeypatch):
    """Setting the env var and re-importing config overrides the constant;
    an unset env restores the coded default. This is the behavior PENDING #2
    was about — editing these in .env now actually does something."""
    monkeypatch.setenv("MAX_CONTRACTS", "3")
    monkeypatch.setenv("MAX_DAILY_LOSS_CENTS", "250")
    monkeypatch.setenv("MODEL_WEIGHT", "0.42")
    monkeypatch.setenv("MAX_FAIR_MARKET_RATIO", "2.5")
    try:
        importlib.reload(config)
        assert config.MAX_CONTRACTS == 3
        assert config.MAX_DAILY_LOSS_CENTS == 250
        assert config.MODEL_WEIGHT == pytest.approx(0.42)
        assert config.MAX_FAIR_MARKET_RATIO == pytest.approx(2.5)
    finally:
        # Restore the module to its env-free state so test order can't leak.
        for var in ("MAX_CONTRACTS", "MAX_DAILY_LOSS_CENTS", "MODEL_WEIGHT", "MAX_FAIR_MARKET_RATIO"):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(config)
    assert config.MAX_CONTRACTS == 8   # default restored


def test_out_of_range_money_path_override_is_rejected_on_import(monkeypatch):
    """A nonsensical money-path knob must fall back to the safe default, not
    silently corrupt pricing / defeat a risk check."""
    monkeypatch.setenv("MODEL_WEIGHT", "5")             # outside [0, 1]
    monkeypatch.setenv("MAX_FAIR_MARKET_RATIO", "inf")  # would disable the ratio cap
    monkeypatch.setenv("MAX_CONTRACTS", "-3")           # negative count
    try:
        importlib.reload(config)
        assert config.MODEL_WEIGHT == pytest.approx(0.3)         # rejected -> default
        assert config.MAX_FAIR_MARKET_RATIO == pytest.approx(3.5)  # rejected -> default
        assert config.MAX_CONTRACTS == 8                          # rejected -> default
    finally:
        for var in ("MODEL_WEIGHT", "MAX_FAIR_MARKET_RATIO", "MAX_CONTRACTS"):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(config)
