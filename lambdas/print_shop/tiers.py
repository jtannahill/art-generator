FORMAT_TO_RATIO = {
    "2048x2048": "1:1",
    "1920x1920": "1:1",
    "2560x1440": "16:9",
    "2400x1600": "3:2",
    "2048x1024": "2:1",
    "1440x2560": "9:16",
    "1024x2048": "1:2",
}

ASPECT_RATIOS = {
    "1:1": {
        "S":   {"dims": "12x12", "limit": 5, "price_cents": 9500},
        "M":   {"dims": "20x20", "limit": 5,  "price_cents": 19500},
        "L":   {"dims": "30x30", "limit": 5,  "price_cents": 35000},
        "XL":  {"dims": "40x40", "limit": 5,  "price_cents": 59500},
        "XXL": {"dims": "60x60", "limit": 5,  "price_cents": 120000},
    },
    "16:9": {
        "S":   {"dims": "16x9",  "limit": 5, "price_cents": 8500},
        "M":   {"dims": "24x14", "limit": 5,  "price_cents": 17500},
        "L":   {"dims": "36x20", "limit": 5,  "price_cents": 32500},
        "XL":  {"dims": "48x27", "limit": 5,  "price_cents": 55000},
        "XXL": {"dims": "60x34", "limit": 5,  "price_cents": 99500},
    },
    "3:2": {
        "S":   {"dims": "12x8",  "limit": 5, "price_cents": 8500},
        "M":   {"dims": "18x12", "limit": 5,  "price_cents": 17500},
        "L":   {"dims": "24x16", "limit": 5,  "price_cents": 32500},
        "XL":  {"dims": "36x24", "limit": 5,  "price_cents": 55000},
        "XXL": {"dims": "50x34", "limit": 5,  "price_cents": 99500},
    },
    "2:1": {
        "S":   {"dims": "20x10", "limit": 5, "price_cents": 9500},
        "M":   {"dims": "30x15", "limit": 5,  "price_cents": 19500},
        "L":   {"dims": "40x20", "limit": 5,  "price_cents": 37500},
        "XL":  {"dims": "50x25", "limit": 5,  "price_cents": 65000},
        "XXL": {"dims": "60x30", "limit": 5,  "price_cents": 110000},
    },
    "9:16": {
        "S":   {"dims": "9x16",  "limit": 5, "price_cents": 8500},
        "M":   {"dims": "14x24", "limit": 5,  "price_cents": 17500},
        "L":   {"dims": "20x36", "limit": 5,  "price_cents": 32500},
        "XL":  {"dims": "27x48", "limit": 5,  "price_cents": 55000},
        "XXL": {"dims": "34x60", "limit": 5,  "price_cents": 99500},
    },
    "1:2": {
        "S":   {"dims": "10x20", "limit": 5, "price_cents": 9500},
        "M":   {"dims": "15x30", "limit": 5,  "price_cents": 19500},
        "L":   {"dims": "20x40", "limit": 5,  "price_cents": 37500},
        "XL":  {"dims": "25x50", "limit": 5,  "price_cents": 65000},
        "XXL": {"dims": "30x60", "limit": 5,  "price_cents": 110000},
    },
}


def format_to_aspect_ratio(canvas_format: str) -> str:
    """Return the aspect ratio string for a given canvas viewBox format.

    Args:
        canvas_format: A string like "2048x2048" representing the canvas dimensions.

    Returns:
        The aspect ratio string, e.g. "1:1".

    Raises:
        ValueError: If the canvas_format is not in the known FORMAT_TO_RATIO mapping.
    """
    if canvas_format not in FORMAT_TO_RATIO:
        raise ValueError(f"Unknown canvas format: {canvas_format!r}")
    return FORMAT_TO_RATIO[canvas_format]


def get_tiers_for_format(canvas_format: str) -> dict:
    """Return the print size tiers for a given canvas viewBox format.

    Each size tier includes a ``sold`` counter initialised to 0. The canonical
    ASPECT_RATIOS dict is never mutated — each call returns fresh copies.

    Args:
        canvas_format: A string like "2048x2048" representing the canvas dimensions.

    Returns:
        A dict with keys:
            - ``aspect_ratio``: the ratio string (e.g. "1:1")
            - ``sizes``: a dict of tier dicts, each containing dims, limit,
              price_cents, and sold (always 0).

    Raises:
        ValueError: If the canvas_format is not in the known FORMAT_TO_RATIO mapping.
    """
    aspect_ratio = format_to_aspect_ratio(canvas_format)
    sizes = {
        tier: {**data, "sold": 0}
        for tier, data in ASPECT_RATIOS[aspect_ratio].items()
    }
    return {"aspect_ratio": aspect_ratio, "sizes": sizes}
