# Near-miss hints and the allocations export

## Near-miss reference hints (advisory)

The reference gate is an **exact** match after cleanup (case, spaces and punctuation removed). That
is deliberate: it is a hard money gate, so a similar reference must never pass it.

What reviewers get instead is a hint. When a candidate credit fails only because its reference
is *almost* the one on the receipt, the review screen labels it:

- `lookalike_characters`: the two differ only by characters people and OCR confuse (O/0, I/1/L, S/5, B/8);
- `typo`: at most two characters differ.

References shorter than 8 characters never get a hint (they collide by chance). The hint does not
change the gate, the score or the claim's state, and the credit **cannot be confirmed as it stands**.
If the receipt was misread, the reviewer corrects the extracted transaction ID (an audited action
with a reason); the exact match then passes and the normal confirmation (typed last four
characters, approvers) applies. The API exposes it as `near_miss` on each candidate of
`GET /review/claims/{id}`.

## Allocations export

`GET /allocations/export` (reviewer role and above; also on the Credits page) returns CSV:

| Parameter | Meaning |
| --- | --- |
| `since`, `until` | `YYYY-MM-DD`, UTC, both inclusive; omit for no bound |
| `kind` | `allocation` or `reversal`; omit for both |

One row per ledger entry. A reversal is a **negative** row, so a column total is the net amount
paid. Columns: `allocation_id`, `created_at_utc`, `kind`, `claim_id`, `obligation_id`,
`external_ref`, `learner_ref`, `payment_code`, `credit_id`, `credit_source`, `provider`,
`provider_txn_id`, `currency`, `amount` (signed, major units), `amount_minor` (signed), `reverses`,
`reason`, `created_by`.

- UTF-8 with a byte-order mark, so Excel reads names correctly.
- Text that begins with `=`, `+`, `-` or `@` (names and references can come from payers) is
  prefixed with an apostrophe so a spreadsheet shows it instead of running it.
- At most 100,000 rows per export; a larger range returns `413` asking for a narrower one.
- Every export is audited (`allocations.exported`, with the row count and filters).
- It only ever covers the caller's organisation.
