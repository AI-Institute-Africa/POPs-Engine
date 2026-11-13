import { describe, expect, it } from "vitest";
import { bucketOf, slaOf } from "./triage";

const base = { approval_tier: "AMBER", risk_level: "low", reason_codes: [] as string[], state: "PENDING_REVIEW" };

describe("bucketOf", () => {
  it("amber with no flags needs review", () => expect(bucketOf(base)).toBe("needs_review"));
  it("red tier is flagged", () => expect(bucketOf({ ...base, approval_tier: "RED" })).toBe("flagged"));
  it("high risk is flagged", () => expect(bucketOf({ ...base, risk_level: "high" })).toBe("flagged"));
  it("duplicate hold is flagged", () => expect(bucketOf({ ...base, state: "DUPLICATE_HOLD" })).toBe("flagged"));
  it("a red reason code is flagged", () => expect(bucketOf({ ...base, reason_codes: ["AMOUNT_MISMATCH"] })).toBe("flagged"));
  it("an advisory reason is not", () => expect(bucketOf({ ...base, reason_codes: ["AWAITING_CREDIT"] })).toBe("needs_review"));
});

describe("slaOf", () => {
  const now = Date.parse("2026-01-02T00:00:00Z");
  it("is level 0 when fresh", () => expect(slaOf("2026-01-01T23:30:00Z", now).level).toBe(0));
  it("is level 1 after an hour", () => expect(slaOf("2026-01-01T22:30:00Z", now).level).toBe(1));
  it("is level 3 and bad after a day", () => {
    const s = slaOf("2025-12-31T00:00:00Z", now);
    expect(s.level).toBe(3);
    expect(s.tone).toBe("bad");
  });
  it("ignores future timestamps", () => expect(slaOf("2026-02-01T00:00:00Z", now).level).toBe(0));
});
