"""Synthetic but realistic-looking sample images for the demo.

- `receipt_png`: a mobile-money style receipt (a fictional layout, no real brand artwork).
- `id_card_png`: a fictional "Republic of Validia" identity card photographed on a desk.
- `selfie_png`: a drawn selfie; the same persona as an ID's portrait matches, others do not.

The images carry their "ground truth" in PNG text chunks, exactly like the test fixtures, because
the demo runs with simulated readers (see `app.extraction.readers.fake`). Everything is labelled
SAMPLE on the image itself so nobody can mistake it for a real document.
"""

import io
import json
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PIL.PngImagePlugin import PngInfo

# --- fonts ----------------------------------------------------------------------------------------

_FONT_FILES = (
    "DejaVuSans.ttf",
    "LiberationSans-Regular.ttf",
    "Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
_BOLD_FILES = (
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
    "Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
_MONO_FILES = (
    "DejaVuSansMono.ttf",
    "LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
)


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.ImageFont:
    for name in _MONO_FILES if mono else (_BOLD_FILES if bold else _FONT_FILES):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _png(img: Image.Image, **text: str) -> bytes:
    info = PngInfo()
    for k, v in text.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def _center(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, f, fill) -> None:
    d.text(xy, text, font=f, fill=fill, anchor="mm")


# --- receipts -------------------------------------------------------------------------------------

_BRANDS = {  # header colours by provider; plain text, no logos
    "ecocash": ((0, 122, 94), (0, 168, 120)),
    "mpesa": ((18, 120, 60), (40, 170, 90)),
    "bank": ((30, 64, 120), (60, 110, 190)),
}


def _qr_like(
    d: ImageDraw.ImageDraw, x: int, y: int, seed: str, cell: int = 10, n: int = 25
) -> None:
    """A QR-looking block seeded by the transaction id. It also makes every sample receipt
    perceptually distinct, so the duplicate detector only fires when we reuse a file on purpose."""
    rnd = random.Random(seed)
    d.rectangle([x - 8, y - 8, x + n * cell + 8, y + n * cell + 8], fill="white")
    for r in range(n):
        for c in range(n):
            finder = (r < 7 and c < 7) or (r < 7 and c >= n - 7) or (r >= n - 7 and c < 7)
            on = rnd.random() < 0.5
            if finder:
                rr, cc = r % (n - 7) if r >= n - 7 else r, c % (n - 7) if c >= n - 7 else c
                on = rr in (0, 6) or cc in (0, 6) or (2 <= rr <= 4 and 2 <= cc <= 4)
            if on:
                d.rectangle(
                    [x + c * cell, y + r * cell, x + (c + 1) * cell - 1, y + (r + 1) * cell - 1],
                    fill=(20, 24, 31),
                )


def _wash(img: Image.Image, seed: str) -> None:  # noqa: C901
    """Large soft shapes and bands, unique per receipt (like a promo background). Real receipts
    differ in many ways; this keeps every sample perceptually distinct, even when blurred, so the
    duplicate detector only fires when a file is reused on purpose. The perceptual hash looks at
    coarse structure, so the variation has to be coarse and high-contrast."""
    rnd = random.Random("wash:" + seed)
    W, H = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # coarse 4 x 8 grid of tinted cells over the whole page, random strength
    gw, gh = W // 4, H // 8
    for gx in range(4):
        for gy in range(8):
            v = rnd.randrange(0, 256)
            ld.rectangle(
                [gx * gw, gy * gh, (gx + 1) * gw, (gy + 1) * gh],
                fill=(v, rnd.randrange(60, 230), 255 - v, rnd.randrange(90, 200)),
            )
    for _ in range(4):
        r = rnd.randrange(140, 320)
        x, y = rnd.randrange(0, W), rnd.randrange(0, H)
        v = rnd.randrange(0, 256)
        ld.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=(v, 255 - v, rnd.randrange(256), rnd.randrange(90, 190)),
        )
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"))


def receipt_png(
    fields: dict, *, brand: str = "ecocash", tilt: float = 0.0, blur: float = 0.0, salt: int = 0
) -> bytes:
    """A phone-screenshot style payment receipt for `fields` (see `devtools.ecocash_fields`)."""
    W, H = 720, 1280
    top, bottom = _BRANDS.get(brand, _BRANDS["ecocash"])
    img = Image.new("RGB", (W, H), (244, 246, 248))
    _wash(img, f"{fields.get('transaction_id', '')}:{salt}")
    d = ImageDraw.Draw(img)
    head = Image.new("RGB", (W, 300))
    hd = ImageDraw.Draw(head)
    for y in range(300):  # header gradient
        t = y / 300
        hd.line(
            [(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        )
    img.paste(Image.blend(img.crop((0, 0, W, 300)), head, 0.72), (0, 0))
    d = ImageDraw.Draw(img)
    d.text(
        (40, 36),
        str(fields.get("provider") or "Mobile money"),
        font=font(34, bold=True),
        fill="white",
    )
    d.text((W - 40, 44), "Receipt", font=font(26), fill=(220, 245, 235), anchor="ra")
    # status badge
    ok = str(fields.get("status", "")).lower() in ("successful", "success", "completed")
    cx, cy = W // 2, 190
    d.ellipse([cx - 46, cy - 46, cx + 46, cy + 46], fill="white")
    if ok:
        d.line(
            [(cx - 20, cy + 2), (cx - 5, cy + 18), (cx + 24, cy - 16)],
            fill=top,
            width=9,
            joint="curve",
        )
    else:
        d.text((cx, cy), "…", font=font(60, bold=True), fill=(190, 130, 20), anchor="mm")
    _center(
        d,
        (W // 2, 262),
        "Payment " + ("successful" if ok else str(fields.get("status") or "received")),
        font(30, bold=True),
        "white",
    )
    # card
    d.rounded_rectangle(
        [30, 330, W - 30, 880], radius=26, fill="white", outline=(226, 232, 240), width=2
    )
    amount = f"{fields.get('currency', 'USD')} {fields.get('amount', '')}"
    _center(d, (W // 2, 410), amount, font(64, bold=True), (15, 23, 42))
    rows = [
        ("Transaction ID", fields.get("transaction_id")),
        ("Date", fields.get("date")),
        ("From", fields.get("sender")),
        ("To", fields.get("beneficiary")),
        ("Status", fields.get("status")),
    ]
    y = 480
    for label, value in rows:
        d.line([(60, y - 14), (W - 60, y - 14)], fill=(238, 242, 247), width=2)
        d.text((60, y), label, font=font(24), fill=(100, 116, 139))
        d.text(
            (W - 60, y), str(value or "—"), font=font(26, bold=True), fill=(15, 23, 42), anchor="ra"
        )
        y += 76
    _qr_like(d, (W - 250) // 2, 905, f"{fields.get('transaction_id', '')}:{salt}")
    d.text(
        (W // 2, 1200),
        "SAMPLE RECEIPT · generated for the Validate demo",
        font=font(20),
        fill=(148, 163, 184),
        anchor="mm",
    )
    d.text((W // 2, 1232), "Not a real payment", font=font(18), fill=(148, 163, 184), anchor="mm")
    if tilt:
        img = img.rotate(tilt, resample=Image.BICUBIC, fillcolor=(244, 246, 248))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    payload = json.dumps(fields)
    return _png(img, **{"pops-vlm": payload, "pops-ocr": payload})


# --- people ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Persona:
    key: str
    name: str  # as printed: GIVEN NAMES, SURNAME
    given: str
    surname: str
    sex: str
    dob: str  # DD/MM/YYYY
    skin: tuple[int, int, int]
    hair: tuple[int, int, int]
    style: str  # short | long | bald | afro | bun | cap
    bg: tuple[int, int, int]
    shirt: tuple[int, int, int]
    glasses: bool = False

    @property
    def full(self) -> str:
        return f"{self.given} {self.surname}".upper()


PERSONAS = {
    p.key: p
    for p in (
        Persona(
            "tendai",
            "Tendai Moyo",
            "Tendai Grace",
            "Moyo",
            "F",
            "14/03/1995",
            (141, 85, 54),
            (28, 22, 20),
            "long",
            (176, 206, 232),
            (212, 74, 92),
        ),
        Persona(
            "farai",
            "Farai Ncube",
            "Farai",
            "Ncube",
            "M",
            "02/11/1990",
            (96, 58, 38),
            (18, 16, 16),
            "short",
            (236, 205, 150),
            (38, 70, 120),
            True,
        ),
        Persona(
            "amina",
            "Amina Okoye",
            "Amina",
            "Okoye",
            "F",
            "27/07/1988",
            (112, 70, 44),
            (22, 18, 18),
            "afro",
            (200, 226, 190),
            (240, 190, 60),
        ),
        Persona(
            "liam",
            "Liam Dube",
            "Liam Peter",
            "Dube",
            "M",
            "09/01/2000",
            (232, 190, 160),
            (196, 146, 70),
            "short",
            (230, 196, 214),
            (60, 110, 80),
        ),
        Persona(
            "naledi",
            "Naledi Khumalo",
            "Naledi",
            "Khumalo",
            "F",
            "30/05/1993",
            (196, 132, 92),
            (120, 58, 28),
            "bun",
            (92, 142, 152),
            (130, 60, 150),
        ),
        Persona(
            "kuda",
            "Kuda Sibanda",
            "Kuda",
            "Sibanda",
            "M",
            "18/09/1985",
            (168, 112, 78),
            (24, 20, 20),
            "bald",
            (66, 84, 118),
            (226, 132, 48),
        ),
    )
}


def draw_avatar(
    img: Image.Image,
    box: tuple[int, int, int, int],
    p: Persona,
    *,
    pose: float = 0.0,
    seed: int = 0,
) -> None:
    """Draw `p` into `box` of `img`. `pose` (0..1) shifts and rescales the head and changes the
    light, as a different photo of the same person would. Drawn at 3x and shrunk for smooth edges."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    S = 3
    rnd = random.Random(seed)
    dx, dy = (rnd.uniform(-1, 1) * 0.022 * pose, rnd.uniform(-1, 1) * 0.016 * pose)
    scale = 1 + rnd.uniform(-1, 1) * 0.03 * pose
    layer = Image.new("RGB", (w * S, h * S), p.bg)
    d = ImageDraw.Draw(layer)
    W, H = w * S, h * S
    cx, cy = W * (0.5 + dx), H * (0.44 + dy)
    rx, ry = W * 0.23 * scale, H * 0.28 * scale
    shirt = p.shirt
    d.ellipse([cx - W * 0.46, H * 0.80, cx + W * 0.46, H * 1.35], fill=shirt)  # shoulders
    d.rectangle(
        [cx - rx * 0.38, cy + ry * 0.7, cx + rx * 0.38, H * 0.86],
        fill=tuple(int(c * 0.86) for c in p.skin),
    )
    hair = p.hair
    if p.style == "long":
        d.rounded_rectangle(
            [cx - rx * 1.16, cy - ry * 0.95, cx + rx * 1.16, cy + ry * 1.05],
            radius=int(rx * 0.7),
            fill=hair,
        )
    elif p.style == "afro":
        d.ellipse([cx - rx * 1.55, cy - ry * 1.45, cx + rx * 1.55, cy + ry * 0.85], fill=hair)
    elif p.style == "bun":
        d.ellipse([cx - rx * 0.4, cy - ry * 1.5, cx + rx * 0.4, cy - ry * 0.85], fill=hair)
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=p.skin)  # face
    if p.style in ("short", "bun", "long"):
        d.pieslice(
            [cx - rx * 1.06, cy - ry * 1.12, cx + rx * 1.06, cy + ry * 0.55], 180, 360, fill=hair
        )
    ey = cy - ry * 0.08
    for sx in (-1, 1):
        ex = cx + sx * rx * 0.42
        d.ellipse([ex - rx * 0.17, ey - ry * 0.07, ex + rx * 0.17, ey + ry * 0.09], fill="white")
        d.ellipse(
            [ex - rx * 0.07, ey - ry * 0.06, ex + rx * 0.07, ey + ry * 0.08], fill=(30, 24, 22)
        )
        d.line(
            [(ex - rx * 0.2, ey - ry * 0.2), (ex + rx * 0.2, ey - ry * 0.22)],
            fill=hair,
            width=max(2, int(W * 0.012)),
        )
        if p.glasses:
            d.ellipse(
                [ex - rx * 0.27, ey - ry * 0.17, ex + rx * 0.27, ey + ry * 0.2],
                outline=(30, 30, 34),
                width=max(2, int(W * 0.008)),
            )
    if p.glasses:
        d.line(
            [(cx - rx * 0.15, ey), (cx + rx * 0.15, ey)],
            fill=(30, 30, 34),
            width=max(2, int(W * 0.008)),
        )
    d.line(
        [(cx, ey + ry * 0.05), (cx - rx * 0.07, cy + ry * 0.32)],
        fill=tuple(int(c * 0.78) for c in p.skin),
        width=max(2, int(W * 0.01)),
    )
    d.arc(
        [cx - rx * 0.32, cy + ry * 0.38, cx + rx * 0.32, cy + ry * 0.72],
        20,
        160,
        fill=(120, 50, 50),
        width=max(3, int(W * 0.012)),
    )
    if pose:  # a little directional light and sensor noise
        arr = np.asarray(layer).astype(np.float32)
        grad = np.linspace(1 - 0.05 * pose, 1 + 0.05 * pose, W)[None, :, None]
        arr = arr * (grad if rnd.random() < 0.5 else grad[:, ::-1])
        arr += np.random.RandomState(seed).normal(0, 3 * pose, arr.shape)
        layer = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    img.paste(layer.resize((w, h), Image.LANCZOS), (x0, y0))


def selfie_png(persona: str | None, *, seed: int = 1, size: int = 720) -> bytes:
    """A selfie of `persona` (None = a photo with no face in it: a plant on a shelf)."""
    img = Image.new("RGB", (size, size), (200, 205, 210))
    if persona is None:
        d = ImageDraw.Draw(img)
        for y in range(size):  # wall + shelf
            d.line([(0, y), (size, y)], fill=(214 - y // 14, 218 - y // 16, 222 - y // 18))
        d.rectangle([0, int(size * 0.74), size, int(size * 0.78)], fill=(120, 96, 72))
        d.ellipse([size * 0.38, size * 0.5, size * 0.62, size * 0.74], fill=(70, 130, 80))
        d.rectangle([size * 0.44, size * 0.66, size * 0.56, size * 0.74], fill=(176, 100, 70))
    else:
        draw_avatar(img, (0, 0, size, size), PERSONAS[persona], pose=1.0, seed=seed)
    return _png(img)


# --- identity card --------------------------------------------------------------------------------

COUNTRY = "VDA"  # fictional: Republic of Validia
PORTRAIT = (0.05, 0.26, 0.30, 0.55)  # x, y, w, h on the card; shared with the demo face matcher
CARD = (1000, 630)


def _card_face(
    fields: dict, persona: Persona, *, blank_face: bool, issued: bool = True
) -> Image.Image:
    W, H = CARD
    card = Image.new("RGB", CARD, (232, 240, 236))
    d = ImageDraw.Draw(card)
    for y in range(H):  # soft gradient
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(214 + 24 * t), int(232 + 8 * t), int(226 + 4 * t)))
    for i in range(34):  # guilloche-like wave lines
        pts = [(x, H * 0.55 + 70 * np.sin(x / 70 + i / 3) + i * 4 - 60) for x in range(0, W, 8)]
        d.line(pts, fill=(196, 218, 208), width=2)
    d.rectangle([0, 0, W, 92], fill=(22, 74, 99))
    d.text((34, 24), "REPUBLIC OF VALIDIA", font=font(34, bold=True), fill="white")
    d.text((W - 34, 30), "NATIONAL IDENTITY CARD", font=font(22), fill=(190, 225, 235), anchor="ra")
    px, py, pw, ph = PORTRAIT
    box = (int(px * W), int(py * H), int((px + pw) * W), int((py + ph) * H))
    if blank_face:
        d.rectangle(box, fill=(226, 232, 230), outline=(160, 175, 170), width=3)
        d.text(
            ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2),
            "NO PHOTO",
            font=font(24),
            fill=(130, 145, 140),
            anchor="mm",
        )
    else:
        draw_avatar(card, box, persona)
        d.rectangle(box, outline=(120, 140, 135), width=3)
    labels = [
        ("SURNAME", persona.surname.upper()),
        ("GIVEN NAMES", persona.given.upper()),
        ("ID NO", str(fields.get("document_number", ""))),
        ("DATE OF BIRTH", str(fields.get("date_of_birth", ""))),
        ("DATE OF EXPIRY", str(fields.get("expiry_date", ""))),
        ("SEX", str(fields.get("sex", ""))),
    ]
    x, y = 392, 118
    for i, (lab, val) in enumerate(labels):
        col = i % 2
        row = i // 2
        lx, ly = x + col * 300, y + row * 120
        if i < 2:
            lx, ly = x, y + i * 84
            d.text((lx, ly), lab, font=font(17), fill=(90, 110, 118))
            d.text((lx, ly + 22), val, font=font(34, bold=True), fill=(15, 30, 38))
        else:
            k = i - 2
            lx, ly = x + (k % 2) * 300, y + 2 * 84 + 6 + (k // 2) * 92
            d.text((lx, ly), lab, font=font(17), fill=(90, 110, 118))
            d.text((lx, ly + 22), val, font=font(28, bold=True), fill=(15, 30, 38))
    d.text(
        (34, H - 38),
        "SAMPLE · NOT A REAL DOCUMENT · Validate demo",
        font=font(19),
        fill=(120, 40, 40),
    )
    d.text(
        (W - 34, H - 38),
        f"VDA {fields.get('document_number', '')}",
        font=font(19, mono=True),
        fill=(90, 110, 118),
        anchor="ra",
    )
    return card


def id_card_png(
    fields: dict, persona_key: str, *, blank_face: bool = False, desk: bool = True, seed: int = 3
) -> bytes:
    """A fictional ID, by default photographed at a slight angle on a desk. `fields` are the values
    the simulated readers 'read' (doc_type, issuing_country, full_name, document_number, ...)."""
    persona = PERSONAS[persona_key]
    card = _card_face(fields, persona, blank_face=blank_face)
    payload = json.dumps(fields)
    if not desk:
        return _png(card, **{"pops-id-vlm": payload, "pops-id-ocr": payload})
    rnd = random.Random(seed)
    W, H = 1500, 1080
    scene = Image.new("RGB", (W, H), (52, 46, 42))
    d = ImageDraw.Draw(scene)
    for y in range(H):  # wood-ish desk
        d.line([(0, y), (W, y)], fill=(70 + y // 22, 58 + y // 26, 46 + y // 30))
    for _ in range(120):
        y = rnd.randrange(H)
        d.line(
            [(0, y), (W, y + rnd.randrange(-6, 6))],
            fill=(58 + rnd.randrange(14), 48 + rnd.randrange(10), 38),
            width=1,
        )
    cw, ch = CARD
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    dst = np.float32([[250, 190], [1230, 235], [1200, 840], [215, 810]])
    import cv2

    M = cv2.getPerspectiveTransform(src, dst)
    shadow = cv2.warpPerspective(np.full((ch, cw), 255, np.uint8), M, (W, H))
    shadow = cv2.GaussianBlur(np.roll(shadow, (14, 18), axis=(0, 1)), (0, 0), 14)
    base = np.asarray(scene).astype(np.float32)
    base *= 1 - 0.45 * shadow[..., None] / 255.0
    warped = cv2.warpPerspective(np.asarray(card), M, (W, H), flags=cv2.INTER_CUBIC)
    mask = cv2.warpPerspective(np.full((ch, cw), 255, np.uint8), M, (W, H))
    out = np.where(mask[..., None] > 0, warped.astype(np.float32), base)
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(0.6)
    )
    return _png(img, **{"pops-id-vlm": payload, "pops-id-ocr": payload})


def id_fields(persona_key: str, number: str, *, expiry: str = "30/06/2032") -> dict:
    p = PERSONAS[persona_key]
    return {
        "doc_type": "national_id",
        "issuing_country": COUNTRY,
        "full_name": p.full,
        "document_number": number,
        "date_of_birth": p.dob,
        "expiry_date": expiry,
        "sex": p.sex,
    }


# --- passport -------------------------------------------------------------------------------------

PASSPORT = (1000, 704)


def passport_png(
    persona_key: str,
    number: str,
    *,
    expiry: str = "30/06/2032",
    mrz_expiry: str | None = None,
    desk: bool = True,
) -> bytes:
    """A fictional passport data page with a machine-readable zone (two 44-character lines with
    check digits). `mrz_expiry` lets a sample print one expiry date and encode another, which is
    what an edited passport looks like: the printed text no longer matches the checked zone."""
    from datetime import date, datetime

    from app.kyc.doc import mrz as mrz_mod

    p = PERSONAS[persona_key]
    W, H = PASSPORT
    page = Image.new("RGB", PASSPORT, (238, 232, 218))
    d = ImageDraw.Draw(page)
    for i in range(30):
        pts = [(x, 230 + 40 * np.sin(x / 60 + i / 2) + i * 8) for x in range(0, W, 8)]
        d.line(pts, fill=(222, 214, 196), width=2)
    d.rectangle([0, 0, W, 78], fill=(98, 36, 52))
    d.text((34, 20), "REPUBLIC OF VALIDIA", font=font(32, bold=True), fill="white")
    d.text((W - 34, 28), "PASSPORT / P", font=font(22), fill=(240, 210, 215), anchor="ra")
    px, py, pw, ph = PORTRAIT
    box = (int(px * W), int(py * H), int((px + pw) * W), int((py + ph) * H))
    draw_avatar(page, box, p)
    d.rectangle(box, outline=(120, 110, 96), width=3)
    exp_d = datetime.strptime(mrz_expiry or expiry, "%d/%m/%Y").date()
    dob_d = datetime.strptime(p.dob, "%d/%m/%Y").date()
    lines = mrz_mod.compose_td3(
        country=COUNTRY,
        surname=p.surname,
        given=p.given,
        number=number.replace("-", ""),
        nationality=COUNTRY,
        dob=dob_d,
        sex=p.sex,
        expiry=exp_d if isinstance(exp_d, date) else exp_d,
    )
    fields = [
        ("SURNAME", p.surname.upper()),
        ("GIVEN NAMES", p.given.upper()),
        ("PASSPORT NO", number),
        ("DATE OF BIRTH", p.dob),
        ("DATE OF EXPIRY", expiry),
        ("SEX", p.sex),
    ]
    x, y = 392, 104
    for i, (lab, val) in enumerate(fields):
        if i < 3:
            ly = y + i * 78
            d.text((x, ly), lab, font=font(16), fill=(110, 100, 86))
            d.text((x, ly + 20), val, font=font(30, bold=True), fill=(30, 26, 22))
        else:
            k = i - 3
            ly = y + 3 * 78 + 4
            lx = x + k * 200
            d.text((lx, ly), lab, font=font(16), fill=(110, 100, 86))
            d.text((lx, ly + 20), val, font=font(24, bold=True), fill=(30, 26, 22))
    d.rectangle([0, H - 118, W, H], fill=(250, 248, 240))
    for i, ln in enumerate(lines):
        d.text((24, H - 104 + i * 42), ln, font=font(30, mono=True), fill=(20, 20, 20))
    d.text(
        (392, H - 150),
        "SAMPLE · NOT A REAL DOCUMENT · Validate demo",
        font=font(17),
        fill=(120, 40, 40),
    )
    payload = {
        "doc_type": "passport",
        "issuing_country": COUNTRY,
        "full_name": p.full,
        "document_number": number.replace("-", ""),
        "date_of_birth": p.dob,
        "expiry_date": expiry,
        "sex": p.sex,
    }
    chunks = {
        "pops-id-vlm": json.dumps(payload),
        "pops-id-ocr": json.dumps({**payload, "mrz": "\n".join(lines)}),
    }
    if not desk:
        return _png(page, **chunks)
    import cv2

    rnd = random.Random(11)
    SW, SH = 1500, 1160
    scene = Image.new("RGB", (SW, SH), (44, 52, 60))
    sd = ImageDraw.Draw(scene)
    for y2 in range(SH):
        sd.line([(0, y2), (SW, y2)], fill=(52 + y2 // 30, 60 + y2 // 30, 68 + y2 // 32))
    for _ in range(80):
        y2 = rnd.randrange(SH)
        sd.line(
            [(0, y2), (SW, y2 + rnd.randrange(-4, 4))],
            fill=(48 + rnd.randrange(12), 56, 64),
            width=1,
        )
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[230, 170], [1260, 150], [1285, 920], [250, 950]])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(np.asarray(page), M, (SW, SH), flags=cv2.INTER_CUBIC)
    mask = cv2.warpPerspective(np.full((H, W), 255, np.uint8), M, (SW, SH))
    shadow = cv2.GaussianBlur(np.roll(mask, (14, 18), axis=(0, 1)), (0, 0), 14)
    base = np.asarray(scene).astype(np.float32) * (1 - 0.45 * shadow[..., None] / 255.0)
    out = np.where(mask[..., None] > 0, warped.astype(np.float32), base)
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(0.6)
    )
    return _png(img, **chunks)


def phash_int(png: bytes) -> int:
    """The engine's perceptual hash of an image, as the signed 64-bit integer it stores."""
    import imagehash

    from app.intake.files import phash_to_int

    return phash_to_int(str(imagehash.phash(Image.open(io.BytesIO(png)).convert("RGB"))))


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def distinct_receipt(fields: dict, known: list[int], *, min_distance: int = 10, **kw) -> bytes:
    """A receipt whose perceptual hash is at least `min_distance` bits from every hash in `known`
    (what is already stored), re-rendering with a new salt until it is. The engine treats near
    identical files as duplicates, which is right, so a demo must not create them by accident."""
    png = b""
    for salt in range(40):
        png = receipt_png(fields, salt=salt, **kw)
        h = phash_int(png)
        if all(hamming(h, k) >= min_distance for k in known):
            return png
    return png
