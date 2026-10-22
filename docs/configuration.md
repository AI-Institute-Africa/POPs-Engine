# Configuration reference

All settings are environment variables with the `POPS_` prefix (see `backend/app/core/config.py`
and `.env.example`). Business thresholds live in the versioned payment policy, not here
(`docs/guardrails.md`).

## Production safety

The service defaults to `POPS_ENV=prod`. In prod it **refuses to start** when:

- `POPS_JWT_SECRET` is the dev default or shorter than 32 characters
- `POPS_COGNIFY_WEBHOOK_URL` is set but the webhook secret is the dev default
- `POPS_VLM_BACKEND=fake`
- `POPS_CLAMAV_HOST` is empty (every upload must be malware-scanned)
- `POPS_PIPELINE_MODE=inline`

`/auth/dev-login` exists only when `POPS_ENV` is `dev` or `test`.

## Authentication

| Variable | Purpose |
| --- | --- |
| `POPS_OIDC_ISSUER`, `POPS_OIDC_AUDIENCE`, `POPS_OIDC_JWKS_URL` | Accept RS256/ES256 tokens from Cognify identity. |
| `POPS_OIDC_ROLE_CLAIM` (`pops_role`), `POPS_OIDC_ORG_CLAIM` (`pops_org`) | Claims that carry the role and organisation UUID. |
| `POPS_OIDC_REQUIRE_MFA_FOR_FINANCE` (true) | Reviewer, approver and admin tokens must have `mfa` or `otp` in `amr`. |
| `POPS_JWT_SECRET` | Signs internal tokens; `scripts/issue_service_token.py` mints service tokens with it. |

Console: build with `VITE_OIDC_AUTHORITY`, `VITE_OIDC_CLIENT_ID` (and optionally
`VITE_OIDC_SCOPE`) to replace the dev sign-in with an OIDC authorization-code + PKCE login.
Tokens are kept in `sessionStorage`, never `localStorage`.

## Uploads

| Variable | Default | Purpose |
| --- | --- | --- |
| `POPS_CLAMAV_HOST`, `POPS_CLAMAV_PORT` | –, 3310 | clamd INSTREAM scanning. If the scanner is unreachable, uploads get `503` + `Retry-After` (fail closed). |
| `POPS_RATE_LIMIT_UPLOADS_PER_MINUTE` | 30 | Per authenticated caller. |
| `POPS_RATE_LIMIT_CLAIMS_PER_CLAIMANT_PER_DAY` | 20 | Per learner/payer reference within an organisation. Limits are kept in Redis. |
| `POPS_MAX_UPLOAD_BYTES` | 10 MB | |

## Pipeline resilience

| Variable | Default | Purpose |
| --- | --- | --- |
| `POPS_PIPELINE_MAX_ATTEMPTS` | 3 | After this many failures the claim goes to a reviewer as `PIPELINE_FAILED`. |
| `POPS_STUCK_AFTER_SECONDS` | 300 | The sweeper (Celery beat, every minute) re-dispatches claims still `RECEIVED` after this. `GET /ops/stuck` lists them; the `pops_stuck_claims` gauge alerts on them. |

## Readers and calibration files

| Variable | Purpose |
| --- | --- |
| `POPS_VLM_BACKEND`, `POPS_OLLAMA_URL`, `POPS_OLLAMA_API_KEY`, `POPS_OLLAMA_MODEL` | Vision reader (see `docs/models.md`). |
| `POPS_OCR_BACKEND`, `POPS_OCR_LANGUAGES` | Tesseract second reader (`eng` by default). |
| `POPS_PROVIDER_TEMPLATES_FILE` | JSON list that adds or overrides provider layouts, without a code change. |
| `POPS_STATEMENT_PROFILES_FILE` | JSON list that adds or overrides bank statement CSV layouts. |

Provider template entry:

```json
{"provider": "INNBUCKS", "document_type": "mobile_money_receipt", "keywords": ["innbucks"],
 "txn_patterns": ["\\b(IB\\d{10})\\b"], "date_order": "DMY"}
```

Statement profile entry (use `credit_col`/`debit_col` or a signed `amount_col`):

```json
{"name": "cbz_export", "provider": "CBZ", "date_col": "Value Date", "date_format": "%d/%m/%Y",
 "narrative_col": "Description", "reference_col": "Reference", "credit_col": "Credit",
 "debit_col": "Debit", "currency": "USD"}
```

Build both from the Phase 0 samples; `scripts/benchmark_reader.py` and
`scripts/calibrate_phash.py` measure the effect.

## Phase-4 advisory features

Both features are **risk flags and advice only** — neither can approve, reject or allocate
anything (blueprint §8, innovation 8).

| Variable | Default | Purpose |
| --- | --- | --- |
| `POPS_SHADOW_MODE_ENABLED` | false | Record advisory proposals (rules baseline + AI when allowed) beside each claim and score them against human decisions via `GET /shadow/scorecard`. Proposals are append-only; no decision path reads them. |
| policy `tamper_enabled` | true | Per-org payment policy switch for the tamper forensics check (`POST /admin/policy`). |
| policy `tamper_review_score` | 0.6 | Manipulation score at or above which the claim gains `TAMPER_SUSPECTED` and routes to a reviewer. |

The AI proposer obeys the same guardrail kill switch / external-processing block / model
allowlist as the AI reader: when the reader is off, only the deterministic rules baseline keeps
proposing. Thresholds for the forensics live in `app/extraction/tamper.py` (robust z-scores over
per-cell ELA residual and noise MAD) and are exposed as `tamper` on the review detail payload.

## Observability

- JSON logs with `request_id` (echoed in `X-Request-Id`). Logs carry no secrets or document
  contents.
- Prometheus at `/metrics`: `pops_claim_outcomes_total{state}`, `pops_pipeline_seconds`,
  `pops_pipeline_failures_total`, `pops_reader_failures_total{reader}`,
  `pops_guardrail_hits_total{effect}`, `pops_uploads_rejected_total{reason}`, `pops_stuck_claims`,
  `pops_outbox_pending`, `pops_http_seconds`, `pops_tamper_flags_total`,
  `pops_shadow_proposals_total{proposer,outcome}`, `pops_shadow_errors_total`.
- `POPS_SENTRY_DSN` and `POPS_OTEL_ENDPOINT` switch on Sentry and OpenTelemetry tracing when the
  `observability` extra is installed (`pip install -e ".[observability]"`).
