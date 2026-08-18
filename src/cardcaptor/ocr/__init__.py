"""OCR engines."""

from __future__ import annotations

from ..config import AppConfig, get_config
from .base import FieldRecognitionResult, NullOCREngine, OCREngine
from .tesseract import TesseractOCREngine


def get_default_engine(config: AppConfig | None = None) -> OCREngine:
    """Return the Tesseract engine, falling back to a null engine if unavailable."""
    config = config or get_config()
    engine = TesseractOCREngine(tesseract_path=config.tesseract_path)
    if engine.available:
        return engine
    return NullOCREngine()


__all__ = [
    "FieldRecognitionResult",
    "NullOCREngine",
    "OCREngine",
    "TesseractOCREngine",
    "get_default_engine",
]
