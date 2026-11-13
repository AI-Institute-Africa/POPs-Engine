import { describe, expect, it } from "vitest";
import { describePolicyRisks, needsAcknowledgement } from "./policyRisks";

const keys = (a: object, d: object) => describePolicyRisks(a as never, d as never).map((r) => r.key);

describe("describePolicyRisks", () => {
  it("lists nothing when nothing changed", () => expect(keys({}, {})).toEqual([]));
  it("flags switching auto-approval on as danger", () => {
    const r = describePolicyRisks({}, { auto_approve_enabled: true });
    expect(r[0]).toMatchObject({ key: "auto_approve_enabled", severity: "danger" });
    expect(needsAcknowledgement(r)).toBe(true);
  });
  it("never warns about tightening", () => {
    expect(keys({ auto_approve_enabled: true }, { auto_approve_enabled: false })).toEqual([]);
    expect(keys({}, { auto_approve_audit_pct: 50 })).toEqual([]);
    expect(keys({}, { tamper_review_score: 0.3 })).toEqual([]);
  });
  it("treats a missing key as the engine default", () => {
    // active omits tamper_enabled (default true); turning it off is a danger
    expect(keys({ date_window_hours: 72 }, { tamper_enabled: false, date_window_hours: 72 })).toContain("tamper_enabled");
  });
  it("flags a zero audit sample as danger and a reduced one as warning", () => {
    expect(describePolicyRisks({}, { auto_approve_audit_pct: 0 })[0].severity).toBe("danger");
    expect(describePolicyRisks({}, { auto_approve_audit_pct: 1 })[0].severity).toBe("warning");
  });
  it("flags raised or new currency caps only", () => {
    expect(keys({ auto_approve_max_minor: { USD: 100 } }, { auto_approve_max_minor: { USD: 100 } })).toEqual([]);
    expect(keys({ auto_approve_max_minor: { USD: 100 } }, { auto_approve_max_minor: { USD: 200 } })).toEqual(["auto_approve_max_minor"]);
    expect(keys({ auto_approve_max_minor: { USD: 100 } }, { auto_approve_max_minor: { USD: 100, ZWG: 5 } })).toEqual(["auto_approve_max_minor"]);
  });
  it("sorts danger before warning before info and info alone needs no acknowledgement", () => {
    const r = describePolicyRisks({}, { date_window_hours: 96, amount_tolerance_minor: 5, auto_approve_enabled: true });
    expect(r.map((x) => x.severity)).toEqual(["danger", "warning", "info"]);
    expect(needsAcknowledgement(describePolicyRisks({}, { date_window_hours: 96 }))).toBe(false);
  });
  it("flags loosening the low-confidence rule but not enabling it", () => {
    expect(keys({ max_low_confidence_fields: 2 }, { max_low_confidence_fields: null })).toEqual(["max_low_confidence_fields"]);
    expect(keys({ max_low_confidence_fields: 2 }, { max_low_confidence_fields: 4 })).toEqual(["max_low_confidence_fields"]);
    expect(keys({}, { max_low_confidence_fields: 2 })).toEqual([]);
  });
});
