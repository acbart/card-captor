"""Tesseract-backed OCR engine. Runs fully locally, no network access."""

from __future__ import annotations

import logging
import re
import shutil
from typing import Optional

import cv2
import numpy as np

from .base import FieldRecognitionResult, OCREngine

logger = logging.getLogger(__name__)

try:  # pytesseract is a hard dependency, but tesseract itself may be missing
    import pytesseract
    from pytesseract import TesseractError, TesseractNotFoundError
except ImportError:  # pragma: no cover - defensive
    pytesseract = None  # type: ignore[assignment]

    class TesseractError(Exception):  # type: ignore[no-redef]
        pass

    class TesseractNotFoundError(Exception):  # type: ignore[no-redef]
        pass


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

_PSM_SINGLE_LINE = 7
_PSM_BLOCK = 6
_PSM_SINGLE_CHAR = 10


class TesseractOCREngine(OCREngine):
    """OCR engine using Tesseract with per-field preprocessing."""

    def __init__(self, tesseract_path: str = "tesseract", lang: str = "eng") -> None:
        self.tesseract_path = tesseract_path
        self.lang = lang
        self._available: Optional[bool] = None
        if pytesseract is not None and tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

    # -- availability ----------------------------------------------------
    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self._check_available()
            if not self._available:
                logger.warning(
                    "Tesseract binary %r not found; OCR results will be empty and "
                    "every card will be flagged for manual review.",
                    self.tesseract_path,
                )
        return self._available

    def _check_available(self) -> bool:
        if pytesseract is None:
            return False
        if shutil.which(self.tesseract_path) is None:
            return False
        try:
            pytesseract.get_tesseract_version()
        except Exception:  # pragma: no cover - environment dependent
            return False
        return True

    # -- preprocessing ---------------------------------------------------
    def _preprocess(self, image: np.ndarray, field_type: str) -> np.ndarray:
        """Apply appropriate preprocessing for a field type."""
        if image is None or image.size == 0:
            return np.full((10, 10), 255, dtype=np.uint8)

        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        gray = self._crop_to_ink(gray)
        gray = self._deskew(gray)

        target_height = 96 if field_type in ("mcq", "number") else 64
        if gray.shape[0] < target_height:
            scale = min(6.0, target_height / max(gray.shape[0], 1))
            gray = cv2.resize(
                gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
            )

        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        if field_type in ("mcq", "number"):
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

        # Tesseract expects a quiet zone around the text.
        return cv2.copyMakeBorder(
            binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255
        )

    @staticmethod
    def _crop_to_ink(gray: np.ndarray, pad: int = 8) -> np.ndarray:
        """Crop to the bounding box of the writing, dropping empty margins."""
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        points = cv2.findNonZero(binary)
        if points is None:
            return gray
        x, y, width, height = cv2.boundingRect(points)
        if width < 3 or height < 3:
            return gray
        top = max(0, y - pad)
        left = max(0, x - pad)
        bottom = min(gray.shape[0], y + height + pad)
        right = min(gray.shape[1], x + width + pad)
        cropped = gray[top:bottom, left:right]
        return cropped if cropped.size else gray

    @staticmethod
    def _deskew(gray: np.ndarray) -> np.ndarray:
        """Rotate the image so text lines are horizontal."""
        inverted = cv2.bitwise_not(gray)
        _, thresh = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        coords = cv2.findNonZero(thresh)
        if coords is None or len(coords) < 10:
            return gray
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle
        elif angle > 45:
            angle = angle - 90
        if abs(angle) < 1.0 or abs(angle) > 15:
            return gray
        height, width = gray.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
        return cv2.warpAffine(
            gray, matrix, (width, height), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

    # -- tesseract configuration ----------------------------------------
    def _configs_for(
        self, field_type: str, constraints: Optional[list[str]] = None
    ) -> list[str]:
        """Tesseract configurations to try, in order, for a field type.

        Single characters are sometimes reported with an unusable confidence in
        block mode, so a single-character pass is used as a fallback. Retrying
        never invents a score: the confidence always comes from Tesseract.
        """
        primary = self._config_for(field_type, constraints)
        if field_type == "mcq":
            whitelist = self._mcq_whitelist(constraints)
            return [
                primary,
                f"--psm {_PSM_BLOCK} -c tessedit_char_whitelist={whitelist}",
                f"--psm {_PSM_SINGLE_LINE} -c tessedit_char_whitelist={whitelist}",
            ]
        if field_type == "number":
            return [primary, f"--psm {_PSM_BLOCK} -c tessedit_char_whitelist=0123456789.-"]
        return [primary, f"--psm {_PSM_SINGLE_LINE}"]

    @staticmethod
    def _mcq_whitelist(constraints: Optional[list[str]]) -> str:
        allowed = "".join(
            sorted(
                {
                    c.strip().upper()[:1]
                    for c in (constraints or list("ABCD"))
                    if c.strip()
                }
            )
        )
        return allowed or "ABCD"

    def _config_for(self, field_type: str, constraints: Optional[list[str]]) -> str:
        if field_type == "mcq":
            allowed = self._mcq_whitelist(constraints)
            return f"--psm {_PSM_SINGLE_CHAR} -c tessedit_char_whitelist={allowed}"
        if field_type == "number":
            return f"--psm {_PSM_SINGLE_LINE} -c tessedit_char_whitelist=0123456789.-"
        if field_type == "email":
            whitelist = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@._-"
            return f"--psm {_PSM_BLOCK} -c tessedit_char_whitelist={whitelist}"
        return f"--psm {_PSM_BLOCK}"

    # -- confidence ------------------------------------------------------
    def _extract_confidence(self, data: dict) -> float:
        """Extract a normalized (0..1) confidence from a pytesseract data dict."""
        confs: list[float] = []
        texts = data.get("text", [])
        raw_confs = data.get("conf", [])
        for text, conf in zip(texts, raw_confs):
            if not str(text).strip():
                continue
            try:
                value = float(conf)
            except (TypeError, ValueError):
                continue
            if value < 0:
                continue
            confs.append(value)
        if not confs:
            return 0.0
        return float(min(max(sum(confs) / len(confs) / 100.0, 0.0), 1.0))

    @staticmethod
    def _join_text(data: dict) -> str:
        words = [str(t).strip() for t in data.get("text", []) if str(t).strip()]
        return " ".join(words).strip()

    def _run(self, image: np.ndarray, config: str) -> dict:
        assert pytesseract is not None
        return pytesseract.image_to_data(
            image, lang=self.lang, config=config, output_type=pytesseract.Output.DICT
        )

    # -- public API ------------------------------------------------------
    def recognize_field(
        self,
        image_region: np.ndarray,
        field_type: str,
        constraints: Optional[list[str]] = None,
    ) -> FieldRecognitionResult:
        if image_region is None or image_region.size == 0 or not self.available:
            return FieldRecognitionResult("", 0.0, [], field_type)

        processed = self._preprocess(image_region, field_type)
        attempts: list[tuple[str, float]] = []
        for config in self._configs_for(field_type, constraints):
            try:
                data = self._run(processed, config)
            except (TesseractError, TesseractNotFoundError, OSError) as exc:
                logger.warning("Tesseract failed on a %s field: %s", field_type, exc)
                self._available = False
                return FieldRecognitionResult("", 0.0, [], field_type)
            attempts.append((self._join_text(data), self._extract_confidence(data)))
            if attempts[-1][0] and attempts[-1][1] > 0.0:
                break

        # Prefer the pass that produced text with a usable confidence score.
        raw_text, confidence = max(
            attempts, key=lambda item: (bool(item[0]), item[1]), default=("", 0.0)
        )
        candidates: list[tuple[str, float]] = []
        if raw_text:
            candidates.append((raw_text, confidence))

        cleaned = self._postprocess(raw_text, field_type)
        if cleaned and cleaned != raw_text:
            # Cleaned variants never exceed the raw OCR confidence.
            candidates.append((cleaned, confidence))

        if field_type == "mcq" and constraints:
            candidates = self._rank_by_constraints(candidates, raw_text, constraints, confidence)

        # Always report the raw OCR text as the primary result.
        return FieldRecognitionResult(
            raw_text=raw_text,
            confidence=confidence,
            candidates=candidates,
            field_type=field_type,
        )

    @staticmethod
    def _postprocess(text: str, field_type: str) -> str:
        if not text:
            return ""
        if field_type == "email":
            cleaned = re.sub(r"\s+", "", text).lower()
            cleaned = cleaned.replace("(at)", "@").replace("[at]", "@")
            return cleaned
        if field_type == "mcq":
            letters = re.sub(r"[^A-Za-z]", "", text).upper()
            return letters[:1]
        if field_type == "number":
            match = re.search(r"-?\d+(?:\.\d+)?", text.replace(" ", ""))
            return match.group(0) if match else ""
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _rank_by_constraints(
        candidates: list[tuple[str, float]],
        raw_text: str,
        constraints: list[str],
        confidence: float,
    ) -> list[tuple[str, float]]:
        """Order candidates so constraint matches come first (confidence unchanged)."""
        letters = re.sub(r"[^A-Za-z]", "", raw_text).upper()
        normalized = {c.strip().upper() for c in constraints if c.strip()}
        ranked = [c for c in candidates if c[0].strip().upper() in normalized]
        rest = [c for c in candidates if c[0].strip().upper() not in normalized]
        if letters and letters[:1] in normalized and letters[:1] not in {c[0] for c in ranked}:
            ranked.insert(0, (letters[:1], confidence))
        return ranked + rest

    @staticmethod
    def is_valid_email(text: str) -> bool:
        return bool(EMAIL_RE.match((text or "").strip()))

    def segment_card_regions(self, card_image: np.ndarray) -> dict[str, np.ndarray]:
        return self.default_segments(card_image)
