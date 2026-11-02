import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, hasRole, money, post } from "../api";
import { useToast } from "../toast";

type Row = {
  claim_id: string;
  approved_at: string;
  amount_minor: number | null;
  currency: string | null;
  channel: string | null;
  claim_state: string | null;
  sampled: boolean;
  verdict: { outcome: string; by: string; at: string; note: string | null } | null;
};
type Listing = {
  enabled: boolean;
  audit_pct: number;
  summary: { approved: number; sampled: number; audited: number; overturned: number; overturn_rate: number | null };
  claims: Row[];
};

// Claims the rules approved without a person. A sample goes in front of a reviewer; the overturn rate
// decides whether the cap may rise (blueprint: under 1 % before widening).
export default function AutoApproved() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["auto-approved"], queryFn: () => api<Listing>("/review/auto-approved"), refetchInterval: 60000 });
  const verdict = useMutation({
    mutationFn: (v: { id: string; verdict: "ok" | "overturned"; note: string | null }) =>
      post(`/review/claims/${v.id}/audit-verdict`, { verdict: v.verdict, note: v.note }),
    onSuccess: (_r, v) => {
      toast("ok", v.verdict === "ok" ? "Marked correct." : "Marked overturned. Reverse the allocation from the claim page to put the money back.");
      qc.invalidateQueries({ queryKey: ["auto-approved"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const overturn = (id: string) => {
    const note = window.prompt("What was wrong with this approval?");
    if (note && note.trim().length >= 3) verdict.mutate({ id, verdict: "overturned", note: note.trim() });
  };
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  if (!q.data) return <p>Loading…</p>;
  const d = q.data;
  const rate = d.summary.overturn_rate;
  return (
    <section>
      <div className="card">
        <h2>Automatic approvals</h2>
        <p className="muted small">
          {d.enabled ? "On." : "Off for this organisation."} The rules approve small, exactly-paid claims that a statement or
          merchant alert confirmed and nothing looked wrong with; {d.audit_pct}% are sampled for a reviewer to check.
        </p>
        <p>
          {d.summary.approved} approved · {d.summary.sampled} sampled · {d.summary.audited} audited · {d.summary.overturned} overturned
          {rate !== null && <> · overturn rate <strong>{(rate * 100).toFixed(1)}%</strong></>}
        </p>
      </div>
      <div className="card">
        <table>
          <thead><tr><th>Approved</th><th>Amount</th><th>Channel</th><th>Audit</th><th /></tr></thead>
          <tbody>
            {d.claims.length === 0 && <tr><td colSpan={5} className="muted">No automatic approvals yet.</td></tr>}
            {d.claims.map((r) => (
              <tr key={r.claim_id}>
                <td><Link to={`/claims/${r.claim_id}`}>{new Date(r.approved_at).toLocaleString()}</Link></td>
                <td>{money(r.amount_minor, r.currency)}</td>
                <td>{r.channel}</td>
                <td>
                  {r.verdict
                    ? `${r.verdict.outcome === "AUTO_AUDIT_OK" ? "Correct" : "Overturned"} by ${r.verdict.by}${r.verdict.note ? ` — ${r.verdict.note}` : ""}`
                    : r.sampled
                      ? "In the sample: please check"
                      : "Not sampled"}
                </td>
                <td>
                  {!r.verdict && hasRole("reviewer") && (
                    <>
                      <button className="ghost small" onClick={() => verdict.mutate({ id: r.claim_id, verdict: "ok", note: null })}>Correct</button>{" "}
                      <button className="danger small" onClick={() => overturn(r.claim_id)}>Overturn</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
