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

    # guardrails: sign in as admin, add a recommended rule, switch AI off, then remove it
    page.get_by_role("button", name="Sign out").click()
    page.get_by_label("Email").fill("admin@cognify.dev")
    page.get_by_label("Role").select_option("admin")
    page.get_by_role("button", name="Sign in").click()
    # suspected fakes: the edited receipt and the reused screenshot are flagged for the admin
    page.get_by_role("link", name="Suspected fakes").click()
    expect(page.locator(".alert").first).to_be_visible()
    expect(page.locator(".alert", has_text="amount edit suspected")).to_be_visible()
    page.screenshot(path=f"{out}/6-suspected-fakes.png", full_page=True)
    edited = page.locator(".alert", has_text="amount edit suspected").first
    edited.get_by_placeholder("What did you check?").fill("bank shows 40.00; receipt altered")
    edited.get_by_role("button", name="Confirm fake & reject").click()
    expect(page.locator(".alert", has_text="amount edit suspected")).to_have_count(0)

    # alerts: the merchant phone's SMS became a credit; the OTP was ignored
    page.get_by_role("link", name="Alerts").click()
    expect(page.get_by_role("heading", name="Payment alert sources")).to_be_visible()
    expect(page.locator("tbody tr", has_text="credited").first).to_be_visible()
    expect(page.locator("tbody tr", has_text="ignored").first).to_be_visible()
    page.screenshot(path=f"{out}/7-alerts.png", full_page=True)

    # replay: run the active policy over past claims, and read reader / fraud accuracy
    page.get_by_role("link", name="Replay & accuracy").click()
    expect(page.get_by_role("heading", name="Readers vs confirmed payments")).to_be_visible()
    page.get_by_role("button", name="Replay active policy").click()
    expect(page.locator("p", has_text="claims:").first).to_be_visible()
    page.screenshot(path=f"{out}/8-replay.png", full_page=True)

    page.get_by_role("link", name="Guardrails & policies").click()
    expect(page.get_by_role("heading", name="Locked guardrails")).to_be_visible()
    kill = page.locator(".rule", has_text="AI kill switch").first
    kill.get_by_role("button", name="Add").click()
    expect(page.locator(".reader")).to_contain_text("AI reader: off")
    page.screenshot(path=f"{out}/5-guardrails.png", full_page=True)
    page.on("dialog", lambda d: d.accept("smoke test cleanup"))
    page.locator(".rule", has_text="AI kill switch").first.get_by_role("button", name="Remove").click()
    expect(page.locator(".reader")).to_contain_text("AI reader: on")
    browser.close()
print("smoke ok")
