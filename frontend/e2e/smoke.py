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

    # review queue: open a claim whose money has matched, type the last four characters, confirm
    page.get_by_role("link", name="Review queue").click()
    expect(page.get_by_role("heading", name="Review queue")).to_be_visible()
    matched = page.locator("tbody tr", has_text="FINANCIALLY CONFIRMED").first
    expect(matched).to_be_visible()
    page.screenshot(path=f"{out}/6-queue.png", full_page=True)
    matched.get_by_role("link").first.click()
    candidate = page.locator(".candidate").first
    expect(candidate).to_be_visible()
    # the card reads "<provider> · <transaction id>"; confirming means typing its last four
    ref = candidate.locator("span", has_text="·").first.inner_text().split("·")[-1].strip()
    assert len(ref) >= 4, f"the candidate credit should show its transaction id, got {ref!r}"
    page.get_by_placeholder("Last 4 of txn ID (from source)").fill(ref[-4:])
    page.get_by_role("button", name="Confirm credit 1").click()
    expect(page.locator(".toast", has_text="Confirmed: APPROVED")).to_be_visible()
    page.screenshot(path=f"{out}/7-claim-confirmed.png", full_page=True)

    # an invoice with no payer and no claim, to receive money that arrived outside a statement
    page.goto(f"{base}/obligations")
    page.get_by_placeholder("Learner ref").fill("e2e-learner")
    page.get_by_role("button", name="Create").click()
    expect(page.locator("tbody tr", has_text="e2e-learner")).to_be_visible()

    # record a received payment, then have a *different* approver allocate it to that invoice
    page.goto(f"{base}/credits")
    page.get_by_label("Transaction ID").fill("MAN-E2E-0001")
    page.get_by_label("Amount").fill("12.34")
    page.get_by_label("Received at").fill("2026-11-02T10:00")
    page.get_by_label("Where you saw it").fill("Merchant portal, checked by the smoke test")
    page.get_by_role("button", name="Record payment").click()
    expect(page.locator(".toast", has_text="Credit recorded")).to_be_visible()
    expect(page.locator("tbody tr", has_text="MAN-E2E-0001").first).to_be_visible()
    page.goto(f"{base}/credits?as=approver:second@local.dev")
    row = page.locator("tbody tr", has_text="MAN-E2E-0001").first
    row.get_by_role("button", name="Allocate…").click()
    form = page.locator(".allocate-row")
    invoice = form.get_by_label("Invoice")
    option = invoice.locator("option", has_text="e2e-learner").first
    invoice.select_option(value=option.get_attribute("value"))
    form.get_by_label("Last four characters of the transaction ID").fill("0001")
    form.get_by_role("button", name="Allocate").click()
    expect(page.locator(".toast", has_text="Allocated")).to_be_visible()
    page.screenshot(path=f"{out}/8-credits.png", full_page=True)

    # the host-facing views render
    page.goto(f"{base}/auto")
    expect(page.get_by_role("heading", name="Automatic approvals")).to_be_visible()
    page.goto(f"{base}/exposure?as=admin")
    expect(page.get_by_role("heading", name="Provisional access")).to_be_visible()
    page.screenshot(path=f"{out}/9-exposure.png", full_page=True)
    browser.close()
print("smoke ok")
