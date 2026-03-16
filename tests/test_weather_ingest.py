"""Tests for weather_ingest Lambda handler."""

from lambdas.weather_ingest.handler import score_regions, make_slug


def test_score_regions_returns_top_10():
    """score_regions returns up to 10 regions sorted by score descending."""
    weather_data = []
    for i in range(20):
        weather_data.append({
            "lat": -60 + i * 6,
            "lng": -180 + i * 18,
            "temp": 10 + i * 2,
            "humidity": 50 + i,
            "pressure": 1013 - i * 3,
            "wind_speed": 5 + i * 3,
            "wind_direction": i * 18,
            "precipitation": i * 0.5,
        })

    regions = score_regions(weather_data)

    assert len(regions) <= 10
    assert len(regions) >= 1
    assert all("score" in r for r in regions)
    assert all("lat" in r and "lng" in r for r in regions)
    assert all("pressure" in r and "wind_speed" in r and "temp" in r for r in regions)
    for i in range(len(regions) - 1):
        assert regions[i]["score"] >= regions[i + 1]["score"]


def test_score_regions_includes_humidity_and_precipitation():
    """score_regions includes humidity and precipitation in output."""
    weather_data = [
        {"lat": 45, "lng": -30, "temp": 15, "humidity": 80, "pressure": 990,
         "wind_speed": 60, "wind_direction": 225, "precipitation": 12},
        {"lat": -15, "lng": 100, "temp": 30, "humidity": 40, "pressure": 1020,
         "wind_speed": 10, "wind_direction": 90, "precipitation": 0},
    ]

    regions = score_regions(weather_data)
    assert all("humidity" in r for r in regions)
    assert all("precipitation" in r for r in regions)
    assert all(r["humidity"] is not None for r in regions)
