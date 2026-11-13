import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, hasRole, money, post, type Credit } from "../api";
import { parseBulk } from "../lib/bulkCsv";
import { Chip, EmptyState, Loading, PageHeader } from "../ui";
import "./review.css";

type Row = { index: number; status: string; detail: string | null; obligation_id: string | null; amount_minor: number | null; allocation_id: string | null };
type Out = { preview: boolean; aborted: boolean; rows: Row[] };

const TONE: Record<string, "ok" | "warn" | "bad" | "info" | "neutral"> = {
  ready: "ok",
  check: "warn",
  fix: "bad",
  allocated: "ok",
  needs_second_approver: "info",
  failed: "bad",
  skipped: "neutral",
};

const LABEL: Record<string, string> = {
  ready: "Ready",
  check: "Check",
  fix: "Fix",
  allocated: "Allocated",
  needs_second_approver: "Needs a second approver",
  failed: "Failed",
  skipped: "Not run",
};

/** Split one employer payment (a recorded credit) across many invoices: preview, then apply. */
export default function Bulk() {
  const credits = useQuery({ queryKey: ["credits", "unallocated"], queryFn: () => api<Credit[]>("/credits?unallocated=true") });
  const [creditId, setCreditId] = useState("");
  const [text, setText] = useState("");
  const [last4, setLast4] = useState("");
  const [runId, setRunId] = useState(() => crypto.randomUUID());
  const parsed = useMemo(() => (text.trim() ? parseBulk(text) : null), [text]);
  const credit = credits.data?.find((c) => c.credit_id === creditId);

  const body = (preview: boolean) => ({
    run_id: runId,
    preview,
    txn_last4: preview ? undefined : last4,
    rows: (parsed?.rows ?? []).map((r) => ({ payment_code: r.payment_code, amount_minor: r.amount_minor, note: r.note })),
  });
  const plan = useMutation({ mutationFn: () => post<Out>(`/credits/${creditId}/allocate/bulk`, body(true)) });
  const apply = useMutation({ mutationFn: () => post<Out>(`/credits/${creditId}/allocate/bulk`, body(false)) });

  const result = apply.data ?? plan.data;
  const rowsOk = !!parsed && parsed.rows.length > 0 && parsed.errors.length === 0;
  const planned = plan.data && !apply.data ? plan.data.rows : null;
  const blocking = planned?.some((r) => r.status === "fix") ?? false;
  const err = (plan.error ?? apply.error) as Error | null;

  if (!hasRole("reviewer")) return <EmptyState icon="🔒" title="Reviewers only">Ask a reviewer or approver to split a payment.</EmptyState>;

  const reset = () => {
    plan.reset();
    apply.reset();
    setRunId(crypto.randomUUID());
    setLast4("");
  };

  return (
    <section className="rv">
      <PageHeader
        title="Split one payment across invoices"
        sub="For money that arrived in one lump, such as an employer paying for many learners. Every row is checked and allocated exactly like a single allocation."
        actions={<Link className="btn secondary" to="/credits">← Credits</Link>}
      />
      <div className="card">
        <h3>1. Pick the payment</h3>
        {credits.isLoading && <Loading lines={2} />}
        {credits.data?.length === 0 && <p className="muted">No credit has money left to allocate. Import a statement or record a credit first.</p>}
        <select value={creditId} onChange={(e) => { setCreditId(e.target.value); reset(); }} aria-label="Credit to split">
          <option value="">Choose a credit…</option>
          {credits.data?.map((c) => (
            <option key={c.credit_id} value={c.credit_id}>
              {c.provider} {c.provider_txn_id} · {money(c.available_minor, c.currency)} left
            </option>
          ))}
        </select>

        <h3>2. Paste or load the list</h3>
        <p className="rv-help">One invoice per line: payment code, amount (optional), note (optional). Commas, semicolons and tabs all work, with or without a header row. Up to 500 rows.</p>
        <textarea rows={8} value={text} onChange={(e) => { setText(e.target.value); plan.reset(); apply.reset(); }} placeholder={"code,amount,note\nCG-H4CC093,40.00,Tendai Moyo"} aria-label="Allocation list" />
        <div className="inline">
          <label className="btn ghost small">
            Load a CSV file
            <input type="file" accept=".csv,.tsv,.txt,text/csv" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); plan.reset(); apply.reset(); } e.currentTarget.value = ""; }} />
          </label>
          {parsed && <span className="muted small">{parsed.rows.length} row{parsed.rows.length === 1 ? "" : "s"} read</span>}
        </div>
        {parsed?.errors.map((e) => <p key={e} className="error small">{e}</p>)}

        <h3>3. Preview</h3>
        <button type="button" disabled={!creditId || !rowsOk || plan.isPending} onClick={() => { apply.reset(); plan.mutate(); }}>
          {plan.isPending ? "Checking…" : "Preview"}
        </button>
        {credit && <span className="muted small"> {money(credit.available_minor, credit.currency)} available on this credit</span>}
      </div>

      {err && <p className="error" role="alert">{err.message}</p>}

      {result && (
        <div className="card rv-flush">
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Code</th><th className="num">Amount</th><th>Result</th><th>Detail</th></tr></thead>
              <tbody>
                {result.rows.map((r) => (
                  <tr key={r.index}>
                    <td>{r.index + 1}</td>
                    <td>{parsed?.rows[r.index]?.payment_code}</td>
                    <td className="num">{r.amount_minor != null ? money(r.amount_minor, credit?.currency) : "—"}</td>
                    <td><Chip tone={TONE[r.status] ?? "neutral"}>{LABEL[r.status] ?? r.status}</Chip></td>
                    <td className="muted small">{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {planned && (
        <div className="card">
          <h3>4. Apply</h3>
          {blocking && <p className="warn-text small">Rows marked Fix will be skipped with an error; the rest go ahead. Fix the list and preview again if you want them included.</p>}
          <p className="rv-help">Look up the transaction ID at the source (bank app or merchant portal), not in this list, and type its last 4 characters. A wrong answer stops the whole run.</p>
          <form className="inline" onSubmit={(e) => { e.preventDefault(); apply.mutate(); }}>
            <input value={last4} maxLength={4} onChange={(e) => setLast4(e.target.value)} placeholder="Last 4 of txn ID (from source)" aria-label="Last four characters of transaction ID" />
            <button disabled={last4.length !== 4 || apply.isPending}>{apply.isPending ? "Allocating…" : `Allocate ${planned.filter((r) => r.status !== "fix").length} rows`}</button>
          </form>
        </div>
      )}

      {apply.data && (
        <div className="card">
          <p>
            {apply.data.aborted ? <strong className="error">The run stopped early.</strong> : <strong>Done.</strong>}{" "}
            {apply.data.rows.filter((r) => r.status === "allocated").length} allocated,{" "}
            {apply.data.rows.filter((r) => r.status === "failed").length} failed,{" "}
            {apply.data.rows.filter((r) => r.status === "needs_second_approver").length} waiting for a second approver.
          </p>
          <p className="muted small">If something was interrupted, press Apply again with the same list: rows already allocated are not repeated. Rows waiting for a second approver need a different approver to run this same list.</p>
          <button type="button" className="ghost" onClick={reset}>Start a new run</button>
        </div>
      )}
    </section>
  );
}
