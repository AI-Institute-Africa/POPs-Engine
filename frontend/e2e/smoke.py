"""Browser smoke test of the Validate UI against a seeded dev stack (scripts/seed_demo.py).

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

    # no login screen: the app silently picks up a dev session
    page.goto(f"{base}/")
    expect(page.get_by_role("heading", name="Verify a payment")).to_be_visible()
    expect(page.get_by_role("button", name="Take a photo")).to_be_visible()
    expect(page.get_by_role("button", name="Upload file").first).to_be_visible()
    page.screenshot(path=f"{out}/1-scan.png", full_page=True)

    # suspected fakes: the edited receipt and the reused screenshot are flagged for the admin
    page.get_by_role("link", name="Suspected fakes").click()
    expect(page.locator(".alert").first).to_be_visible()
    expect(page.locator(".alert", has_text="amount edit suspected")).to_be_visible()
    page.screenshot(path=f"{out}/3-suspected-fakes.png", full_page=True)
    edited = page.locator(".alert", has_text="amount edit suspected").first
    edited.get_by_placeholder("What did you check?").fill("bank shows 40.00; receipt altered")
    edited.get_by_role("button", name="Confirm fake & reject").click()
    expect(page.locator(".alert", has_text="amount edit suspected")).to_have_count(0)

    # alerts: the merchant phone's SMS became a credit; the OTP was ignored
    page.get_by_role("link", name="Alerts").click()
    expect(page.get_by_role("heading", name="Payment alert sources")).to_be_visible()
    expect(page.locator("tbody tr", has_text="credited").first).to_be_visible()
    expect(page.locator("tbody tr", has_text="ignored").first).to_be_visible()
    page.screenshot(path=f"{out}/4-alerts.png", full_page=True)

    # replay: run the active policy over past claims, and read reader / fraud accuracy
    page.get_by_role("link", name="Replay & accuracy").click()
    expect(page.get_by_role("heading", name="Readers vs confirmed payments")).to_be_visible()
    page.get_by_role("button", name="Replay active policy").click()
    expect(page.locator("p", has_text="claims:").first).to_be_visible()
    page.screenshot(path=f"{out}/5-replay.png", full_page=True)

    # guardrails page renders the locked set
    page.get_by_role("link", name="Guardrails").click()
    expect(page.get_by_role("heading", name="Locked guardrails")).to_be_visible()
    expect(page.locator("ul.locked li").first).to_be_visible()
    kill = page.locator(".rule", has_text="AI kill switch").first
    page.on("dialog", lambda d: d.accept("smoke test cleanup"))  # confirm switch-off / removal
    kill.get_by_role("button", name="Switch AI off").click()
    expect(page.locator(".reader")).to_contain_text("AI reader: off")
    page.screenshot(path=f"{out}/2-guardrails.png", full_page=True)
    page.locator(".rule", has_text="AI kill switch").first.get_by_role("button", name="Remove").click()
    expect(page.locator(".reader")).to_contain_text("AI reader: on")
    browser.close()
print("smoke ok")
