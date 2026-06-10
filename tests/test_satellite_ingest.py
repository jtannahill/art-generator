"""Tests for satellite_ingest Lambda."""

import io
import json
import os
import sys
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.satellite_ingest.handler import (
    filter_active_locations,
    get_last_ingested_hash,
)


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


def test_last_hash_returns_value_when_marker_exists():
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": io.BytesIO(b"abc123\n")}
    assert get_last_ingested_hash(s3, "uluru") == "abc123"


def test_last_hash_returns_none_when_marker_missing():
    s3 = MagicMock()
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject"
    )
    assert get_last_ingested_hash(s3, "uluru") is None


def test_last_hash_propagates_unexpected_errors():
    s3 = MagicMock()
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
    )
    try:
        get_last_ingested_hash(s3, "uluru")
    except ClientError as e:
        assert e.response["Error"]["Code"] == "AccessDenied"
    else:
        raise AssertionError("expected ClientError to propagate")
