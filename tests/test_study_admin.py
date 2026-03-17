"""Tests for study_admin Lambda handler."""

from lambdas.study_admin.handler import generate_study_id, parse_create_params


# --- parse_create_params ---

def test_parse_create_params_basic():
    """Parses basic study creation params."""
    params = {
        "name": "Arctic Storm",
        "start_date": "2026-03-10",
        "end_date": "2026-03-15",
        "lat": "65.5",
        "lng": "-18.2",
        "artist": "claude",
    }
    result = parse_create_params(params)
    assert result["name"] == "Arctic Storm"
    assert result["start_date"] == "2026-03-10"
    assert result["end_date"] == "2026-03-15"
    assert result["artist"] == "claude"
    assert len(result["coordinates"]) == 1
    assert result["coordinates"][0]["lat"] == 65.5
    assert result["coordinates"][0]["lng"] == -18.2


def test_parse_create_params_multiple_coordinates():
    """Parses comma-separated lat/lng for multiple coordinates."""
    params = {
        "name": "Multi Point Study",
        "start_date": "2026-03-10",
        "lat": "45.0,50.0,55.0",
        "lng": "10.0,15.0,20.0",
    }
    result = parse_create_params(params)
    assert len(result["coordinates"]) == 3
    assert result["coordinates"][0] == {"lat": 45.0, "lng": 10.0}
    assert result["coordinates"][1] == {"lat": 50.0, "lng": 15.0}
    assert result["coordinates"][2] == {"lat": 55.0, "lng": 20.0}


def test_parse_create_params_defaults():
    """end_date defaults to start_date, missing fields are empty."""
    params = {"name": "Test", "start_date": "2026-03-10"}
    result = parse_create_params(params)
    assert result["end_date"] == "2026-03-10"
    assert result["artist"] == ""
    assert result["coordinates"] == []


def test_parse_create_params_empty():
    """Handles empty params gracefully."""
    result = parse_create_params({})
    assert result["name"] == ""
    assert result["start_date"] == ""
    assert result["coordinates"] == []


# --- generate_study_id ---

def test_generate_study_id_basic():
    """Slugifies name and appends date."""
    assert generate_study_id("Arctic Storm", "2026-03-10") == "arctic-storm-2026-03-10"


def test_generate_study_id_special_chars():
    """Strips special characters from name."""
    assert generate_study_id("Storm #3 (Pacific)", "2026-03-10") == "storm-3-pacific-2026-03-10"


def test_generate_study_id_extra_spaces():
    """Collapses whitespace into single hyphens."""
    assert generate_study_id("  Big   Storm  ", "2026-01-01") == "big-storm-2026-01-01"


def test_generate_study_id_already_clean():
    """Clean names pass through correctly."""
    assert generate_study_id("simple", "2026-06-15") == "simple-2026-06-15"
