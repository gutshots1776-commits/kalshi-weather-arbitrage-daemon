"""Tests for configuration helpers and the hardened .env parser.

The .env parsing tests pin the safety fix: an inline `# comment` after a value
must not leak into the value (which previously could flip PAPER_TRADING off and
silently enable live trading).
"""
import os
from datetime import datetime

from kalshi.config import (
    _strip_inline_comment,
    _parse_env_line,
    _load_env,
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
