"""Generate a clearly synthetic Zimbabwean proof-of-payment sample dataset."""

import csv
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont


OUTPUT_DIR = Path(__file__).resolve().parents[1] / "sample_data" / "mock_pops"
SCREENSHOT_TEXT_DIR = OUTPUT_DIR / "screenshots" / "text"
SCREENSHOT_IMAGE_DIR = OUTPUT_DIR / "screenshots" / "images"
SMS_TEXT_DIR = OUTPUT_DIR / "sms" / "text"
SMS_IMAGE_DIR = OUTPUT_DIR / "sms" / "images"
COMPLEX_TEXT_DIR = OUTPUT_DIR / "complex" / "text"
COMPLEX_IMAGE_DIR = OUTPUT_DIR / "complex" / "images"
SCREENSHOT_COUNT = 200
SMS_COUNT = 100
COMPLEX_COUNT = 200

BANKS = ["CBZ Bank", "NMB Bank", "Stanbic Bank", "CABS", "FBC Bank", "Steward Bank", "ZB Bank", "First Capital Bank", "EcoCash"]
SURNAMES = ["Moyo", "Sibanda", "Maphosa", "Ndlovu", "Chuma", "Dube", "Ncube", "Shumba", "Nyoni", "Phiri", "Mutasa", "Gumbo", "Zhou", "Makoni"]
FIRST_NAMES = ["Tendai", "Farai", "Blessing", "Chipo", "Tinashe", "Rufaro", "Kudakwashe", "Tariro", "Nqobile", "Thabo", "Memory", "Anesu", "Tafadzwa"]
COMPANIES = ["Econet Wireless", "ZETDC", "NetOne", "ZOL Zimbabwe", "Liquid Intelligent Technologies", "Delta Corporation", "Choppies Supermarket", "OK Zimbabwe"]


def digits(length: int) -> str:
    return "".join(random.choices(string.digits, k=length))


def random_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(SURNAMES)}"


def transaction_reference(bank: str) -> str:
    if bank == "EcoCash":
        return "PP" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    if bank == "CBZ Bank":
        return "CBZ" + digits(10)
    if bank == "NMB Bank":
        return "NMB-TXN-" + digits(8)
    return "FT" + digits(12)


def make_receipt(index: int, category: str) -> tuple[str, dict[str, str]]:
    is_sms = category == "sms"
    bank = "EcoCash" if is_sms else random.choice(BANKS[:-1])
    sender = random_name()
    is_person = random.random() > 0.4
    beneficiary = random_name() if is_person else random.choice(COMPANIES)
    beneficiary_account = digits(12 if is_person else 7)
    sender_account = digits(12)
    currency = random.choice(["USD", "ZWG"])
    amount = f"{random.uniform(5, 1500):.2f}"
    date = (datetime.now() - timedelta(days=random.randint(1, 300))).replace(
        hour=random.randint(7, 21), minute=random.randint(0, 59), second=random.randint(0, 59)
    ).strftime("%Y-%m-%d %H:%M:%S")
    reference = transaction_reference(bank)
    status = "SUCCESSFUL" if random.random() > 0.05 else "PENDING"

    if is_sms:
        body = [
            f"{date}  EcoCash",
            f"Wallet payment {status.lower()}. {currency} {amount} sent to {beneficiary}.",
            f"Reference: {reference}. Available balance: {currency} {random.uniform(10, 2500):.2f}.",
            "This is a synthetic sample message, not a real transaction.",
        ]
    else:
        body = [
            "========================================",
            f"{bank}  •  Payment receipt",
            f"{status}  |  {currency} {amount}",
            f"Paid to: {beneficiary}",
            f"Amount Transferred: {currency} {amount}",
            f"Account: {beneficiary_account}",
            f"From: {sender}  ({sender_account[:4]}••••{sender_account[-4:]})",
            f"Reference: {reference}",
            f"Date: {date}",
            "SYNTHETIC SAMPLE - NOT A REAL PAYMENT",
        ]

    return "\n".join(body), {
        "id": f"{category}_{index:03d}", "category": category, "bank": bank, "reference": reference,
        "status": status, "date_time": date, "currency": currency,
        "amount": amount,
        "text_file": f"{category}/text/{category}_{index:03d}.txt",
        "image_file": f"{category}/images/{category}_{index:03d}.jpg",
        "synthetic": "true",
    }


def render_receipt(content: str, path: Path, category: str) -> None:
    font = ImageFont.load_default()
    width, height = 460, 900
    image = Image.new("RGB", (width, height), "#F4F6F8")
    draw = ImageDraw.Draw(image)
    if category == "sms":
        draw.rectangle((0, 0, width, 54), fill="#E9EDF0")
        draw.text((22, 20), "9:41                                      ●●●  4G  ▰", fill="#202124", font=font)
        draw.rectangle((0, 54, width, 118), fill="#FFFFFF")
        draw.text((22, 80), "‹     EcoCash                         ⋮", fill="#202124", font=font)
        draw.text((130, 145), "Today", fill="#687078", font=font)
        draw.rounded_rectangle((30, 180, 355, 335), radius=18, fill="#E4E7EB")
        y = 200
        for line in content.splitlines():
            # Wrap message text to fit a typical mobile SMS bubble.
            words = line.split()
            wrapped, current = [], ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if len(candidate) > 42:
                    wrapped.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                wrapped.append(current)
            for text_line in wrapped:
                draw.text((48, y), text_line, fill="#202124", font=font)
                y += 24
            y += 8
        draw.rounded_rectangle((20, 820, 440, 870), radius=22, outline="#C8CDD2", width=2)
        draw.text((38, 840), "Text message", fill="#737980", font=font)
    else:
        draw.rectangle((0, 0, width, 54), fill="#173D6A")
        draw.text((22, 20), "9:41                                      ●●●  4G  ▰", fill="#FFFFFF", font=font)
        draw.rectangle((0, 54, width, 122), fill="#1E5A91")
        draw.text((24, 84), "‹      PAYMENT DETAILS", fill="#FFFFFF", font=font)
        draw.rounded_rectangle((22, 150, width - 22, 790), radius=14, fill="#FFFFFF", outline="#D9DEE4", width=2)
        draw.ellipse((190, 178, 270, 258), fill="#16865B")
        draw.text((220, 211), "✓", fill="#FFFFFF", font=font)
        draw.text((164, 280), "Payment complete", fill="#183247", font=font)
        draw.text((35, 340), "SYNTHETIC SAMPLE  •  NOT A REAL PAYMENT", fill="#B3261E", font=font)
        y = 395
        for line in content.splitlines():
            draw.line((36, y - 12, width - 36, y - 12), fill="#E7EAEE", width=1)
            draw.text((38, y), line, fill="#273746", font=font)
            y += 44
        draw.text((22, 842), "‹ Back                     Home                     Menu", fill="#5B6670", font=font)
    image.save(path, "JPEG", quality=92)


def render_complex_receipt(content: str, path: Path, index: int) -> None:
    """Render a varied, noisy training image with a prominent synthetic label."""
    font = ImageFont.load_default()
    width = random.choice([720, 760, 820])
    height = random.choice([960, 1040, 1120])
    background = random.choice(["#E5E7E9", "#E8E3D9", "#DDE3E5", "#F0EDE6"])
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    left = random.choice([28, 40, 54])
    right = width - left
    top = random.choice([34, 46, 58])
    bottom = height - random.choice([34, 46, 58])
    paper = random.choice(["#FFFFFF", "#FCFBF7", "#F8F9FA"])
    draw.rounded_rectangle((left + 6, top + 8, right + 6, bottom + 8), radius=8, fill="#B9BEC2")
    draw.rectangle((left, top, right, bottom), fill=paper, outline="#C7CDD1", width=2)

    bank = next((line.split(" PROOF")[0] for line in content.splitlines() if " PROOF" in line), "BANK PAYMENT")
    palette = [("#173D6A", "#E7EFF8"), ("#17614A", "#E7F2EC"), ("#5A3A73", "#F0EAF5"), ("#8A4B19", "#F8EEE3")]
    accent, pale = palette[index % len(palette)]
    layout = index % 4
    header_h = 120 if layout in (0, 2) else 95
    draw.rectangle((left, top, right, top + header_h), fill=accent)
    draw.text((left + 22, top + 20), bank[:34], fill="#FFFFFF", font=font)
    draw.text((left + 22, top + 48), random.choice(["TRANSFER CONFIRMATION", "PAYMENT ADVICE", "TRANSACTION RECEIPT"]), fill="#FFFFFF", font=font)
    draw.text((left + 22, top + 76), "SYNTHETIC TRAINING SAMPLE", fill="#FFE38A", font=font)

    y = top + header_h + 24
    lines = content.splitlines()
    # Prominent amount panel; the exact value is also present in the text label.
    amount_line = next((line.split(":", 1)[-1].strip() for line in lines if "Amount Transferred" in line), "USD 0.00")
    draw.rounded_rectangle((left + 18, y, right - 18, y + 90), radius=8, fill=pale, outline="#D6DCE0", width=1)
    draw.text((left + 34, y + 16), "AMOUNT", fill="#5B6670", font=font)
    draw.text((left + 34, y + 48), amount_line[:42], fill=accent, font=font)
    y += 116

    # Use different row treatments to create layout variety while preserving readable fields.
    detail_lines = [line for line in lines if ":" in line]
    for row, line in enumerate(detail_lines):
        label, value = line.split(":", 1)
        row_h = 56 if layout in (0, 1) else 64
        if layout == 1 and row % 2 == 0:
            draw.rectangle((left + 16, y - 8, right - 16, y + row_h - 4), fill="#F1F4F6")
        draw.text((left + 26, y), label.strip()[:28], fill="#56616B", font=font)
        draw.text((left + 250, y), value.strip()[:44], fill="#17232D", font=font)
        y += row_h
        if y > bottom - 150:
            break
    y = min(y + 8, bottom - 100)
    draw.line((left + 20, y, right - 20, y), fill="#C8CED3", width=2)
    draw.text((left + 24, y + 18), "NOT A REAL PAYMENT  •  FOR OCR TRAINING ONLY", fill="#A32925", font=font)
    draw.text((left + 24, bottom - 46), f"SAMPLE {index:03d}  |  NO BANKING VALUE", fill="#707980", font=font)

    # Add restrained scan/camera variation. Keep the synthetic warning legible.
    if index % 3 == 0:
        image = image.rotate(random.uniform(-1.3, 1.3), resample=Image.Resampling.BICUBIC, expand=False, fillcolor=background)
    if index % 5 == 0:
        pixels = image.load()
        for _ in range(width * height // 180):
            x, y_noise = random.randrange(width), random.randrange(height)
            shade = random.choice(["#E1E1DF", "#F7F6F1", "#D7D9D7"])
            pixels[x, y_noise] = ImageColor.getrgb(shade)
    image.save(path, "JPEG", quality=random.choice([82, 88, 93]))


def main() -> None:
    random.seed(20261022)
    for directory in (SCREENSHOT_TEXT_DIR, SCREENSHOT_IMAGE_DIR, SMS_TEXT_DIR, SMS_IMAGE_DIR, COMPLEX_TEXT_DIR, COMPLEX_IMAGE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    manifest_path = OUTPUT_DIR / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.DictWriter(manifest_file, fieldnames=[
            "id", "category", "bank", "reference", "status", "date_time", "currency",
            "amount", "text_file", "image_file", "synthetic",
        ])
        writer.writeheader()
        for category, count, text_dir, image_dir in (
            ("screenshots", SCREENSHOT_COUNT, SCREENSHOT_TEXT_DIR, SCREENSHOT_IMAGE_DIR),
            ("sms", SMS_COUNT, SMS_TEXT_DIR, SMS_IMAGE_DIR),
            ("complex", COMPLEX_COUNT, COMPLEX_TEXT_DIR, COMPLEX_IMAGE_DIR),
        ):
            for index in range(1, count + 1):
                content, metadata = make_receipt(index, category)
                (text_dir / f"{category}_{index:03d}.txt").write_text(content + "\n", encoding="utf-8")
                image_path = image_dir / f"{category}_{index:03d}.jpg"
                if category == "complex":
                    render_complex_receipt(content, image_path, index)
                else:
                    render_receipt(content, image_path, category)
                writer.writerow(metadata)
    (OUTPUT_DIR / "README.txt").write_text(
        "Synthetic sample dataset for parser/OCR development.\n"
        "All transactions and account details are randomly generated and are not real.\n"
        "200 app-style payment screenshots: screenshots/text and screenshots/images.\n"
        "100 mobile-wallet SMS examples: sms/text and sms/images.\n"
        "200 varied, scan-style complex receipt images: complex/text and complex/images.\n"
        "manifest.csv maps each record to its text and image files.\n",
        encoding="utf-8",
    )
    print(f"Generated {SCREENSHOT_COUNT} screenshots and {SMS_COUNT} SMS examples in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
