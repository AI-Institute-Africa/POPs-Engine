"""Passive selfie liveness, heuristic version 1.

Looks for the traces of the cheap attacks: a selfie of a phone/monitor (moire pattern in the
frequency spectrum, blown-out reflections), a very small or very soft face, and a flat, low-detail
image. It is NOT a substitute for active liveness (blink, head turn) or depth sensing, and its score
is only trustworthy once thresholds are calibrated on real captures. It sits behind the
`LivenessChecker` protocol so a stronger model can replace it without touching the rules.
"""

from dataclasses import dataclass, field
from typing import Protocol

import cv2
import numpy as np
from PIL import Image


@dataclass
class Liveness:
    score: float  # 0..1, higher = more likely a live capture
    signals: list[str] = field(default_factory=list)


class LivenessChecker(Protocol):
    name: str

    def check(self, selfie: Image.Image, face: Image.Image | None) -> Liveness: ...


def _moire_ratio(gray: np.ndarray) -> float:
    """Peak-to-background ratio of the mid/high-frequency spectrum. Screens re-photographed
    through a camera leave isolated periodic peaks; natural skin and rooms do not."""
    g = cv2.resize(gray, (256, 256)).astype(np.float32)
    g -= g.mean()
    mag = np.log1p(
        np.abs(np.fft.fftshift(np.fft.fft2(g * np.outer(np.hanning(256), np.hanning(256)))))
    )
    yy, xx = np.ogrid[:256, :256]
    r = np.hypot(yy - 128, xx - 128)
    band = mag[(r > 40) & (r < 120)]
    return float((band.max() - band.mean()) / (band.std() + 1e-6))


class HeuristicLiveness:
    name = "passive_heuristics_v1"
    MOIRE_Z = 9.0

    def check(self, selfie: Image.Image, face: Image.Image | None) -> Liveness:
        gray = cv2.cvtColor(np.asarray(selfie.convert("RGB")), cv2.COLOR_RGB2GRAY)
        score, signals = 1.0, []
        if _moire_ratio(gray) > self.MOIRE_Z:
            score -= 0.5
            signals.append("SCREEN_REPLAY_PATTERN")
        if float((gray >= 250).mean()) > 0.06:
            score -= 0.2
            signals.append("GLARE_OR_REFLECTION")
        if min(selfie.size) < 240:
            score -= 0.3
            signals.append("SELFIE_TOO_SMALL")
        if face is not None:
            fgray = cv2.cvtColor(np.asarray(face.convert("RGB")), cv2.COLOR_RGB2GRAY)
            if min(face.size) < 80:
                score -= 0.2
                signals.append("FACE_TOO_SMALL")
            if float(cv2.Laplacian(fgray, cv2.CV_64F).var()) < 15.0:
                score -= 0.2
                signals.append("FACE_TOO_SOFT")
        return Liveness(round(max(score, 0.0), 3), signals)


def build_checker() -> LivenessChecker:
    return HeuristicLiveness()
