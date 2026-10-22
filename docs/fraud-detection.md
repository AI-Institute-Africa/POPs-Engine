# Validating proofs of payment and flagging fakes

POPs Engine answers two separate questions about every uploaded proof of payment (POP):

1. **Did the money actually arrive?** This is *validation*. A POP is only ever a claim. It is
   confirmed when it matches a credit on **our** side (bank statement line, merchant alert,
   gateway callback, or an attested manual credit) and that credit passes every hard gate:
   reference, amount, currency, receiving account, settled status, unspent balance and date
   window. No image, however convincing, confirms a payment on its own.
2. **Does this POP look fake?** This is *fraud risk*. Every claim gets a 0–100 risk score from
   independent, explainable signals. Suspected fakes are escalated to an admin.

Validation protects the money: a fake POP with no matching credit can never be approved.
Fraud risk protects the process: it surfaces people trying to cheat, so an admin can act, and it
stops a suspected fake from being confirmed until an admin has looked.

## Risk levels

| Score | Level | What happens |
| --- | --- | --- |
| 0–29 | low | Normal review. |
| 30–59 | **suspicious** | A badge shows in the queue and on the claim; the alert appears under *Suspected fakes* (filter "suspicious"). Confirmation is not blocked. |
| 60–100 | **suspected fake** | Admin alert (console badge, plus webhook and/or email). The claim is marked Red and **cannot be confirmed** until an admin decides. |

Both thresholds are set per organisation in the payment policy (`fraud_high_score`,
`fraud_medium_score`), as is the weight of each signal (`fraud_weights`).

## Signals

| Signal | Default weight | What it means |
| --- | --- | --- |
| `REUSED_RECEIPT` | 70 | The same file, a near-identical image, or the same transaction reference was already submitted or credited, in any organisation. |
| `AMOUNT_EDIT_SUSPECTED` | 70 | A bank record with this exact reference shows a **different amount**: the classic edited receipt. Re-checked whenever a new statement arrives. |
| `AI_GENERATED_MARKERS` | 60 | The file carries metadata written by an image generator (Stable Diffusion parameters, C2PA, DALL·E, Midjourney, Firefly…). |
| `NO_CREDIT_AFTER_STATEMENT` | 45 | Statements covering the receipt's date (plus the date window) have been imported and no matching payment arrived. |
| `DATE_EDIT_SUSPECTED` | 40 | The bank record with this reference has a different date. |
| `WRONG_RECIPIENT` | 40 | The transaction with this reference went to an account that is not ours. |
| `FUTURE_DATE` | 40 | The receipt is dated in the future. |
| `EDITING_SOFTWARE` | 35 | Metadata names an editor (Photoshop, GIMP, Canva, PicsArt, Snapseed, PDF editors…). |
| `PROMPT_INJECTION_TEXT` | 30 | The receipt contains text aimed at the AI or the reviewer (needs the *Prompt-injection phrases* guardrail). |
| `PDF_EDITED_AFTER_CREATION` | 30 | The PDF has incremental saves, or its modified date differs from its created date. |
| `EDITABLE_DOCUMENT` | 30 | The proof is a Word document. Banks and mobile-money apps don't issue those, and they're easy to type up. |
| `PROOFS_DISAGREE` | 35 | Two proofs sent for the same payment show different amounts, dates or references (each read by both readers). The field goes to a reviewer; the engine never picks one. |
| `PAYER_HISTORY` | 30 | The same payer has earlier claims confirmed as fake, duplicate or never paid. |
| `PASTED_TEXT` | 25 | The proof is pasted or forwarded text (an SMS copied into the form or sent on WhatsApp). Anyone can type it, so it is only as good as the bank record it matches. Dampened once the money is confirmed. |
| `ELA_LOCAL_EDIT` | 25 | Image forensics (Error Level Analysis on JPEGs) found a region compressed differently from the rest, as happens when an amount is pasted in. The heatmap is shown to admins. |
| `REFERENCE_FORMAT_INVALID` | 25 | The reference does not match the provider's known format. |
| `AI_SUSPECTS_EDITING` | 15 | The vision model noticed visible signs of editing (mismatched fonts, misaligned digits). |
| `READERS_DISAGREE` | 15 | The two independent readers read different values for a critical field. |
| `STATUS_NOT_SUCCESS` | 15 | The receipt shows the payment as pending or failed. |
| `VELOCITY` | 15 | More than `velocity_claims_per_day` claims from this payer in 24 hours. |
| `DATE_BEFORE_INVOICE` | 15 | The receipt is dated before the invoice existed. |
| `TYPED_DETAILS_DIFFER` | 10 | What the payer typed differs from the receipt. |

**Money confirmed dampens file-only doubts.** When a bank record independently confirms the
payment, signals that are only about the file (editing software, ELA, model notes, reader
disagreement, format, pasted text) count half: someone may have cropped a genuine screenshot. Reuse and
AI-generation markers are never dampened.

**Every signal explains itself.** The admin sees a plain-language reason and the evidence behind
each signal (the conflicting bank amount, the metadata that named the editor, the ELA score).

## The admin workflow

1. The alert appears under **Suspected fakes** (highest score first). The nav badge counts open
   suspected fakes. If configured, the admin webhook and/or email also fire. The notification
   carries the score and signal codes only: no payer names, numbers or document contents.
2. The admin opens the claim and looks at the evidence, the signals and the ELA heatmap.
3. The admin decides, with a required note:
   - **Confirm fake & reject.** The claim is rejected as `TAMPERED_DOCUMENT`, and the payer's
     future claims carry `PAYER_HISTORY`.
   - **Not fake, return to review.** The alert closes and reviewers can confirm the claim
     normally. If *new* signals appear later, the alert reopens.
4. A reviewer who rejects a claim as `TAMPERED_DOCUMENT`, `DUPLICATE_CONFIRMED` or
   `PAYMENT_NOT_RECEIVED` closes its open alert as a confirmed fake.

Every decision is audited (`fraud.alert_opened`, `fraud.confirmed_fake`,
`fraud.false_positive`, `fraud.alert_reopened`). Resolved alerts form the labelled data set for
a trained fraud model later (blueprint §12: "after shadow mode").

## What payers see

Nothing that accuses them. Cognify's API view of a claim hides the risk score, the signals and
the `SUSPECTED_FAKE` code; the message is neutral ("Your payment needs a manual check by our
finance team"). The blueprint is explicit: investigate, but never accuse a user from an AI score
alone.

## Configuration

```
POPS_ADMIN_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/…   # Slack/Teams/Chat "text" webhook
POPS_SMTP_HOST=smtp.example.com  POPS_SMTP_PORT=587  POPS_SMTP_USER=…  POPS_SMTP_PASSWORD=…
POPS_SMTP_FROM=pops@cognify.example
POPS_ADMIN_ALERT_EMAILS=["finance-lead@cognify.example","coo@cognify.example"]
```

Policy (per organisation, versioned, under *Guardrails & policies*):

```json
{"fraud_high_score": 60, "fraud_medium_score": 30, "velocity_claims_per_day": 5,
 "fraud_weights": {"EDITING_SOFTWARE": 45, "REFERENCE_FORMAT_INVALID": 0}}
```

A weight of 0 switches a signal off.

## Limits and calibration

- **ELA** only works on JPEGs, and misses edits re-saved at exactly the original quality. On
  synthetic tests it caught 5 of 5 pasted amounts with no false positives on 5 genuine images,
  but real phone photos vary. Measure it on Phase 0 samples before trusting its weight.
- **Metadata** is easily stripped by screenshotting, so its absence proves nothing. Its presence
  is a useful hint.
- **The strongest signals come from our own records**: a bank line that contradicts the receipt,
  or no money after the statement covers the date. Importing statements promptly is what makes
  fake detection sharp.
- Tune weights from resolved alerts. If admins keep clearing one signal, lower its weight; if a
  confirmed-fake pattern keeps scoring low, raise the signals it triggers.
