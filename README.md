# POPs Engine

Proof-of-payment (POP) verification and billing engine for Cognify. It is built from
[`docs/blueprint-v1.1.md`](docs/blueprint-v1.1.md).

A receipt is a claim, never a confirmation. The engine reads any uploaded POP as untrusted
evidence. It confirms money only from the recipient's side (bank statements, alerts, gateway
callbacks, attested manual credits), and lets deterministic rules and a constrained ledger, not
AI, decide what can be approved.

This is **slice 1 (review-only MVP)**. There is no auto-approval path. Every allocation needs a
finance reviewer, and the reviewer can only confirm a credit that passes every hard gate.

## How a claim flows

```
upload ─► intake (type sniff, SHA-256, pHash) ─► quality check ─► VLM + OCR readers ─► agreement
        ─► normalise + typed-field cross-check ─► duplicate & consumed-reference checks
        ─► reconcile against credits (hard gates, then ranking) ─► state + evidence tier + Green/Amber/Red
        ─► reviewer console ─► confirm (type last 4 of txn ID) ─► atomic allocation ─► Cognify webhook

statement CSV / manual credit ─► normalised credits ─► credit-first re-match of waiting claims
```

| Layer (blueprint §11) | Where |
| --- | --- |
| 1 Capture quality, file safety | `app/intake/files.py`, `app/extraction/preprocess.py` |
| 2 Multi-reader extraction | `app/extraction/` (Ollama VLM, Tesseract + templates, agreement) |
| 3 Validation, typed cross-check | `app/normalise/`, `app/claims/facts.py` |
| 4–5 Independent confirmation, hard gates | `app/credits/`, `app/reconcile/matching.py` |
| 6 Policy tiers and AI guardrails | `app/policy/engine.py`, `app/guardrails/rules.py`, `app/extraction/tamper.py` (versioned, per-org) |
| 7 Ledger invariants | `app/ledger/service.py` + DB triggers in `migrations/versions/0002_*` |
| 8 Review and audit | `app/review/`, `audit_events`, `decisions`, React console |

## What it reads

Every proof-of-payment format people actually send:

- Screenshots and photos: PNG, JPEG, WEBP and iPhone HEIC.
- PDFs: **every page**. A PDF's own text layer is read as an exact, independent third reader.
- Word documents (.docx): text, tables and embedded screenshots. Macro documents are refused.
- Pasted text: the payment SMS or banking-app message pasted into the form (`text` field).
- Forwarded emails (.eml): the body and every attachment (images, PDFs, Word).
- **Several proofs for one payment**: up to 5 files per claim (e.g. the bank-app screenshot
  and the SMS). They are read together; if two proofs disagree on the amount, date or
  reference, the claim goes to a reviewer with `PROOFS_DISAGREE`.
- **WhatsApp**: payers send the photo to the business number with their payment code; see
  [`docs/whatsapp.md`](docs/whatsapp.md).
- English, Shona and isiNdebele documents (month names, configurable status words).

Bank statements import as CSV or as a **digital PDF** (the same column profile reads both).

Readers run on each page, and the first page that yields a field wins. Recipient-side
**SMS and email alerts** (the merchant phone's EcoCash SMS, bank credit emails) become credits
within seconds; see [`docs/alerts.md`](docs/alerts.md).

## Measuring it: replay and accuracy

*Replay & accuracy* in the console (and `POST /admin/replay`, `GET /admin/accuracy`) gives three
read-only views:

- **Policy what-if:** re-runs past claims under the active or a draft policy and lists every
  claim that would match, lose its match, or change risk.
- **Reader accuracy:** per reader, channel and provider, measured against confirmed payments.
- **Fraud-signal precision:** measured against admins' fake / not-fake decisions, including the
  fakes the score missed.

To benchmark candidate vision models on your own real receipts, export confirmed payments as a
labelled set (`scripts/export_labelled_set.py <dir>`, ground truth taken from the bank record)
and run `scripts/benchmark_reader.py <dir> <models...>`. The folder holds personal data: keep
it on approved infrastructure and delete it afterwards.

Two Phase-4 capabilities are already in the slice, both advisory by design:

- **Tamper forensics** (`app/extraction/tamper.py`): error-level-analysis and noise-consistency
  signals produce a manipulation score with highlighted regions. A hit adds `TAMPER_SUSPECTED`
  and routes the claim to a reviewer — it can never approve, reject or allocate on its own
  (blueprint §8: risk flag only). Thresholds live in the payment policy.
- **Shadow-mode scorecard** (`app/shadow/`): a rules baseline and (when allowed) the AI propose
  an outcome beside each claim; proposals are append-only and no decision path reads them. The
  scorecard compares them with what reviewers later decided — agreement, false-approval and
  false-hold rates — so the automation go-live decision is a measurement, not a debate
  (blueprint innovation 8). Off by default; enable with `POPS_SHADOW_MODE_ENABLED=true`.

## Validation and fake detection

Every proof of payment is **validated** against our own records: it is confirmed only when it
matches a credit on our side that passes every hard gate. Every claim also gets a **fake-POP risk
score** from explainable signals:

- a reused receipt
- a bank record that contradicts the amount or date
- money that never arrived once statements cover the date
- editing-software or AI-generator metadata, and PDF edit history
- ELA image forensics
- the vision model's own observations
- payer history and velocity

Suspected fakes (score 60+) go to admins under **Suspected fakes**, through the console badge
plus a webhook and/or email. They cannot be confirmed until an admin marks them *confirmed fake*
(rejected) or *not fake*. Payers never see an accusation. Details:
[`docs/fraud-detection.md`](docs/fraud-detection.md).

## Guardrails and policies for the AI

The AI only reads. Seven **locked guardrails** are always on: AI never approves, schema-only
output, evidence is untrusted, two readers for critical fields, fail closed, ledger invariants,
and guardrails only tighten. On top of those, admins **add, disable and remove** their own
guardrails in the console (*Guardrails & policies*): prompt-injection phrases, reference
formats, amount ranges, required fields, confidence floors, reading hints, a model allowlist, a
data-protection block on external AI, and a kill switch. They also publish and retire versions
of the **payment policy** there. Every change is audited. Details:
[`docs/guardrails.md`](docs/guardrails.md).

Ledger guarantees are enforced by PostgreSQL itself: a credit or obligation can never be
over-allocated, currencies never mix, unsettled credits cannot be allocated, and allocations,
decisions and audit events are append-only. Reversals are compensating rows.

## Run it

### Docker (full stack)

```bash
cp .env.example .env          # add your Ollama Cloud key, or set POPS_VLM_BACKEND=fake
docker compose up --build     # includes ClamAV; its first start downloads signatures
```

Validate UI (Scan + Guardrails, no login) at http://localhost:5173, API docs at http://localhost:8000/docs. Evidence sits on a
Docker volume in this stack; production uses S3-compatible storage (`POPS_STORAGE_BACKEND=s3`).

### Local development

```bash
# backend (needs PostgreSQL 16; tesseract optional — without it claims go to review)
cd backend
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
createdb pops && alembic upgrade head
export POPS_ENV=dev POPS_PIPELINE_MODE=inline POPS_VLM_BACKEND=fake
uvicorn app.main:app --reload
PYTHONPATH=. python scripts/seed_demo.py      # demo claims + statement (fake reader)

# console
cd frontend && npm install && npm run dev       # proxies /api to :8000
```

### Tests

```bash
cd backend && createdb pops_test && pytest      # ledger invariants (Hypothesis), concurrency race,
                                                # review flows, isolation, guardrails, fake
                                                # detection, tamper forensics, shadow scorecard,
                                                # SMS/email alerts, PDF/Word/HEIC, replay, OIDC,
                                                # malware scan,
                                                # real Tesseract OCR
cd frontend && npm run build
python e2e/smoke.py http://127.0.0.1:5173       # Playwright smoke test against a seeded stack
```

## Integrating Cognify

1. `POST /obligations` for each invoice or instalment. The response carries a **payment code**
   (e.g. `CG-H4CC093`). Show it at checkout and ask payers to put it in the payment reference.
2. `POST /claims` (multipart) when a learner uploads a POP: one or more `file` fields and/or a
   pasted `text` field. `GET /claims/{id}` returns the state,
   the outcome and plain-language `messages` you can show the learner.
3. Receive `entitlement.updated` webhooks, signed with `X-POPs-Signature: t=<ts>,v1=<hmac-sha256>`
   over `"<ts>." + body`. Deduplicate on `X-POPs-Event-Id`. The payload gives paid and outstanding
   balances; **Cognify's own billing rules decide assessment access**.

Auth is JWT. In production, Cognify identity issues OIDC tokens carrying `pops_role` and
`pops_org` claims, and finance roles must sign in with MFA. For service-to-service calls,
`scripts/issue_service_token.py` mints a token. `POST /auth/dev-login` exists only when
`POPS_ENV=dev|test`. Roles are `submitter` < `reviewer` < `approver` < `admin`. See
[`docs/configuration.md`](docs/configuration.md).

## Production safety

The service defaults to production rules and refuses to start with dev secrets, without a
malware scanner, with the fake reader, or in inline mode. Uploads are malware-scanned (fail
closed) and rate-limited. Failed pipeline runs are retried, then handed to a reviewer as
`PIPELINE_FAILED`; a sweeper re-dispatches lost tasks every minute. Logs are JSON with request
IDs, metrics are at `/metrics`, and Sentry/OpenTelemetry are optional.

## Models

The Ollama reader works with Ollama Cloud (default) or a self-hosted Ollama. See
[`docs/models.md`](docs/models.md) for the recommended free cloud vision models, the Phase 0
benchmark script, and the open-weights models to fine-tune for v2.

### Local Donut live demo

The trained local Donut checkpoint is available in `var/models/donut-pops`. Start the demo stack
from the repository root in PowerShell:

```powershell
docker compose -f docker-compose.yml -f docker-compose.donut-demo.yml up --build -d
.venv\Scripts\python.exe scripts/seed_live_demo.py
```

Open the console at <http://localhost:5173>. The seed command prints a payment code and a
synthetic screenshot, SMS, and complex proof image to upload. API docs are at
<http://localhost:8000/docs>. Use the review console to inspect extraction; synthetic uploads
are evidence only and do not create recipient-side credits or confirm payments. Stop the stack
with `docker compose -f docker-compose.yml -f docker-compose.donut-demo.yml down`.

## Needs real data (Phase 0)

Provider templates, statement layouts and the near-duplicate threshold are starting guesses.
Calibrate them from real samples without code changes: `POPS_PROVIDER_TEMPLATES_FILE`,
`POPS_STATEMENT_PROFILES_FILE`, `scripts/benchmark_reader.py` and `scripts/calibrate_phash.py`.

## Not in this slice

Auto-approval and provisional access (Phase 5), outcome replies to WhatsApp payers, gateway
webhooks and the EcoCash API (Phase 3), a trained fraud model on top of the
tamper risk flags (Phase 4), reader fine-tuning (§12).
