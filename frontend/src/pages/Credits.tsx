import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api, Credit, download, hasRole, money, Obligation, post } from "../api";
import { humanize } from "../labels";
import { useToast } from "../toast";
import { Chip, EmptyState, Loading, PageHeader } from "../ui";
import "./review.css";

type ImportResult = {
  import_id: string;
  rows: number;
  created: number;
  duplicates: number;
  errors: string[];
  control_totals: Record<string, number>;
  rematched_claims: number | null;
  confirmed_alerts: number | null;
};
type Unclaimed = { credit: Credit; suggested_obligations: Obligation[] };
type AllocateResult = {
  allocation: { allocation_id: string; amount_minor: number } | null;
  needs_second_approval: boolean;
  outstanding_minor: number;
  payment_status: string;
};

function toMinor(amount: string): number {
  return Math.round(Number(amount) * 100);
}

// Money that arrived outside a claim: a finance user saw it on the recipient side and records it.
function ManualCredit() {
  const qc = useQueryClient();
  const toast = useToast();
  const blank = { provider: "ECOCASH", txn: "", amount: "", currency: "USD", at: "", beneficiary: "MAIN", payer: "", narrative: "", attestation: "" };
  const [f, setF] = useState(blank);
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () =>
      post<Credit>("/credits", {
        provider: f.provider,
        provider_txn_id: f.txn.trim(),
        amount_minor: toMinor(f.amount),
        currency: f.currency,
        occurred_at: new Date(f.at).toISOString(),
        beneficiary: f.beneficiary,
        payer: f.payer || null,
        narrative: f.narrative || null,
        attestation: f.attestation,
      }),
    onSuccess: () => {
      toast("ok", "Credit recorded. Waiting claims are being matched.");
      setF(blank);
      qc.invalidateQueries({ queryKey: ["credits"] });
      qc.invalidateQueries({ queryKey: ["unclaimed"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const ready = f.txn.trim() && Number(f.amount) > 0 && f.at && f.attestation.trim().length >= 10;
  return (
    <div className="card">
      <h2>Record a received payment</h2>
      <p className="rv-help">
        For money you saw arrive (merchant portal, bank app) that is not in an imported statement yet.
        It is kept as an attested credit and can be matched to a payer's claim or allocated below.
        {!hasRole("approver") && " Recording needs the approver role under the default policy."}
      </p>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="form-grid">
          <label>Provider <input value={f.provider} onChange={set("provider")} /></label>
          <label>Transaction ID <input value={f.txn} onChange={set("txn")} required /></label>
          <label>Amount <input type="number" min="0" step="0.01" value={f.amount} onChange={set("amount")} required /></label>
          <label>Currency
            <select value={f.currency} onChange={set("currency")}><option>USD</option><option>ZWG</option></select>
          </label>
          <label>Received at <input type="datetime-local" value={f.at} onChange={set("at")} required /></label>
          <label>Account <input value={f.beneficiary} onChange={set("beneficiary")} /></label>
          <label>Payer (optional) <input value={f.payer} onChange={set("payer")} /></label>
          <label>Narrative (optional) <input value={f.narrative} onChange={set("narrative")} placeholder="e.g. payment code CG-…" /></label>
          <label className="wide">Where you saw it, and who (at least 10 characters)
            <input value={f.attestation} onChange={set("attestation")} placeholder="Merchant portal, checked by T. Moyo at 15:00" required />
          </label>
        </div>
        <button disabled={!ready || save.isPending}>Record payment</button>
      </form>
    </div>
  );
}

// Allocations and reversals as a spreadsheet, for finance and bookkeeping.
function ExportCard() {
  const toast = useToast();
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [kind, setKind] = useState("");
  const run = useMutation({
    mutationFn: () => {
      const q = new URLSearchParams();
      if (since) q.set("since", since);
      if (until) q.set("until", until);
      if (kind) q.set("kind", kind);
      return download(`/allocations/export?${q.toString()}`);
    },
    onSuccess: () => toast("ok", "Export downloaded."),
    onError: (e) => toast("err", (e as Error).message),
  });
  return (
    <div className="card">
      <h2>Export allocations</h2>
      <p className="rv-help">
        One row per allocation or reversal (a reversal is a negative row, so a column total is the net amount
        paid). Dates are UTC and inclusive. Leave them empty for everything.
      </p>
      <form className="inline" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        <label>From <input type="date" value={since} onChange={(e) => setSince(e.target.value)} /></label>
        <label>To <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">Allocations and reversals</option>
          <option value="allocation">Allocations only</option>
          <option value="reversal">Reversals only</option>
        </select>
        <button disabled={run.isPending}>Download CSV</button>
      </form>
    </div>
  );
}

// Allocate an already-recorded credit to an invoice when no payer claim exists.
function AllocateRow({ u, obligations, onDone }: { u: Unclaimed; obligations: Obligation[]; onDone: () => void }) {
  const toast = useToast();
  const same = obligations.filter((o) => o.currency === u.credit.currency);
  const suggested = u.suggested_obligations[0]?.obligation_id ?? "";
  const [ob, setOb] = useState(suggested);
  const [amount, setAmount] = useState("");
  const [last4, setLast4] = useState("");
  const [note, setNote] = useState("");
  const run = useMutation({
    mutationFn: () =>
      post<AllocateResult>(`/credits/${u.credit.credit_id}/allocate`, {
        obligation_id: ob,
        amount_minor: amount ? toMinor(amount) : null,
        txn_last4: last4,
        note: note || null,
      }),
    onSuccess: (r) => {
      if (r.needs_second_approval) {
        toast("info", "First approval recorded. A different approver must repeat this allocation to complete it.");
      } else {
        toast("ok", `Allocated ${money(r.allocation?.amount_minor, u.credit.currency)} (${r.payment_status.replace("_", " ")}).`);
        onDone();
      }
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  return (
    <tr className="allocate-row">
      <td colSpan={7}>
        <form className="inline" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
          <select value={ob} onChange={(e) => setOb(e.target.value)} aria-label="Invoice" required>
            <option value="">Choose invoice…</option>
            {same.map((o) => (
              <option key={o.obligation_id} value={o.obligation_id}>
                {o.learner_ref} · {o.payment_code} · {money(o.amount_minor, o.currency)}
              </option>
            ))}
          </select>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount (default: as much as allowed)" type="number" min="0" step="0.01" aria-label="Amount" />
          <input value={last4} onChange={(e) => setLast4(e.target.value)} placeholder={`Last 4 of ${u.credit.provider_txn_id.slice(-4).replace(/./g, "•")} (type to confirm)`} aria-label="Last four characters of the transaction ID" required />
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note" aria-label="Note" />
          <button disabled={!ob || !last4 || run.isPending}>Allocate</button>
        </form>
      </td>
    </tr>
  );
}

export default function Credits() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [alias, setAlias] = useState("MAIN");
  const [profile, setProfile] = useState("generic");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const credits = useQuery({ queryKey: ["credits", onlyOpen], queryFn: () => api<Credit[]>(`/credits?unallocated=${onlyOpen}`) });
  const unclaimed = useQuery({ queryKey: ["unclaimed"], queryFn: () => api<Unclaimed[]>("/credits/unclaimed") });
  const obligations = useQuery({ queryKey: ["obligations"], queryFn: () => api<Obligation[]>("/obligations") });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["credits"] });
    qc.invalidateQueries({ queryKey: ["unclaimed"] });
    qc.invalidateQueries({ queryKey: ["obligations"] });
    qc.invalidateQueries({ queryKey: ["queue"] });
  };
  const imp = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      fd.append("account_alias", alias);
      fd.append("profile", profile);
      return post<ImportResult>("/credits/imports", fd);
    },
    onSuccess: refresh,
  });

  const un = unclaimed.data;
  return (
    <section className="rv">
      <PageHeader
        title="Bank credits"
        sub="The bank and merchant side of every payment. Import statements, record money you saw arrive, and allocate credits that have no claim yet."
      />

      <div className="card">
        <h2>Unclaimed credits</h2>
        <p className="rv-help">
          Money received with no claim pointing at it. Payment codes in the narrative suggest the invoice.
          Allocate it to an invoice here when the payer will not send a proof; if a claim is already waiting on the
          invoice, confirm it from the review queue instead.
        </p>
        {unclaimed.isLoading && <Loading lines={3} />}
        {unclaimed.error && <p className="error">{(unclaimed.error as Error).message}</p>}
        {un?.length === 0 && (
          <div className="rv-inline-empty">Nothing unclaimed. Every credit is matched to a claim or an invoice.</div>
        )}
        {!!un?.length && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th className="num">Amount</th><th className="num">Available</th><th>Reference</th><th>Narrative</th><th>Suggested invoice</th><th /></tr></thead>
              <tbody>
                {un.map((u) => (
                  <Fragment key={u.credit.credit_id}>
                    <tr>
                      <td>{new Date(u.credit.occurred_at).toLocaleDateString()}</td>
                      <td className="num">{money(u.credit.amount_minor, u.credit.currency)}</td>
                      <td className="num">{money(u.credit.available_minor, u.credit.currency)}</td>
                      <td><code>{u.credit.provider_txn_id}</code></td>
                      <td>{u.credit.narrative}</td>
                      <td>{u.suggested_obligations.map((o) => `${o.learner_ref} (${o.payment_code})`).join(", ") || "—"}</td>
                      <td>
                        <button className="ghost small" disabled={!hasRole("approver")} title={hasRole("approver") ? "" : "Needs the approver role under the default policy"} onClick={() => setOpen(open === u.credit.credit_id ? null : u.credit.credit_id)}>
                          {open === u.credit.credit_id ? "Close" : "Allocate…"}
                        </button>
                      </td>
                    </tr>
                    {open === u.credit.credit_id && (
                      <AllocateRow u={u} obligations={obligations.data ?? []} onDone={() => { setOpen(null); refresh(); }} />
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Import a statement</h2>
        <p className="rv-help">Upload a bank or merchant CSV. New credits are added and waiting claims are matched automatically.</p>
        <form className="inline" onSubmit={(e) => { e.preventDefault(); imp.mutate(); }}>
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Statement file" />
          <label>Account alias <input value={alias} onChange={(e) => setAlias(e.target.value)} /></label>
          <select value={profile} onChange={(e) => setProfile(e.target.value)} aria-label="Statement profile">
            <option value="generic">Generic bank CSV</option>
            <option value="ecocash_merchant">EcoCash merchant export</option>
          </select>
          <button disabled={!file || imp.isPending}>Import</button>
        </form>
        {imp.error && <p className="error">{(imp.error as Error).message}</p>}
        {imp.data && (
          <p className="flash">
            {imp.data.rows} rows · {imp.data.created} new credits · {imp.data.duplicates} duplicates · {imp.data.rematched_claims === null ? "claim matching queued" : `${imp.data.rematched_claims} claims matched`} ·
            totals {Object.entries(imp.data.control_totals).map(([c, v]) => money(v, c)).join(", ")}
            {imp.data.errors.length > 0 && <> · {imp.data.errors.length} errors: {imp.data.errors.slice(0, 3).join("; ")}</>}
          </p>
        )}
      </div>

      <ManualCredit />

      <div className="card">
        <div className="rv-card-head">
          <h2>All credits</h2>
          <label style={{ flexDirection: "row", alignItems: "center", margin: 0, minHeight: 36 }}>
            <input type="checkbox" style={{ width: "auto" }} checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> only with unallocated balance
          </label>
        </div>
        {credits.isLoading && <Loading lines={4} />}
        {credits.error && <p className="error">{(credits.error as Error).message}</p>}
        {credits.data?.length === 0 && (
          <EmptyState icon="🏦" title="No credits yet">Import a statement or record a payment above. Run a story from Home to fill this.</EmptyState>
        )}
        {!!credits.data?.length && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Source</th><th>Provider</th><th>Reference</th><th className="num">Amount</th><th className="num">Available</th><th>Status</th><th>Account</th><th>Payer</th></tr></thead>
              <tbody>
                {credits.data.map((c) => (
                  <tr key={c.credit_id}>
                    <td>{new Date(c.occurred_at).toLocaleString()}</td>
                    <td>{humanize(c.source)}</td>
                    <td>{c.provider}</td>
                    <td><code>{c.provider_txn_id}</code></td>
                    <td className="num">{money(c.amount_minor, c.currency)}</td>
                    <td className="num">{money(c.available_minor, c.currency)}</td>
                    <td><Chip tone={c.status === "success" ? "ok" : "warn"} title={c.status === "success" ? "A successful payment that can be matched" : "Not a successful payment, so it cannot be matched"}>{c.status === "success" ? "Received" : humanize(c.status)}</Chip></td>
                    <td>{c.beneficiary}</td>
                    <td>{c.payer}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ExportCard />
    </section>
  );
}
