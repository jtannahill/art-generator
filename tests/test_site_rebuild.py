"""Tests for site_rebuild Lambda handler."""

from lambdas.site_rebuild.handler import (
    group_by_date,
    group_by_location,
    group_palette_by_date,
    _parse_colors,
)


def test_group_weather_by_date():
    """3 items across 2 dates should produce 2 groups with correct counts."""
    items = [
        {"PK": "WEATHER#2026-03-15", "SK": "sahara", "score": 8.5},
        {"PK": "WEATHER#2026-03-15", "SK": "iceland", "score": 7.2},
        {"PK": "WEATHER#2026-03-14", "SK": "tuscany", "score": 6.0},
    ]

    result = group_by_date(items, prefix="WEATHER#")

    assert len(result) == 2
    assert "2026-03-15" in result
    assert "2026-03-14" in result
    assert len(result["2026-03-15"]) == 2
    assert len(result["2026-03-14"]) == 1

    # Verify date-descending order (most recent first)
    dates = list(result.keys())
    assert dates[0] == "2026-03-15"
    assert dates[1] == "2026-03-14"


def test_group_palettes_by_location():
    """3 items across 2 locations should group correctly with date-desc sort."""
    items = [
        {"PK": "PALETTE#sahara", "SK": "2026-03-14", "mood": "warm dunes"},
        {"PK": "PALETTE#sahara", "SK": "2026-03-15", "mood": "golden haze"},
        {"PK": "PALETTE#iceland", "SK": "2026-03-15", "mood": "frozen blue"},
    ]

    result = group_by_location(items)

    assert len(result) == 2
    assert "sahara" in result
    assert "iceland" in result
    assert len(result["sahara"]) == 2
    assert len(result["iceland"]) == 1

    # Verify date-desc sort within each location
    sahara_dates = [p["SK"] for p in result["sahara"]]
    assert sahara_dates == ["2026-03-15", "2026-03-14"]


def test_group_palette_by_date():
    """Palette items should group by SK (date) in descending order."""
    items = [
        {"PK": "PALETTE#sahara", "SK": "2026-03-14"},
        {"PK": "PALETTE#iceland", "SK": "2026-03-15"},
        {"PK": "PALETTE#sahara", "SK": "2026-03-15"},
    ]

    result = group_palette_by_date(items)

    assert len(result) == 2
    dates = list(result.keys())
    assert dates[0] == "2026-03-15"
    assert len(result["2026-03-15"]) == 2
    assert len(result["2026-03-14"]) == 1


def test_parse_colors_json_string():
    """Colors stored as JSON string should parse to list."""
    assert _parse_colors('["#FF0000", "#00FF00"]') == ["#FF0000", "#00FF00"]


def test_parse_colors_list():
    """Colors already a list should pass through."""
    assert _parse_colors(["#FF0000", "#00FF00"]) == ["#FF0000", "#00FF00"]


def test_parse_colors_invalid():
    """Invalid input should return empty list."""
    assert _parse_colors("not json") == []
    assert _parse_colors(None) == []
