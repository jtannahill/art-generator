"""Tests for satellite_ingest Lambda."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.satellite_ingest.handler import filter_active_locations


def _load_locations():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "locations.json")
    with open(config_path, "r") as f:
        return json.load(f)


def test_filter_locations_by_month():
    """March: Sahara not active, Reef active."""
    locations = _load_locations()
    active = filter_active_locations(locations, month=3)

    slugs = [loc["slug"] for loc in active]

    # Sahara active_months = [4,5,6,7,8,9] — March excluded
    assert "sahara" not in slugs

    # Great Barrier Reef active_months = [1..12] — always active
    assert "great-barrier-reef" in slugs


def test_filter_locations_december():
    """December: Norway active, Tulips not active."""
    locations = _load_locations()
    active = filter_active_locations(locations, month=12)

    slugs = [loc["slug"] for loc in active]

    # Norwegian Fjords active_months = [11,12,1,2]
    assert "norwegian-fjords" in slugs

    # Dutch Tulips active_months = [4,5]
    assert "dutch-tulips" not in slugs
