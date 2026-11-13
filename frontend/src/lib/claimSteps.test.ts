import { describe, expect, it } from "vitest";
import { claimProgress, STEP_KEYS } from "./claimSteps";

const status = (state: string) => Object.fromEntries(claimProgress(state).steps.map((s) => [s.key, s.status]));

describe("claimProgress", () => {
  it("always returns the six steps in order", () => {
    expect(claimProgress("APPROVED").steps.map((s) => s.key)).toEqual([...STEP_KEYS]);
  });
  it("is case-insensitive", () => {
    expect(claimProgress("processing").panel).toBe(claimProgress("PROCESSING").panel);
  });
  it("approved is fully done and stops polling", () => {
    const p = claimProgress("APPROVED");
    expect(p.steps.every((s) => s.status === "done")).toBe(true);
    expect(p.final).toBe(true);
    expect(p.pollMs).toBe(false);
  });
  it("rejected fails the last step", () => {
    expect(status("REJECTED").decided).toBe("failed");
    expect(claimProgress("REJECTED").final).toBe(true);
  });
  it("needs-info fails the check step and polls slowly", () => {
    expect(status("NEEDS_INFORMATION").checked).toBe("failed");
    expect(claimProgress("NEEDS_INFORMATION").pollMs).toBe(30000);
  });
  it("a duplicate hold reads as under review, never as a duplicate", () => {
    expect(claimProgress("DUPLICATE_HOLD").panel).toBe("under_review");
  });
  it("processing polls fast, review polls at 15s", () => {
    expect(claimProgress("PROCESSING").pollMs).toBe(3000);
    expect(claimProgress("PENDING_REVIEW").pollMs).toBe(15000);
  });
  it("unknown states are treated as under review", () => {
    expect(claimProgress("SOMETHING_NEW").panel).toBe("under_review");
  });
});
