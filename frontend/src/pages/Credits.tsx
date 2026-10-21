import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, Credit, money, Obligation, post } from "../api";

type ImportResult = { rows: number; created: number; duplicates: number; errors: string[]; control_totals: Record<string, number>; rematched_claims: number | null };
type Unclaimed = { credit: Credit; suggested_obligations: Obligation[] };

export default function Credits() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [alias, setAlias] = useState("MAIN");
  const [profile, setProfile] = useState("generic");
  const [onlyOpen, setOnlyOpen] = useState(false);

  const credits = useQuery({ queryKey: ["credits", onlyOpen], queryFn: () => api<Credit[]>(`/credits?unallocated=${onlyOpen}`) });
  const unclaimed = useQuery({ queryKey: ["unclaimed"], queryFn: () => api<Unclaimed[]>("/credits/unclaimed") });
  const imp = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      fd.append("account_alias", alias);
      fd.append("profile", profile);
      return post<ImportResult>("/credits/imports", fd);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["credits"] });
      qc.invalidateQueries({ queryKey: ["unclaimed"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  return (
    <section>
      <div className="card">
        <h2>Import statement</h2>
        <form className="inline" onSubmit={(e) => { e.preventDefault(); imp.mutate(); }}>
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
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

      <div className="card">
        <h2>Unclaimed credits</h2>
        <p className="muted small">Money received with no claim pointing at it. Payment codes in the narrative suggest the invoice.</p>
        <table>
          <thead><tr><th>Date</th><th>Amount</th><th>Available</th><th>Reference</th><th>Narrative</th><th>Suggested</th></tr></thead>
          <tbody>
            {unclaimed.data?.map((u) => (
              <tr key={u.credit.credit_id}>
                <td>{new Date(u.credit.occurred_at).toLocaleDateString()}</td>
                <td>{money(u.credit.amount_minor, u.credit.currency)}</td>
                <td>{money(u.credit.available_minor, u.credit.currency)}</td>
                <td>{u.credit.provider_txn_id}</td>
                <td>{u.credit.narrative}</td>
                <td>{u.suggested_obligations.map((o) => `${o.learner_ref} (${o.payment_code})`).join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="toolbar">
          <h2>All credits</h2>
          <label><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> only with unallocated balance</label>
        </div>
        <table>
          <thead><tr><th>Date</th><th>Source</th><th>Provider</th><th>Reference</th><th>Amount</th><th>Available</th><th>Status</th><th>Account</th><th>Payer</th></tr></thead>
          <tbody>
            {credits.data?.map((c) => (
              <tr key={c.credit_id}>
                <td>{new Date(c.occurred_at).toLocaleString()}</td>
                <td>{c.source}</td>
                <td>{c.provider}</td>
                <td>{c.provider_txn_id}</td>
                <td>{money(c.amount_minor, c.currency)}</td>
                <td>{money(c.available_minor, c.currency)}</td>
                <td>{c.status}</td>
                <td>{c.beneficiary}</td>
                <td>{c.payer}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
