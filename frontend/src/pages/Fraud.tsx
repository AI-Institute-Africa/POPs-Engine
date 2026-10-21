import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { API, api, hasRole, post } from "../api";

export type RiskSignal = { code: string; weight: number; reason: string; evidence: Record<string, unknown> };
type Alert = {
  alert_id: string;
  claim_id: string;
  level: string;
  score: number;
  signals: RiskSignal[];
  status: string;
  resolved_by: string | null;
  resolution_note: string | null;
  resolved_at: string | null;
  created_at: string;
  claim_state: string;
  claimant_ref: string;
  channel: string;
  declared: string | null;
  obligation: string;
  preview_url: string | null;
  forensics_url: string | null;
};

export function RiskBadge({ level, score }: { level: string | null | undefined; score?: number | null }) {
  if (!level || level === "low") return null;
  const label = level === "high" ? "suspected fake" : "suspicious";
  return <span className={`badge risk-${level}`}>{label}{score != null ? ` ${score}` : ""}</span>;
}

export function SignalList({ signals }: { signals: RiskSignal[] }) {
  return (
    <ul className="signals">
      {signals.map((s) => (
        <li key={s.code}>
          <span className="weight">+{s.weight}</span> <strong>{s.code.replace(/_/g, " ").toLowerCase()}</strong> — {s.reason}
          {Object.keys(s.evidence).length > 0 && <code className="config">{JSON.stringify(s.evidence)}</code>}
        </li>
      ))}
    </ul>
  );
}

function AlertCard({ a, admin }: { a: Alert; admin: boolean }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const resolve = useMutation({
    mutationFn: (resolution: string) => post(`/fraud/alerts/${a.alert_id}/resolve`, { resolution, note }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fraud"] });
      qc.invalidateQueries({ queryKey: ["fraud-summary"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
    },
  });
  return (
    <div className={`card alert alert-${a.level}`}>
      <div className="alert-body">
        <div className="thumbs">
          {a.preview_url && (
            <a href={`${API}${a.preview_url}`} target="_blank" rel="noreferrer">
              <img src={`${API}${a.preview_url}`} alt="Submitted proof of payment" />
            </a>
          )}
          {a.forensics_url && (
            <a href={`${API}${a.forensics_url}`} target="_blank" rel="noreferrer" title="Error level analysis: bright patches were compressed differently">
              <img src={`${API}${a.forensics_url}`} alt="Forensic heatmap" />
              <span className="muted small">ELA heatmap</span>
            </a>
          )}
        </div>
        <div className="alert-main">
          <div className="row">
            <RiskBadge level={a.level} score={a.score} />
            <strong>{a.obligation}</strong>
            <span className="muted">{a.claimant_ref} · {a.channel} · {a.declared ?? "no amount typed"}</span>
            <Link to={`/claims/${a.claim_id}`}>open claim</Link>
          </div>
          <div className="muted small">
            flagged {new Date(a.created_at).toLocaleString()} · claim {a.claim_state.replace(/_/g, " ").toLowerCase()}
          </div>
          <SignalList signals={a.signals} />
          {a.status === "open" ? (
            admin ? (
              <form className="inline" onSubmit={(e) => e.preventDefault()}>
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you check? (required, kept in the audit log)" />
                <button type="button" className="danger" disabled={note.length < 3 || resolve.isPending} onClick={() => resolve.mutate("confirmed_fake")}>
                  Confirm fake &amp; reject
                </button>
                <button type="button" className="ghost" disabled={note.length < 3 || resolve.isPending} onClick={() => resolve.mutate("false_positive")}>
                  Not fake — return to review
                </button>
              </form>
            ) : (
              <p className="muted small">Waiting for an admin decision. The claim cannot be confirmed until then.</p>
            )
          ) : (
            <p className="muted small">
              {a.status === "confirmed_fake" ? "Confirmed fake" : "Cleared as not fake"} by {a.resolved_by} — {a.resolution_note}
            </p>
          )}
          {resolve.error && <p className="error">{(resolve.error as Error).message}</p>}
        </div>
      </div>
    </div>
  );
}

export default function Fraud() {
  const [status, setStatus] = useState("open");
  const [level, setLevel] = useState("");
  const admin = hasRole("admin");
  const q = useQuery({
    queryKey: ["fraud", status, level],
    queryFn: () => api<Alert[]>(`/fraud/alerts?status=${status}${level ? `&level=${level}` : ""}`),
    refetchInterval: 20000,
  });
  return (
    <section>
      <div className="toolbar">
        <h2>Suspected fakes</h2>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="open">Open</option>
          <option value="confirmed_fake">Confirmed fake</option>
          <option value="false_positive">Cleared</option>
          <option value="all">All</option>
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
          <option value="">Suspected fake &amp; suspicious</option>
          <option value="high">Suspected fake only</option>
          <option value="medium">Suspicious only</option>
        </select>
        <span className="muted small">Highest risk first. Payers are never told a claim is suspected.</span>
      </div>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {q.data?.length === 0 && <p className="muted">Nothing flagged.</p>}
      {q.data?.map((a) => <AlertCard key={a.alert_id} a={a} admin={admin} />)}
    </section>
  );
}
