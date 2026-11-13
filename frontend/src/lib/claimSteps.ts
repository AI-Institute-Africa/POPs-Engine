// Pure mapping from a claim's server state to what a payer sees: a six-step timeline, one
// "next step" panel and how often to poll. Payers never see risk, tamper or duplicate detail:
// anything suspicious reads as "a person is checking".

export const STEP_KEYS = ["uploaded", "checked", "read", "matched", "reviewed", "decided"] as const;
export type StepKey = (typeof STEP_KEYS)[number];
export type StepStatus = "done" | "current" | "failed" | "pending";

export type PanelKey =
  | "checking"
  | "waiting_money"
  | "under_review"
  | "needs_info"
  | "approved"
  | "part_paid"
  | "rejected"
  | "reversed";

export type PanelTone = "info" | "warn" | "ok" | "bad" | "neutral";

export type ClaimProgress = {
  steps: { key: StepKey; status: StepStatus }[];
  panel: PanelKey;
  tone: PanelTone;
  final: boolean;
  /** Milliseconds between polls, or false once nothing more will change. */
  pollMs: number | false;
};

type Spec = { done: number; current?: StepKey; failed?: StepKey; panel: PanelKey; tone: PanelTone; final: boolean; pollMs: number | false };

// done = how many leading steps are complete
const SPECS: Record<string, Spec> = {
  RECEIVED: { done: 1, current: "checked", panel: "checking", tone: "info", final: false, pollMs: 3000 },
  PROCESSING: { done: 2, current: "read", panel: "checking", tone: "info", final: false, pollMs: 3000 },
  NEEDS_INFORMATION: { done: 1, failed: "checked", panel: "needs_info", tone: "warn", final: false, pollMs: 30000 },
  DOCUMENT_VERIFIED: { done: 3, current: "matched", panel: "checking", tone: "info", final: false, pollMs: 3000 },
  AWAITING_FINANCIAL_CONFIRMATION: { done: 3, current: "matched", panel: "waiting_money", tone: "info", final: false, pollMs: 15000 },
  FINANCIALLY_CONFIRMED: { done: 4, current: "reviewed", panel: "under_review", tone: "info", final: false, pollMs: 15000 },
  PENDING_REVIEW: { done: 4, current: "reviewed", panel: "under_review", tone: "warn", final: false, pollMs: 15000 },
  // held as a possible duplicate: the payer is only told a person is checking
  DUPLICATE_HOLD: { done: 4, current: "reviewed", panel: "under_review", tone: "warn", final: false, pollMs: 15000 },
  PARTIALLY_ALLOCATED: { done: 5, current: "decided", panel: "part_paid", tone: "info", final: false, pollMs: 15000 },
  APPROVED: { done: 6, panel: "approved", tone: "ok", final: true, pollMs: false },
  REJECTED: { done: 5, failed: "decided", panel: "rejected", tone: "bad", final: true, pollMs: false },
  REVERSED_OR_REFUNDED: { done: 6, panel: "reversed", tone: "neutral", final: true, pollMs: false },
};

const FALLBACK: Spec = SPECS.PENDING_REVIEW;

export function claimProgress(state: string | null | undefined): ClaimProgress {
  const spec = SPECS[(state ?? "").toUpperCase()] ?? FALLBACK;
  const steps = STEP_KEYS.map((key, i) => {
    let status: StepStatus = "pending";
    if (spec.failed === key) status = "failed";
    else if (i < spec.done) status = "done";
    else if (spec.current === key) status = "current";
    return { key, status };
  });
  return { steps, panel: spec.panel, tone: spec.tone, final: spec.final, pollMs: spec.pollMs };
}

/** Payer-safe wording for a rejection: never says why, never hints at fraud. */
export const NEUTRAL_REJECTION = "We couldn't confirm this payment. You can send a new proof or contact the organisation.";
