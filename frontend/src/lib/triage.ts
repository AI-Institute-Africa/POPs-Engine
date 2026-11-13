// Queue triage: split claims into "flagged" (do not approve as it stands) and "needs review",
// and show how long each has waited. Pure, so the rules are testable.

import type { Claim } from "../api";

// Mirrors RED_REASONS in backend/app/policy/engine.py plus fake-receipt signals.
export const FLAG_REASONS = new Set([
  "DUPLICATE_FILE",
  "DUPLICATE_NEAR_FILE",
  "DUPLICATE_REFERENCE",
  "REFERENCE_ALREADY_CONSUMED",
  "CREDIT_NOT_SUCCESS",
  "WRONG_BENEFICIARY",
  "CREDIT_FULLY_ALLOCATED",
  "CURRENCY_MISMATCH",
  "AMOUNT_MISMATCH",
  "SUSPECTED_FAKE",
  "TAMPER_SUSPECTED",
]);

export type Bucket = "needs_review" | "flagged";

export function bucketOf(c: Pick<Claim, "approval_tier" | "risk_level" | "reason_codes" | "state">): Bucket {
  if (c.state === "DUPLICATE_HOLD") return "flagged";
  if ((c.approval_tier ?? "").toUpperCase() === "RED") return "flagged";
  if ((c.risk_level ?? "").toLowerCase() === "high") return "flagged";
  if (c.reason_codes.some((r) => FLAG_REASONS.has(r))) return "flagged";
  return "needs_review";
}

// Same default reminder ladder as backend review/aging.py (minutes).
export const SLA_MINUTES = [60, 240, 1440] as const;

export type Sla = { level: 0 | 1 | 2 | 3; tone: "neutral" | "warn" | "bad" };

export function slaOf(createdIso: string, nowMs: number): Sla {
  const mins = (nowMs - Date.parse(createdIso)) / 60000;
  if (!(mins >= 0)) return { level: 0, tone: "neutral" };
  const level = SLA_MINUTES.filter((m) => mins >= m).length as 0 | 1 | 2 | 3;
  return { level, tone: level >= 3 ? "bad" : level >= 1 ? "warn" : "neutral" };
}
