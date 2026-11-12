import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, hasRole, money, post } from "../api";
import { useToast } from "../toast";
import { Chip, EmptyState, Loading, PageHeader, StateChip } from "../ui";
import { agoText } from "./Queue";
import "./review.css";

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
  if (q.error) return <section className="rv"><p className="error">{(q.error as Error).message}</p></section>;
  if (!q.data) return <section className="rv"><PageHeader title="Auto-approved" /><Loading lines={4} /></section>;
  const d = q.data;
  const rate = d.summary.overturn_rate;
  return (
    <section className="rv">
      <PageHeader
        title="Auto-approved"
        sub="Claims the rules approved without a person. A sample goes in front of a reviewer, and the overturn rate decides whether the limit may rise."
      />
      <div className="card">
        <div className="rv-card-head">
          <h2>How it is going</h2>
          <Chip tone={d.enabled ? "ok" : "neutral"}>{d.enabled ? "Switched on" : "Off for this organisation"}</Chip>
        </div>
        <p className="rv-help">
          The rules approve small, exactly-paid claims that a statement or merchant alert confirmed and nothing looked wrong with; {d.audit_pct}% are sampled for a reviewer to check.
        </p>
        <div className="rv-stats">
          <div className="stat"><div className="n">{d.summary.approved}</div><div className="l">approved by the rules</div></div>
          <div className="stat"><div className="n">{d.summary.sampled}</div><div className="l">picked for a check</div></div>
          <div className="stat"><div className="n">{d.summary.audited}</div><div className="l">checked so far</div></div>
          <div className="stat"><div className="n">{d.summary.overturned}</div><div className="l">found wrong</div></div>
          <div className="stat" title="Aim for under 1% before widening the rules">
            <div className="n">{rate !== null ? `${(rate * 100).toFixed(1)}%` : "n/a"}</div>
            <div className="l">overturn rate</div>
          </div>
        </div>
      </div>
      {d.claims.length === 0 ? (
        <EmptyState icon="⚡" title="No automatic approvals yet">When a small, exactly-paid claim is confirmed by the bank record it shows up here. Run a story from Home to fill this.</EmptyState>
      ) : (
        <div className="card rv-flush">
          <div className="table-wrap">
            <table>
              <thead><tr><th>Approved</th><th className="num">Amount</th><th>Channel</th><th>Audit</th><th /></tr></thead>
              <tbody>
                {d.claims.map((r) => (
                  <tr key={r.claim_id}>
                    <td>
                      <Link to={`/claims/${r.claim_id}`} title={new Date(r.approved_at).toLocaleString()}>{agoText(r.approved_at)}</Link>
                      {r.claim_state && <span className="rv-sub"><StateChip state={r.claim_state} /></span>}
                    </td>
                    <td className="num">{money(r.amount_minor, r.currency)}</td>
                    <td>{r.channel}</td>
                    <td>
                      {r.verdict ? (
                        <>
                          <Chip tone={r.verdict.outcome === "AUTO_AUDIT_OK" ? "ok" : "bad"}>{r.verdict.outcome === "AUTO_AUDIT_OK" ? "Correct" : "Overturned"}</Chip>
                          <span className="rv-sub">by {r.verdict.by}{r.verdict.note ? ` — ${r.verdict.note}` : ""}</span>
                        </>
                      ) : r.sampled ? (
                        <Chip tone="warn">In the sample: please check</Chip>
                      ) : (
                        <span className="muted">Not sampled</span>
                      )}
                    </td>
                    <td>
                      {!r.verdict && hasRole("reviewer") && (
                        <span className="rv-row-actions">
                          <button className="ghost small" onClick={() => verdict.mutate({ id: r.claim_id, verdict: "ok", note: null })}>Correct</button>
                          <button className="danger small" onClick={() => overturn(r.claim_id)}>Overturn</button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
