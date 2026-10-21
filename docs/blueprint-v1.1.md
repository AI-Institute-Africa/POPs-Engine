# Universal POP Verification System – Blueprint v1.1

Oct 21, 2026 · @Aiia

Cognify should read any proof of payment (POP) as untrusted evidence, then confirm money from the recipient's side (credits, gateways, statements) and let deterministic policy, not AI, approve access. This v1.1 sharpens the original design document with a recipient-first confirmation model, a cheaper extraction cascade and measurable targets.

## 1. Definition and scope

"POP" here means proof of payment: any evidence a person submits to claim that money was sent for a Cognify obligation (course fee, instalment, subscription, usage charge). It is a claim, never a confirmation.

**Problems it must solve:**

- Slow confirmation of learner payments.
- The same receipt submitted repeatedly.
- Altered or misleading proof-of-payment documents.
- Payments allocated to the wrong learner or course.
- Assessments unlocked before payment eligibility is established.
- Decisions that are hard to trace or dispute afterwards.

**Key distinction:** a readable receipt is not a confirmed payment, and a confirmed payment is not automatically an approved course entitlement. The system keeps these three steps separate.

**In scope (evidence types):** mobile money receipts and SMS, bank transfer screenshots, bank statement PDF/CSV, gateway receipts, POS/card slips, cash and cheque deposit slips, wallet transfers, international remittances (SWIFT/UETR), bulk employer remittances, forwarded emails or WhatsApp notices, and handwritten receipts.

**Out of scope:** AI proving settlement from an image alone, scraping private accounts, and automatic refunds.

| Persona | Goal | What the system must give them |
| --- | --- | --- |
| Learner / parent / sponsor | Get access quickly after paying | Clear status, fast feedback, fix-it prompts when a POP is unreadable |
| Organisation admin | Pay for many learners in one transfer | Remittance schedule upload, per-learner allocation, batch status |
| Finance reviewer | Clear exceptions fast and defensibly | Evidence viewer, side-by-side credit match, one-click confirm with audit trail |
| Super Admin / owner | Control fraud loss and policy | Thresholds per channel, kill switch, exposure dashboard |
| Auditor | Reproduce any decision | Append-only trail with policy version and evidence snapshot |

**Zimbabwe-specific constants:** dual-currency (USD and ZiG), many channels (EcoCash, OneMoney, InnBucks, bank apps, ZIPIT/RTGS, gateways), and uneven API access. Design for the case where only receipts and statement exports exist.

## 2. Vision and success criteria

The goal is to turn most POP claims into confirmed or rejected decisions in minutes, with a false-approval rate near zero. Five capabilities set it apart:

1. **Recipient-first confirmation.** Money is confirmed from your own credits (alerts, statements, gateway callbacks), not from the claimant's picture.
2. **Evidence-tier model.** Every claim carries an explicit strength (claim-only, document-consistent, source-corroborated, settled) that drives access and reporting.
3. **Format-agnostic, multi-reader extraction.** A vision-language model (VLM) reads any layout into a strict schema while OCR and provider templates act as independent checks, so new layouts degrade gracefully instead of failing.
4. **Allocation-aware ledger.** One credit can split across invoices, many credits can fund one invoice, and reversals are compensating events.
5. **Explainable, replayable decisions.** Any approval can be re-run from stored evidence and the policy version.

**Starting targets** (set after the Phase 1 baseline, tune with real data):

| Metric | Target | Why it matters |
| --- | --- | --- |
| False approvals (credit posted, funds never received) | 0 in replay tests; under 0.1% of approvals live | Direct financial loss |
| Median time from upload to decision, verifiable channels | Under 5 minutes | Learner experience |
| Median time to decision, receipt-only channels | Under 4 working hours | Reviewer SLA |
| Share of volume confirmed without human touch | 60% by Phase 5 | Cost and speed |
| Field extraction accuracy (amount, reference, date) | 98% on known templates; 90% on unseen layouts | Reduces reviewer load |
| Duplicate detection recall on labelled set | 99% for exact and near-duplicate files | Prevents double credit |
| Reviewer overturn rate on auto-approvals (sampled audit) | Under 1% | Trust in automation |
| Unmatched credits older than 7 days | Under 2% of credit value | Reconciliation health |

**Also baseline before the pilot** (from the Cognify proposal):

| Metric | What it measures |
| --- | --- |
| Manual review rate | Share of claims that need staff intervention |
| False-hold rate | Valid claims held unnecessarily |
| Reconciliation success rate | Share of claims matched to trusted financial records |
| Audit completeness | Material decisions that have the required evidence and history |
| Access-control correctness | Course access follows the approved billing rules |
| Duplicate-credit rate | Zero in the replay and concurrency test suite; non-negotiable |

### Expected business value

| Area | Intended improvement |
| --- | --- |
| Operational efficiency | Less manual data entry, faster handling of straightforward claims, one central review queue |
| Financial integrity | No duplicate allocations, accurate partial payments and balances, preserved refund and reversal history |
| Learner experience | Clear payment statuses, less doubt about whether a payment was recognised, access tied to confirmed allocations |
| Institutional accountability | Traceable decisions, visible outstanding claims and exceptions, support for disputes |
| Scalability | Many organisations and channels, new providers through adapters, one engine for fees, subscriptions and organisation payments |

These are intended benefits; measure them in the pilot rather than assume them.

## 3. Benchmarks and patterns to copy

No single product verifies arbitrary POPs, so the design borrows one strong pattern from each of five system families. These are general, publicly known patterns; confirm vendor specifics before relying on them.

| System family | Pattern to emulate | How it applies to POPs |
| --- | --- | --- |
| Payment platforms (Stripe-style APIs) | Signed webhooks, idempotency keys, immutable event log | Process every callback and upload idempotently; the event log is the source for replays |
| Accounting bank-feed reconciliation (Xero, QuickBooks, Odoo) | Match suggestions ranked per bank line, user-confirmed rules that auto-apply next time | Credit-first matching; reviewers' confirmed matches become reusable rules |
| Document AI receipt and expense processors (Azure, Google, Textract) | Field-level confidence plus bounding boxes for each extracted value | Store confidence and region per field so reviewers see exactly what was read |
| Fraud and risk platforms (velocity, entity-link, case queues) | Velocity checks, link analysis across devices and references, analyst case management | Flag repeated references, shared devices and bursty submissions; route to case queue |
| Mobile money and bank merchant portals | Daily settlement and statement exports with unique transaction IDs | The practical first confirmation source where no live API exists |

**Common lesson:** every strong system separates *what was claimed* from *what the money rails say happened*, and keeps both on the record.

## 4. Architecture and data strategy

The system has two inputs that meet in a reconciliation core: **claims** (what users upload) and **credits** (what your accounts and providers report). Approval happens only where they match under policy.

&#91;embedded content: architecture · claims and credits meet in reconciliation\]

Claims flow through extraction and credits flow through normalisation; the reconcile step joins them, policy tiers the result, and only green cases post to the ledger without a reviewer.

### 4.1 Pipeline stages

1. **Intake:** accept web upload, email inbox attachments, WhatsApp and API submissions through one gateway; validate file type and signature, malware scan, hash (SHA-256 plus perceptual), store privately.
2. **Classify:** pick the evidence type and its verification profile.
3. **Extract:** preprocess images (OpenCV deskew, crop, contrast; PDFs rendered at high resolution), then a VLM reads the image into a fixed JSON schema; OCR and provider templates run alongside as independent readers; keep confidence and region per field.
4. **Normalise:** currency, decimals, timezone, phone formats, reference casing, status synonyms; keep raw values.
5. **Cross-check:** compare extraction with user-typed transaction ID and amount; disagreement drops the claim to review.
6. **Credit ingestion (parallel):** recipient SMS and email alerts, statement CSV/PDF imports, gateway webhooks and merchant-portal exports become normalised credits.
7. **Reconcile:** hard gates first (reference, amount, currency, beneficiary, success status, unallocated balance), then rank survivors.
8. **Risk and policy:** versioned rules assign a tier; AI flags may escalate but never override a hard block.
9. **Decide and post:** idempotent ledger write, entitlement event, notification; later reversals append compensating entries.

### 4.2 Data types and metadata

| Entity | Key fields |
| --- | --- |
| Claim | claim\_id, claimant, org, obligation, declared amount and currency, channel, evidence tier, state |
| Evidence | evidence\_id, object key, SHA-256, perceptual hash, EXIF and PDF metadata, source type, received\_at |
| Extracted field | name, raw value, normalised value, confidence, extractor and version, page region |
| Credit | credit\_id, source (alert, statement, gateway), provider reference, amount (minor units), currency, timestamp, status, source\_record\_id, import\_id |
| Allocation | allocation\_id, credit\_id, obligation\_id, amount, currency, status, reversal link |
| Decision | decision\_id, claim, outcome, reason codes, policy version, actor (rule, reviewer), evidence snapshot |
| Audit event | actor, action, entity, before and after state, correlation\_id, timestamp |

### 4.3 Privacy and security

- Either self-host an open-weights VLM (for example Qwen2.5-VL served with vLLM) or mask account and phone numbers before anything goes to an external API, keeping the unredacted original locally; masking can reduce accuracy on those fields, so benchmark both options in Phase 0 on accuracy, cost per document and data residency.
- Private storage, short-lived signed URLs, per-organisation scoping in every query, masked values in the reviewer UI.
- Treat all extracted text, QR contents and message bodies as untrusted (prompt-injection and link risk).
- Get a local data-protection review (Zimbabwe's Cyber and Data Protection Act) for retention, consent and cross-border processing before choosing a vendor.

### 4.4 Core algorithms

| Task | Approach |
| --- | --- |
| Classification | Rules on keywords and layout first; vision LLM for unknown formats; confidence threshold sends the rest to review |
| Extraction | OpenCV preprocessing, then a VLM with strict JSON schema as primary reader; OCR and provider templates as cross-checks; accept a field only when two readers agree or confidence is high |
| Duplicate detection | Exact hash, perceptual hash (pHash) distance, unique constraint on provider plus transaction ID, text-embedding similarity for pasted messages |
| Candidate matching | Lexicographic: hard gates pass or fail, then rank by reference exactness, amount, time proximity, payer consistency |
| Anomaly and fraud flags | Velocity per payer, device and reference; template deviation; metadata edit traces; gradient-boosted model on labelled outcomes in shadow mode first |
| Impact analysis | Track false approvals, false holds, reviewer minutes and exposure value per channel to tune thresholds |

### 4.5 Evidence tiers and approval tiers

Evidence tier says how strongly the money is confirmed; approval tier says what the system does about it. Keep them as separate fields.

| Evidence tier | Meaning | Typical source | Access treatment |
| --- | --- | --- | --- |
| E0 Claim only | User typed details, no readable file | Form entry | None |
| E1 Document-consistent | Readable, readers agree, no duplicate; funds not confirmed | Screenshot, receipt, slip | Review; provisional access only if policy allows |
| E2 Source-corroborated | Matches a credit seen on the recipient side | Forwarded merchant alert, mailbox alert | Auto-approve only within configured limits |
| E3 Settled | Matches an authoritative record | Bank statement line, signed gateway callback, provider API record | Full access; auto-approval eligible |

| Approval tier | Conditions | Action |
| --- | --- | --- |
| Green | E2 or E3 match; beneficiary, currency and status pass; amount allocatable; no duplicate or reversal; within limits | Auto-approve if the organisation's policy enables it; post atomically |
| Amber | Receipt-only evidence, fuzzy reference, legitimate payer difference, partial or over-payment, extraction uncertainty, provider down | Hold or provisional access; request information or send to reviewer |
| Red | Known duplicate, failed or reversed payment, wrong beneficiary, already fully allocated, amount or currency mismatch, tampering plus material mismatch | Block automation; preserve evidence; investigate without accusing the user from an AI score alone |

**Four operational outcomes** sit on top of the detailed states in Appendix B:

| Outcome | Detailed states | System action |
| --- | --- | --- |
| Approved | APPROVED | Commit the allocation and recalculate payment eligibility |
| Pending review | PENDING\_REVIEW, AWAITING\_FINANCIAL\_CONFIRMATION | Send to an authorised reviewer, or wait for a trusted match |
| Needs information | NEEDS\_INFORMATION | Ask for corrected or additional evidence |
| Rejected or duplicate hold | REJECTED, DUPLICATE\_HOLD | Block allocation, record the reason, preserve the evidence |

**Initial automation policy:**

- Automatic approval only for eligible channels with independently confirmed transactions and every required check passed.
- Human approval for receipt-only claims, ambiguous matches, unusual transactions, unsupported channels and policy exceptions.
- Automatic rejection or blocking only for clearly defined rule violations, such as a known duplicate or a confirmed failed transaction.
- AI flags discrepancies and explains possible matches; it never posts allocations or grants access.

### 4.6 Allocation rules

```latex
\text{available} = \text{confirmed credit} - \text{prior allocations} - \text{reversals}
\text{allocation} = \min(\text{available},\ \text{invoice outstanding},\ \text{amount authorised})
```

| Case | Rule |
| --- | --- |
| Partial payment | Allocate what was received; invoice stays partly paid; access follows the billing policy, not the upload |
| Overpayment | Allocate up to outstanding; keep the remainder unapplied for the payer's decision or the next instalment |
| Short by fees | Apply the configured fee policy (sender-paid or recipient-net); never assume the receipt shows the net credit |
| Split payment | Each credit confirmed separately; invoice paid total is the sum of valid allocations |
| Bulk payment | Reconcile the credit to the batch first, then line items; the sum cannot exceed the credit; the remainder stays unapplied |
| USD and ZiG | Compare only like currencies; any conversion uses a configured, timestamped rate source and keeps both amounts |
| Reversal or chargeback | Append a compensating entry, recalculate the balance and access; never edit history |

### 4.7 Threat model

| Attack | Example | Controls |
| --- | --- | --- |
| Edited or fabricated receipt | Amount or date changed in an image editor | Approval requires a matching credit; manipulation score escalates; template deviation flag |
| Reused receipt | One transaction claimed against two invoices or organisations | Unique provider plus transaction ID; perceptual hash; global reference registry |
| Pending or failed shown as success | Screenshot taken before the transfer failed | Status gate; credit-based confirmation |
| Wrong beneficiary | Payment sent to a different account | Beneficiary gate with an alias list |
| Spoofed alert or SMS | Fake notification forwarded | Accept alerts only from your own forwarder device or authenticated mailbox; cap at E2 |
| Prompt injection | Text in the image says "approve this payment" | Model output is schema-only data with no tool authority; rules decide |
| Webhook replay or forgery | Repeated or unsigned callback | Signature, timestamp and idempotency checks |
| Reviewer misuse | Reviewer approves their own manual entry | Separation of duties, second approver above limits, sampled audit |
| Structuring | Many small claims to stay under limits | Velocity rules per payer, device and reference |

### 4.8 Worked examples

Amounts are illustrative.

| Scenario | Flow | Outcome |
| --- | --- | --- |
| Learner pays a course instalment by EcoCash and uploads a screenshot with the payment code | Extraction reads code and amount; the merchant alert credit arrives with the same reference and amount | E2, Green; auto-approved if within the E2 limit |
| Parent uploads a bank transfer screenshot; no alert exists | Amber, provisional access with a cap; next-day statement import matches the credit | E3, approved; if no match within the set window, access revokes automatically |
| Employer pays one bank credit for 20 learners with a remittance schedule | Batch reconciled first, then 20 allocations; the difference stays unapplied | Matched lines approved; remainder and mismatches go to review |
| Same screenshot uploaded from two accounts | Perceptual hash and transaction ID collide | Both claims Red, duplicate hold; a reviewer decides which, if any, is valid |

### 4.9 Course payment and assessment access

This is the most important integration for Cognify. Example: a learner is enrolled in a $120 course payable as three $40 monthly instalments. The exact access conditions must match Cognify's approved billing rules.

| Scenario | Payment-system outcome | Assessment-access action |
| --- | --- | --- |
| Learner uploads a $40 receipt screenshot | Awaiting financial confirmation | Do not unlock assessments on the screenshot alone |
| Trusted evidence confirms the $40 payment | Allocate $40 to the right obligation | Recalculate eligibility under the instalment policy |
| Learner submits the same transaction twice | Duplicate hold | Do not credit the payment twice |
| Learner pays $25 of a $40 instalment | Record the confirmed $25 allocation | Apply the partial-payment rule; do not unlock if the policy requires the full instalment |
| The payment is later reversed | Record a reversal and recalculate the balance | Recalculate access under the approved policy |
| An organisation pays for several learners | Reconcile the batch, allocate confirmed amounts | Update each learner's eligibility separately |

Keep course fees, platform subscriptions and other obligations as separate types, and never allocate more than the confirmed, unallocated balance of a credit.

## 5. Innovative features

1. **Recipient-side alert ingestion.** An SMS forwarder on the merchant phone and a mailbox parser turn your own credit notifications into trusted credits within seconds, which closes the P2P confirmation gap.
2. **Credit-first matching with an unclaimed-credits queue.** Every incoming credit is matched to open invoices before anyone uploads a POP; payers who never upload are still found.
3. **Cognify payment codes.** Each invoice gets a short unique reference (and a QR) that the payer includes in the narrative; an exact reference match is the strongest signal short of a gateway callback.
4. **Verification-by-channel nudges.** The checkout shows verifiable channels first (gateway, reference payment) and offers POP upload as the fallback, shifting volume toward auto-confirmable rails.
5. **Provider layout library with drift alerts.** A versioned library of EcoCash, bank and wallet receipt templates; a sudden rise in template mismatches signals either an app redesign or a forgery wave.
6. **Provisional access with capped exposure.** Low-value, low-risk claims get time-boxed access that auto-revokes if no credit appears; caps are per learner, per organisation and per day.
7. **Reviewer learning loop.** Each reviewer correction becomes a labelled example and, once confirmed, a reusable matching rule, so the manual queue shrinks over time.
8. **Fast-confirm reviewer console.** Side-by-side POP and candidate credit, keyboard shortcuts, one-click confirm that requires typing the last four characters of the transaction ID from the source system.
9. **Cross-organisation reference registry.** Every consumed transaction ID is globally unique, so a receipt used for one organisation cannot be reused for another.
10. **Explainable decision cards.** Each decision shows which gates passed, the evidence tier, the policy version and a plain-language reason that can be sent to the learner.

## 6. Roadmap and MVP

The MVP is review-only: it reads POPs and shows reviewers a candidate credit, but never auto-credits. Timelines are indicative for a team of two to three engineers plus a finance reviewer.

| Phase | Timeline | Scope | Exit criteria |
| --- | --- | --- | --- |
| 0. Discovery | Weeks 1–2 | Inspect repo, database, payment modules and RBAC; collect 300+ real POPs across channels; list bank and provider access; benchmark VLM and OCR options (self-hosted versus API); confirm one alert source; build the labelled test set and fault-injection POPs | Architecture decision record signed; labelled sample set exists |
| 1. MVP: intake and review | Weeks 3–8 | Secure upload and email-inbox ingestion, image preprocessing, hashing, duplicate checks, VLM extraction with OCR cross-check for the top 3 channels, typed-field cross-check, reviewer console, audit log, payment codes; error layers 1, 2, 3 and 7; small document-type classifier; weekly fault-injection run | Reviewers process 100% of test claims; zero auto-credits; 95% extraction accuracy on top-3 templates; duplicate recall 99%; every seeded error stopped by a layer |
| 2. Credit ingestion and reconciliation | Weeks 9–14 | Statement CSV import, SMS and email alert ingestion, credit-first matching, allocations (partial, split, bulk); error layers 4 and 5; payer status tracker | Reconciliation passes the test corpus; ledger invariants hold in replay; unmatched credits under 5% |
| 3. Provider connectors | Weeks 15–20 | Gateway webhooks with signature checks; gateway-first checkout; EcoCash or other APIs only where production access is confirmed | Contract tests pass; live scope confirmed in writing |
| 4. Shadow-mode AI | Weeks 15–22 (overlaps) | AI proposes classification, matches and risk flags; humans decide; tamper flags and risk model trained on labelled outcomes; decide whether to fine-tune the reader (section 12.1); layers 6 and 8 measured | Measured false-approval and false-hold rates meet agreed limits for 4 straight weeks |
| 5. Controlled automation | Weeks 23–30 | Auto-approve Green tier on selected channels and amounts, provisional access, earned payer trust levels, 5% sampled audit, kill switch, shadow-mode scorecard | Finance sign-off; overturn rate under 1%; incident runbook tested |
| 6. Cognify-wide rollout | Weeks 31+ | Course instalments, subscriptions, org batches, reporting, dashboards; accounting export and searchable evidence archive | Balances and entitlements reconciled in production for one full billing cycle |

**MVP success criteria:** median reviewer time per claim under 2 minutes, 90% of claims correctly pre-matched to the right credit when one exists, and no duplicate credit in any test or pilot run.

**Scope guard for the first release:** secure submission, extraction, duplicate detection, finance review with documented manual verification, authorised statement imports, allocation, audit logging and integration with Cognify's existing billing and assessment-access logic. Provider APIs and automatic approval follow once the first release is tested. Do not start by building a large, independent AI platform or enabling automation for every receipt; approve Phase 0 first, then the MVP.

## 7. Deliverables

### 7.1 Technical blueprint

This document is the blueprint. Phase 0 adds an architecture decision record covering the real Cognify stack, the ledger database choice and the OCR vendor benchmark.

### 7.2 Data model sketch (relational ledger)

```sql
-- money in integer minor units; never floats
CREATE TABLE credits (
  credit_id        uuid PRIMARY KEY,
  source           text NOT NULL,          -- alert | statement | gateway | manual
  provider         text NOT NULL,
  provider_txn_id  text NOT NULL,
  amount_minor     bigint NOT NULL CHECK (amount_minor > 0),
  currency         char(3) NOT NULL,
  occurred_at      timestamptz NOT NULL,
  status           text NOT NULL,
  UNIQUE (provider, provider_txn_id)
);
CREATE TABLE allocations (
  allocation_id    uuid PRIMARY KEY,
  credit_id        uuid NOT NULL REFERENCES credits,
  obligation_id    uuid NOT NULL,
  amount_minor     bigint NOT NULL CHECK (amount_minor > 0),
  reverses         uuid REFERENCES allocations,
  idempotency_key  text NOT NULL UNIQUE
);
-- invariant: SUM(allocations per credit, net of reversals) <= credit.amount_minor
```

### 7.3 Feature list by priority

| Priority | Features |
| --- | --- |
| Must (MVP) | Secure upload, hashing and duplicate checks, VLM extraction with OCR cross-check for top 3 channels, typed-field cross-check, reviewer console, payment codes, audit log |
| Should | Statement import, alert ingestion, credit-first matching, partial and split allocations, decision cards |
| Could | Gateway webhooks, bulk remittance mapping, provisional access, fraud model in shadow mode |
| Later | Auto-approval, cross-organisation registry analytics, FX handling beyond USD and ZiG |

### 7.4 Evaluation plan

| Area | Method | Pass criterion |
| --- | --- | --- |
| Extraction | Labelled corpus of 300+ local POPs, scored per field and per channel | 98% known templates, 90% unseen |
| Duplicates and replay | Same file, cropped copy, repeated reference, duplicate webhook, repeated statement import | Zero duplicate credit |
| Financial correctness | Exact, partial, split, bulk, overpayment, wrong currency, failed and reversed credits | Ledger invariants hold; no over-allocation |
| Security | Cross-organisation access attempts, malicious files, prompt-injection text in receipts | No leakage; no instruction followed |
| Shadow mode | Run AI beside human decisions for 4+ weeks | False-approval and false-hold rates within agreed limits |
| Operations | Queue depth, reviewer minutes, provider failure drills, kill-switch test | SLA met; automation off within 1 minute |

**Release gate:** no automation goes live on a channel until its replay tests, shadow-mode results and finance sign-off are recorded.

## 8. External suggestions reviewed

Two outside write-ups proposed further upgrades. Most of the practical ones are now in sections 2, 4 and 6; the rest are declined, with reasons, so the decision is on record.

| Suggestion | Verdict | Reasoning and where it landed |
| --- | --- | --- |
| VLM as the primary reader with strict JSON schema | Adopted | Section 4.1 step 3; VLMs handle unseen layouts better than templates |
| "VLM replaces OCR entirely" | Partly | OCR stays as an independent second reader, because VLMs can misread or invent digits in amounts and references |
| OpenCV preprocessing (deskew, crop, contrast) | Adopted | Section 4.1 step 3; cheap and improves every downstream reader |
| Self-hosted open-weights VLM for privacy and cost | Adopted as an option | Section 4.3; decide by Phase 0 benchmark on local POP samples |
| Multi-channel ingestion (email inbox, WhatsApp, API) | Adopted | Section 4.1 step 1; web upload and email in the MVP, WhatsApp in Phase 2 |
| Manipulation score with highlighted suspicious regions | Adopted as a risk flag only | Fakes are cheap to generate, so the score can escalate a case but never approve one |
| Click-to-correct reviewer UI with learning from corrections | Adopted | Already in section 5, items 7 and 8 |
| Accounting and ERP export, searchable archive | Later | Phase 6 reporting; the Cognify ledger stays the system of record |
| Zero-knowledge proofs | Not adopted | A ZKP needs the payment rail to issue a verifiable proof; EcoCash and local banks do not, and it adds nothing for an uploaded receipt. Revisit if a rail offers verifiable credentials |
| Feature-level multimodal fusion, custom model training | Not adopted | Modern VLMs already align image and text internally; no practical return for this system |
| 99.8% extraction accuracy as a headline | Not adopted | Unproven without local data. Targets stay 98% on known templates and 90% on unseen layouts, measured per field, with amount and reference protected by cross-checks |
| Amounts as floating-point numbers | Rejected | Money is stored as integer minor units with an explicit currency |
| Blockchain and crypto payments | Out of scope | Not part of Cognify's current payment channels |
| LLM to interpret payment purpose and flag unusual transactions | Partly | Allowed for explanations and flags; rules and ledger decide |

Model names in the outside write-ups (GPT-4o, Claude 3.5 Sonnet) are dated; benchmark whichever current models are available when Phase 0 starts.

## 9. Risks and open decisions

These decisions gate the roadmap; owners are suggestions to confirm.

| Decision or risk | Suggested owner | Needed by | Recommended default |
| --- | --- | --- | --- |
| Actual Cognify stack, database and current payment modules | Engineering lead | Phase 0, week 1 | Inspect the repo before changing anything; no database migration without a tested plan |
| Which banks and mobile channels give exportable statements or alerts | Finance | Phase 0, week 2 | Start with statement CSV import and a forwarded-alert pilot on one channel |
| EcoCash production API scope | Business lead | Phase 0, week 2 | Treat lookup as unavailable until confirmed in writing; the published sandbox lookup is not proof of production access |
| VLM hosting: self-hosted or external API | Engineering lead | End of Phase 0 | Decide from the benchmark on real local POPs, using the same test set for both |
| Auto-approval limits and provisional-access caps | Finance owner and COO | Before Phase 5 | No automation until shadow-mode results are agreed |
| Who may attest that funds were seen on the recipient side | Finance owner | Before the Phase 1 pilot | Named staff only, with evidence reference and audit entry |
| Data protection, retention and cross-border processing | Legal advisor | Before vendor selection | Local review first; minimise stored personal data |
| Reviewer capacity and SLA | Operations | Before Phase 1 pilot | Size the queue from the Phase 0 sample volumes |

**Further decisions from the Cognify proposal:**

| Decision | Recommended default |
| --- | --- |
| Which payment channels the first release supports | Prioritise channels with accessible, authorised transaction records |
| Who can approve or reverse a payment | Define finance roles, separation of duties and escalation before the pilot |
| How assessment access responds to refunds and reversals | Apply Cognify's approved billing and access policies; do not invent new rules in the payment engine |
| Budget and timeline | Estimate after repository discovery, provider-access confirmation and scope approval |

## 10. Innovation priorities and pilot plan

The project is a payment-evidence engine for Cognify, where most payments arrive as receipts for transfers the platform cannot look up directly. Ranked by impact and feasibility, the best moves all do one thing: get the recipient's own record of the credit into the system.

### 10.1 Prioritised innovations

| Rank | Innovation | Impact | Feasibility | Why it ranks here |
| --- | --- | --- | --- | --- |
| 1 | Payment codes matched to recipient-side alerts | High | High | A unique code in the payment narrative plus your own credit alert gives an exact, trusted match without any provider API. |
| 2 | Gateway-first checkout, POP upload as fallback | High | Medium | Every payment moved onto a verifiable rail removes a manual review, but it needs a gateway agreement. |
| 3 | Credit-first matching with an unclaimed-credits queue | High | Medium | Matching every incoming credit to open invoices finds payers who never upload and cuts claim-hunting. |
| 4 | Fast-confirm reviewer console with a learning loop | Medium-high | High | Reviewer minutes are the main running cost, and each confirmed match can become a reusable rule. |
| 5 | Provisional access with exposure caps | Medium-high | High | Learners get access while evidence is checked, and the caps keep the worst-case loss bounded. |
| 6 | Payer status tracker with self-service fixes | Medium | High | Telling payers exactly what is missing (clearer photo, reference, amount) cuts support messages and resubmissions. |
| 7 | Earned payer trust levels | Medium | Medium | Payers with a clean confirmation history can get higher auto-approval limits, which rewards honest behaviour and shrinks the queue. |
| 8 | Shadow-mode scorecard and kill switch | Medium | High | Daily false-approval and false-hold rates turn the go-live decision into a measurement rather than a debate. |
| 9 | Layout library with drift alerts | Medium | Medium | Known provider layouts speed extraction, and a sudden spike in mismatches flags an app redesign or a forgery wave. |

### 10.2 Implementation prompts for the top five

| Innovation | Key actions | Stakeholders | Metrics |
| --- | --- | --- | --- |
| 1. Payment codes and alerts | Generate a short code per invoice; show it at checkout with instructions; forward merchant SMS and alert emails from a controlled device or mailbox; parse into credits; match on code and amount | Engineering, Finance, learners and parents | Share of payments carrying a valid code; share auto-matched within 5 minutes |
| 2. Gateway-first checkout | Shortlist gateways; confirm signed callbacks and fees; make the gateway the default option; keep POP as a clearly labelled fallback | Business lead, Finance, Engineering | Share of volume paid by gateway; POP volume per month |
| 3. Credit-first matching | Import statements and alerts continuously; match credits to open invoices on code, amount and date; surface unmatched credits daily | Finance, Engineering | Unmatched credit value older than 7 days; claims needing manual search |
| 4. Reviewer console | Side-by-side evidence and candidate credit; keyboard shortcuts; typed last four characters of the transaction ID to confirm; log each correction as a labelled example | Finance reviewers, Engineering | Median minutes per claim; reviewer overturn rate on auto-approvals |
| 5. Provisional access | Define caps per learner, per organisation and per day; time-box access; auto-revoke when no credit appears; report exposure daily | COO, Finance, Academic team | Value of unconfirmed access at risk; percent of provisional claims later confirmed |

### 10.3 Risks and trade-offs for the top three

| Innovation | Risk or trade-off | Mitigation |
| --- | --- | --- |
| 1. Payment codes and alerts | Payers omit or mistype the code; forwarded alerts can be spoofed or missed if the device is offline | Fall back to amount, time and reviewer match; accept alerts only from your own device or authenticated mailbox and cap them at evidence tier E2; heartbeat check on the forwarder |
| 2. Gateway-first checkout | Gateway fees and payer reluctance may push volume back to transfers | Compare fee cost against reviewer cost; keep POP fallback; offer the gateway with clear speed benefits |
| 3. Credit-first matching | Ambiguous narratives cause wrong matches; privacy exposure from broad statement data | Hard gates before ranking; hold fuzzy matches for review; restrict statement access and mask personal data |

### 10.4 Pilot plan for innovation 1 (three two-week sprints)

1. **Sprint 1, capture.** Generate payment codes; set up alert forwarding for one channel (suggest the highest-volume mobile money line); parse alerts into credits; no decisions made. Exit: 95% of alerts in a test week parsed into correct credits.
2. **Sprint 2, match in shadow mode.** Match codes and amounts to open invoices; show the candidate credit in the reviewer console; reviewers still decide everything. Exit: 90% of claims with a code pre-matched to the right credit, zero wrong matches shown as confirmed.
3. **Sprint 3, limited live pilot.** Auto-approve E2 matches under a low cap for a small cohort, with a 5% sampled audit and the kill switch tested. Exit: overturn rate under 1%, median decision time under 5 minutes, and finance sign-off to widen the cap.

## 11. Layered error-containment architecture

Zero errors cannot be promised, so the design aims for something checkable: no undetected error reaches the ledger. Eight independent layers can each stop a claim, and any layer that fails or is unsure sends it to review.

&#91;embedded content: layered architecture · eight error-containment layers\]

Each layer catches a different kind of error. Layer 4 is the one a model cannot replace, because only a real credit proves that money arrived.

### 11.1 What each layer does

| Layer | Error it catches | Mechanism | If it fails |
| --- | --- | --- | --- |
| 1. Capture quality | Blurred, cropped, glared or rotated images | Quality and edge checks, deskew; ask for a clearer copy before extraction | Request a new image |
| 2. Multi-reader extraction | Misread digits and fields | VLM, OCR and provider templates read independently; amount, reference, date and beneficiary are accepted only when two readers agree | Field goes to review |
| 3. Validation | Impossible or inconsistent values | Schema validation, decimal and currency checks, date plausibility, per-provider reference patterns, comparison with the user's typed fields | NEEDS\_INFORMATION |
| 4. Independent confirmation | Fake, edited or unsettled receipts | A matching credit from an alert, statement, gateway or provider record; no match means no approval | AWAITING\_FINANCIAL\_CONFIRMATION |
| 5. Hard-gate reconciliation | Wrong beneficiary, amount, currency or status; duplicates; already-allocated credits | Pass or fail gates run before any ranking | PENDING\_REVIEW or Red |
| 6. Policy and risk tiers | Limit breaches, velocity, tamper flags | Versioned rules assign Green, Amber or Red | Amber or Red handling |
| 7. Ledger invariants | Double posting, over-allocation, partial writes, replays | Unique and check constraints, atomic transactions, idempotency keys, append-only compensating entries | Transaction rolls back |
| 8. Review, audit, monitoring | Errors the layers above missed | Sampled audit of auto-approvals, daily control totals, unmatched-credit report, drift alerts, reversal tracking | Incident procedure and kill switch |

### 11.2 Design rules

- **Fail closed.** A timeout, low confidence or missing source never approves; it routes to review.
- **Independence.** Layers must not share a failure cause: two extraction readers use different methods, and confirmation comes from a source the payer cannot edit.
- **Protect the critical fields.** Amount, reference, beneficiary and date need two agreeing readers plus a matching credit before money posts.
- **Reason codes everywhere.** Every layer logs why it passed or failed, so a decision can be replayed.
- **Inject faults on purpose.** Run seeded bad POPs (altered amount, duplicate, wrong beneficiary, blurred reference) through the live pipeline every week and check that a layer stops each one.
- **Measure escapes.** Track errors found at layer 8 by the layer that should have caught them; a rising count means that layer needs work.

## 12. Model strategy: should we train on local POPs?

Not at first. Start with a pretrained VLM, prompts and a strict schema, then train only where local data gives a measurable edge. Training before you have labelled local data and a baseline would be guesswork.

| Component | Train a model? | Why and when |
| --- | --- | --- |
| Main VLM reader | Not initially; fine-tune later if needed | Benchmark pretrained models on real local POPs first. Fine-tune an open-weights VLM (for example with LoRA) only if a high-volume channel misses its accuracy target, or to cut cost or keep data in-house |
| Document-type classifier | Yes, a small one | Cheap to train on local layouts (mobile money, bank apps, slips) and speeds routing |
| Provider layout fingerprints | No, use rules | Deterministic templates are easier to audit than a model |
| Tamper and forgery detection | Later and carefully | Few labelled fakes exist at first; begin with off-the-shelf forensic checks as flags, then train as real cases accumulate |
| Risk and fraud scoring | After shadow mode | Gradient-boosted model on labelled outcomes; it may escalate a case, never approve one |
| Matching and allocation | No | Rules and ledger constraints, not a model |

### 12.1 When to fine-tune the reader

1. The Phase 0 benchmark shows critical-field accuracy below target on a channel that carries a large share of volume.
2. Reviewer corrections have produced enough labelled examples per layout; a few hundred per major layout is a common starting point, so confirm with a pilot run.
3. The fine-tuned model beats the best pretrained model on a held-out test set by a margin that justifies the maintenance cost.

### 12.2 Data and governance rules

- Build the labelled set from reviewer-corrected claims, with personal data masked where the model does not need it.
- Keep a held-out test set that is never used for training; re-score every model version on it.
- Version every model and prompt, record which version made each decision, and keep a rollback path.
- Re-benchmark when a provider changes its receipt layout or when a new model is released.
- Get the data-protection review (section 4.3) before using real payer data for training.

## 13. Recommended tech stack and frameworks

Use Python, FastAPI, PostgreSQL and a vision-language model behind an adapter, unless Phase 0 shows Cognify already runs something that fits better. The table maps each tool to the error layer it serves (section 11). Names are well-known open-source or managed options; check current versions and licences when you start.

| Need | Recommended | Alternatives | Error layer |
| --- | --- | --- | --- |
| Frontend and reviewer console | React with TypeScript (the existing Cognify UI), TanStack Query, pdf.js for viewing, a canvas overlay for field regions | Existing framework if Cognify uses another | 8 |
| API and services | Python with FastAPI, Pydantic for schemas, Uvicorn behind Gunicorn | Existing backend if it can meet the ledger rules | 3, 5 |
| Database and migrations | PostgreSQL with SQLAlchemy and Alembic | Keep the current database only if it enforces unique, check and atomic constraints | 7 |
| Queue and cache | Redis with Celery | Dramatiq or RQ; Temporal later for long workflows | 7 |
| Evidence storage | S3-compatible private storage (AWS S3, Cloudflare R2 or self-hosted MinIO) with versioning, encryption and signed URLs | Azure Blob or Google Cloud Storage | 7, 8 |
| File safety | python-magic for file signatures, ClamAV for malware scanning | Managed scanning service | 1 |
| Image and PDF processing | OpenCV, Pillow, PyMuPDF for rendering PDFs, imagehash for perceptual hashes | scikit-image, pdf2image | 1 |
| OCR (second reader) | PaddleOCR or Tesseract | Azure Document Intelligence, Google Document AI, Amazon Textract | 2 |
| Vision-language reader | A current open-weights VLM (for example the Qwen2.5-VL family) served with vLLM on a GPU server, or a hosted model through an API with masking | Benchmark current releases; both paths behind one adapter | 2 |
| Model adapter and structured output | LiteLLM or a thin in-house adapter; Pydantic or Instructor for schema-checked output | Outlines for constrained generation | 2, 3 |
| Validation helpers | Python Decimal for money, phonenumbers, python-dateutil, a currency table | py-moneyed | 3 |
| Statement and alert parsing | pandas or polars for CSV, pdfplumber or Camelot for PDF tables, IMAP or mailbox API for alert emails, a small Android SMS forwarder for merchant alerts | Existing SMS-forwarding apps | 4 |
| Provider and gateway integration | One adapter per provider, HMAC signature checks, idempotency keys | Official provider SDKs where offered | 4 |
| WhatsApp intake | WhatsApp Business Platform (Cloud API) | Web upload and email only for the MVP | 1 |
| Rules and policy | Versioned Python policy modules with Pydantic config | Open Policy Agent if rules grow complex | 5, 6 |
| Risk and classifier models | scikit-learn, LightGBM or XGBoost | Add after shadow mode | 6 |
| Labelling and fine-tuning | Label Studio for corrections, Hugging Face Transformers with PEFT (LoRA), MLflow for experiments, DVC for dataset versions | Weights and Biases | 2, 8 |
| Authentication | Existing Cognify identity, OIDC, TOTP-based MFA for finance roles, server-side RBAC | Managed identity provider | 6, 8 |
| Secrets | Cloud secrets manager or HashiCorp Vault | SOPS for small setups | 7 |
| Observability | OpenTelemetry, Prometheus with Grafana, Sentry, structured logging with structlog | ELK or Loki | 8 |
| Testing | pytest, Hypothesis for ledger invariants, Testcontainers, Playwright, contract tests, k6 or Locust for load | Schemathesis for API fuzzing | all |
| Delivery | Docker, GitHub Actions, feature flags with Unleash or GrowthBook | Terraform for infrastructure as code | 8 |

### 13.1 MVP starter configuration

FastAPI, PostgreSQL, Redis with Celery, S3-compatible storage, OpenCV with PaddleOCR, one VLM reached through the adapter (hosted with masking, or self-hosted once the benchmark says so), React reviewer console, and OpenTelemetry with Sentry from day one.

### 13.2 Defer until needed

- Kubernetes and a dedicated workflow engine until one server and Celery cannot cope.
- A separate rules engine until the policy modules become hard to maintain.
- Reader fine-tuning until the section 12.1 conditions are met.
- Provider-specific connectors until production access is confirmed in writing.

## Appendix A. POP types and verification profiles

From the Cognify design document. Classify first, then apply the type's profile; a claim may hold several evidence types, and one transaction may be allocated across several invoices. Without the confirming source in the third column, every type stays at evidence tier E1.

| POP type | Fields and checks | Strongest practical confirmation | Default handling |
| --- | --- | --- | --- |
| Mobile money receipt or SMS | Provider, transaction ID, amount, currency, sender, merchant, time, success status | Provider API or merchant portal record; matching statement entry | Receipt alone stays unconfirmed; an exact trusted match can qualify |
| Bank transfer screenshot | Bank, reference, debit amount, payer, beneficiary account, time, status | Incoming credit on the recipient's statement or a bank API | Initiated or pending is not settled |
| Bank statement PDF or CSV | Period, transaction and value dates, narrative, debit or credit, amount, running balance, reference | Provenance plus a matching credit line; stronger through an authorised import | Check duplicate rows, balances and direction |
| Gateway receipt | Gateway transaction ID, order reference, status, amount, fees, refund or chargeback state | Server-side lookup or signed webhook | Ignore the customer's screenshot when a lookup exists |
| POS or card slip | Terminal, masked card, authorisation code, amount, sale or refund | Acquirer settlement report or processor record | Authorisation is not settlement; check voids and reversals |
| Cash deposit slip | Bank, branch, reference, depositor, account credited, amount | Credit visible on the recipient's statement | A stamped slip is supporting evidence only |
| Cheque deposit | Cheque number, drawee bank, payee, amount, deposit date, clearing status | Statement entry after clearance | Hold until cleared; handle returned cheques |
| Wallet-to-wallet transfer | Provider, sender and recipient wallets, transaction ID, amount, status | Provider record or recipient wallet statement | Confirm recipient identity and settled status |
| International transfer or remittance | UETR or provider reference, parties, value, fees, status | Recipient-bank credit or authorised remittance status | A transfer instruction or MT103 copy alone may not prove beneficiary credit |
| Bulk employer or organisation payment | Payer, batch reference, total, remittance advice, learners included | Bank credit plus an itemised remittance or approved allocation schedule | Reconcile batch total, then line items; prevent over-allocation |
| Split or combined payment | Several transaction IDs and amounts, intended invoices | Each component confirmed separately | Track separately; allocate only confirmed amounts |
| Third-party or sponsor payment | Actual payer, learner or invoice reference, consent if required | Trusted confirmation plus invoice match | Payer name may legitimately differ; apply policy, not name equality |
| Email, WhatsApp or SMS notice | Source, text, reference, amount, recipient, links or attachments | Authenticated provider data or recipient-side statement | Untrusted until corroborated |
| Handwritten or manual receipt | Receipt number, issuer, date, amount, signature or stamp | Cashier reconciliation, receipt book, deposit evidence or authorised finance confirmation | Human review normally required; record the verifier |

## Appendix B. Status model and matching signals

### B.1 Claim states

| State | Use |
| --- | --- |
| RECEIVED | Claim and evidence received; no conclusion yet |
| PROCESSING | Validation, extraction, classification and checks running |
| NEEDS\_INFORMATION | Essential fields or a clearer copy required |
| DOCUMENT\_VERIFIED | Readable and internally consistent; funds not necessarily confirmed (evidence tier E1) |
| AWAITING\_FINANCIAL\_CONFIRMATION | Waiting for a trusted match or authorised manual verification |
| FINANCIALLY\_CONFIRMED | A trusted source confirms the outcome and required fields match (E2 or E3) |
| PARTIALLY\_ALLOCATED | Only part of the confirmed amount is allocated, or the invoice remains partly unpaid |
| PENDING\_REVIEW | A reviewer must resolve a mismatch, risk flag or policy exception |
| APPROVED | Evidence and rules pass; allocation committed |
| REJECTED | Rejected with a required reason code and evidence trail |
| DUPLICATE\_HOLD | Possible duplicate; no credit posted until resolved |
| REVERSED\_OR\_REFUNDED | A later event reversed or refunded a recorded transaction |

### B.2 Matching signals

| Signal | Treatment |
| --- | --- |
| Exact provider transaction ID or bank reference | Strong match; must be unique and not already consumed |
| Exact amount and currency | Required for full allocation unless an explicit FX or fee rule allows a difference |
| Recipient, merchant or account | Mandatory for automatic approval, using configured aliases and masked identifiers |
| Successful or settled status | Mandatory where the source gives a status; pending, failed, voided or reversed is not payment |
| Date and time window | Configurable tolerance for timezone, delayed posting and value date; outside it triggers review |
| Payer identity | Supporting signal only; sponsor, parent and organisation payments are allowed under policy |
| Reference similarity | Finds candidates; fuzzy matching alone never approves |
| Statement narrative similarity | Supporting signal; narratives are not unique |
| Already-allocated amount | Hard constraint: never allocate more than the confirmed, unallocated amount |

The original document's illustrative score is kept here for ranking only, applied after the hard gates above have passed; it never replaces a gate. Tune the weights on labelled data.

```latex
S = 0.35R + 0.25A + 0.20B + 0.10T + 0.10P
```

R is reference match, A amount and currency, B beneficiary, T date window and P payer consistency, each normalised to 0 to 1.

## Appendix C. Technology landscape and recommended stack

No single product proves every externally made payment. The strongest design combines document intelligence, provider and bank evidence, reconciliation patterns, fraud controls and an approval workflow. The rows below come from the Cognify design document; confirm vendor features and regional availability before committing.

### C.1 Existing technologies

| Technology | Contribution | Limit or role in Cognify |
| --- | --- | --- |
| Azure AI Document Intelligence and Content Understanding | OCR, layout, key-value and receipt extraction | Extraction does not prove settlement; confirm service and version |
| Google Cloud Document AI | Managed OCR and structured processing | Evaluate processors, regional availability, data residency and cost |
| Amazon Textract | Text, table and expense extraction | Useful OCR layer; not a transaction verification service |
| Tesseract, PaddleOCR and OpenCV | Open-source OCR, preprocessing and image similarity | Less vendor dependency; hard images need tuning and review |
| Vision-language model with structured output | Primary reader in this blueprint; classifies unfamiliar POPs and explains discrepancies | Probabilistic; always behind schema validation and deterministic rules |
| EcoCash developer portal APIs | Online payment status and notifications where production access exists | The portal labels Transaction Lookup as sandbox-only; verify production scope before designing around it |
| Gateway APIs and signed webhooks | Server-side status, order references, refund and chargeback state | Only for transactions processed through that provider |
| Bank feeds, statement imports and reconciliation tools | Match incoming credits to claims and invoices | Direct feeds vary by bank; CSV or PDF import is the practical first release |
| Workflow and case-management engines | Queues, SLA timers, escalation, reviewer assignment | Cognify stays the system of record for allocations and entitlements |
| Fraud and risk platforms | Velocity checks, entity linking, duplicate patterns | Many target card payments rather than uploaded POPs; test fit first |
| Accounting and ERP reconciliation products | Bank reconciliation, payment matching, exception queues | Integrate where APIs fit; do not duplicate ledger truth |

### C.2 Recommended stack

A target stack, not a claim about what Cognify already runs; confirm after repository inspection. Section 13 gives the detailed tool-by-tool version.

| Layer | Recommended option | Note |
| --- | --- | --- |
| Integration | Existing Cognify backend and frontend, with modular boundaries | Inspect the runtime, database, payment modules and deployment first |
| Backend and APIs | Python with FastAPI, REST and Pydantic schemas if consistent with Cognify's backend | Typed validation and async provider calls |
| Financial ledger | PostgreSQL for transactions, allocations, constraints and audit relations | Keep the existing database only if it can enforce atomicity and uniqueness; never migrate blindly |
| Evidence storage | Private S3-compatible object storage with versioning and signed access; metadata in the application database | Store checksums and immutable references |
| Extraction | Self-hosted or API VLM plus OCR (Azure, Google, Textract, Tesseract or PaddleOCR) | Choose by benchmark on real Zimbabwean POPs, not by vendor |
| Rules and risk | Versioned Python policy service; a dedicated rules engine only if complexity warrants | Deterministic gates, reason codes, reproducible decisions |
| Background jobs | Celery or RQ with Redis, or existing queue infrastructure | Retries and idempotency on every job |
| Provider integration | One adapter per bank, mobile money provider and gateway, with signed webhook handlers | Normalise to one transaction schema |
| Frontend | Existing React UI: review queue, evidence viewer, side-by-side comparison, policy settings | Reuse the design system and role model |
| Authentication | Cognify identity and RBAC, server-side organisation scoping, MFA for finance roles | No client-side-only permissions; separate submitter, reviewer, approver and Super Admin |
| Observability | Structured logs, metrics, tracing and alerts (OpenTelemetry-compatible) | Watch latency, extraction failures, queue depth, duplicate rates, approval outcomes |
| Testing and rollout | Unit, integration, contract, security, load and replay tests; feature flags | Sandbox, then shadow mode, then pilot, then controlled automation |

### C.3 Sources

These links were cited in the Cognify design document and have not been re-opened for this blueprint.

- [Azure Document Intelligence Read and OCR](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept-read)
- [Document Intelligence model overview](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/model-overview)
- [Google Cloud Document AI documentation](https://docs.cloud.google.com/document-ai/docs)
- [Amazon Textract API reference](https://docs.aws.amazon.com/textract/latest/APIReference/)
- [EcoCash developer portal: getting started](https://developers.ecocash.co.zw/docs)
- [EcoCash developer portal: online payments](https://developers.ecocash.co.zw/docs/online-payments)
- [EcoCash developer portal: transaction lookup](https://developers.ecocash.co.zw/docs/transaction-lookup)

## Appendix D. Service modules and full data model

The blueprint recommends building these as modules inside one deployable application first (see section 4); split a module into its own service only when scale or ownership requires it.

### D.1 Modules

| Module | Responsibility | Key controls |
| --- | --- | --- |
| pop\_intake\_service | Create claim, validate upload, store evidence metadata | File validation, malware scan, rate limits, tenant scope |
| document\_intelligence\_service | Preprocess, classify, extract fields | Field confidence, model and version trace, prompt-injection resistance |
| duplicate\_detection\_service | File hashes, perceptual similarity, reference uniqueness | Cross-claim detection under privacy and tenant rules; review for fuzzy duplicates |
| provider\_connector\_service | Query supported APIs; validate signed callbacks | Credential isolation, signature checks, idempotency, timeouts |
| statement\_import\_service | Parse authorised CSV, PDF or XLSX exports into normalised rows | Import provenance, file hash, duplicate-import detection, control totals |
| reconciliation\_service | Find candidates; compute available and unallocated amounts | Hard constraints, no over-allocation, currency-aware matching |
| risk\_engine\_service | Apply versioned rules and risk indicators | Explainable reason codes; AI never overrides a hard block |
| approval\_workflow\_service | Queue, assign, approve, reject, request evidence, escalate | Separation of duties, reviewer permissions, mandatory reasons |
| payment\_ledger\_service | Post allocations, refunds, reversals, corrections | Atomic writes, unique keys, append-only financial events |
| cognify\_entitlement\_adapter | Update course payment status and access rules | Idempotent events; course and subscription policy kept separate |
| notification\_service | Email, SMS, WhatsApp and in-app notices | Template control, delivery tracking, no sensitive data in message bodies |
| audit\_service | Who, what, when, why and evidence references | Append-only, restricted access, retention policy |

### D.2 Additional entities

Section 4.2 lists the core entities. These complete the model.

| Entity | Key fields and purpose |
| --- | --- |
| verification\_runs | run\_id, claim\_id, pipeline version, model versions, checks run, candidate matches, risk flags, outcome, timestamp |
| approval\_decisions | decision\_id, claim\_id, decision, reason codes, actor type and ID, policy version, evidence snapshot references |
| payment\_policies | policy\_id, scope, effective dates, required evidence, amount limits, currency rules, date windows, risk thresholds, auto-approval eligibility |
| statement\_imports | import\_id, account alias, source, statement period, file hash, imported\_by, row count, control totals, reconciliation status |

Store money as fixed-precision decimals or integer minor units with explicit currency, never binary floating point. Enforce uniqueness on provider plus transaction ID and other natural keys, and make allocation writes atomic and idempotent.

## Appendix E. Cognify billing rules, review, audit and security

### E.1 Cognify business integration

- **Course fees and instalments.** Payment confirmation and course eligibility are separate. Allocate a confirmed payment to the right invoice or instalment, compute balances from posted allocations and the course billing policy, and grant assessment access by that policy (for example paid in full, or current on instalments), never because a POP was uploaded.
- **Platform subscriptions and usage.** Keep subscription invoices, included usage allowances, overage charges and course fees as separate obligation types. One credit may be split across obligations only under explicit allocation instructions and never beyond the confirmed, unallocated amount.
- **Organisation and bulk payments.** Let a finance user attach a remittance schedule and map line items to learner invoices. Reconcile the credit to the batch first, then allocate lines; if totals differ, leave the difference unapplied and send the batch to review.
- **Reversals and refunds.** A later refund, reversal or chargeback adds a compensating event and may reduce the paid balance. Recalculate access under policy; never delete the original approval or rewrite history.

### E.2 Human review and audit

- Reviewers see the original evidence, extracted fields, candidate credits, mismatch reasons, rule results and prior claims with restricted visibility.
- Reviewers cannot change extracted values invisibly: a correction keeps the original value, the new value, the actor, the time and the reason.
- A reviewer must not approve their own manually recorded transaction where separation-of-duties rules forbid it.
- High-value approvals, overrides, refunds and reversals can require a second approver.
- Audit events carry actor, action, previous and new state, timestamp, policy version, reason, correlation ID and evidence references.
- A rejected claim is never deleted; the claimant can submit new evidence or appeal through a defined process.

### E.3 Security and compliance checklist

- Encrypt data in transit and at rest; keep credentials in a secrets manager and rotate them.
- Use private storage with short-lived signed URLs, malware scanning, strict content-type checks and retention rules.
- Apply organisation-level access checks in every API and database query, and test for cross-tenant leakage.
- Treat OCR text, receipt metadata, email bodies and QR contents as untrusted; never execute links, macros or embedded instructions.
- Verify webhook signatures, timestamps and replay protection; process callbacks idempotently.
- Mask account and phone numbers in the reviewer interface unless the full value is needed.
- Define retention, access, correction, export and deletion processes that fit Zimbabwean law and your contracts.
- Log security events without secrets, full account numbers or raw document contents.

## Appendix F. Developer guardrails and test strategy

### F.1 Guardrails for developers and build tools

1. Inspect the current repository and document the existing architecture before changing files; reuse sound services and UI patterns.
2. Do not hardcode provider names, currencies, fee amounts, course or organisation IDs, confidence thresholds or approval limits; use configuration records and versioned policies.
3. Do not replace or migrate the current database without a migration plan, a tested backup and restore, and reconciliation of historical balances.
4. Keep OCR, VLM and LLM providers behind adapters so a vendor can change without rewriting business logic.
5. Make every asynchronous operation idempotent and safe to retry; carry a correlation ID from upload through extraction, provider query, decision and ledger posting.
6. Keep document confidence, source reliability, transaction confirmation, fraud risk and business approval as separate fields.
7. Enforce approval, allocation and entitlement changes on the server; the frontend is never authoritative.
8. Write tests for every hard gate, status transition, duplicate path, currency rule, organisation boundary and failure or retry scenario.
9. Launch in shadow or review-only mode; turn on automatic approval only through an explicit feature flag and an approved policy.

### F.2 Test strategy

| Test group | Examples and pass criteria |
| --- | --- |
| Document types | Clear and blurred screenshots, rotated photos, PDFs, multipage statements, low light, many receipt layouts, mixed languages and formats |
| Duplicate and replay | Same file twice, cropped or edited copy, same reference across claims, duplicate webhook, repeated statement import; no duplicate credit |
| Financial correctness | Exact match, partial, split, bulk, overpayment, wrong currency, fees, wrong beneficiary, failed, pending, reversed and refunded transactions |
| Identity and organisation | Parent pays for learner, organisation pays for many learners, same-name ambiguity, tenant boundary attempts |
| Failure handling | Extraction service down, bank API timeout, delayed webhook, queue and database retries, notification failure; no duplicate posting and a clear retry state |
| Decision quality | Precision and recall for extraction, duplicate detection and risk flags; auto-approval false positives and false holds on labelled data |
| Audit and permissions | Every approval, rejection and override traceable; unauthorised users cannot approve, read another organisation's evidence or change the ledger |
| Performance | Concurrent uploads, large statements, queue throughput, review dashboard pagination |

**Further acceptance checks:**

| Test area | Acceptance criterion |
| --- | --- |
| File validation | Unsupported, oversized or invalid files are rejected safely |
| Concurrent submissions | Simultaneous processing cannot allocate the same funds twice |
| Partial payment | Only the confirmed amount is allocated |
| Refunds and reversals | Financial history is preserved and balances are recalculated |
| Assessment gating | Access changes only when the authoritative billing rules permit it |

### F.3 Release gates

- Every posted allocation has a unique transaction and ledger path and passes the hard constraints.
- No duplicate credit appears in any replay test.
- Every automated approval can be reproduced from the recorded evidence and policy version.
- An ambiguous, receipt-only claim is never labelled financially confirmed.
