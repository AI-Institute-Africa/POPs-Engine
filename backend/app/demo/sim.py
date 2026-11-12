"""Simulated face matcher for the demo (`POPS_KYC_FACE_BACKEND=demo`).

The demo identity card and selfies are drawn avatars (see `art.py`), so a real face model has
nothing meaningful to match. This matcher knows where the portrait sits on the demo card, decides
"is there a person in the photo" from skin-tone coverage in the middle of the frame, and compares
the central part of both pictures as a colour thumbnail. The same drawn person scores high, a
different one low, a photo with nobody in it reports no face. It is a stand-in so the demo can show
the whole flow offline; it is refused in production.
"""

import cv2
import numpy as np
from PIL import Image

from app.demo.art import PORTRAIT


def _has_person(img: Image.Image) -> bool:
    w, h = img.size
    mid = img.crop((int(w * 0.25), int(h * 0.12), int(w * 0.75), int(h * 0.7))).convert("RGB")
    hsv = cv2.cvtColor(np.asarray(mid.resize((96, 96))), cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    skin = (hue >= 3) & (hue <= 22) & (sat >= 50) & (sat <= 190) & (val >= 70)
    return float(skin.mean()) > 0.10


class DemoFaceMatcher:
    name = "demo_sim"
    version = "demo:1"

    def id_portrait(self, card: Image.Image) -> Image.Image | None:
        x, y, w, h = PORTRAIT
        W, H = card.size
        face = card.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
        return face if _has_person(face) else None

    def selfie_faces(self, selfie: Image.Image) -> list[Image.Image]:
        return [selfie] if _has_person(selfie) else []

    def embed(self, face: Image.Image) -> np.ndarray:
        """A coarse colour layout in Lab: skin, hair and background colours and where they sit.
        Robust to the small pose and light changes between an ID portrait and a selfie."""
        w, h = face.size
        core = face.crop((int(w * 0.1), int(h * 0.05), int(w * 0.9), int(h * 0.92)))
        small = np.asarray(core.convert("RGB").resize((12, 12), Image.Resampling.BOX))
        lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
        lab = (lab - np.array([128.0, 128.0, 128.0])) / np.array([60.0, 25.0, 25.0])
        v = lab.ravel()
        return v / (np.linalg.norm(v) or 1.0)
