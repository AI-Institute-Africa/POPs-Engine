// Forgiving CSV/TSV parser for the bulk-allocation page: any of , ; or tab as delimiter, headers
// in any order or naming, amounts like "12.50" or "12,50" turned into minor units (2 decimals).

export type BulkRow = { payment_code: string; amount_minor: number | null; note: string | null };
export type ParsedBulk = { rows: BulkRow[]; errors: string[] };

export const MAX_ROWS = 500;

const CODE_HEADERS = ["code", "payment_code", "paymentcode", "payment code", "ref", "reference", "invoice"];
const AMOUNT_HEADERS = ["amount", "amt", "value", "paid"];
const NOTE_HEADERS = ["note", "notes", "memo", "name", "learner"];

function detectDelimiter(line: string): string {
  const counts = ["\t", ";", ","].map((d) => [d, line.split(d).length - 1] as const);
  counts.sort((a, b) => b[1] - a[1]);
  return counts[0][1] > 0 ? counts[0][0] : ",";
}

function splitLine(line: string, d: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (quoted && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else quoted = !quoted;
    } else if (ch === d && !quoted) {
      out.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  out.push(cur.trim());
  return out;
}

export function toMinor(raw: string): number | null {
  const s = raw.replace(/[^\d.,-]/g, "");
  if (!s || s.startsWith("-")) return null;
  // the last separator with 1-2 digits after it is the decimal point
  const m = s.match(/^(\d[\d.,]*?)(?:[.,](\d{1,2}))?$/);
  if (!m) return null;
  const whole = m[1].replace(/[.,]/g, "");
  const frac = (m[2] ?? "").padEnd(2, "0");
  const n = Number(whole) * 100 + Number(frac);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

export function parseBulk(text: string): ParsedBulk {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const errors: string[] = [];
  if (!lines.length) return { rows: [], errors: ["Paste or choose a file with one invoice per line."] };
  const d = detectDelimiter(lines[0]);
  const first = splitLine(lines[0], d).map((h) => h.toLowerCase());
  const find = (names: string[]) => first.findIndex((h) => names.includes(h));
  const hasHeader = find(CODE_HEADERS) >= 0 || find(AMOUNT_HEADERS) >= 0;
  const ci = hasHeader ? Math.max(find(CODE_HEADERS), 0) : 0;
  const ai = hasHeader ? find(AMOUNT_HEADERS) : 1;
  const ni = hasHeader ? find(NOTE_HEADERS) : 2;
  const body = hasHeader ? lines.slice(1) : lines;
  const rows: BulkRow[] = [];
  body.forEach((line, n) => {
    const cells = splitLine(line, d);
    const code = (cells[ci] ?? "").trim().toUpperCase();
    const rawAmount = ai >= 0 ? (cells[ai] ?? "") : "";
    const amount = rawAmount ? toMinor(rawAmount) : null;
    const row = n + (hasHeader ? 2 : 1);
    if (!code) {
      errors.push(`Line ${row}: no payment code.`);
      return;
    }
    if (rawAmount && amount === null) {
      errors.push(`Line ${row}: "${rawAmount}" is not an amount.`);
      return;
    }
    rows.push({ payment_code: code, amount_minor: amount, note: ni >= 0 ? cells[ni] || null : null });
  });
  if (rows.length > MAX_ROWS) {
    errors.push(`Up to ${MAX_ROWS} rows at a time; you have ${rows.length}.`);
    rows.length = MAX_ROWS;
  }
  return { rows, errors };
}
