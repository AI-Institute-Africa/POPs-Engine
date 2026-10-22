import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, Claim, money } from "../api";
import { RiskBadge } from "./Fraud";

const STATES = [
  "",
  "FINANCIALLY_CONFIRMED",
  "PENDING_REVIEW",
  "AWAITING_FINANCIAL_CONFIRMATION",
  "DUPLICATE_HOLD",
  "DOCUMENT_VERIFIED",
];

export function TierBadge({ tier }: { tier: string | null }) {
  return tier ? <span className={`badge tier-${tier.toLowerCase()}`}>{tier}</span> : null;
}

export function StateBadge({ state }: { state: string }) {
  return <span className={`badge state state-${state.toLowerCase()}`}>{state.replace(/_/g, " ")}</span>;
}

export default function Queue() {
  const [state, setState] = useState("");
  const [channel, setChannel] = useState("");
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  if (channel) params.set("channel", channel);
  const q = useQuery({
    queryKey: ["queue", state, channel],
    queryFn: () => api<Claim[]>(`/review/queue?${params}`),
    refetchInterval: 15000,
  });

  return (
    <section>
      <div className="toolbar">
        <h2>Review queue</h2>
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="State">
          {STATES.map((s) => (
            <option key={s} value={s}>
              {s ? s.replace(/_/g, " ") : "All reviewable"}
            </option>
          ))}
        </select>
        <input placeholder="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} />
        <span className="muted">{q.data?.length ?? 0} claims · oldest first</span>
      </div>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      <table>
        <thead>
          <tr>
            <th>Age</th>
            <th>State</th>
            <th>Tier</th>
            <th>Risk</th>
            <th>Evidence</th>
            <th>Claimant</th>
            <th>Channel</th>
            <th>Declared</th>
            <th>Reasons</th>
          </tr>
        </thead>
        <tbody>
          {q.data?.map((c) => (
            <tr key={c.claim_id}>
              <td>
                <Link to={`/claims/${c.claim_id}`}>{ago(c.created_at)}</Link>
              </td>
              <td><StateBadge state={c.state} /></td>
              <td><TierBadge tier={c.approval_tier} /></td>
              <td><RiskBadge level={c.risk_level} score={c.risk_score} /></td>
              <td>{c.evidence_tier}</td>
              <td>{c.claimant_ref}</td>
              <td>{c.channel}</td>
              <td>{money(c.declared_amount_minor, c.declared_currency)}</td>
              <td className="reasons">{c.reason_codes.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {q.data?.length === 0 && <p className="muted">Queue is empty.</p>}
    </section>
  );
}
