# Auto-approval of clear claims

**The AI never approves.** The readers only extract fields. Auto-approval is a fixed list of rules
over facts the engine has already established, and it is **off by default**. A claim it does not
approve is left for a person exactly as before.

## When a claim is approved without a person

All of these must hold at the moment the claim becomes financially confirmed:

| Rule | Why |
| --- | --- |
| Policy `auto_approve_enabled`, and no `POPS_AUTO_APPROVE_DISABLED` | Per-organisation switch, and a global kill switch |
| Claim channel is in `auto_approve_channels` | Pilot one channel at a time |
| Approval tier is Green (every hard gate passed, no flags) | Same gates a reviewer cannot skip |
| Evidence tier at least `auto_approve_min_tier` (E2 alert, or E3 statement) | A receipt alone (E1) is never enough |
| The credit is not a manual (attested) credit | One person's attestation needs a second person |
| Fake-proof score at most `auto_approve_max_risk` (default 15) and no open fraud alert | Anything suspicious goes to people |
| Amount at most `auto_approve_max_minor[currency]`, and not above the second-approver limit | Small amounts only; a currency with no cap is never approved |
| The payment settles the invoice **exactly** | Partial and over-payments need judgement |
| No reviewer has already started on it | A person who looked keeps ownership |
| Hard gates pass again, and the reference is not already used | Re-checked, not trusted |

If the ledger refuses (for example the credit was just used), nothing is changed and the claim stays
in the queue.

## What it records

An automatic approval is an ordinary allocation: the database trigger re-checks the money
invariants, it is reversible (`POST /allocations/{id}/reverse`, approver role), and the host gets
the usual `entitlement.updated`. The decision is recorded by `rule:auto-approve` (`actor_type`
`rule`) with the gates, risk score, cap and policy version, and the claim carries the internal reason
`AUTO_APPROVED` (never shown to payers).

## Policy fields

| Field | Default | Meaning |
| --- | --- | --- |
| `auto_approve_enabled` | `false` | Enabling needs channels and a cap, or the policy is refused |
| `auto_approve_channels` | `[]` | e.g. `["ecocash"]` |
| `auto_approve_max_minor` | `{"USD": 5000}` | Per claim, minor units per currency |
| `auto_approve_min_tier` | `"E2"` | `E2` or `E3` |
| `auto_approve_max_risk` | `15` | Fake-proof score, 0-100 |
| `auto_approve_audit_pct` | `5` | Share of automatic approvals put in front of a reviewer |

Publish a new policy version to change them (`POST /admin/policy`); every change is audited.

## Measure before you switch it on

`POST /admin/replay` with a draft `config` now returns an `auto_approve` block: how many past claims
had a match, how many **would** have been approved, and which rule held the rest back
(`blockers`). It assumes each invoice and credit were unused when the claim arrived, so treat it
as an estimate. Compare it with the shadow-mode scorecard (`GET /shadow/scorecard`).

## Audit and go-live

- `GET /review/auto-approved` lists automatic approvals, which ones are in the sample, the verdicts
  so far and the **overturn rate**.
- A reviewer records `POST /review/claims/{id}/audit-verdict` with `{"verdict": "ok"|"overturned"}`.
  Overturning records the finding only; reversing the money is a separate approver action so each
  correction has its own trail.
- Blueprint go-live criteria: overturn rate under 1 % on the sampled audit, a tested incident
  runbook, and finance sign-off before the cap or channels are widened.

## Turning it off

- One organisation: publish a policy with `auto_approve_enabled: false`.
- Everywhere, immediately: set `POPS_AUTO_APPROVE_DISABLED=true` and restart. It overrides every
  policy.
