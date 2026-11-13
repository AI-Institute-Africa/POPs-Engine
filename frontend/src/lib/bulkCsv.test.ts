import { describe, expect, it } from "vitest";
import { MAX_ROWS, parseBulk, toMinor } from "./bulkCsv";

describe("toMinor", () => {
  it.each([
    ["12.50", 1250],
    ["12,50", 1250],
    ["1,200.00", 120000],
    ["1.200,00", 120000],
    ["$40", 4000],
    ["0.5", 50],
  ])("%s -> %s", (raw, want) => expect(toMinor(raw)).toBe(want));
  it("rejects zero, negatives and junk", () => {
    expect(toMinor("0")).toBeNull();
    expect(toMinor("-5")).toBeNull();
    expect(toMinor("abc")).toBeNull();
  });
});

describe("parseBulk", () => {
  it("reads a headed comma file in any column order", () => {
    const r = parseBulk("Amount,Code,Name\n10.00,cg-a,Tendai\n5,CG-B,");
    expect(r.errors).toEqual([]);
    expect(r.rows).toEqual([
      { payment_code: "CG-A", amount_minor: 1000, note: "Tendai" },
      { payment_code: "CG-B", amount_minor: 500, note: null },
    ]);
  });
  it("reads semicolons and tabs, and headerless files", () => {
    expect(parseBulk("CG-A;10,00").rows[0].amount_minor).toBe(1000);
    expect(parseBulk("code\tamount\nCG-A\t7").rows[0].amount_minor).toBe(700);
  });
  it("allows a missing amount (the most allowed is used) but reports a bad one", () => {
    const r = parseBulk("code,amount\nCG-A,\nCG-B,abc");
    expect(r.rows).toHaveLength(1);
    expect(r.rows[0].amount_minor).toBeNull();
    expect(r.errors).toHaveLength(1);
  });
  it("handles quoted cells and reports an empty input", () => {
    expect(parseBulk('code,note\nCG-A,"Moyo, T"').rows[0].note).toBe("Moyo, T");
    expect(parseBulk("  \n").errors).toHaveLength(1);
  });
  it("caps rows", () => {
    const big = "code\n" + Array.from({ length: MAX_ROWS + 3 }, (_, i) => `CG-${i}`).join("\n");
    const r = parseBulk(big);
    expect(r.rows).toHaveLength(MAX_ROWS);
    expect(r.errors).toHaveLength(1);
  });
});
