"""Image quality checks and preprocessing (error layer 1). OpenCV: grayscale, deskew, contrast."""

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


@dataclass
class Quality:
    width: int
    height: int
    sharpness: float  # variance of the Laplacian; low means blurred

    def problems(self, min_side: int, min_sharpness: float) -> list[str]:
        out = []
        if min(self.width, self.height) < min_side:
            out.append("CAPTURE_TOO_SMALL")
        if self.sharpness < min_sharpness:
            out.append("CAPTURE_BLURRY")
        return out


def _gray(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)


def assess(img: Image.Image) -> Quality:
    gray = _gray(img)
    return Quality(
        width=img.width,
        height=img.height,
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    )


def _deskew(gray: np.ndarray) -> np.ndarray:
    inverted = cv2.bitwise_not(gray)
    thresh = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 50:
        return gray
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    if abs(angle) < 0.5 or abs(angle) > 15:  # tiny: not worth it; large: probably not text skew
        return gray
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def for_ocr(img: Image.Image, max_side: int = 2400) -> Image.Image:
    """Grayscale, upscale small captures, CLAHE contrast, deskew."""
    gray = _gray(img)
    h, w = gray.shape
    scale = min(max_side / max(h, w), 2.0)
    if scale > 1.05 or scale < 0.95:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return Image.fromarray(_deskew(gray))


def for_vlm(img: Image.Image, max_side: int = 1600) -> Image.Image:
    """VLMs read colour well; just bound the size to keep latency and memory predictable."""
    out = img.convert("RGB")
    out.thumbnail((max_side, max_side))
    return out
