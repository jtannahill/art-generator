"""Tests for study_detector Lambda handler."""

from decimal import Decimal

from lambdas.study_detector.handler import (
    detect_clusters,
    detect_persistent,
    group_by_grid,
    is_duplicate_study,
)


def _make_weather_item(lat, lng, date):
    """Helper to build a weather item with Decimal fields like DynamoDB returns."""
    return {
        "PK": f"WEATHER#{lat}#{lng}",
        "SK": f"DAY#{date}",
        "lat": Decimal(str(lat)),
        "lng": Decimal(str(lng)),
    }


# --- group_by_grid ---

def test_group_by_grid_single_cell():
    """Items in the same 5-degree cell are grouped together."""
    items = [
        _make_weather_item(12, 33, "2026-03-01"),
        _make_weather_item(13, 34, "2026-03-02"),
    ]
    grid = group_by_grid(items)
    assert len(grid) == 1
    assert (10, 30) in grid
    assert len(grid[(10, 30)]) == 2


def test_group_by_grid_multiple_cells():
    """Items in different cells land in separate groups."""
    items = [
        _make_weather_item(12, 33, "2026-03-01"),
        _make_weather_item(22, 48, "2026-03-01"),
        _make_weather_item(-7, -14, "2026-03-01"),
    ]
    grid = group_by_grid(items)
    assert len(grid) == 3
    assert (10, 30) in grid
    assert (20, 45) in grid
    assert (-10, -15) in grid


def test_group_by_grid_negative_coords():
    """Negative lat/lng floor correctly."""
    items = [_make_weather_item(-3, -8, "2026-03-01")]
    grid = group_by_grid(items)
    # -3 // 5 = -1, -1 * 5 = -5;  -8 // 5 = -2, -2 * 5 = -10
    assert (-5, -10) in grid


def test_group_by_grid_custom_cell_size():
    """Custom cell_size groups correctly."""
    items = [
        _make_weather_item(12, 33, "2026-03-01"),
        _make_weather_item(18, 38, "2026-03-01"),
    ]
    grid = group_by_grid(items, cell_size=10)
    assert len(grid) == 1
    assert (10, 30) in grid


# --- detect_persistent ---

def test_detect_persistent_finds_consecutive():
    """Detects grid cells with 3+ consecutive days."""
    items = [
        _make_weather_item(12, 33, "2026-03-01"),
        _make_weather_item(12, 33, "2026-03-02"),
        _make_weather_item(12, 33, "2026-03-03"),
    ]
    grid = group_by_grid(items)
    results = detect_persistent(grid)
    assert len(results) == 1
    assert results[0]["dates"] == ["2026-03-01", "2026-03-02", "2026-03-03"]
    assert results[0]["center_lat"] == 12.5
    assert results[0]["center_lng"] == 32.5


def test_detect_persistent_skips_non_consecutive():
    """Does not flag cells with gaps in dates."""
    items = [
        _make_weather_item(12, 33, "2026-03-01"),
        _make_weather_item(12, 33, "2026-03-03"),
        _make_weather_item(12, 33, "2026-03-05"),
    ]
    grid = group_by_grid(items)
    results = detect_persistent(grid)
    assert len(results) == 0


def test_detect_persistent_custom_min_days():
    """Respects min_days parameter."""
    items = [
        _make_weather_item(12, 33, "2026-03-01"),
        _make_weather_item(12, 33, "2026-03-02"),
    ]
    grid = group_by_grid(items)
    assert len(detect_persistent(grid, min_days=3)) == 0
    assert len(detect_persistent(grid, min_days=2)) == 1


# --- detect_clusters ---

def test_detect_clusters_finds_nearby_cells():
    """Detects cluster when 3+ cells are active on same day within max_distance."""
    items = [
        _make_weather_item(10, 30, "2026-03-01"),
        _make_weather_item(10, 35, "2026-03-01"),
        _make_weather_item(15, 32, "2026-03-01"),
    ]
    grid = group_by_grid(items)
    results = detect_clusters(grid)
    assert len(results) == 1
    assert results[0]["date"] == "2026-03-01"
    assert len(results[0]["coordinates"]) >= 3


def test_detect_clusters_ignores_distant_cells():
    """Does not flag clusters when cells are too far apart."""
    items = [
        _make_weather_item(10, 30, "2026-03-01"),
        _make_weather_item(10, 80, "2026-03-01"),
        _make_weather_item(60, 30, "2026-03-01"),
    ]
    grid = group_by_grid(items)
    results = detect_clusters(grid)
    assert len(results) == 0


def test_detect_clusters_needs_min_points():
    """Does not flag cluster with fewer than min_points cells."""
    items = [
        _make_weather_item(10, 30, "2026-03-01"),
        _make_weather_item(10, 35, "2026-03-01"),
    ]
    grid = group_by_grid(items)
    results = detect_clusters(grid, min_points=3)
    assert len(results) == 0


# --- is_duplicate_study ---

def test_is_duplicate_study_overlap():
    """Returns True when region and dates overlap."""
    existing = [{
        "lat": Decimal("45"), "lng": Decimal("30"),
        "start_date": "2026-03-01", "end_date": "2026-03-10",
    }]
    assert is_duplicate_study(existing, 48, 35, "2026-03-05", "2026-03-15") is True


def test_is_duplicate_study_no_region_overlap():
    """Returns False when region does not overlap."""
    existing = [{
        "lat": Decimal("45"), "lng": Decimal("30"),
        "start_date": "2026-03-01", "end_date": "2026-03-10",
    }]
    assert is_duplicate_study(existing, 80, 120, "2026-03-05", "2026-03-15") is False


def test_is_duplicate_study_no_date_overlap():
    """Returns False when dates do not overlap."""
    existing = [{
        "lat": Decimal("45"), "lng": Decimal("30"),
        "start_date": "2026-03-01", "end_date": "2026-03-05",
    }]
    assert is_duplicate_study(existing, 48, 35, "2026-03-06", "2026-03-15") is False


def test_is_duplicate_study_empty_list():
    """Returns False when there are no existing studies."""
    assert is_duplicate_study([], 45, 30, "2026-03-01", "2026-03-10") is False
