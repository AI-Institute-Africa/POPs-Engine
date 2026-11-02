# Provisional access

While a receipt waits for the money to show up on our side, the engine can report that the payer
**may be given limited, time-boxed access**. It is off by default and does nothing on its own. The
engine only reports it; the host system you integrate decides what "access" means (blueprint §9:
the payment engine does not invent access rules).

## When a claim qualifies

Every condition must hold. A missing or unknown value means no access, never a guess.

- `provisional_enabled` is true in the organisation's payment policy.
- The claim is waiting for money and nothing else is wrong with it: its only reason is
  `AWAITING_CREDIT` (no duplicate, tamper, missing detail, channel or guardrail concern).
- Fake-proof risk has been assessed and is below `fraud_medium_score`, and the payer has no earlier
  fakes on record.
- The amount (capped at what is outstanding) is within the caps for the obligation's currency:
  per claim, per payer (all of their open access), and per organisation per day. A currency with no
  cap is never eligible.

| Policy field | Default | Meaning |
| --- | --- | --- |
| `provisional_enabled` | `false` | Master switch |
| `provisional_ttl_hours` | `24` | How long access lasts without a matching payment |
| `provisional_max_minor` | `{"USD": 5000}` | Per claim, minor units per currency |
| `provisional_learner_cap_minor` | `{"USD": 10000}` | Open access per payer |
| `provisional_org_daily_cap_minor` | `{"USD": 10000}` | New access granted per UTC day |

Publish a new policy version to change them (`POST /admin/policy`); every change is audited.

## What ends it

| Outcome | Trigger | Event to the host |
| --- | --- | --- |
| `confirmed` | the claim is approved (a reviewer confirmed the credit) | none; the normal `entitlement.updated` follows the allocation |
| `revoked` | rejected, reversed, more information requested, or the claim became a suspected fake | `entitlement.revoked` |
| `expired` | the window passed with no matching credit (checked every 5 minutes) | `entitlement.revoked` and an admin alert |

## Integrating: push

Events use the same signed webhook as `entitlement.updated` (`POPS_COGNIFY_WEBHOOK_URL`, header
`X-POPs-Signature: t=<ts>,v1=<hmac-sha256>` over `"<ts>." + body`, dedupe on `X-POPs-Event-Id`).

```json
{
  "id": "…", "type": "entitlement.provisional",
  "data": {
    "obligation_id": "…", "external_ref": "…", "learner_ref": "…", "type": "instalment",
    "currency": "USD", "amount_minor": 4000, "paid_minor": 0, "outstanding_minor": 4000,
    "payment_status": "unpaid", "cause": "provisional_granted",
    "provisional": true, "grant_id": "…", "claim_id": "…",
    "exposure_minor": 4000, "expires_at": "2026-11-03T08:12:00+00:00"
  }
}
```

`entitlement.revoked` has the same shape plus `revoked_cause`: `expired`, `rejected`, `reversed`,
`needs_information` or `suspected_fake` (`cause` is `provisional_<revoked_cause>`).

## Integrating: pull

`GET /obligations/{obligation_id}/access` (any authenticated caller in the organisation):

```json
{ "obligation_id": "…", "state": "provisional", "expires_at": "…", "exposure_minor": 4000,
  "paid_minor": 0, "outstanding_minor": 4000, "currency": "USD", "payment_status": "unpaid" }
```

`state` is `confirmed` (fully paid), `provisional` (open grants, with `expires_at` and
`exposure_minor`), or `none`. An overdue grant never counts, even before the sweeper runs.

## Reporting

- `GET /admin/exposure` (admin): active exposure per currency, the caps, the last 7 days (granted,
  confirmed, revoked, expired, % confirmed) and grants ending within 6 hours.
- A daily `ops.exposure_report` goes to the admin webhook/email (06:00 UTC).
- `ops.provisional_expired` is sent when a grant expires unpaid.
- Prometheus: `pops_provisional_exposure_minor{currency}` and `pops_provisional_events_total{event}`.

## What the payer sees

Only neutral wording ("Access is open until … while we confirm your payment"), never risk
information. Standalone, with no webhook configured and nothing polling, the feature has no effect.
