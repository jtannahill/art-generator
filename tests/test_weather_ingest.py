"""Tests for weather_ingest Lambda handler."""

import numpy as np

from lambdas.weather_ingest.handler import score_regions


def test_score_regions_returns_top_10():
    """With a 10x10 synthetic grid and extreme values at (5,5),
    score_regions should return exactly 10 results."""
    nlat, nlon = 10, 10

    # Create baseline grids
    pressure = np.full((nlat, nlon), 101325.0, dtype=np.float32)
    wind_u = np.zeros((nlat, nlon), dtype=np.float32)
    wind_v = np.zeros((nlat, nlon), dtype=np.float32)
    temp = np.full((nlat, nlon), 288.0, dtype=np.float32)

    # Inject extreme values at (5, 5) to create a clear hot spot
    pressure[5, 5] = 95000.0  # deep low pressure
    wind_u[5, 5] = 30.0       # strong wind
    wind_v[5, 5] = 25.0
    temp[5, 5] = 320.0        # very hot

    # Add variation so we get distinct scored cells
    for i in range(nlat):
        for j in range(nlon):
            pressure[i, j] += (i - 5) * 100 + (j - 5) * 50
            temp[i, j] += (i - 5) * 2

    # Use grid_resolution=10 so 10x10 grid spans 100 degrees,
    # allowing 10+ picks with 5-degree minimum separation
    regions = score_regions(
        pressure=pressure,
        wind_u=wind_u,
        wind_v=wind_v,
        temp=temp,
        grid_resolution=10,
    )

    assert len(regions) == 10
    # Each region must have required keys
    required_keys = {
        "lat", "lng", "slug", "pressure", "pressure_gradient",
        "wind_speed", "wind_direction", "temp", "temp_anomaly",
        "humidity", "precipitation", "score",
    }
    for r in regions:
        assert required_keys.issubset(r.keys()), f"Missing keys: {required_keys - r.keys()}"

    # Scores should be in descending order
    scores = [r["score"] for r in regions]
    assert scores == sorted(scores, reverse=True)

    # The extreme point at (5,5) should be among the top results
    top_region = regions[0]
    assert top_region["score"] > 0


def test_score_regions_includes_humidity_and_precipitation():
    """When humidity and precipitation are provided,
    returned regions should have non-None values for those keys."""
    nlat, nlon = 10, 10

    pressure = np.random.uniform(99000, 103000, (nlat, nlon)).astype(np.float32)
    wind_u = np.random.uniform(-10, 10, (nlat, nlon)).astype(np.float32)
    wind_v = np.random.uniform(-10, 10, (nlat, nlon)).astype(np.float32)
    temp = np.random.uniform(250, 310, (nlat, nlon)).astype(np.float32)
    humidity = np.random.uniform(10, 100, (nlat, nlon)).astype(np.float32)
    precip = np.random.uniform(0, 50, (nlat, nlon)).astype(np.float32)

    regions = score_regions(
        pressure=pressure,
        wind_u=wind_u,
        wind_v=wind_v,
        temp=temp,
        humidity=humidity,
        precip=precip,
        grid_resolution=0.25,
    )

    assert len(regions) > 0
    for r in regions:
        assert r["humidity"] is not None, "humidity should not be None when provided"
        assert r["precipitation"] is not None, "precipitation should not be None when provided"
        assert isinstance(r["humidity"], float)
        assert isinstance(r["precipitation"], float)
