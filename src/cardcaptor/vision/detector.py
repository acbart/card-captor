"""Card detection from photographs."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_AREA_RATIO = 0.02
MAX_AREA_RATIO = 0.70
MIN_ASPECT_RATIO = 1.2
MAX_ASPECT_RATIO = 2.5


@dataclass
class DetectedCard:
    """A quadrilateral region believed to be an index card."""

    corners: np.ndarray  # shape (4, 2), float32, ordered TL, TR, BR, BL
    confidence: float
    area: float

    @property
    def center(self) -> tuple[float, float]:
        pts = np.asarray(self.corners, dtype=np.float64)
        return float(pts[:, 0].mean()), float(pts[:, 1].mean())


def order_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    ordered[0] = pts[np.argmin(s)]      # top-left: smallest x+y
    ordered[2] = pts[np.argmax(s)]      # bottom-right: largest x+y
    ordered[1] = pts[np.argmin(diff)]   # top-right: smallest y-x
    ordered[3] = pts[np.argmax(diff)]   # bottom-left: largest y-x
    return ordered


def _quad_side_lengths(quad: np.ndarray) -> tuple[float, float]:
    """Return (long_side, short_side) averaged over opposing edges."""
    tl, tr, br, bl = quad
    top = float(np.linalg.norm(tr - tl))
    bottom = float(np.linalg.norm(br - bl))
    left = float(np.linalg.norm(bl - tl))
    right = float(np.linalg.norm(br - tr))
    width = (top + bottom) / 2.0
    height = (left + right) / 2.0
    return (max(width, height), min(width, height))


def _rectangularity(quad: np.ndarray) -> float:
    """How close the quad's corner angles are to 90 degrees (0..1)."""
    scores = []
    for i in range(4):
        p_prev = quad[(i - 1) % 4]
        p_cur = quad[i]
        p_next = quad[(i + 1) % 4]
        v1 = p_prev - p_cur
        v2 = p_next - p_cur
        n1 = float(np.linalg.norm(v1))
        n2 = float(np.linalg.norm(v2))
        if n1 == 0 or n2 == 0:
            return 0.0
        cos_theta = float(np.dot(v1, v2) / (n1 * n2))
        scores.append(1.0 - min(abs(cos_theta), 1.0))
    return float(sum(scores) / len(scores))


def _binary_candidates(gray: np.ndarray) -> list[np.ndarray]:
    """Produce several binary images to look for card outlines in."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    candidates: list[np.ndarray] = []

    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    candidates.append(edges)

    adaptive = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 35, 10
    )
    candidates.append(adaptive)

    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    candidates.append(otsu)

    return candidates


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _dedupe(cards: list[DetectedCard], min_distance: float) -> list[DetectedCard]:
    """Remove detections whose centers nearly coincide, keeping the best one."""
    kept: list[DetectedCard] = []
    for card in cards:
        cx, cy = card.center
        duplicate = False
        for existing in kept:
            ex, ey = existing.center
            if float(np.hypot(cx - ex, cy - ey)) < min_distance:
                duplicate = True
                break
        if not duplicate:
            kept.append(card)
    return kept


def detect_cards(
    image: np.ndarray,
    min_area_ratio: float = MIN_AREA_RATIO,
    max_area_ratio: float = MAX_AREA_RATIO,
    min_aspect: float = MIN_ASPECT_RATIO,
    max_aspect: float = MAX_ASPECT_RATIO,
) -> list[DetectedCard]:
    """Detect index cards in a photograph.

    Returns detections sorted by area descending.
    """
    if image is None or image.size == 0:
        return []

    gray = _to_gray(image)
    img_h, img_w = gray.shape[:2]
    img_area = float(img_h * img_w)
    if img_area == 0:
        return []

    detections: list[DetectedCard] = []
    for binary in _binary_candidates(gray):
        contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < img_area * min_area_ratio or area > img_area * max_area_ratio:
                continue
            peri = cv2.arcLength(contour, True)
            if peri <= 0:
                continue
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            quad = order_points(approx.reshape(4, 2).astype(np.float32))
            long_side, short_side = _quad_side_lengths(quad)
            if short_side <= 0:
                continue
            aspect = long_side / short_side
            if not (min_aspect <= aspect <= max_aspect):
                continue

            quad_area = float(cv2.contourArea(quad.astype(np.float32)))
            fill = quad_area / area if area > 0 else 0.0
            rect_score = _rectangularity(quad)
            aspect_score = 1.0 - min(abs(aspect - 5.0 / 3.0) / 1.0, 1.0)
            confidence = float(
                np.clip(0.45 * rect_score + 0.35 * aspect_score + 0.20 * min(fill, 1.0), 0.0, 1.0)
            )
            detections.append(
                DetectedCard(corners=quad, confidence=confidence, area=quad_area)
            )

    detections.sort(key=lambda c: (c.area, c.confidence), reverse=True)
    min_distance = 0.03 * float(np.hypot(img_w, img_h))
    detections = _dedupe(detections, min_distance)
    detections.sort(key=lambda c: c.area, reverse=True)
    return detections


def normalized_center(card: DetectedCard, image_shape: tuple[int, ...]) -> tuple[float, float]:
    """Return the card center normalized to [0, 1] within the source image."""
    height, width = image_shape[:2]
    cx, cy = card.center
    if width == 0 or height == 0:
        return (0.0, 0.0)
    return (float(cx / width), float(cy / height))
