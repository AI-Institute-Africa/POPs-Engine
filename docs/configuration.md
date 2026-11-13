# Configuration reference

All settings are environment variables with the `POPS_` prefix (see `backend/app/core/config.py`
and `.env.example`). Business thresholds live in the versioned payment policy, not here
(`docs/guardrails.md`).

## Production safety

The service defaults to `POPS_ENV=prod`. In prod it **refuses to start** when:

- `POPS_JWT_SECRET` is the dev default or shorter than 32 characters
- `POPS_COGNIFY_WEBHOOK_URL` is set but the webhook secret is the dev default
- `POPS_PROVISIONING_KEY` is set but shorter than 32 characters
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

## Host-system provisioning

The engine has no user or organisation management: the host signs the tokens it sends, and the
`org` claim must name an organisation that already exists. A host can create one on demand:

| Variable | Default | Purpose |
| --- | --- | --- |
| `POPS_PROVISIONING_KEY` | unset | Shared secret for `POST /internal/orgs`. While unset the endpoint does not exist (404). In production it must be 32+ characters. |

`POST /internal/orgs {"name": "..."}` with header `X-POPs-Provisioning-Key` returns
`{org_id, name, created}`. It finds the organisation with that exact name or creates it, and is
safe to repeat and to call concurrently. It can do nothing else, but keep `/internal` off the
public internet and mount it only on the network the host uses.

Hosts that mirror the ledger read `GET /obligations/{id}/allocations` (every allocation and
reversal of an obligation, oldest first, with the credit it drew on) and can look an obligation
up by its own reference with `GET /obligations?external_ref=...`.

Console: build with `VITE_OIDC_AUTHORITY`, `VITE_OIDC_CLIENT_ID` (and optionally
`VITE_OIDC_SCOPE`) to replace the dev sign-in with an OIDC authorization-code + PKCE login.
Tokens are kept in `sessionStorage`, never `localStorage`.

## Uploads

| Variable | Default | Purpose |
| --- | --- | --- |
| `POPS_CLAMAV_HOST`, `POPS_CLAMAV_PORT` | –, 3310 | clamd INSTREAM scanning. If the scanner is unreachable, uploads get `503` + `Retry-After` (fail closed). |
| `POPS_RATE_LIMIT_UPLOADS_PER_MINUTE` | 30 | Per authenticated caller. |
| `POPS_RATE_LIMIT_CLAIMS_PER_CLAIMANT_PER_DAY` | 20 | Per learner/payer reference within an organisation. Limits are kept in Redis. |
| `POPS_RATE_LIMIT_PREVIEWS_PER_MINUTE` | 20 | Per authenticated caller, for `POST /claims/preview` (it runs the readers before any claim exists). |
| `POPS_RATE_LIMIT_TEXT_CONVERSIONS_PER_MINUTE` | 20 | Per authenticated caller, for the standalone `POST /image-to-text` converter (see [`image-to-text.md`](image-to-text.md)). |
| `POPS_AUTO_APPROVE_DISABLED` | `false` | Global kill switch for rules-based auto-approval; overrides every policy (see [`auto-approval.md`](auto-approval.md)). |
| `POPS_MAX_UPLOAD_BYTES` | 10 MB | Per file. A claim may carry up to 5 files (repeat the `file` form field) and/or a pasted `text` field of up to 20,000 characters. |

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
| `POPS_STATEMENT_PROFILES_FILE` | JSON list that adds or overrides bank statement layouts. The same profile reads a CSV or a digital PDF statement (column names matched case-insensitively in the PDF's table header). |
| `POPS_STATUS_SYNONYMS_FILE` | JSON object of extra payment-status words, e.g. in Shona or isiNdebele: `{"word": "success"}` (values: `success`, `pending`, `failed`, `reversed`). |

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

## Languages

The vision reader is told that documents may be in English, Shona or isiNdebele and to copy
values as printed. Shona and isiNdebele month names are understood in dates
(`app/normalise/fields.py`, `LOCAL_MONTHS`; have a first-language speaker review them).
Tesseract has no Shona or isiNdebele model; with `POPS_OCR_LANGUAGES=eng` it still reads the
digits, amounts and references that matter, which is what the two-reader check compares.

## WhatsApp

See [`whatsapp.md`](whatsapp.md): `POPS_WHATSAPP_VERIFY_TOKEN`, `POPS_WHATSAPP_APP_SECRET`,
`POPS_WHATSAPP_ACCESS_TOKEN`, `POPS_WHATSAPP_PHONE_NUMBER_ID` (all four together, or none),
optional `POPS_WHATSAPP_ORG_ID` and `POPS_WHATSAPP_GRAPH_URL`.

## Alerts

See [`alerts.md`](alerts.md): `POPS_ALERT_IMAP_HOST/USER/PASSWORD/FOLDER/SOURCE_ID`,
`POPS_ALERT_SOURCE_STALE_MINUTES` (default 30), `POPS_ALERT_TEMPLATES_FILE`, and
`POPS_RATE_LIMIT_ALERTS_PER_MINUTE` (default 120 per source).

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

## Admin notifications

Admins are told about events through a Slack/Teams/Google-Chat style webhook and/or email. Events are
queued in the outbox (retried every 30 s) and carry ids and reason codes only, never payer names,
numbers or amounts.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POPS_ADMIN_ALERT_WEBHOOK_URL` | – | Incoming webhook that accepts a top-level `text`. |
| `POPS_SMTP_HOST`, `POPS_SMTP_PORT`, `POPS_SMTP_USER`, `POPS_SMTP_PASSWORD`, `POPS_SMTP_FROM` | –, 587, –, –, `pops-engine@localhost` | SMTP relay (STARTTLS). |
| `POPS_ADMIN_ALERT_EMAILS` | `[]` | JSON list of recipients. |

What admins receive:

- `fraud.suspected`: a claim scored 60+ for fake-proof risk.
- `ops.review_pending`: **any** claim that starts waiting for a human (`PENDING_REVIEW` or
  `DUPLICATE_HOLD`). Sent once per wait. A suspected fake also sends `fraud.suspected`.
- `ops.review_overdue`: a reminder when a claim has waited past an SLA level. Levels are the payment
  policy's `review_sla_minutes` (default `[60, 240, 1440]`, positive and ascending); one reminder per
  level, checked every 5 minutes.
- `ops.alert_source_stale`, `ops.alert_credit_disputed`: payment-alert forwarder problems.
- `ops.provisional_expired` and the daily `ops.exposure_report`: see
  [`provisional-access.md`](provisional-access.md).

## Observability

- JSON logs with `request_id` (echoed in `X-Request-Id`). Logs carry no secrets or document
  contents.
- Prometheus at `/metrics`: `pops_claim_outcomes_total{state}`, `pops_pipeline_seconds`,
  `pops_pipeline_failures_total`, `pops_reader_failures_total{reader}`,
  `pops_guardrail_hits_total{effect}`, `pops_uploads_rejected_total{reason}`, `pops_stuck_claims`,
  `pops_outbox_pending`, `pops_http_seconds`, `pops_fraud_alerts_total{level}`,
  `pops_alerts_received_total{kind,status}`, `pops_stale_alert_sources`,
  `pops_tamper_flags_total`, `pops_shadow_proposals_total{proposer,outcome}`,
  `pops_shadow_errors_total`.
- `POPS_SENTRY_DSN` and `POPS_OTEL_ENDPOINT` switch on Sentry and OpenTelemetry tracing when the
  `observability` extra is installed (`pip install -e ".[observability]"`).

## Console settings (frontend build)

- `VITE_ALLOW_UNREVIEWED_TRANSLATIONS=true`: show the draft Shona and Ndebele strings. By default the
  console shows English until a fluent reviewer sets `REVIEWED[lang]` to `true` in
  `frontend/src/lib/i18nGate.ts`. A browser can also opt in with
  `localStorage["validate-allow-draft-translations"]="1"`.

## Alerts

- `POPS_ALERT_REQUIRE_SIGNATURE` (default `false`): refuse unsigned alert requests. See `docs/alerts.md`.
