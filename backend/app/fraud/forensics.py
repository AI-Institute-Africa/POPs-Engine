"""Image forensics for tamper flags (blueprint 4.7, 12). Flags escalate; they never approve.

Error Level Analysis (ELA): re-save a JPEG at a known quality and look at how much each region
changes. A region pasted in from another image (a new amount, a moved date) has usually been
compressed a different number of times, so it stands out from its surroundings. ELA is only
meaningful for JPEGs; lossless screenshots (PNG) are skipped. It is a weak signal on its own and
is weighted accordingly.

We scan several re-save qualities ("JPEG ghosts"): a region pasted in after the original was
compressed stands out most when re-saved near the original's quality. An edit saved at exactly
the original quality stays invisible, a known limit of ELA.
"""

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

BLOCK = 16


@dataclass
class ElaResult:
    score: float  # robust z-score of the most anomalous region
    outlier_share: float  # share of textured blocks that are anomalous
    suspicious: bool
    heatmap_png: bytes


GHOST_QUALITIES = (60, 65, 70, 75, 80, 85, 90)


def ela(img: Image.Image) -> ElaResult:
    """Most anomalous result across re-save qualities."""
    results = [_ela_at(img, q) for q in GHOST_QUALITIES]
    return max(results, key=lambda r: (r.suspicious, r.score))


def _ela_at(img: Image.Image, quality: int) -> ElaResult:
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=quality)
    resaved = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    diff = np.abs(np.asarray(rgb, dtype=np.int16) - np.asarray(resaved, dtype=np.int16))
    err = diff.max(axis=2).astype(np.float32)

    h, w = err.shape
    hb, wb = h // BLOCK, w // BLOCK
    if hb < 4 or wb < 4:
        return ElaResult(0.0, 0.0, False, _heatmap(err))
    blocks = err[: hb * BLOCK, : wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK).mean(axis=(1, 3))
    gray = np.asarray(rgb.convert("L"), dtype=np.float32)
    texture = gray[: hb * BLOCK, : wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK).std(axis=(1, 3))
    # flat background compresses to ~0 error everywhere; compare like with like (textured blocks)
    textured = blocks[texture > 4]
    if textured.size < 16:
        return ElaResult(0.0, 0.0, False, _heatmap(err))
    median = float(np.median(textured))
    mad = float(np.median(np.abs(textured - median))) * 1.4826 + 0.5
    z = (textured - median) / mad
    score = float(z.max())
    share = float((z > 6).mean())
    # a local edit: a few strongly anomalous blocks, not the whole image (which means re-save)
    # calibrated on synthetic edits: genuine re-saves stay below ~3.5, pasted regions reach 6-9
    suspicious = score > 5.5 and 0.001 < share < 0.2
    return ElaResult(round(score, 2), round(share, 4), suspicious, _heatmap(err))


def _heatmap(err: np.ndarray) -> bytes:
    peak = float(err.max()) or 1.0
    scaled = np.clip(err * (255.0 / peak), 0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(scaled, mode="L").save(out, format="PNG")
    return out.getvalue()
