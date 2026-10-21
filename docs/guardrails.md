# Guardrails and policies for the AI

POPs Engine uses AI (a vision-language model) to *read* proofs of payment. It never lets AI
*decide*. This page covers the controls around the AI: the locked guardrails that are always on,
the guardrails an admin can add and remove, and the payment policy those rules work against.

Manage all of it in the console under **Guardrails & policies**, or through the API under
`/admin/guardrails` and `/admin/policy`. Reviewers can see everything; only admins can change
anything, and every change is written to the audit log.

## 1. Locked guardrails (always on, cannot be removed)

| Guardrail | What it guarantees | Where it is enforced |
| --- | --- | --- |
| AI never approves | No model output can approve a claim, post an allocation or grant access. A named reviewer confirms, and only against a recipient-side credit that passes every hard gate. | `app/review/service.py`, `app/workers/pipeline.py` (there is no auto-approve path) |
| Schema-only output | The model must return the fixed receipt JSON schema (`format=` in the Ollama call). Its text is data, it has no tools, and it is never interpreted as an instruction. | `app/extraction/readers/ollama_vlm.py`, `app/extraction/schema.py` |
| Evidence is untrusted | The prompt tells the model to ignore any instructions in the image. Receipt text, QR contents and messages are stored as plain values. | `ollama_vlm.SYSTEM_PROMPT` |
| Two readers for critical fields | Amount, reference, date and beneficiary count only when two independent readers (the VLM and Tesseract + templates) agree, or a reviewer corrects them. | `app/extraction/agreement.py` |
| Fail closed | Reader errors, timeouts, low confidence, a disabled AI or a missing credit send the claim to a person. They never approve. | pipeline routing |
| Ledger invariants | PostgreSQL rejects over-allocation, currency mixing, allocating an unsettled credit, and any edit to allocations, decisions or audit events. | `migrations/versions/0002_ledger_invariants.py` |
| Guardrails only tighten | Admin rules can escalate a claim, distrust a reading, add reading hints or switch the AI off. No rule can approve, allocate or relax a gate. | `app/guardrails/rules.py` (action whitelist) |

## 2. Admin guardrails (add, disable, remove)

Each rule has a **kind**, an **action**, a JSON **config** and a description reviewers see. When a
rule fires, the claim gets the reason code `GUARDRAIL_<RULE_NAME>`, and the verification run
records which rules applied.

| Kind | Action | Config | Effect |
| --- | --- | --- | --- |
| `blocked_text` | review / needs_info | `{"patterns": [regex…], "fields": [optional]}` | Flags a claim when any read field matches a pattern (e.g. prompt-injection phrases). |
| `field_pattern` | review / needs_info | `{"field", "pattern", "provider"?}` | Distrusts a field that does not fully match the pattern; the field stops counting as agreed. |
| `amount_range` | review / needs_info | `{"currency", "min_minor"?, "max_minor"?}` | Flags amounts outside a range (minor units: 100 = 1.00). |
| `require_field` | review / needs_info | `{"field", "must_agree"}` | Flags, or asks the payer for a clearer copy, when a field is missing. |
| `min_confidence` | review / needs_info | `{"field", "min"}` | Distrusts a field read below a confidence. |
| `prompt_instruction` | prompt | `{"text"}` | Adds a reading hint after the fixed prompt. Hints that mention approving, allocating, ignoring instructions, JSON or schema are refused. |
| `model_allowlist` | disable_ai | `{"models": […]}` | Switches the AI reader off if the configured model is not on the list. |
| `external_processing_block` | disable_ai | `{}` | Switches the AI reader off whenever it points at an external service such as Ollama Cloud (data-protection gate). |
| `ai_kill_switch` | disable_ai | `{}` | Switches the AI reader off for the organisation immediately. |

When the AI is switched off, claims are still accepted. They are read by OCR only, which never
reaches two-reader agreement, so every one goes to a reviewer. The console banner shows the
reader's status and which rule turned it off.

**Lifecycle.** *Add* validates the rule (kind/action pairing, regex syntax and length, known field
names). *Disable* pauses a rule and keeps it. *Remove* asks for a reason and soft-deletes the
rule: it disappears from the active list, stays visible under "show removed", and is never
hard-deleted, so past decisions remain explainable. Each step writes `guardrail.added`,
`guardrail.disabled`/`enabled` or `guardrail.removed` to the audit log.

**Recommended rules** (one click to add, then edit or remove): prompt-injection phrases, EcoCash
reference format, USD amount sanity range, transaction reference required, day-first dates hint,
keep evidence on our servers, approved models only, AI kill switch.

> Calibrate pattern-based rules (EcoCash reference format, amount ranges) on real Phase 0 samples
> before relying on them. A too-strict pattern does not cause wrong approvals, but it sends good
> claims to review.

### API

```
GET    /admin/guardrails?include_removed=false   system + org rules + reader status   (reviewer)
GET    /admin/guardrails/templates               recommended rules                    (reviewer)
POST   /admin/guardrails                         {name, kind, action, config, description} (admin)
POST   /admin/guardrails/{id}/enabled            {enabled: bool}                      (admin)
DELETE /admin/guardrails/{id}                    {reason}                             (admin)
```

## 3. Payment policy (versioned)

The policy holds the numbers the gates and reviewers work to. Publishing creates a new immutable
version; retiring a version falls back to the previous active one, or to the built-in default.
Every verification run and decision records the policy version it used.

| Setting | Default | Meaning |
| --- | --- | --- |
| `beneficiary_aliases` | `["MAIN"]` | Receiving accounts and merchant codes a credit must land on. |
| `date_window_hours` | 72 | How far a credit's time may be from the receipt's. Ambiguous numeric dates are tried both ways. |
| `amount_tolerance_minor` | 0 | Allowed difference for fees (0 = exact). |
| `second_approver_above_minor` | `{"USD": 50000}` | **Per currency.** A currency with no limit always needs two approvers, so set a ZWG limit explicitly. |
| `manual_credit_min_role` | `approver` | Who may record an attested manual credit. |
| `enabled_channels` | `null` (all) | Pilot scoping: claims on other channels go to review with `CHANNEL_NOT_IN_PILOT`. |
| `phash_duplicate_distance` | 6 | Near-duplicate threshold (Hamming distance of 64-bit pHash). Calibrate with `scripts/calibrate_phash.py`. |
| `min_image_side_px`, `min_sharpness` | 300, 40 | Capture quality floor before asking for a clearer copy. |
| `critical_fields`, `single_reader_min_confidence` | amount, transaction_id, date, beneficiary; 0.97 | Fields that need two-reader agreement. |
| `auto_approve_enabled` | `false` | Cannot be set to true in this release; publishing it is refused. |

```
GET  /admin/policy                         active version + defaults      (reviewer)
GET  /admin/policies                       version history                (reviewer)
POST /admin/policy                         {version, config}              (admin)
POST /admin/policies/{id}/deactivate       {reason}                       (admin)
```

## 4. Decisions only your team can make

Record these as policy versions or guardrails once agreed:

- **Who may record manual credits**, and the per-currency second-approver limits (especially ZWG).
- **Whether receipts may go to Ollama Cloud** before the data-protection review. Until then, add
  *Keep evidence on our servers* and self-host the reader.
- **Which channel to pilot first.** Set `enabled_channels` to that channel.
