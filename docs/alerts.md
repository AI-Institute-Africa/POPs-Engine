# SMS and email payment alerts

Your own credit notifications are the fastest and most trustworthy confirmation the engine can
get without a provider API: the EcoCash merchant SMS on the business phone, and the "your account
has been credited" emails from your bank. Each alert becomes a **credit** (evidence tier E2), so
claims match within seconds, and receipts that were never paid have nothing to match.

## Register a source (console → Alerts, admin)

| Field | Example | Notes |
| --- | --- | --- |
| Kind | SMS forwarder / Alert mailbox | |
| Provider | `ECOCASH`, `CBZ` | Becomes the credit's provider |
| Our account alias | `MAIN` | Must be one of the policy's `beneficiary_aliases` |
| Allowed senders | `EcoCash`, `+263…` / `@cbz.co.zw` | **Required for SMS.** Anything else is rejected |

Registering shows a **source key once**. Rotate it from the same page if a phone is lost;
deactivating a source stops it immediately.

## SMS: forwarder app on the merchant phone

Use any Android SMS-forwarding app that can POST to a URL with a custom header, filtered to the
provider's sender ID:

```
POST https://<pops-host>/api/alerts/sms
X-POPs-Source-Key: pops_ak_…
Content-Type: application/json

{"from": "EcoCash", "text": "You have received USD40.00 from TENDAI MOYO (0771234567). Approval Code: PP251021.1432.A12345 …"}
```

The endpoint also accepts `sender`/`message`/`body` keys and form posts, which most forwarder apps
send. Have the app (or a scheduled task on the phone) call `POST /api/alerts/heartbeat` with the
same header every few minutes. If a source is silent for `POPS_ALERT_SOURCE_STALE_MINUTES`
(default 30), admins get one notification and the source shows as **silent**.

## Email: alert mailbox

Either:

- **Poll a mailbox**: set `POPS_ALERT_IMAP_HOST/USER/PASSWORD` and `POPS_ALERT_IMAP_SOURCE_ID`
  (the registered email source). The worker checks for unseen mail every minute.
- **Inbound-mail webhook** (Mailgun, SES, Postmark): POST the raw RFC 822 message to
  `/api/alerts/email` with the source key.

Emails must carry a **valid DKIM signature** from an allowed sender address or `@domain`. This is
what stops someone forwarding a forged "credit" email. Turn it off per source only for a
provider that does not sign its mail.

## What becomes a credit

| Status | Meaning |
| --- | --- |
| `credited` | An incoming payment with amount, currency and reference: a credit is created and waiting claims re-match. |
| `duplicate` | The same reference is already a credit (from a statement or another alert). Nothing is double-counted. |
| `ignored` | Not an incoming payment: debits, OTPs, PINs, balances, promotions. |
| `unparsed` | Looks like a payment but has no reference. Kept for finance, **not credited**, because it could not be told apart from a later statement line. |
| `rejected` | Sender not allowed, or the email's DKIM signature is missing or invalid. |
| `conflict` | The reference is already known with a different amount. Possible spoofing; check it. |

Every alert is stored and listed on the Alerts page, whatever its status.

## Alerts and bank statements together

- When a statement line arrives for money an alert already reported (same reference, or the
  reference inside the narrative, same amount), the alert credit is **confirmed by the statement**.
  It becomes evidence tier E3 and no second credit is created.
- When the statement **contradicts** the alert (same reference, different amount), the alert
  credit is **disputed**: it can no longer be allocated (the database refuses), claims matched
  to it go back to review with `CREDIT_DISPUTED`, the bank's own line is recorded, and admins
  are notified. Any allocations already made on it are listed for reversal.

## Templates

The built-in parsers (`ecocash_merchant`, `bank_credit`, plus a generic fallback) are starting
guesses. Confirm them against real alerts from each provider during Phase 0, then add or override
them without a code change with `POPS_ALERT_TEMPLATES_FILE`:

```json
[{"name": "innbucks_merchant", "provider": "INNBUCKS", "sender_pattern": "innbucks",
  "pattern": "received (?P<currency>USD|ZWG)\\s?(?P<amount>[\\d,]+\\.\\d{2}) from (?P<payer>[A-Z ]+).*?ref[:\\s]*(?P<reference>[A-Z0-9]{8,})"}]
```

Named groups: `amount` (required), `currency`, `reference`, `payer`, `phone`, `when`.
