import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("TABLE_NAME", "art-generator-test")
os.environ.setdefault("BUCKET_NAME", "art-generator-test")
os.environ.setdefault("SECRET_ID", "test/secret")


@pytest.fixture
def mock_table():
    table = MagicMock()
    table.table_name = "art-generator-test"
    return table


@pytest.fixture
def sample_weather_item():
    return {
        "PK": "WEATHER#2026-03-16-130500",
        "SK": "arctic-70n-20w",
        "run_id": "2026-03-16-130500",
        "slug": "arctic-70n-20w",
        "artist": "sam_francis",
        "canvas_format": "2048x2048",
        "lat": 70, "lng": -20, "score": 0.85,
    }


@pytest.fixture
def sample_edition_item():
    return {
        "PK": "EDITION#2026-03-16-130500#arctic-70n-20w",
        "SK": "META",
        "canvas_format": "2048x2048",
        "aspect_ratio": "1:1",
        "featured": False,
        "sizes": {
            "S":   {"dims": "12x12", "limit": 5, "sold": 0, "price_cents": 15500},
            "M":   {"dims": "20x20", "limit": 5,  "sold": 0, "price_cents": 59500},
            "L":   {"dims": "30x30", "limit": 5,  "sold": 0, "price_cents": 69500},
            "XL":  {"dims": "40x40", "limit": 5,  "sold": 0, "price_cents": 115000},
            "XXL": {"dims": "60x60", "limit": 5,  "sold": 0, "price_cents": 260000},
        },
    }
