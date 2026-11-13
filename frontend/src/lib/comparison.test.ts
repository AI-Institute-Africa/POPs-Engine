import { describe, expect, it } from "vitest";
import { compareRows, lowConfidenceCount } from "./comparison";

const fld = (name: string, v: string | null, agreed = true) => ({ name, normalised_value: v, raw_value: v, agreed });
const claim = { declared_amount_minor: 12000, declared_currency: "USD", declared_reference: "TXN-8841" };
const credit = { amount_minor: 12000, currency: "USD", provider_txn_id: "TXN8841", occurred_at: "2026-01-05T10:00:00Z" };
const status = (rows: ReturnType<typeof compareRows>["rows"], k: string) => rows.find((r) => r.key === k)?.status;

describe("compareRows", () => {
  it("matches amount and reference, ignoring punctuation", () => {
    const { rows } = compareRows([fld("amount", "12000"), fld("transaction_id", "TXN8841")], claim, credit);
    expect(status(rows, "amount")).toBe("match");
    expect(status(rows, "reference")).toBe("match");
  });
  it("flags a one-character reference difference as mismatch", () => {
    const { rows } = compareRows([fld("transaction_id", "TXN884I")], claim, credit);
    expect(status(rows, "reference")).toBe("mismatch");
  });
  it("reports a value the readers never read as missing", () => {
    expect(status(compareRows([], claim, credit).rows, "amount")).toBe("missing");
  });
  it("is a note, not a match, when readers disagreed", () => {
    expect(status(compareRows([fld("amount", "12000", false)], claim, credit).rows, "amount")).toBe("note");
  });
  it("says not_compared with no credit and no typed value, and lists what was not checked", () => {
    const bare = { declared_amount_minor: null, declared_currency: null, declared_reference: null };
    const r = compareRows([fld("amount", "100"), fld("date", "2026-01-05T00:00:00Z")], bare, null);
    expect(status(r.rows, "amount")).toBe("not_compared");
    expect(status(r.rows, "date")).toBe("not_compared");
    expect(r.notPerformed).toHaveLength(3);
  });
  it("compares the date part only", () => {
    expect(status(compareRows([fld("date", "2026-01-05T00:00:00Z")], claim, credit).rows, "date")).toBe("match");
    expect(status(compareRows([fld("date", "2026-01-06T00:00:00Z")], claim, credit).rows, "date")).toBe("mismatch");
  });
});

describe("lowConfidenceCount", () => {
  it("counts unagreed or low-confidence fields, not reviewer-corrected ones", () => {
    const f = [
      { agreed: true, confidence: 0.9, corrected_by: null },
      { agreed: false, confidence: 0.9, corrected_by: null },
      { agreed: true, confidence: 0.5, corrected_by: null },
      { agreed: false, confidence: 0.1, corrected_by: "rev" },
    ];
    expect(lowConfidenceCount(f)).toBe(2);
  });
});
