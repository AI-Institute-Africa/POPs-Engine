import { describe, expect, it } from "vitest";
import { parityProblems, translate, translationActive, type Lang } from "./i18nGate";

const dicts: Record<Lang, Record<string, string>> = { en: { a: "Hello", b: "Only EN" }, sn: { a: "Mhoro" }, nd: { a: "Sawubona" } };
const reviewed = { en: true, sn: false, nd: true };

describe("translation gate", () => {
  it("falls back to English for an unreviewed language", () => {
    expect(translate("sn", "a", dicts, false, reviewed)).toBe("Hello");
  });
  it("uses a reviewed language, and falls back per missing key", () => {
    expect(translate("nd", "a", dicts, false, reviewed)).toBe("Sawubona");
    expect(translate("nd", "b", dicts, false, reviewed)).toBe("Only EN");
  });
  it("lets staging opt in to drafts", () => {
    expect(translate("sn", "a", dicts, true, reviewed)).toBe("Mhoro");
    expect(translationActive("sn", true, reviewed)).toBe(true);
  });
  it("returns the key when nothing matches", () => expect(translate("en", "zzz", dicts, false)).toBe("zzz"));
});

describe("parityProblems", () => {
  it("reports missing keys and placeholder mismatches", () => {
    expect(parityProblems({ x: "Hi {name}", y: "Yo" }, { x: "Mhoro" })).toEqual(["placeholders differ: x", "missing: y"]);
  });
  it("is empty when in step", () => expect(parityProblems({ x: "Hi {name}" }, { x: "Mhoro {name}" })).toEqual([]));
});
