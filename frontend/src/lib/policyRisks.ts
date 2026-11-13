// Plain-language consequences of a policy edit. Only settings being switched ON or loosened
// are listed; tightening never needs a warning. Pure and dependency-free so it is unit-tested.
//
// Every policy version is a full snapshot, so "before" is the active config laid over the
// engine defaults (mirrored below from PolicyConfig in backend/app/policy/engine.py).

export type Severity = "danger" | "warning" | "info";
export type Risk = { key: string; severity: Severity; text: string };
type Cfg = Record<string, unknown>;

export const DEFAULTS: Cfg = {
  auto_approve_enabled: false,
  auto_approve_max_minor: { USD: 5000 },
  auto_approve_min_tier: "E2",
  auto_approve_max_risk: 15,
  auto_approve_audit_pct: 5,
  ai_temp_approve_enabled: false,
  ai_temp_approve_disabled: false,
  ai_temp_block_on_tamper: true,
  ai_temp_audit_pct: 100,
  ai_temp_max_risk: 10,
  kyc_auto_verify_mode: "off",
  kyc_required_for_auto_approve: false,
  kyc_required_for_staff: false,
  provisional_enabled: false,
  provisional_max_minor: { USD: 5000 },
  amount_tolerance_minor: 0,
  date_window_hours: 72,
  phash_duplicate_distance: 6,
  single_reader_min_confidence: 0.97,
  max_low_confidence_fields: null,
  tamper_enabled: true,
  tamper_review_score: 0.6,
  second_approver_above_minor: { USD: 50000 },
  fraud_high_score: 60,
};

const num = (v: unknown): number => (typeof v === "number" ? v : Number.NaN);
const map = (v: unknown): Record<string, number> => (v && typeof v === "object" ? (v as Record<string, number>) : {});

function raisedCaps(before: unknown, after: unknown): string[] {
  const b = map(before);
  const a = map(after);
  return Object.keys(a).filter((cur) => !(cur in b) || a[cur] > b[cur]);
}

type Rule = (before: unknown, after: unknown) => Omit<Risk, "key"> | null;

const turnedOn = (danger: string): Rule => (b, a) => (a === true && b !== true ? { severity: "danger", text: danger } : null);
const turnedOff = (severity: Severity, text: string): Rule => (b, a) => (a === false && b !== false ? { severity, text } : null);
const raised = (severity: Severity, text: (n: number) => string): Rule => (b, a) => (num(a) > num(b) ? { severity, text: text(num(a)) } : null);
const lowered = (severity: Severity, text: (n: number) => string): Rule => (b, a) => (num(a) < num(b) ? { severity, text: text(num(a)) } : null);
const capsRaised = (severity: Severity, text: (cur: string[]) => string): Rule => (b, a) => {
  const cur = raisedCaps(b, a);
  return cur.length ? { severity, text: text(cur) } : null;
};

const RULES: Record<string, Rule> = {
  auto_approve_enabled: turnedOn("Switches ON automatic approval. Claims that pass every rule will be approved with no person looking at them (apart from the audit sample)."),
  auto_approve_max_minor: capsRaised("warning", (c) => `Raises the automatic-approval cap for ${c.join(", ")}.`),
  auto_approve_min_tier: (b, a) => (b === "E3" && a === "E2" ? { severity: "warning", text: "Automatic approval now accepts a merchant alert (E2), not only a bank statement line (E3)." } : null),
  auto_approve_max_risk: raised("warning", (n) => `Automatic approval now tolerates a fake-receipt score up to ${n}.`),
  auto_approve_audit_pct: (b, a) =>
    num(a) === 0 && num(b) !== 0
      ? { severity: "danger", text: "Turns the human audit sample of auto-approved claims OFF." }
      : num(a) < num(b)
        ? { severity: "warning", text: `Fewer auto-approved claims will be audited (${num(a)}%).` }
        : null,
  ai_temp_approve_enabled: turnedOn("Switches ON AI temporary approval: the model's recognition can grant temporary approval before a person looks."),
  ai_temp_approve_disabled: turnedOff("danger", "Clears the AI temporary-approval kill switch for this organisation."),
  ai_temp_block_on_tamper: turnedOff("danger", "AI temporary approval will no longer stop on suspected image tampering."),
  ai_temp_audit_pct: lowered("warning", (n) => `Fewer AI temporary approvals will be audited (${n}%).`),
  ai_temp_max_risk: raised("warning", (n) => `AI temporary approval now tolerates a fake-receipt score up to ${n}.`),
  kyc_auto_verify_mode: (b, a) =>
    a === "on" && b !== "on"
      ? { severity: "danger", text: "Switches ON automatic identity verification: clear cases are verified without a person." }
      : a === "shadow" && b === "off"
        ? { severity: "info", text: "Identity rules will run in shadow mode: recorded, but changing nothing." }
        : null,
  kyc_required_for_auto_approve: turnedOff("warning", "Automatic approval no longer requires the payer to hold a valid identity check."),
  kyc_required_for_staff: turnedOff("warning", "Approver and admin roles no longer require an identity-verified user."),
  provisional_enabled: turnedOn("Switches ON provisional access: payers get time-boxed access before the money is confirmed. This is money at risk."),
  provisional_max_minor: capsRaised("warning", (c) => `Raises the provisional-access cap for ${c.join(", ")}.`),
  amount_tolerance_minor: raised("warning", (n) => `Payments may now differ from the invoice by up to ${n} minor units and still match.`),
  date_window_hours: raised("info", (n) => `A credit may now be up to ${n} hours from the receipt date and still match.`),
  phash_duplicate_distance: lowered("warning", (n) => `Near-duplicate image detection is stricter-to-trigger (distance ${n}); fewer copies will be caught.`),
  single_reader_min_confidence: lowered("warning", (n) => `One reader alone is now trusted at ${(n * 100).toFixed(0)}% confidence.`),
  max_low_confidence_fields: (b, a) =>
    typeof b === "number" && a === null
      ? { severity: "warning", text: "Turns OFF the rule that sends receipts with many shaky fields to a person." }
      : typeof b === "number" && typeof a === "number" && a > b
        ? { severity: "warning", text: `Receipts may now have up to ${a} shaky fields before a person is asked.` }
        : null,
  tamper_enabled: turnedOff("danger", "Turns OFF image-tampering forensics. Edited receipts will no longer be flagged."),
  tamper_review_score: raised("warning", (n) => `Image tampering is flagged less often (threshold ${n}).`),
  second_approver_above_minor: capsRaised("warning", (c) => `A second approver is only required above a higher amount for ${c.join(", ")}.`),
  fraud_high_score: raised("warning", (n) => `A claim must score ${n} before it counts as a suspected fake.`),
};

export function describePolicyRisks(active: Cfg, draft: Cfg, defaults: Cfg = DEFAULTS): Risk[] {
  const before = { ...defaults, ...active };
  const after = { ...defaults, ...draft };
  const out: Risk[] = [];
  for (const [key, rule] of Object.entries(RULES)) {
    if (JSON.stringify(before[key]) === JSON.stringify(after[key])) continue;
    const r = rule(before[key], after[key]);
    if (r) out.push({ key, ...r });
  }
  const order: Record<Severity, number> = { danger: 0, warning: 1, info: 2 };
  return out.sort((x, y) => order[x.severity] - order[y.severity]);
}

export const needsAcknowledgement = (risks: Risk[]): boolean => risks.some((r) => r.severity !== "info");
