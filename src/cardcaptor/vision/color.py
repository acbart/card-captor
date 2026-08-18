"""Card color analysis in CIE Lab space."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

# Reference points are defaults only - instructors may add or modify groups.
DEFAULT_COLOR_REFERENCES: dict[str, tuple[float, float, float]] = {
    "white": (95.0, 0.0, 5.0),
    "yellow": (90.0, -5.0, 45.0),
    "pink": (70.0, 25.0, 5.0),
    "blue": (60.0, -10.0, -30.0),
    "green": (65.0, -20.0, 10.0),
    "orange": (65.0, 25.0, 50.0),
}

UNKNOWN_CATEGORY = "unknown"
#: Beyond this Lab distance from every reference, the color is reported unknown.
UNKNOWN_DISTANCE = 45.0
#: Distance used to scale "closeness" into a 0..1 confidence component.
CONFIDENCE_SCALE = 60.0


@dataclass
class ColorResult:
    lab_l: float
    lab_a: float
    lab_b: float
    category: str
    confidence: float
    rgb_mean: tuple[float, float, float]
    distances: dict[str, float] = field(default_factory=dict)

    @property
    def lab(self) -> tuple[float, float, float]:
        return (self.lab_l, self.lab_a, self.lab_b)


def _as_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def bgr_to_lab(pixels: np.ndarray) -> np.ndarray:
    """Convert an (N, 3) uint8 BGR array to real-valued Lab (L 0-100, a/b -128..127)."""
    arr = np.asarray(pixels, dtype=np.uint8).reshape(-1, 1, 3)
    lab = cv2.cvtColor(arr, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float64)
    lab[:, 0] = lab[:, 0] * 100.0 / 255.0
    lab[:, 1] = lab[:, 1] - 128.0
    lab[:, 2] = lab[:, 2] - 128.0
    return lab


def lab_to_bgr(lab: tuple[float, float, float]) -> tuple[int, int, int]:
    """Convert a real-valued Lab triple back to 8-bit BGR (useful for previews/tests)."""
    l, a, b = lab
    encoded = np.array(
        [[[np.clip(l * 255.0 / 100.0, 0, 255), np.clip(a + 128.0, 0, 255), np.clip(b + 128.0, 0, 255)]]],
        dtype=np.uint8,
    )
    bgr = cv2.cvtColor(encoded, cv2.COLOR_LAB2BGR).reshape(3)
    return (int(bgr[0]), int(bgr[1]), int(bgr[2]))


def sample_border_pixels(card_image: np.ndarray, strip_ratio: float = 0.12) -> np.ndarray:
    """Sample the border strips of a card, which are usually free of writing."""
    image = _as_bgr(card_image)
    height, width = image.shape[:2]
    if height == 0 or width == 0:
        return np.empty((0, 3), dtype=np.uint8)

    v_strip = max(1, int(round(height * strip_ratio)))
    h_strip = max(1, int(round(width * strip_ratio)))

    strips = [
        image[0:v_strip, :, :],
        image[height - v_strip : height, :, :],
        image[:, 0:h_strip, :],
        image[:, width - h_strip : width, :],
    ]
    pixels = np.concatenate([s.reshape(-1, 3) for s in strips], axis=0)
    if pixels.size == 0:
        return np.empty((0, 3), dtype=np.uint8)

    # Drop the darkest half (ink, shadows, card edges) to estimate the paper color.
    luminance = pixels.astype(np.float64).mean(axis=1)
    cutoff = float(np.percentile(luminance, 50.0))
    bright = pixels[luminance >= cutoff]
    return bright if bright.size else pixels


def classify_lab(
    lab: tuple[float, float, float],
    references: dict[str, tuple[float, float, float]] | None = None,
    unknown_distance: float = UNKNOWN_DISTANCE,
) -> tuple[str, float, dict[str, float]]:
    """Classify a Lab triple against named reference colors.

    Returns ``(category, confidence, distances)``.
    """
    refs = references or DEFAULT_COLOR_REFERENCES
    if not refs:
        return (UNKNOWN_CATEGORY, 0.0, {})

    point = np.asarray(lab, dtype=np.float64)
    distances = {
        name: float(np.linalg.norm(point - np.asarray(ref, dtype=np.float64)))
        for name, ref in refs.items()
    }
    ordered = sorted(distances.items(), key=lambda kv: kv[1])
    best_name, best_distance = ordered[0]

    if best_distance > unknown_distance:
        # Still report how confident we are that it is *not* a known color.
        return (UNKNOWN_CATEGORY, 0.0, distances)

    closeness = max(0.0, 1.0 - best_distance / CONFIDENCE_SCALE)
    if len(ordered) > 1:
        second_distance = ordered[1][1]
        separation = (
            (second_distance - best_distance) / second_distance if second_distance > 0 else 0.0
        )
    else:
        separation = 1.0
    separation = float(np.clip(separation, 0.0, 1.0))
    confidence = float(np.clip(closeness * (0.5 + 0.5 * separation), 0.0, 1.0))
    return (best_name, confidence, distances)


def analyze_card_color(
    card_image: np.ndarray,
    references: dict[str, tuple[float, float, float]] | None = None,
    strip_ratio: float = 0.12,
) -> ColorResult:
    """Estimate the physical color of an index card."""
    pixels = sample_border_pixels(card_image, strip_ratio=strip_ratio)
    if pixels.size == 0:
        return ColorResult(0.0, 0.0, 0.0, UNKNOWN_CATEGORY, 0.0, (0.0, 0.0, 0.0), {})

    lab_pixels = bgr_to_lab(pixels)
    lab_mean = tuple(float(v) for v in np.median(lab_pixels, axis=0))
    bgr_mean = np.median(pixels.astype(np.float64), axis=0)
    rgb_mean = (float(bgr_mean[2]), float(bgr_mean[1]), float(bgr_mean[0]))

    category, confidence, distances = classify_lab(lab_mean, references=references)
    return ColorResult(
        lab_l=lab_mean[0],
        lab_a=lab_mean[1],
        lab_b=lab_mean[2],
        category=category,
        confidence=confidence,
        rgb_mean=rgb_mean,
        distances=distances,
    )
