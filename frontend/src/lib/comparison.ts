// Receipt-versus-record comparison for the reviewer. One row per value, each with a status that
// says what was and was not checked, so the screen never implies more verification than happened:
//   match | mismatch | missing (not read from the receipt) | not_compared (nothing to compare with)
//   | note (read, but the readers disagreed, so it is soft evidence)

export type CompareStatus = "match" | "mismatch" | "missing" | "not_compared" | "note";
export type CompareRow = { key: string; label: string; observed: string | null; expected: string | null; status: CompareStatus; detail?: string };

export type FieldIn = { name: string; normalised_value: string | null; raw_value: string | null; agreed: boolean };
export type CreditIn = { amount_minor: number; currency: string; provider_txn_id: string; occurred_at: string };
export type ClaimIn = { declared_amount_minor: number | null; declared_currency: string | null; declared_reference: string | null };

const ref = (s: string | null | undefined) => (s ?? "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();

function latest(fields: FieldIn[]): Map<string, FieldIn> {
  const m = new Map<string, FieldIn>();
  fields.forEach((f) => m.set(f.name, f));
  return m;
}

function row(key: string, label: string, observedField: FieldIn | undefined, observed: string | null, expected: string | null, same: (a: string, b: string) => boolean): CompareRow {
  if (!observedField || observed === null) return { key, label, observed: null, expected, status: "missing", detail: "Not read from the receipt." };
  if (expected === null) return { key, label, observed, expected, status: "not_compared", detail: "Nothing to compare it with." };
  if (!same(observed, expected)) return { key, label, observed, expected, status: "mismatch" };
  if (!observedField.agreed) return { key, label, observed, expected, status: "note", detail: "Matches, but the readers did not agree on it." };
  return { key, label, observed, expected, status: "match" };
}

export function compareRows(fields: FieldIn[], claim: ClaimIn, credit: CreditIn | null): { rows: CompareRow[]; notPerformed: string[] } {
  const f = latest(fields);
  const fmt = (minor: number) => (minor / 100).toFixed(2);
  const amount = f.get("amount");
  const txn = f.get("transaction_id");
  const date = f.get("date");
  const cur = f.get("currency");
  const rows: CompareRow[] = [];

  const amountExpected = credit ? fmt(credit.amount_minor) : claim.declared_amount_minor != null ? fmt(claim.declared_amount_minor) : null;
  rows.push(row("amount", credit ? "Amount (receipt vs bank credit)" : "Amount (receipt vs typed)", amount, amount?.normalised_value ? fmt(Number(amount.normalised_value)) : null, amountExpected, (a, b) => a === b));

  const refExpected = credit ? credit.provider_txn_id : claim.declared_reference;
  rows.push(row("reference", credit ? "Reference (receipt vs bank credit)" : "Reference (receipt vs typed)", txn, txn?.normalised_value ?? null, refExpected ? ref(refExpected) : null, (a, b) => ref(a) === ref(b)));

  rows.push(row("currency", "Currency", cur, cur?.normalised_value ?? null, credit?.currency ?? claim.declared_currency, (a, b) => a.toUpperCase() === b.toUpperCase()));

  rows.push(row("date", "Date (receipt vs bank credit)", date, date?.normalised_value?.slice(0, 10) ?? null, credit ? credit.occurred_at.slice(0, 10) : null, (a, b) => a === b));

  const notPerformed: string[] = [];
  if (!credit) notPerformed.push("No bank or merchant credit is selected, so the receipt was not compared with real money.");
  if (claim.declared_amount_minor == null) notPerformed.push("The payer typed no amount, so there is no typed amount to cross-check.");
  if (!claim.declared_reference) notPerformed.push("The payer typed no reference, so there is no typed reference to cross-check.");
  return { rows, notPerformed };
}

/** Fields the readers were unsure of: unagreed, or below the confidence floor. */
export function lowConfidenceCount(fields: { agreed: boolean; confidence: number; corrected_by: string | null }[], floor = 0.75): number {
  return fields.filter((x) => !x.corrected_by && (!x.agreed || x.confidence < floor)).length;
}
