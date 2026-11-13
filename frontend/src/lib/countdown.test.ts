import { describe, expect, it } from "vitest";
import { timeLeft } from "./countdown";

const now = Date.parse("2026-01-01T00:00:00Z");

describe("timeLeft", () => {
  it("returns null when expired or invalid", () => {
    expect(timeLeft("2025-12-31T00:00:00Z", now)).toBeNull();
    expect(timeLeft("nope", now)).toBeNull();
  });
  it("rounds down and formats days", () => {
    expect(timeLeft("2026-01-03T05:59:59Z", now)?.label).toBe("2d 5h");
  });
  it("is urgent under 24 hours", () => {
    const t = timeLeft("2026-01-01T10:30:00Z", now);
    expect(t?.urgent).toBe(true);
    expect(t?.label).toBe("10h 30m");
  });
  it("never shows 0m while time remains", () => {
    expect(timeLeft("2026-01-01T00:00:20Z", now)?.label).toBe("1m");
  });
});
