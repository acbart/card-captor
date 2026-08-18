"""Abstract OCR interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

FIELD_TYPES = ("name", "email", "answer", "mcq", "number", "generic")


@dataclass
class FieldRecognitionResult:
    raw_text: str
    confidence: float  # 0.0 - 1.0
    candidates: list[tuple[str, float]] = field(default_factory=list)
    field_type: str = "generic"

    def as_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "confidence": self.confidence,
            "candidates": [list(c) for c in self.candidates],
            "field_type": self.field_type,
        }


class OCREngine(ABC):
    """Interface implemented by all OCR backends."""

    @abstractmethod
    def recognize_field(
        self,
        image_region: np.ndarray,
        field_type: str,
        constraints: Optional[list[str]] = None,
    ) -> FieldRecognitionResult:
        """Recognize text in an image region.

        ``constraints`` (e.g. MCQ choices) may reorder candidate interpretations
        but must never manufacture confidence: low-confidence OCR stays
        low-confidence even when the text matches an expected answer.
        """

    @abstractmethod
    def segment_card_regions(self, card_image: np.ndarray) -> dict[str, np.ndarray]:
        """Split a rectified card image into 'name', 'email' and 'answer' regions."""

    # -- shared helpers -------------------------------------------------
    @staticmethod
    def default_segments(card_image: np.ndarray) -> dict[str, np.ndarray]:
        """Split a card into name / email / answer regions.

        Handwriting rarely lands exactly on thirds, so text bands are located
        first; the horizontal-thirds split is used as a fallback.
        """
        if card_image is None or card_image.size == 0:
            return {}

        inner = _strip_card_border(card_image)
        bands = _text_bands(inner)
        height = inner.shape[0]

        def _slice(start: int, end: int) -> np.ndarray:
            start = max(0, min(start, height - 1))
            end = max(start + 1, min(end, height))
            return inner[start:end]

        if len(bands) >= 3:
            return {
                "name": _slice(*bands[0]),
                "email": _slice(*bands[1]),
                "answer": _slice(*bands[-1]),
            }

        third = max(1, height // 3)
        if len(bands) == 2:
            return {
                "name": _slice(*bands[0]),
                "email": _slice(*bands[1]),
                "answer": _slice(2 * third, height),
            }
        return {
            "name": _slice(0, third),
            "email": _slice(third, 2 * third),
            "answer": _slice(2 * third, height),
        }


class NullOCREngine(OCREngine):
    """Fallback engine used when no OCR backend is available.

    It always returns empty text with zero confidence so downstream code can
    still run (and flags everything for human review).
    """

    def recognize_field(
        self,
        image_region: np.ndarray,
        field_type: str,
        constraints: Optional[list[str]] = None,
    ) -> FieldRecognitionResult:
        return FieldRecognitionResult(
            raw_text="", confidence=0.0, candidates=[], field_type=field_type
        )

    def segment_card_regions(self, card_image: np.ndarray) -> dict[str, np.ndarray]:
        return self.default_segments(card_image)


def _strip_card_border(card_image: np.ndarray, margin: float = 0.05) -> np.ndarray:
    """Remove the outer frame of a rectified card so edges are not read as text."""
    height, width = card_image.shape[:2]
    dy = int(round(height * margin))
    dx = int(round(width * margin * 0.7))
    if height - 2 * dy < 10 or width - 2 * dx < 10:
        return card_image
    return card_image[dy : height - dy, dx : width - dx]


def _text_bands(image: np.ndarray, min_gap: int = 6, pad: int = 6) -> list[tuple[int, int]]:
    """Return (start_row, end_row) ranges that contain ink, top to bottom."""
    if image is None or image.size == 0:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    profile = (binary > 0).sum(axis=1)
    if profile.max() == 0:
        return []

    threshold = max(1, int(0.02 * image.shape[1]))
    rows = profile > threshold

    bands: list[tuple[int, int]] = []
    start: Optional[int] = None
    gap = 0
    for index, filled in enumerate(rows):
        if filled:
            if start is None:
                start = index
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                bands.append((start, index - gap + 1))
                start = None
                gap = 0
    if start is not None:
        bands.append((start, len(rows)))

    height = image.shape[0]
    min_band_height = max(4, int(0.03 * height))
    merged = [
        (max(0, s - pad), min(height, e + pad))
        for s, e in bands
        if (e - s) >= min_band_height
    ]
    return merged
