"""Tests for card color analysis."""

from __future__ import annotations

import numpy as np

from cardcaptor.vision.color import (
    DEFAULT_COLOR_REFERENCES,
    analyze_card_color,
    classify_lab,
    lab_to_bgr,
)


def _card_of_lab(lab: tuple[float, float, float], size=(120, 200)) -> np.ndarray:
    """Build a uniform card image whose paper color is the given Lab value."""
    bgr = lab_to_bgr(lab)
    image = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    image[:, :] = bgr
    # Simulate handwriting in the middle - the analyzer samples the border.
    image[size[0] // 3 : 2 * size[0] // 3, size[1] // 4 : 3 * size[1] // 4] = (10, 10, 10)
    return image


def test_white_card_is_classified_white():
    card = _card_of_lab(DEFAULT_COLOR_REFERENCES["white"])
    result = analyze_card_color(card)
    assert result.category == "white"
    assert result.confidence > 0.5
    assert result.lab_l > 80
    assert abs(result.lab_a) < 10


def test_yellow_card_is_classified_yellow():
    card = _card_of_lab(DEFAULT_COLOR_REFERENCES["yellow"])
    result = analyze_card_color(card)
    assert result.category == "yellow"
    assert result.lab_b > 20
    assert result.confidence > 0.5


def test_pink_card_is_classified_pink():
    card = _card_of_lab(DEFAULT_COLOR_REFERENCES["pink"])
    result = analyze_card_color(card)
    assert result.category == "pink"
    assert result.lab_a > 10
    assert result.confidence > 0.5


def test_blue_and_green_cards():
    for name in ("blue", "green"):
        card = _card_of_lab(DEFAULT_COLOR_REFERENCES[name])
        assert analyze_card_color(card).category == name


def test_confidence_drops_when_between_two_references():
    midpoint = tuple(
        (a + b) / 2
        for a, b in zip(DEFAULT_COLOR_REFERENCES["yellow"], DEFAULT_COLOR_REFERENCES["orange"])
    )
    category, confidence, distances = classify_lab(midpoint)
    assert category in ("yellow", "orange")
    assert confidence < 0.5
    assert set(distances) == set(DEFAULT_COLOR_REFERENCES)


def test_unknown_color_when_far_from_every_reference():
    category, confidence, _ = classify_lab((20.0, 60.0, -90.0))
    assert category == "unknown"
    assert confidence == 0.0


def test_custom_reference_colors_can_be_supplied():
    references = {"lavender": (75.0, 15.0, -20.0)}
    category, confidence, _ = classify_lab((75.0, 15.0, -20.0), references=references)
    assert category == "lavender"
    assert confidence > 0.8


def test_empty_image_returns_unknown():
    result = analyze_card_color(np.zeros((0, 0, 3), dtype=np.uint8))
    assert result.category == "unknown"
    assert result.confidence == 0.0


def test_grayscale_image_is_supported():
    gray = np.full((60, 100), 240, dtype=np.uint8)
    result = analyze_card_color(gray)
    assert result.category == "white"
    assert result.rgb_mean[0] > 200
