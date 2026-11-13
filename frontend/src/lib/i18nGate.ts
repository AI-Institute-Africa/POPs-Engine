// Draft translations (Shona, Ndebele) are shown only after a fluent reviewer signs them off.
// Until then the English text is used, so a wrong word never reaches a payer. Staging or a
// demo can opt in with VITE_ALLOW_UNREVIEWED_TRANSLATIONS=true or a local switch.

export type Lang = "en" | "sn" | "nd";

export const REVIEWED: Record<Lang, boolean> = { en: true, sn: false, nd: false };

export function translationActive(lang: Lang, allowDrafts: boolean, reviewed: Record<Lang, boolean> = REVIEWED): boolean {
  return lang === "en" || reviewed[lang] || allowDrafts;
}

export function translate(
  lang: Lang,
  key: string,
  dicts: Record<Lang, Record<string, string>>,
  allowDrafts: boolean,
  reviewed: Record<Lang, boolean> = REVIEWED,
): string {
  const active = translationActive(lang, allowDrafts, reviewed) ? lang : "en";
  return dicts[active][key] ?? dicts.en[key] ?? key;
}

/** Keys missing from a language, and keys whose {placeholders} differ from English. */
export function parityProblems(en: Record<string, string>, other: Record<string, string>): string[] {
  const holes = (s: string) => (s.match(/\{[a-z_]+\}/gi) ?? []).sort().join(",");
  const out: string[] = [];
  for (const [k, v] of Object.entries(en)) {
    if (!(k in other)) out.push(`missing: ${k}`);
    else if (holes(v) !== holes(other[k])) out.push(`placeholders differ: ${k}`);
  }
  return out;
}
