import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { API, api, hasRole, post } from "../api";
import { humanize } from "../labels";
import { Chip, EmptyState, Loading, PageHeader, StateChip } from "../ui";
import { agoText } from "./Queue";
import "./review.css";

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
  const high = level === "high";
  return (
    <Chip
      tone={high ? "bad" : "warn"}
      title={high ? "Risk signals suggest this receipt may be fake. An administrator decides." : "Some risk signals fired. Look closely before approving."}
    >
      {high ? "Suspected fake" : "Suspicious"}
      {score != null && <span className="rv-code">{score}/100</span>}
    </Chip>
  );
}

// Each signal: how much it adds, what it means in words, the code for auditors, and the raw evidence on request.
export function SignalList({ signals }: { signals: RiskSignal[] }) {
  return (
    <ul className="rv-signals">
      {signals.map((s) => (
        <li key={s.code}>
          <span className="w" title="Points added to the risk score">+{s.weight}</span>
          <span className="t">
            <strong>{humanize(s.code)}</strong>: {s.reason} <code className="rv-code">{s.code}</code>
          </span>
          {Object.keys(s.evidence).length > 0 && (
            <details>
              <summary>Technical detail</summary>
              <code>{JSON.stringify(s.evidence, null, 1)}</code>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}

const STATUS_CHIP: Record<string, { label: string; tone: "bad" | "ok" | "warn" }> = {
  open: { label: "Open", tone: "warn" },
  confirmed_fake: { label: "Confirmed fake", tone: "bad" },
  false_positive: { label: "Cleared", tone: "ok" },
};

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
  const st = STATUS_CHIP[a.status];
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
            {st && a.status !== "open" && <Chip tone={st.tone}>{st.label}</Chip>}
            <span className="title">{a.obligation}</span>
          </div>
          <div className="muted small" style={{ margin: "4px 0" }}>
            {a.claimant_ref} · {a.channel} · {a.declared ?? "no amount typed"} · flagged{" "}
            <span title={new Date(a.created_at).toLocaleString()}>{agoText(a.created_at)}</span>
          </div>
          <div className="rv-chips" style={{ marginBottom: 6 }}>
            <span className="muted small">Claim is</span>
            <StateChip state={a.claim_state} />
            <Link to={`/claims/${a.claim_id}`}>Open the claim</Link>
          </div>
          <h3>Why it was flagged</h3>
          <SignalList signals={a.signals} />
          {a.status === "open" ? (
            admin ? (
              <form className="rv-note-row" onSubmit={(e) => e.preventDefault()}>
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you check? (required, kept in the audit log)" aria-label="What did you check" />
                <div className="btns">
                  <button type="button" className="danger" disabled={note.length < 3 || resolve.isPending} onClick={() => resolve.mutate("confirmed_fake")}>
                    Confirm fake &amp; reject
                  </button>
                  <button type="button" className="ghost" disabled={note.length < 3 || resolve.isPending} onClick={() => resolve.mutate("false_positive")}>
                    Not fake — return to review
                  </button>
                </div>
              </form>
            ) : (
              <p className="muted small">Waiting for an admin decision. The claim cannot be confirmed until then.</p>
            )
          ) : (
            <div className="rv-resolved">
              {a.status === "confirmed_fake" ? "Confirmed fake" : "Cleared as not fake"} by {a.resolved_by} — {a.resolution_note}
            </div>
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
    <section className="rv">
      <PageHeader
        title="Suspected fakes"
        sub="Receipts the risk checks think may be forged, highest risk first. An administrator decides; payers are never told a claim is suspected."
      />
      <div className="rv-filters">
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
        <span className="rv-count">{q.data ? `${q.data.length} alert${q.data.length === 1 ? "" : "s"}` : ""}</span>
      </div>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {q.isLoading && <Loading lines={4} />}
      {q.data?.length === 0 && (
        <EmptyState icon="🛡" title={status === "open" ? "Nothing flagged" : "No alerts here"}>
          {status === "open"
            ? "No receipt is waiting on a fake check. Run a story from Home to fill this."
            : "No alerts match these filters. Try “All”."}
        </EmptyState>
      )}
      {q.data?.map((a) => <AlertCard key={a.alert_id} a={a} admin={admin} />)}
    </section>
  );
}
