"""File validation, hashing and image preparation (pipeline stage 1, error layer 1)."""

import hashlib
import io
from dataclasses import dataclass, field
from typing import Any

import imagehash
from PIL import ExifTags, Image, ImageOps

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard

# Magic numbers: we trust file signatures, never the client's content type or extension.
SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"%PDF-", "application/pdf"),
]


class RejectedFile(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sniff(data: bytes) -> str:
    for sig, ctype in SIGNATURES:
        if data.startswith(sig):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise RejectedFile("UNSUPPORTED_TYPE", "only PNG, JPEG, WEBP and PDF evidence is accepted")


@dataclass
class PreparedFile:
    content_type: str
    sha256: str
    image: Image.Image  # first page / the image, RGB, orientation corrected
    phash: str
    meta: dict[str, Any] = field(default_factory=dict)
    preview_png: bytes = b""


def _render_pdf(data: bytes) -> tuple[Image.Image, dict[str, Any]]:
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise RejectedFile("CORRUPT_FILE", "the PDF could not be opened") from exc
    if doc.needs_pass:
        raise RejectedFile("ENCRYPTED_PDF", "password-protected PDFs are not accepted")
    if doc.page_count == 0:
        raise RejectedFile("CORRUPT_FILE", "the PDF has no pages")
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    meta["page_count"] = doc.page_count
    # each incremental save appends a new trailer: more than one %%EOF means the PDF was edited
    # after it was first written (banks' generated statements are written once)
    meta["eof_markers"] = data.count(b"%%EOF")
    meta["has_text"] = bool(doc.load_page(0).get_text().strip())
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=200)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    return img, {"pdf": meta}


def _exif(img: Image.Image) -> dict[str, Any]:
    try:
        raw = img.getexif()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for tag_id, value in raw.items():
        name = ExifTags.TAGS.get(tag_id, str(tag_id))
        if name in {
            "Software",
            "DateTime",
            "Make",
            "Model",
            "DateTimeOriginal",
            "ProcessingSoftware",
            "Artist",
            "ImageDescription",
            "HostComputer",
        }:
            out[name] = str(value)
    return out


# metadata keys editors and generators write into PNG/WEBP text chunks and XMP
_TEXT_KEYS = (
    "software",
    "comment",
    "description",
    "parameters",
    "prompt",
    "workflow",
    "creator",
    "author",
    "title",
    "xmp",
    "xml:com.adobe.xmp",
    "c2pa",
    "source",
)


def _image_text(img: Image.Image) -> dict[str, str]:
    out = {}
    for key, value in (img.info or {}).items():
        if not isinstance(key, str) or key.startswith("pops-"):  # pops-* = test fixtures
            continue
        if any(k in key.lower() for k in _TEXT_KEYS) and isinstance(value, (str, bytes)):
            text = value.decode(errors="replace") if isinstance(value, bytes) else value
            out[key] = text[:500]
    return out


def _jpeg_quality(img: Image.Image) -> int | None:
    """Rough JPEG quality from the luminance quantisation table (re-saved images change it)."""
    try:
        tables = img.quantization  # type: ignore[attr-defined]
    except AttributeError:
        return None
    if not tables or 0 not in tables:
        return None
    avg = sum(tables[0]) / len(tables[0])
    # libjpeg's standard table averages ~57.6 at quality 50; scale per the IJG formula
    scale = avg / 57.625 * 100
    quality = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return max(1, min(100, round(quality)))


def prepare(data: bytes, max_bytes: int) -> PreparedFile:
    if len(data) == 0:
        raise RejectedFile("EMPTY_FILE", "the file is empty")
    if len(data) > max_bytes:
        raise RejectedFile("TOO_LARGE", f"files must be under {max_bytes // (1024 * 1024)} MB")
    ctype = sniff(data)
    sha = hashlib.sha256(data).hexdigest()
    meta: dict[str, Any] = {}
    if ctype == "application/pdf":
        img, meta = _render_pdf(data)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise RejectedFile("CORRUPT_FILE", "the image could not be decoded") from exc
        exif = _exif(img)
        if exif:
            meta["exif"] = exif  # a hint only: screenshots wipe EXIF, editors leave traces
        text_chunks = _image_text(img)
        if text_chunks:
            meta["image_text"] = text_chunks
        if ctype == "image/jpeg":
            meta["jpeg_quality"] = _jpeg_quality(img)
        img = ImageOps.exif_transpose(img).convert("RGB")
    meta["width"], meta["height"] = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return PreparedFile(
        content_type=ctype,
        sha256=sha,
        image=img,
        phash=str(imagehash.phash(img)),
        meta=meta,
        preview_png=buf.getvalue(),
    )


def phash_distance(a: str, b: str) -> int:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


def phash_to_int(hex_hash: str) -> int:
    """64-bit perceptual hash as a signed BIGINT (same bits as Postgres ('x'||hex)::bit(64))."""
    n = int(hex_hash, 16)
    return n - (1 << 64) if n >= 1 << 63 else n
