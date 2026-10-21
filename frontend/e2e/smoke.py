"""Browser smoke test of the review flow against a seeded dev stack (scripts/seed_demo.py).

    python e2e/smoke.py http://127.0.0.1:5173 ./shots
"""

import os
import sys

from playwright.sync_api import expect, sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173"
out = sys.argv[2] if len(sys.argv) > 2 else "shots"
os.makedirs(out, exist_ok=True)
exe = os.environ.get("CHROMIUM_PATH")  # e.g. a preinstalled browser; else Playwright default

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(f"{base}/login")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Review queue")).to_be_visible()
    expect(page.locator("tbody tr").first).to_be_visible()
    page.screenshot(path=f"{out}/1-queue.png", full_page=True)

    # open the financially confirmed claim and confirm it
    page.locator("tbody tr", has_text="FINANCIALLY CONFIRMED").first.locator("a").click()
    expect(page.get_by_role("heading", name="Candidate credits")).to_be_visible()
    page.screenshot(path=f"{out}/2-claim.png", full_page=True)
    txn = page.locator(".candidate.selected .row span").nth(1).inner_text().split("·")[-1].strip()
    page.get_by_label("Last four characters of transaction ID").fill(txn[-4:])
    page.get_by_role("button", name="Confirm credit 1").click()
    expect(page.locator(".flash")).to_contain_text("Confirmed")
    page.screenshot(path=f"{out}/3-approved.png", full_page=True)

    page.goto(f"{base}/credits")
    expect(page.get_by_role("heading", name="Unclaimed credits")).to_be_visible()
    page.screenshot(path=f"{out}/4-credits.png", full_page=True)
    browser.close()
print("smoke ok")
