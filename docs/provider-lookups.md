# Provider lookups (browser agent, optional)

A reviewer can ask the system to open **one allow-listed provider page** (for example an EcoCash or
Paynow receipt link) and read what it shows. The answer is **evidence for a person**: it is written to
the claim's audit trail with `decision_effect: none`. It never approves, rejects or moves a claim.

Idea borrowed from [browser-use](https://github.com/browser-use/browser-use) (MIT): typed output,
domain allow-lists, an injection flag from the agent. The library is an optional extra
(`pip install -e "backend[agent]"`) and tests never need it.

## Use

`POST /claims/{id}/provider-lookup` with `{"url": "https://..."}` (reviewer role). Result:
`CONFIRMS` (reference, amount, currency and status agree), `CONTRADICTS` (same reference, different
amount or a not-successful status) or `INCONCLUSIVE` (page not readable, nothing found, a different
transaction, or the page tried to give instructions). Not finding a payment is never proof it is missing.

## Settings

| Setting | Meaning |
| --- | --- |
| `POPS_LOOKUP_MODE` | `off` (default) or `shadow` (record the result) |
| `POPS_LOOKUP_DISABLED` | global kill switch |
| `POPS_LOOKUP_BACKEND` | `fake` (tests/demo) or `browser` |
| `POPS_LOOKUP_ALLOWED_DOMAINS` | JSON list, e.g. `["ecocash.co.zw"]`; https only, no raw IPs |
| `POPS_LOOKUP_TIMEOUT_SECONDS` | hard time limit (default 60); any failure means "no evidence" |

Production refuses to start with lookups on and the fake backend or an empty domain list.

## Safety

browser-use is **not a sandbox**. Run the browser backend in a separate container with no database or
secrets, never log in with personal credentials, never solve captchas. Registry lookups for KYC are
deliberately not built: they need legal sign-off (portal terms, POTRAZ rules) first.

## Agent tour

`backend/tools/agent_tour.py` lets an agent explore the demo as each persona and write a short report.
It is for reading, not a CI gate; `frontend/e2e` stays the gate.
