import { describe, expect, it } from "vitest";
import { DICTS, EN } from "./i18n";
import { parityProblems } from "./lib/i18nGate";

describe("dictionaries", () => {
  for (const lang of ["sn", "nd"] as const) {
    it(`${lang} has no keys English lacks and keeps every placeholder`, () => {
      const extra = Object.keys(DICTS[lang]).filter((k) => !(k in EN));
      expect(extra).toEqual([]);
      // keys not yet translated fall back to English by design; only placeholder drift is an error
      const problems = parityProblems(EN, DICTS[lang]).filter((p) => p.startsWith("placeholders"));
      expect(problems).toEqual([]);
    });
  }
});
