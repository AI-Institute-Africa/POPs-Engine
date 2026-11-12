"""Find the ID card in a photo and flatten it, plus capture-quality checks.

The corner search follows the approach of Final-Project-OCR (TomerGibor, MIT): probabilistic Hough
lines -> the outermost near-horizontal and near-vertical lines -> their four intersections ->
perspective warp. A contour search is the fallback, and if neither finds a card the whole frame is
used and the result says so (`found=False`), which the rules treat as a weaker capture.
"""

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

ID1_RATIO = 85.6 / 54.0  # ID-1 card (national ID, licence): 1.585
CARD_WIDTH = 1000


@dataclass
class Card:
    image: Image.Image
    found: bool
    method: str  # hough | contour | frame
    area_fraction: float  # how much of the photo the card fills


@dataclass
class Capture:
    sharpness: float
    glare_fraction: float  # share of blown-out pixels
    problems: list[str]


def _order(pts: np.ndarray) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


def _intersect(a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-6:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x1 * y4 - y1 * x4)) / den
    return np.array([px, py], dtype=np.float32)


def _hough_quad(gray: np.ndarray) -> np.ndarray | None:
    h, w = gray.shape
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=80, minLineLength=int(min(h, w) * 0.3), maxLineGap=25
    )
    if lines is None:
        return None
    horiz, vert = [], []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):  # (N,1,4) on OpenCV 4, (N,4) on 5
        angle = abs(math.degrees(math.atan2(y2 - y1, x2 - x1)))
        if angle < 25 or angle > 155:
            horiz.append(np.array([x1, y1, x2, y2], dtype=np.float32))
        elif 65 < angle < 115:
            vert.append(np.array([x1, y1, x2, y2], dtype=np.float32))
    if len(horiz) < 2 or len(vert) < 2:
        return None
    ymid = lambda ln: (ln[1] + ln[3]) / 2  # noqa: E731
    xmid = lambda ln: (ln[0] + ln[2]) / 2  # noqa: E731
    top, bottom = min(horiz, key=ymid), max(horiz, key=ymid)
    left, right = min(vert, key=xmid), max(vert, key=xmid)
    if ymid(bottom) - ymid(top) < 0.3 * h or xmid(right) - xmid(left) < 0.3 * w:
        return None
    corners = [
        _intersect(top, left),
        _intersect(top, right),
        _intersect(bottom, right),
        _intersect(bottom, left),
    ]
    if any(c is None for c in corners):
        return None
    quad = np.array(corners, dtype=np.float32)
    if (quad < -0.1 * max(h, w)).any() or (quad > 1.1 * max(h, w)).any():
        return None
    return quad


def _contour_quad(gray: np.ndarray) -> np.ndarray | None:
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            best = approx.reshape(4, 2).astype(np.float32)
            break
    return best


def _quad_area(quad: np.ndarray) -> float:
    return float(cv2.contourArea(quad.reshape(-1, 1, 2)))


def find_card(img: Image.Image) -> Card:
    """The card flattened to a standard size, or the whole frame when no card edge is found."""
    rgb = np.asarray(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    scale = 1000 / max(h, w) if max(h, w) > 1000 else 1.0  # search on a bounded size
    small = cv2.resize(gray, None, fx=scale, fy=scale) if scale != 1.0 else gray
    for method, finder in (("hough", _hough_quad), ("contour", _contour_quad)):
        quad = finder(small)
        if quad is None:
            continue
        frac = _quad_area(quad) / float(small.shape[0] * small.shape[1])
        if not 0.2 <= frac <= 1.05:
            continue
        quad = _order(quad / scale)
        top = float(np.linalg.norm(quad[1] - quad[0]))
        side = float(np.linalg.norm(quad[3] - quad[0]))
        ratio = top / side if side else ID1_RATIO
        if ratio < 1:  # shot in portrait orientation: rotate the corner order
            quad = np.roll(quad, -1, axis=0)
            ratio = 1 / ratio if ratio else ID1_RATIO
        ratio = ratio if 1.2 <= ratio <= 1.8 else ID1_RATIO
        out_w, out_h = CARD_WIDTH, round(CARD_WIDTH / ratio)
        dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], dtype=np.float32)
        warped = cv2.warpPerspective(
            rgb, cv2.getPerspectiveTransform(quad, dst), (out_w, out_h), flags=cv2.INTER_CUBIC
        )
        return Card(Image.fromarray(warped), True, method, round(min(frac, 1.0), 3))
    return Card(img.convert("RGB"), False, "frame", 1.0)


def capture_quality(img: Image.Image, min_side: int, min_sharpness: float) -> Capture:
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    glare = float((gray >= 250).mean())
    problems = []
    if min(img.size) < min_side:
        problems.append("CAPTURE_TOO_SMALL")
    if sharp < min_sharpness:
        problems.append("CAPTURE_BLURRY")
    if glare > 0.08:
        problems.append("CAPTURE_GLARE")
    return Capture(round(sharp, 2), round(glare, 4), problems)
