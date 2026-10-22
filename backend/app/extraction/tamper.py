"""Document tamper signals (blueprint 8, error layer 6): a risk flag, never a verdict.

Fakes are cheap to generate, so a manipulation score can only escalate a claim to a reviewer.
It can never approve, reject or allocate (blueprint 8: "adopted as a risk flag only").

Two deterministic, format-agnostic signals, both computed per 32px cell:

- Error level analysis. The image is re-encoded at a fixed JPEG quality and the per-cell
  residual is measured. A region with a different compression history (a pasted crop) leaves
  a residual the rest of the image does not have. Plain ELA fires on text edges, so each
  cell's residual is normalised by what its gradient energy predicts, and only the unexplained
  part is scored.
- Noise inconsistency. Local noise sigma (median of the absolute high-pass residual, so
  sparse text strokes inside a cell cannot masquerade as noise) in flat cells - flatness is
  edge-density based, because a noisy patch raises its own gradient and a rank-based rule
  would exempt exactly the anomaly being hunted. A crop from another capture carries a
  different noise floor.

Both signals are robust z-scores over the cell population, so thresholds transfer across
providers and capture types without per-layout calibration. The absolute floors and the
score weights are calibrated on synthetic receipts only (app/devtools.py fixtures) and must
be re-validated on real Phase 0 samples before production use.
"""

import io
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

# z-score above which a cell is anomalous, and the absolute floors that keep tiny numerical
# differences on clean images from ever flagging (blueprint: calibrate on real samples)
Z_THRESHOLD = 6.0
NOISE_FLOOR = 1.5
ELA_FLOOR = 3.0  # gray levels of residual the local edges do not explain
CELL_PX = 32
MIN_SIDE = 160  # below this there are too few cells for a meaningful population
ELA_QUALITY = 90
# a pixel gradient above this is a real content edge, not sensor noise
GRAD_EDGE = 32.0
# a cell with fewer than 5% real-edge pixels counts as flat (noise can then be judged there)
FLAT_EDGE_DENSITY = 0.05
# score = weight * (coverage + intensity), both saturating: a paste covering ~5% of the
# cells, or a region a good bit beyond the population noise, is review-worthy; isolated
# extreme cells are not. Calibrated on synthetic receipts only - real samples must
# re-calibrate (blueprint 8: risk flag, never a verdict).
SUSPICIOUS_COVERAGE = 0.05  # fraction of anomalous cells at which the coverage term saturates
SUSPICIOUS_EXCESS = 8.0  # mean sigma over Z_THRESHOLD at which the intensity term saturates
SCORE_WEIGHT = 0.5


@dataclass
class TamperReport:
    score: float = 0.0  # 0..1; >= policy.tamper_review_score routes the claim to a reviewer
    regions: list[dict] = field(default_factory=list)  # {x,y,w,h,score} normalised 0..1
    signals: list[str] = field(default_factory=list)
    skipped: str | None = None

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "regions": self.regions,
            "signals": self.signals,
            "skipped": self.skipped,
        }


def _cells(gray: np.ndarray) -> tuple[int, int]:
    h, w = gray.shape
    return max(1, h // CELL_PX), max(1, w // CELL_PX)


def _block_mean(a: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Mean of each cell in a rows x cols grid (edge cells included, partial)."""
    return a.reshape(rows, CELL_PX, cols, CELL_PX).mean(axis=(1, 3))


def _block_median(a: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Median of each cell - robust to sparse content pixels (text strokes)."""
    blocks = (
        a[: rows * CELL_PX, : cols * CELL_PX]
        .reshape(rows, CELL_PX, cols, CELL_PX)
        .transpose(0, 2, 1, 3)
        .reshape(rows, cols, -1)
    )
    return np.median(blocks, axis=2)


def _robust_z(values: np.ndarray) -> np.ndarray:
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    sigma = 1.4826 * mad
    if sigma < 1e-6:  # degenerate population: many identical values (e.g. digital flats)
        mean_dev = float(np.abs(values - med).mean())
        if mean_dev < 1e-6:  # a perfectly uniform image: nothing stands out
            return np.zeros_like(values)
        sigma = 1.2533 * mean_dev  # fall back to the mean deviation as the scale estimate
    return (values - med) / sigma


def _ela_map(img: Image.Image, quality: int = ELA_QUALITY) -> np.ndarray:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    reencoded = np.asarray(Image.open(buf).convert("L"), dtype=np.float32)
    original = np.asarray(img.convert("L"), dtype=np.float32)
    if reencoded.shape != original.shape:  # defensive: JPEG needs even dimensions
        reencoded = np.asarray(
            cv2.resize(reencoded, (original.shape[1], original.shape[0])), dtype=np.float32
        )
    return np.abs(original - reencoded)


def _merge_regions(mask: np.ndarray) -> list[dict]:
    """Flagged cells -> merged bounding boxes, normalised to 0..1."""
    kernel = np.ones((3, 3), np.uint8)
    expanded = cv2.dilate(mask.astype(np.uint8), kernel, iterations=1)
    count, _, stats, _ = cv2.connectedComponentsWithStats(expanded, connectivity=8)
    rows, cols = mask.shape
    out = []
    for i in range(1, count):  # 0 is the background component
        x, y, w, h, area = stats[i]
        cell_flags = mask[y : y + h, x : x + w].sum()
        if not cell_flags:
            continue
        out.append(
            {
                # plain floats: these ride in run.checks (JSONB) and must survive json.dumps
                "x": float(round(x / cols, 4)),
                "y": float(round(y / rows, 4)),
                "w": float(round(w / cols, 4)),
                "h": float(round(h / rows, 4)),
                "score": float(round(min(1.0, cell_flags / max(1, area)), 3)),
            }
        )
    out.sort(key=lambda r: r["w"] * r["h"] * r["score"], reverse=True)
    return out[:8]


def analyse(img: Image.Image) -> TamperReport:
    """Manipulation score and highlighted regions for one evidence image.

    Pure and deterministic: the same bytes always produce the same report, so a verification
    run stays replayable. Never raises on odd input - an analysis that cannot run is reported
    as skipped and the caller decides (the pipeline fails closed to review).
    """
    report = TamperReport()
    if min(img.size) < MIN_SIDE:
        report.skipped = "image_too_small"
        return report
    try:
        gray = np.asarray(img.convert("L"), dtype=np.float32)
        rows, cols = _cells(gray)
        h, w = rows * CELL_PX, cols * CELL_PX
        gray = gray[:h, :w]

        # signal 1: error level analysis, normalised by what the local edges explain
        ela = _block_mean(_ela_map(Image.fromarray(gray.astype(np.uint8))), rows, cols)
        # scale=1/8 turns the ksize=3 Sobel response into a per-pixel gradient (a unit ramp
        # reads 1), so the GRAD_EDGE threshold means the same thing as in calibration
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3, scale=0.125)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3, scale=0.125)
        grad = _block_mean(np.hypot(gx, gy), rows, cols)
        k = float(np.median(ela / (grad + 1.0)))
        ela_residual = ela - k * grad
        z_ela = _robust_z(ela_residual.ravel()).reshape(rows, cols)

        # signal 2: noise floor in flat cells only (edges and text carry real variance).
        # Flatness is edge-density based: a rank-based rule (grad <= median) would exempt
        # exactly the anomaly we hunt, because a noisy patch raises its own gradient.
        denoised = cv2.medianBlur(gray.astype(np.uint8), 3).astype(np.float32)
        # the per-cell noise estimate is a median, so sparse text strokes inside a cell
        # cannot masquerade as a noise floor
        noise = _block_median(np.abs(gray - denoised), rows, cols)
        edge_px = (np.hypot(gx, gy) > GRAD_EDGE).astype(np.float32)
        flat = _block_mean(edge_px, rows, cols) < FLAT_EDGE_DENSITY
        z_noise = np.zeros_like(noise)
        if flat.sum() >= 9:
            z_noise[flat] = _robust_z(noise[flat])

        anomalous = ((z_ela > Z_THRESHOLD) & (ela_residual > ELA_FLOOR)) | (
            (z_noise > Z_THRESHOLD) & (noise > NOISE_FLOOR) & flat
        )
        if anomalous.any():
            # only signals that actually crossed the threshold count toward the intensity
            # term; sub-threshold values of the other signal would poison the mean
            fired = np.concatenate(
                [
                    z_ela[anomalous & (z_ela > Z_THRESHOLD)],
                    z_noise[anomalous & (z_noise > Z_THRESHOLD)],
                ]
            )
            excess = float(np.mean(np.minimum(fired - Z_THRESHOLD, 4 * SUSPICIOUS_EXCESS)))
            coverage = float(anomalous.mean())
            report.score = round(
                min(
                    1.0,
                    max(
                        0.0,
                        SCORE_WEIGHT
                        * (
                            min(1.0, coverage / SUSPICIOUS_COVERAGE)
                            + min(1.0, excess / SUSPICIOUS_EXCESS)
                        ),
                    ),
                ),
                3,
            )
            if ((z_ela > Z_THRESHOLD) & (ela_residual > ELA_FLOOR)).any():
                report.signals.append("ELA_DEVIATION")
            if ((z_noise > Z_THRESHOLD) & (noise > NOISE_FLOOR) & flat).any():
                report.signals.append("NOISE_INCONSISTENCY")
            report.regions = _merge_regions(anomalous)
        return report
    except Exception:  # noqa: BLE001 - forensics must never break a claim; fail closed upstream
        report.skipped = "analysis_failed"
        return report
