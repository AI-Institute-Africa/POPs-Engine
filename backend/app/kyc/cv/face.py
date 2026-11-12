"""ID-photo to selfie face matching behind a small interface.

DocFace (seasonSH, MIT) showed that matching a document photo to a live selfie is its own problem
(small, compressed, often old photos) and that the decision should be a calibrated threshold on an
embedding similarity. Its TensorFlow 1.x / MATLAB / Caffe stack is not runnable here, so this
module keeps the idea and swaps the engine: any ArcFace-style ONNX embedding model, run on CPU.
Model weights are not bundled: point POPS_KYC_FACE_MODEL_PATH at one whose licence suits you.

The score is a cosine similarity, never a verdict. `kyc_min_face_score` in the policy is the
threshold and must be calibrated on your own ID/selfie pairs before auto-verification is turned on.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import cv2
import numpy as np
from PIL import Image

from app.core.config import get_settings

log = logging.getLogger(__name__)


class FaceUnavailable(Exception):
    """No usable face model. The pipeline fails closed: a person decides."""


class FaceMatcher(Protocol):
    name: str
    version: str

    def id_portrait(self, card: Image.Image) -> Image.Image | None: ...

    def selfie_faces(self, selfie: Image.Image) -> list[Image.Image]: ...

    def embed(self, face: Image.Image) -> np.ndarray: ...


@dataclass
class FaceMatch:
    score: float | None
    problems: list[str] = field(default_factory=list)
    portrait_phash_source: Image.Image | None = None
    selfie_face: Image.Image | None = None


def _crop(img: Image.Image, box: tuple[int, int, int, int], margin: float = 0.25) -> Image.Image:
    x, y, w, h = box
    mx, my = int(w * margin), int(h * margin)
    return img.crop(
        (max(0, x - mx), max(0, y - my), min(img.width, x + w + mx), min(img.height, y + h + my))
    )


class _Detector:
    """YuNet when its model file is present, else OpenCV 4's bundled Haar cascade."""

    def __init__(self) -> None:
        self.path = get_settings().kyc_face_detector_path
        self.yunet = os.path.exists(self.path) and hasattr(cv2, "FaceDetectorYN")
        self.haar: Any = None
        data = getattr(cv2, "data", None)  # OpenCV 4 ships the Haar cascades; 5 does not
        if not self.yunet and hasattr(cv2, "CascadeClassifier") and data is not None:
            xml = os.path.join(data.haarcascades, "haarcascade_frontalface_default.xml")
            if os.path.exists(xml):
                self.haar = cv2.CascadeClassifier(xml)
        if not self.yunet and self.haar is None:
            raise FaceUnavailable(f"no face detector: {self.path} not found")

    def boxes(self, img: Image.Image) -> list[tuple[int, int, int, int]]:
        rgb = np.asarray(img.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if self.yunet:
            det = cv2.FaceDetectorYN.create(self.path, "", (bgr.shape[1], bgr.shape[0]), 0.7)
            _, faces = det.detect(bgr)
            found = [] if faces is None else [tuple(int(v) for v in f[:4]) for f in faces]
        else:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            found = [
                tuple(int(v) for v in b)
                for b in self.haar.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
            ]
        return sorted(found, key=lambda b: b[2] * b[3], reverse=True)  # type: ignore[arg-type,return-value]


class OnnxFaceMatcher:
    name = "onnx_face"

    def __init__(self) -> None:
        s = get_settings()
        if not os.path.exists(s.kyc_face_model_path):
            raise FaceUnavailable(f"face model not found at {s.kyc_face_model_path}")
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            s.kyc_face_model_path, providers=["CPUExecutionProvider"]
        )
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        # NCHW (or NHWC) with a fixed square side, 112 for ArcFace-style models
        dims = [d for d in inp.shape if isinstance(d, int) and d > 3]
        self.side = dims[0] if dims else 112
        self.version = f"onnx:{os.path.basename(s.kyc_face_model_path)}"
        self.detector = _Detector()

    def id_portrait(self, card: Image.Image) -> Image.Image | None:
        boxes = self.detector.boxes(card)
        return _crop(card, boxes[0]) if boxes else None

    def selfie_faces(self, selfie: Image.Image) -> list[Image.Image]:
        return [_crop(selfie, b) for b in self.detector.boxes(selfie)]

    def embed(self, face: Image.Image) -> np.ndarray:
        arr = np.asarray(face.convert("RGB").resize((self.side, self.side)), dtype=np.float32)
        arr = ((arr - 127.5) / 127.5).transpose(2, 0, 1)[None]
        out = self.session.run(None, {self.input_name: arr})[0][0].astype(np.float32)
        return out / (np.linalg.norm(out) or 1.0)


class FakeFaceMatcher:
    """Tests and demos: the portrait is a fixed region of the card, the selfie is the whole
    frame, and an embedding is a normalised thumbnail, so the same picture matches itself and
    different pictures do not. Never used in production (see validate_for_startup)."""

    name = "fake_face"
    version = "fake:1"
    PORTRAIT = (0.05, 0.25, 0.30, 0.55)  # x, y, w, h as fractions of the card

    def id_portrait(self, card: Image.Image) -> Image.Image | None:
        x, y, w, h = self.PORTRAIT
        W, H = card.size
        face = card.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
        return face if np.asarray(face.convert("L")).std() > 5 else None  # blank = no face

    def selfie_faces(self, selfie: Image.Image) -> list[Image.Image]:
        return [selfie] if np.asarray(selfie.convert("L")).std() > 5 else []

    def embed(self, face: Image.Image) -> np.ndarray:
        v = np.asarray(face.convert("L").resize((16, 16)), dtype=np.float32).ravel()
        v = v - v.mean()
        return v / (np.linalg.norm(v) or 1.0)


def build_matcher() -> FaceMatcher:
    backend = get_settings().kyc_face_backend
    if backend == "fake":
        return FakeFaceMatcher()
    if backend == "demo":
        from app.demo.sim import DemoFaceMatcher

        return DemoFaceMatcher()
    if backend == "onnx":
        return OnnxFaceMatcher()
    raise FaceUnavailable("face matching is not configured (POPS_KYC_FACE_BACKEND=none)")


def match(matcher: FaceMatcher, card: Image.Image, selfie: Image.Image) -> FaceMatch:
    """Cosine similarity between the ID portrait and the selfie face, with the reasons when
    there is no usable score."""
    portrait = matcher.id_portrait(card)
    faces = matcher.selfie_faces(selfie)
    problems = []
    if portrait is None:
        problems.append("NO_FACE_ON_ID")
    if not faces:
        problems.append("NO_FACE_IN_SELFIE")
    if len(faces) > 1:
        problems.append("MULTIPLE_FACES_IN_SELFIE")
    if portrait is None or not faces:
        return FaceMatch(None, problems)
    score = float(np.dot(matcher.embed(portrait), matcher.embed(faces[0])))
    return FaceMatch(round(score, 4), problems, portrait, faces[0])
