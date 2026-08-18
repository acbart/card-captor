"""Computer-vision helpers: card detection, perspective correction, color analysis."""

from .color import DEFAULT_COLOR_REFERENCES, ColorResult, analyze_card_color, classify_lab
from .detector import DetectedCard, detect_cards, normalized_center, order_points
from .perspective import correct_perspective, order_corners

__all__ = [
    "DEFAULT_COLOR_REFERENCES",
    "ColorResult",
    "DetectedCard",
    "analyze_card_color",
    "classify_lab",
    "correct_perspective",
    "detect_cards",
    "normalized_center",
    "order_corners",
    "order_points",
]
