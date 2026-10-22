import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, post } from "../api";

type ReplayResult = {
  policy_version: string;
  claims_replayed: number;
  changes: Record<string, number>;
  risk_levels: { now: Record<string, number>; replay: Record<string, number> };
  changed_claims: {
    claim_id: string;
    state: string;
    changes: string[];
    risk_now: string | null;
    risk_replay: string;
    score_now: number | null;
    score_replay: number;
    signals_replay: string[];
    matched_now: boolean;
    matched_replay: string | null;
  }[];
};
type Stat = { correct: number; read: number; accuracy_pct: number | null };
type Accuracy = {
  since_days: number;
  readers: {
    confirmed_claims: number;
    by_reader: Record<string, Record<string, Stat>>;
    by_channel: Record<string, { claims: number; amount_accuracy_pct: number | null; reference_accuracy_pct: number | null; auto_agreed_pct: number | null; corrected_pct: number | null }>;
  };
  fraud: {
    resolved_alerts: number;
    confirmed_fakes: number;
    overall_precision_pct: number | null;
    by_signal: Record<string, { confirmed_fake: number; cleared: number; precision_pct: number | null }>;
    fakes_missed_by_score: number;
  };
};

const CHANGE_LABEL: Record<string, string> = {
  newly_matched: "would now match a credit",
  match_lost: "would lose its credit match",
  risk_up: "would become suspicious",
  risk_down: "would become less risky",
  newly_suspected_fake: "would become a suspected fake",
};

const pct = (v: number | null | undefined) => (v == null ? "—" : `${v}%`);

export default function Replay() {
  const [days, setDays] = useState(90);
  const current = useQuery({ queryKey: ["policy"], queryFn: () => api<{ version: string; config: Record<string, unknown> }>("/admin/policy") });
  const [draft, setDraft] = useState<string | null>(null);
  const acc = useQuery({ queryKey: ["accuracy", days], queryFn: () => api<Accuracy>(`/admin/accuracy?since_days=${days}`) });
  const run = useMutation({
    mutationFn: (useDraft: boolean) =>
      post<ReplayResult>("/admin/replay", { since_days: days, ...(useDraft && draft ? { config: JSON.parse(draft) } : {}) }),
  });
  const text = draft ?? JSON.stringify(current.data?.config ?? {}, null, 2);
  let bad: string | null = null;
  try {
    JSON.parse(text);
  } catch (e) {
    bad = (e as Error).message;
  }
  const r = run.data;

  return (
    <section>
      <div className="toolbar">
        <h2>Replay &amp; accuracy</h2>
        <label className="small">
          Look back <input type="number" min={1} max={730} value={days} onChange={(e) => setDays(Number(e.target.value) || 90)} style={{ width: 80 }} /> days
        </label>
        <span className="muted small">Measured on your own decided claims. Nothing here changes any claim.</span>
      </div>

      <div className="split even">
        <div className="card">
          <h2>Readers vs confirmed payments</h2>
          <p className="muted small">
            When a bank or alert record confirmed a claim, we know what the receipt really said. {acc.data?.readers.confirmed_claims ?? 0} confirmed claims in range.
          </p>
          <h3>By reader</h3>
          <table>
            <thead><tr><th>Reader</th><th>Amount</th><th>Reference</th></tr></thead>
            <tbody>
              {Object.entries(acc.data?.readers.by_reader ?? {}).map(([reader, f]) => (
                <tr key={reader}>
                  <td>{reader}</td>
                  <td>{pct(f.amount?.accuracy_pct)} <span className="muted small">({f.amount?.correct ?? 0}/{f.amount?.read ?? 0})</span></td>
                  <td>{pct(f.transaction_id?.accuracy_pct)} <span className="muted small">({f.transaction_id?.correct ?? 0}/{f.transaction_id?.read ?? 0})</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>By channel and provider</h3>
          <table>
            <thead><tr><th>Channel / provider</th><th>Claims</th><th>Amount</th><th>Reference</th><th>Both readers agreed</th><th>Corrected</th></tr></thead>
            <tbody>
              {Object.entries(acc.data?.readers.by_channel ?? {}).map(([k, g]) => (
                <tr key={k}>
                  <td>{k}</td><td>{g.claims}</td><td>{pct(g.amount_accuracy_pct)}</td><td>{pct(g.reference_accuracy_pct)}</td>
                  <td>{pct(g.auto_agreed_pct)}</td><td>{pct(g.corrected_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h2>Fraud signals vs admin decisions</h2>
          <p className="muted small">
            {acc.data?.fraud.resolved_alerts ?? 0} resolved alerts · {acc.data?.fraud.confirmed_fakes ?? 0} confirmed fakes · overall precision{" "}
            {pct(acc.data?.fraud.overall_precision_pct)} · fakes the score missed: {acc.data?.fraud.fakes_missed_by_score ?? 0}
          </p>
          <table>
            <thead><tr><th>Signal</th><th>Confirmed fake</th><th>Cleared</th><th>Precision</th></tr></thead>
            <tbody>
              {Object.entries(acc.data?.fraud.by_signal ?? {}).map(([code, s]) => (
                <tr key={code}>
                  <td>{code.replace(/_/g, " ").toLowerCase()}</td><td>{s.confirmed_fake}</td><td>{s.cleared}</td><td>{pct(s.precision_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Low precision: admins keep clearing it — lower its weight. Missed fakes: raise the weights of the signals they show.</p>
        </div>
      </div>

      <div className="card">
        <h2>Policy what-if</h2>
        <p className="muted small">
          Edit a draft of the active policy (<code>{current.data?.version}</code>) and replay past claims to see which would match, lose a match, or change risk
          before you publish it under Guardrails &amp; policies.
        </p>
        <textarea rows={10} value={text} onChange={(e) => setDraft(e.target.value)} spellCheck={false} aria-label="Draft policy (JSON)" />
        {bad && <p className="error small">Draft is not valid JSON: {bad}</p>}
        <div className="inline">
          <button className="ghost" disabled={run.isPending} onClick={() => run.mutate(false)}>Replay active policy</button>
          <button disabled={!!bad || draft === null || run.isPending} onClick={() => run.mutate(true)}>Replay draft</button>
          {draft !== null && <button className="ghost" onClick={() => setDraft(null)}>Reset draft</button>}
        </div>
        {run.error && <p className="error">{(run.error as Error).message}</p>}
        {r && (
          <>
            <p>
              <strong>{r.policy_version}</strong> over {r.claims_replayed} claims:{" "}
              {Object.keys(r.changes).length === 0
                ? "no outcome would change."
                : Object.entries(r.changes).map(([k, n]) => `${n} ${CHANGE_LABEL[k] ?? k}`).join(" · ")}
            </p>
            <p className="muted small">
              Risk levels now {JSON.stringify(r.risk_levels.now)} → replay {JSON.stringify(r.risk_levels.replay)}
            </p>
            <table>
              <thead><tr><th>Claim</th><th>State</th><th>Change</th><th>Risk</th><th>Signals (replay)</th></tr></thead>
              <tbody>
                {r.changed_claims.map((c) => (
                  <tr key={c.claim_id}>
                    <td><code title={c.claim_id}>{c.claim_id.slice(0, 8)}</code></td>
                    <td className="small">{c.state.replace(/_/g, " ").toLowerCase()}</td>
                    <td>{c.changes.map((k) => CHANGE_LABEL[k] ?? k).join("; ")}</td>
                    <td>{c.risk_now ?? "low"} {c.score_now ?? 0} → {c.risk_replay} {c.score_replay}</td>
                    <td className="small">{c.signals_replay.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </section>
  );
}
