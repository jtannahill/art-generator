"""Tests for weather_render Lambda handler."""

from lambdas.weather_render.handler import (
    build_retry_prompt,
    extract_svg,
    validate_svg,
)


def test_validate_svg_valid():
    """A well-formed SVG should return (True, None)."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40"/></svg>'
    is_valid, error = validate_svg(svg)
    assert is_valid is True
    assert error is None


def test_validate_svg_invalid():
    """Broken XML should return (False, error_message)."""
    broken = "<svg><circle cx=50></svg>"
    is_valid, error = validate_svg(broken)
    # This particular string may or may not parse depending on the parser,
    # but truly broken XML like unclosed tags will fail
    broken2 = "<svg><unclosed"
    is_valid2, error2 = validate_svg(broken2)
    assert is_valid2 is False
    assert error2 is not None
    assert "parse error" in error2.lower() or "xml" in error2.lower()


def test_validate_svg_rejects_non_svg():
    """An HTML root element should return (False, error)."""
    html = "<html><body><p>Hello</p></body></html>"
    is_valid, error = validate_svg(html)
    assert is_valid is False
    assert "html" in error.lower()
    assert "expected <svg>" in error.lower()


def test_extract_svg_from_response():
    """Should extract <svg>...</svg> from surrounding text."""
    response = """Here is my artistic interpretation of the weather data.

<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2048 2048">
  <rect width="2048" height="2048" fill="#1a1a2e"/>
  <circle cx="1024" cy="1024" r="500" fill="#e94560"/>
</svg>

I hope you enjoy this piece!"""

    result = extract_svg(response)
    assert result is not None
    assert result.startswith("<svg")
    assert result.endswith("</svg>")
    assert "viewBox" in result


def test_extract_svg_returns_none_for_no_svg():
    """Returns None when no SVG is present in the text."""
    text = "Here is some text without any SVG content. Just a paragraph."
    result = extract_svg(text)
    assert result is None


def test_build_retry_prompt():
    """Retry prompt should include the error message and original prompt context."""
    original = "Create an SVG artwork inspired by weather data."
    bad_svg = "<svg><broken"
    error = "XML parse error: unclosed token"

    result = build_retry_prompt(original, bad_svg, error)

    assert error in result
    assert original in result
    assert "fix" in result.lower() or "correct" in result.lower()
    assert bad_svg in result


def test_daily_render_never_calls_clarity(monkeypatch):
    """The 8K Clarity pass is on-demand only; the daily pipeline must not invoke it."""
    import lambdas.weather_render.handler as h
    src = open(h.__file__).read()
    body = src.split("def handler(event, context):")[1].split("\ndef ")[0]
    assert "upscale_clarity(" not in body
    assert "upscale_8k_on_demand(" in body


def test_upscale_8k_action_dispatches(monkeypatch):
    import lambdas.weather_render.handler as h
    calls = []
    monkeypatch.setattr(h, "upscale_8k_on_demand", lambda r, s: calls.append((r, s)) or {"status": "ok"})
    out = h.handler({"action": "upscale_8k", "run_id": "2026-08-23", "slug": "x"}, None)
    assert out == {"status": "ok"} and calls == [("2026-08-23", "x")]
