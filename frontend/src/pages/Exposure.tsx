import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, hasRole, money } from "../api";

type Report = {
  enabled: boolean;
  active_exposure_minor: Record<string, number>;
  caps: {
    per_claim_minor: Record<string, number>;
    per_learner_minor: Record<string, number>;
    org_daily_minor: Record<string, number>;
    ttl_hours: number;
  };
  last_7_days: { granted: number; active: number; confirmed: number; revoked: number; expired: number; confirmed_pct: number | null };
  expiring_soon: { grant_id: string; claim_id: string; obligation_id: string; exposure_minor: number; currency: string; expires_at: string }[];
};

const caps = (m: Record<string, number>) =>
  Object.entries(m).map(([c, v]) => money(v, c)).join(", ") || "none (nothing is eligible)";

// Value at risk from provisional access: the host system opens it, this shows how much and for how long.
export default function Exposure() {
  const q = useQuery({ queryKey: ["exposure"], queryFn: () => api<Report>("/admin/exposure"), refetchInterval: 60000 });
  if (!hasRole("admin")) return <p className="muted">Only admins can see provisional access exposure.</p>;
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  if (!q.data) return <p>Loading…</p>;
  const r = q.data;
  const active = Object.entries(r.active_exposure_minor);
  return (
    <section>
      <div className="card">
        <h2>Provisional access</h2>
        <p className="muted small">
          {r.enabled
            ? `On. Access lasts ${r.caps.ttl_hours} h without a matching payment, then ends automatically.`
            : "Off for this organisation. Turn it on by publishing a payment policy with provisional_enabled."}{" "}
          The host system decides what access means; see docs/provisional-access.md.
        </p>
        <dl>
          <dt>Open exposure</dt>
          <dd>{active.length ? active.map(([c, v]) => money(v, c)).join(", ") : "none"}</dd>
          <dt>Per claim cap</dt>
          <dd>{caps(r.caps.per_claim_minor)}</dd>
          <dt>Per payer cap</dt>
          <dd>{caps(r.caps.per_learner_minor)}</dd>
          <dt>Daily cap</dt>
          <dd>{caps(r.caps.org_daily_minor)}</dd>
        </dl>
      </div>
      <div className="card">
        <h2>Last 7 days</h2>
        <p>
          {r.last_7_days.granted} granted · {r.last_7_days.active} open · {r.last_7_days.confirmed} confirmed ·{" "}
          {r.last_7_days.revoked} revoked · {r.last_7_days.expired} expired
          {r.last_7_days.confirmed_pct !== null && <> · {r.last_7_days.confirmed_pct}% of settled grants were later confirmed</>}
        </p>
      </div>
      <div className="card">
        <h2>Ending within 6 hours</h2>
        <table>
          <thead><tr><th>Ends</th><th>Exposure</th><th>Claim</th></tr></thead>
          <tbody>
            {r.expiring_soon.length === 0 && <tr><td colSpan={3} className="muted">Nothing is about to expire.</td></tr>}
            {r.expiring_soon.map((g) => (
              <tr key={g.grant_id}>
                <td>{new Date(g.expires_at).toLocaleString()}</td>
                <td>{money(g.exposure_minor, g.currency)}</td>
                <td><Link to={`/claims/${g.claim_id}`}>{g.claim_id.slice(0, 8)}</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
