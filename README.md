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
| 6 Policy tiers | `app/policy/engine.py` (versioned, per-org overrides) |
| 7 Ledger invariants | `app/ledger/service.py` + DB triggers in `migrations/versions/0002_*` |
| 8 Review and audit | `app/review/`, `audit_events`, `decisions`, React console |

Ledger guarantees are enforced by PostgreSQL itself: a credit or obligation can never be
over-allocated, currencies never mix, unsettled credits cannot be allocated, and allocations,
decisions and audit events are append-only. Reversals are compensating rows.

## Run it

### Docker (full stack)

```bash
cp .env.example .env          # add your Ollama Cloud key, or set POPS_VLM_BACKEND=fake
docker compose up --build
```

Console at http://localhost:5173, API docs at http://localhost:8000/docs, MinIO at
http://localhost:9001.

### Local development

```bash
# backend (needs PostgreSQL 16; tesseract optional — without it claims go to review)
cd backend
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
createdb pops && alembic upgrade head
POPS_VLM_BACKEND=fake uvicorn app.main:app --reload
PYTHONPATH=. python scripts/seed_demo.py      # demo claims + statement (fake reader)

# console
cd frontend && npm install && npm run dev       # proxies /api to :8000
```

### Tests

```bash
cd backend && createdb pops_test && pytest      # 48 tests: ledger invariants (Hypothesis),
                                                # concurrency race, all review flows, isolation
cd frontend && npm run build
python e2e/smoke.py http://127.0.0.1:5173       # Playwright smoke test against a seeded stack
```

## Integrating Cognify

1. `POST /obligations` for each invoice or instalment. The response carries a **payment code**
   (e.g. `CG-H4CC093`). Show it at checkout and ask payers to put it in the payment reference.
2. `POST /claims` (multipart) when a learner uploads a POP. `GET /claims/{id}` returns the state,
   the outcome and plain-language `messages` you can show the learner.
3. Receive `entitlement.updated` webhooks, signed with `X-POPs-Signature: t=<ts>,v1=<hmac-sha256>`
   over `"<ts>." + body`. Deduplicate on `X-POPs-Event-Id`. The payload gives paid and outstanding
   balances; **Cognify's own billing rules decide assessment access**.

Auth is JWT. `POST /auth/dev-login` exists only outside `prod`; wire Cognify's OIDC identity
before going live. Roles are `submitter` < `reviewer` < `approver` < `admin`.

## Models

The Ollama reader works with Ollama Cloud (default) or a self-hosted Ollama. See
[`docs/models.md`](docs/models.md) for the recommended free cloud vision models, the Phase 0
benchmark script, and the open-weights models to fine-tune for v2.

## Not in this slice

Auto-approval and provisional access (Phase 5), SMS/email alert ingestion and WhatsApp
(Phase 2), gateway webhooks and the EcoCash API (Phase 3), fraud and tamper models (Phase 4),
reader fine-tuning (§12), OIDC/MFA, OpenTelemetry/Sentry.
