"""Perspective correction for detected cards."""

from __future__ import annotations

import cv2
import numpy as np

from .detector import order_points

DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 480


def order_corners(corners: np.ndarray) -> np.ndarray:
    """Order 4 corners as top-left, top-right, bottom-right, bottom-left."""
    return order_points(corners)


def _needs_rotation(quad: np.ndarray) -> bool:
    """True when the quad is taller than wide (card photographed sideways)."""
    tl, tr, br, bl = quad
    width = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2.0
    height = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2.0
    return bool(height > width)


def correct_perspective(
    image: np.ndarray,
    corners: np.ndarray,
    target_width: int = DEFAULT_WIDTH,
    target_height: int = DEFAULT_HEIGHT,
) -> np.ndarray:
    """Warp the quadrilateral defined by ``corners`` into a rectangular image."""
    if image is None or image.size == 0:
        raise ValueError("image must not be empty")

    quad = order_corners(np.asarray(corners, dtype=np.float32))
    if _needs_rotation(quad):
        # Rotate the corner ordering so the long edge becomes the width.
        quad = np.roll(quad, -1, axis=0).astype(np.float32)

    destination = np.array(
        [
            [0, 0],
            [target_width - 1, 0],
            [target_width - 1, target_height - 1],
            [0, target_height - 1],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(quad, destination)
    return cv2.warpPerspective(image, matrix, (target_width, target_height))
