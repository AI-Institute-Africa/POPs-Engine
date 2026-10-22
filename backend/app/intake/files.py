"""File validation, hashing and image preparation (pipeline stage 1, error layer 1)."""

import email
import email.policy
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

import imagehash
from PIL import ExifTags, Image, ImageDraw, ImageFont, ImageOps

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard

try:  # iPhone photos (HEIC/HEIF)
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF = True
except ImportError:  # pragma: no cover
    HEIF = False

MAX_PAGES = 10  # pages read per document; longer documents are flagged for review
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT = "text/plain"  # pasted SMS / WhatsApp / banking-app text
EML = "message/rfc822"  # a forwarded email, with its attachments
MAX_TEXT_CHARS = 20_000
_EMAIL_HEADER = re.compile(
    rb"^(From|To|Subject|Date|Received|Return-Path|MIME-Version|Message-ID|Delivered-To|"
    rb"DKIM-Signature|X-[A-Za-z-]+):",
    re.M | re.I,
)
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"}

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
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS and HEIF:
        return "image/heic"
    if data.startswith(b"PK\x03\x04"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile as exc:
            raise RejectedFile("CORRUPT_FILE", "the document could not be opened") from exc
        if "word/document.xml" in names:
            return DOCX
    if b"\x00" not in data[:4096]:
        head = data[:4096]
        if len(_EMAIL_HEADER.findall(head)) >= 3 and re.search(rb"^From:", head, re.M | re.I):
            return EML
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = ""
        printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
        if text.strip() and len(text) <= MAX_TEXT_CHARS and printable / len(text) > 0.97:
            return TEXT
    raise RejectedFile(
        "UNSUPPORTED_TYPE",
        "accepted evidence: PNG, JPEG, WEBP, HEIC photos, PDF, Word (.docx), forwarded emails "
        "(.eml) and pasted text",
    )


@dataclass
class PreparedFile:
    content_type: str
    sha256: str
    image: Image.Image  # first page / the image, RGB, orientation corrected
    phash: str
    meta: dict[str, Any] = field(default_factory=dict)
    preview_png: bytes = b""
    pages: list[Image.Image] = field(default_factory=list)  # every page/image to read
    page_texts: list[str] = field(default_factory=list)  # digital text per page (PDF/DOCX)
    # preview is the pages stacked vertically; (y0, h) of each page as fractions of the stack
    page_layout: list[tuple[float, float]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(t for t in self.page_texts if t)


def _render_pdf(data: bytes) -> tuple[list[Image.Image], list[str], dict[str, Any]]:
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
    pages, texts = [], []
    for n in range(min(doc.page_count, MAX_PAGES)):
        page = doc.load_page(n)
        texts.append(page.get_text().strip())
        pix = page.get_pixmap(dpi=150 if doc.page_count > 3 else 200)
        pages.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    meta["has_text"] = any(texts)
    meta["pages_read"] = len(pages)
    return pages, texts, {"pdf": meta}


def _read_docx(data: bytes) -> tuple[list[Image.Image], list[str], dict[str, Any]]:
    """Word documents: the text (paragraphs and tables) plus any embedded screenshots."""
    from docx import Document

    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = zf.infolist()
    if len(infos) > 2000 or sum(i.file_size for i in infos) > 60 * 1024 * 1024:
        raise RejectedFile("CORRUPT_FILE", "the document is too large when unpacked")
    names = {i.filename for i in infos}
    content_types = zf.read("[Content_Types].xml").decode(errors="replace")
    if "word/vbaProject.bin" in names or "macroEnabled" in content_types:
        raise RejectedFile("MACRO_DOCUMENT", "documents with macros are not accepted")
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise RejectedFile("CORRUPT_FILE", "the document could not be read") from exc

    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(dict.fromkeys(cells)))
    text = "\n".join(lines)

    images: list[Image.Image] = []
    for name in sorted(n for n in names if n.startswith("word/media/")):
        if len(images) >= MAX_PAGES - 1:
            break
        try:
            img = Image.open(io.BytesIO(zf.read(name)))
            img.load()
            if min(img.size) >= 100:  # skip logos and icons
                images.append(ImageOps.exif_transpose(img).convert("RGB"))
        except Exception:  # noqa: BLE001, S112 - EMF/WMF and odd formats are skipped
            continue

    pages = ([_render_text(text)] if text else []) + images
    texts = [text] + [""] * len(images) if text else [""] * len(images)
    if not pages:
        raise RejectedFile("UNREADABLE_DOCUMENT", "the document has no text or images to read")
    cp = doc.core_properties
    props = {
        "author": cp.author,
        "last_modified_by": cp.last_modified_by,
        "created": cp.created.isoformat() if cp.created else None,
        "modified": cp.modified.isoformat() if cp.modified else None,
        "revision": cp.revision,
    }
    if "docProps/app.xml" in names:
        app_xml = zf.read("docProps/app.xml").decode(errors="replace")
        start = app_xml.find("<Application>")
        if start >= 0:
            props["application"] = app_xml[start + 13 : app_xml.find("</Application>")][:100]
    meta = {"docx": {k: v for k, v in props.items() if v}, "embedded_images": len(images)}
    return pages, texts, meta


def _read_eml(data: bytes, max_bytes: int) -> tuple[list[Image.Image], list[str], dict[str, Any]]:
    """A forwarded email: its body as text, plus every attachment read like an upload."""
    msg = email.message_from_bytes(data, policy=email.policy.default)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        try:
            body = body_part.get_content()
        except (LookupError, ValueError):
            body = ""
        if body_part.get_content_type() == "text/html":
            body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
            body = re.sub(r"<br\s*/?>|</p>|</tr>|</div>", "\n", body, flags=re.I)
            body = re.sub(r"<[^>]+>", " ", body)
    body = "\n".join(" ".join(line.split()) for line in body.splitlines() if line.strip())
    header_text = "\n".join(f"{h}: {msg.get(h)}" for h in ("From", "Date", "Subject") if msg.get(h))
    pages: list[Image.Image] = []
    texts: list[str] = []
    if body:
        text = f"{header_text}\n{body}"[:MAX_TEXT_CHARS]
        pages.append(_render_text(text))
        texts.append(text)
    attachments = []
    for part in msg.iter_attachments():
        if len(pages) >= MAX_PAGES:
            break
        payload = part.get_payload(decode=True)
        name = part.get_filename() or part.get_content_type()
        try:
            # an attached message/rfc822 decodes to a Message, not bytes
            if not isinstance(payload, bytes) or sniff(payload) == EML:
                raise RejectedFile("NESTED_EMAIL", "nested emails are not unpacked")
            sub = prepare(payload, max_bytes)
        except RejectedFile as exc:
            attachments.append({"name": name, "read": False, "reason": exc.code})
            continue
        attachments.append(
            {
                "name": name,
                "read": True,
                "type": sub.content_type,
                "pages": len(sub.pages),
                "sha256": sub.sha256,
            }
        )
        room = MAX_PAGES - len(pages)
        pages.extend(sub.pages[:room])
        texts.extend(sub.page_texts[:room])
    if not pages:
        raise RejectedFile("UNREADABLE_DOCUMENT", "the email has no text or readable attachments")
    meta = {
        "email": {
            "from": str(msg.get("From", ""))[:200],
            "date": str(msg.get("Date", ""))[:100],
            "subject": str(msg.get("Subject", ""))[:300],
            "dkim_signed": msg.get("DKIM-Signature") is not None,
            "attachments": attachments,
        }
    }
    return pages, texts, meta


def _render_text(text: str, width: int = 1240) -> Image.Image:
    """A readable page image of document text, for the readers and the reviewer's viewer."""
    try:
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.truetype(
            "DejaVuSans.ttf", 26
        )
    except OSError:
        font = ImageFont.load_default()
    wrapped: list[str] = []
    for line in text.splitlines()[:120]:
        while len(line) > 80:
            cut = line.rfind(" ", 0, 80)
            cut = cut if cut > 20 else 80
            wrapped.append(line[:cut])
            line = line[cut:].lstrip()
        wrapped.append(line)
    img = Image.new("RGB", (width, max(400, 60 + 38 * len(wrapped))), "white")
    d = ImageDraw.Draw(img)
    for n, line in enumerate(wrapped):
        d.text((40, 30 + 38 * n), line, fill="black", font=font)
    return img


def _stack(pages: list[Image.Image], width: int = 1000) -> tuple[Image.Image, list]:
    """Pages scaled to one width and stacked, with each page's position as fractions."""
    scaled = [p.resize((width, max(1, round(p.height * width / p.width)))) for p in pages]
    total = sum(p.height for p in scaled)
    out = Image.new("RGB", (width, total), "white")
    layout, y = [], 0
    for p in scaled:
        out.paste(p, (0, y))
        layout.append((y / total, p.height / total))
        y += p.height
    return out, layout


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
    texts: list[str] = []
    if ctype == "application/pdf":
        pages, texts, meta = _render_pdf(data)
    elif ctype == DOCX:
        pages, texts, meta = _read_docx(data)
    elif ctype == TEXT:
        text = data.decode("utf-8-sig").strip()
        pages, texts, meta = [_render_text(text)], [text], {"pasted_text": {"chars": len(text)}}
    elif ctype == EML:
        pages, texts, meta = _read_eml(data, max_bytes)
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
        pages = [ImageOps.exif_transpose(img).convert("RGB")]
    first = pages[0]
    meta["width"], meta["height"] = first.size
    meta["pages"] = len(pages)
    if len(pages) > 1:
        preview, layout = _stack(pages)
    else:
        preview, layout = first, [(0.0, 1.0)]
    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return PreparedFile(
        content_type=ctype,
        sha256=sha,
        image=first,
        phash=str(imagehash.phash(first)),
        meta=meta,
        preview_png=buf.getvalue(),
        pages=pages,
        page_texts=texts or [""] * len(pages),
        page_layout=layout,
    )


def phash_distance(a: str, b: str) -> int:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


def phash_to_int(hex_hash: str) -> int:
    """64-bit perceptual hash as a signed BIGINT (same bits as Postgres ('x'||hex)::bit(64))."""
    n = int(hex_hash, 16)
    return n - (1 << 64) if n >= 1 << 63 else n
