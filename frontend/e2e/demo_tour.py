"""Browser test of the standalone demo: the guided tour, try-it-yourself, persona switching.

    python -m app.demo --no-browser --port 8000 &        # from backend/
    python e2e/demo_tour.py http://127.0.0.1:8000 ./shots

Each tour story must reach the outcome it promises; a console error fails the run.
"""

import os
import sys

from playwright.sync_api import expect, sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
out = sys.argv[2] if len(sys.argv) > 2 else "shots"
os.makedirs(out, exist_ok=True)
exe = os.environ.get("CHROMIUM_PATH")

# (story title in the tour, label the result must show, a phrase from the explanation)
TOUR = [
    ("Pay and get approved", "Approved", "Approved in seconds"),
    ("Receipt before the money", "Waiting for the money", "waits for the money"),
    ("Edited receipt", "Waiting for the money", "Caught"),
    ("Verify an ID in seconds", "Verified", "Verified in seconds"),
    ("Someone else's selfie", "Needs a person", "not the person on the ID"),
    ("Edited passport", "Needs a person", "printed expiry was edited"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1360, "height": 900})
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(f"{base}/?persona=admin")
    expect(page.get_by_role("heading", name="Know that a payment, or a person, is real. In seconds.")).to_be_visible()
    expect(page.locator(".demo-bar")).to_contain_text("synthetic")
    page.screenshot(path=f"{out}/1-home.png", full_page=True)

    page.get_by_role("button", name="Start the 3-minute tour").click()
    for i, (title, label, phrase) in enumerate(TOUR):
        step = page.get_by_role("button", name=title).first
        step.click()
        panel = page.locator(".scn", has_text=title).first
        panel.get_by_role("button", name="Run this story").click()
        outcome = panel.locator(".outcome")
        expect(outcome).to_be_visible(timeout=30000)
        expect(outcome).to_contain_text(label)
        expect(outcome).to_contain_text(phrase)
        expect(outcome.locator(".story li").first).to_be_visible()
        if i == 1:  # receipt before the money: deliver it, and it is approved
            panel.get_by_role("button", name="Simulate the bank statement arriving").click()
            expect(panel.locator(".outcome")).to_contain_text("The money arrived", timeout=30000)
            expect(panel.locator(".outcome")).to_contain_text("Approved")
        if i == 2:  # what the payer is told is neutral, never an accusation
            expect(outcome.locator(".bubble").first).to_be_visible()
            expect(outcome.locator(".bubble").first).not_to_contain_text("fake")
        page.screenshot(path=f"{out}/2-tour-{i + 1}.png", full_page=True)

    # step into the reviewer's seat and work an identity check
    page.get_by_role("button", name="Administrator").click()
    page.get_by_role("menuitem", name="Finance reviewer").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("a", has_text="ID checks").first).to_be_visible()
    page.get_by_role("link", name="ID checks").click()
    page.locator("tbody tr").first.click()
    expect(page.get_by_text("Why it needs a person")).to_be_visible()
    expect(page.locator(".compare img").first).to_be_visible(timeout=15000)
    page.screenshot(path=f"{out}/3-kyc-review.png", full_page=True)

    # the payer's seat sees only the payer pages
    page.get_by_role("button", name="Finance reviewer").click()
    page.get_by_role("menuitem", name="Payer").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("link", name="Review queue")).to_have_count(0)
    expect(page.get_by_role("link", name="Verify a payment")).to_be_visible()

    # try it yourself: a sample fills the form and the normal submit button approves it
    page.get_by_role("link", name="Verify a payment").click()
    page.get_by_role("button", name="Paid, bank has it").click()
    submit = page.get_by_role("button", name="Validate this payment")
    expect(submit).to_be_enabled(timeout=30000)
    submit.click()
    expect(page.locator("body")).to_contain_text("Approved", timeout=30000)
    page.screenshot(path=f"{out}/4-try-it.png", full_page=True)

    assert not errors, errors
    browser.close()
print("demo tour: ok")
