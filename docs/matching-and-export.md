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

## Splitting one payment across many invoices (bulk allocation)

For one lump payment, such as an employer paying for many learners. In the console: Credits, then
"Split one payment across invoices".

1. Pick the credit and paste or load a list: payment code, amount (optional), note (optional). Commas,
   semicolons and tabs work, with or without a header row; up to 500 rows.
2. **Preview** marks each row Ready, Check (no amount given, so the most allowed is used) or Fix
   (unknown code, wrong currency, a claim already waiting, more than is left).
3. **Apply** with the last 4 characters of the transaction id, looked up at the source.

`POST /credits/{credit_id}/allocate/bulk` takes `{run_id, preview, txn_last4, rows[]}`. Every row goes
through the same controls as a single allocation (typed last 4, separation of duties, a second approver
above the limit, the ledger trigger), so a bulk run can never do what one allocation could not. The
preview is never trusted: apply re-checks every row. A row's request id is derived from
`run_id + row number + invoice + amount`, so re-sending the same run after a timeout allocates each row
at most once and finishes the rest. A wrong last 4 or missing permission stops the whole run; any other
row error is reported and the run continues.

## Console additions

- Payers: `/track/{claim_id}` shows a six-step timeline, what happens next, and the provisional-access
  countdown. It never shows risk, tamper or duplicate detail. A rejection reads "We could not confirm your
  payment" with no reason.
- Reviewers: triage tabs (needs review, flagged), an overdue chip using the review SLA ladder, a sticky
  decision bar, a reject dialog showing the exact message the payer will see, a reverse dialog, zoom,
  rotate and brightness on the receipt, a proof switcher, and a receipt-against-record table whose rows
  can say "Not compared" so it never implies a check that did not happen.
- Admins: publishing a policy that switches something on or loosens it lists the consequences and needs an
  "I understand" tick.
