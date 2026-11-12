import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, hasRole, money } from "../api";
import { Chip, EmptyState, Loading, PageHeader } from "../ui";
import "./review.css";

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
  if (!hasRole("admin"))
    return (
      <section className="rv">
        <PageHeader title="Exposure" />
        <EmptyState icon="🔒" title="Admins only">Provisional access exposure is visible to administrators. Switch persona from the top right to see it.</EmptyState>
      </section>
    );
  if (q.error) return <section className="rv"><p className="error">{(q.error as Error).message}</p></section>;
  if (!q.data) return <section className="rv"><PageHeader title="Exposure" /><Loading lines={4} /></section>;
  const r = q.data;
  const active = Object.entries(r.active_exposure_minor);
  const l7 = r.last_7_days;
  return (
    <section className="rv">
      <PageHeader
        title="Exposure"
        sub="Provisional access lets a payer in before the money is confirmed. This is how much is at risk right now and for how long."
        actions={<Chip tone={r.enabled ? "ok" : "neutral"}>{r.enabled ? "Provisional access on" : "Provisional access off"}</Chip>}
      />
      <div className="card">
        <h2>Right now</h2>
        <p className="rv-help">
          {r.enabled
            ? `Access lasts ${r.caps.ttl_hours} h without a matching payment, then ends automatically.`
            : "Off for this organisation. Turn it on by publishing a payment policy with provisional_enabled."}{" "}
          The host system decides what access means; see docs/provisional-access.md.
        </p>
        <div className="rv-stats">
          <div className="stat">
            <div className="n">{active.length ? active.map(([c, v]) => money(v, c)).join(", ") : "None"}</div>
            <div className="l">open exposure</div>
          </div>
        </div>
        <h3>Limits</h3>
        <dl className="rv-kv">
          <dt>Per claim</dt>
          <dd>{caps(r.caps.per_claim_minor)}</dd>
          <dt>Per payer</dt>
          <dd>{caps(r.caps.per_learner_minor)}</dd>
          <dt>Per day, whole organisation</dt>
          <dd>{caps(r.caps.org_daily_minor)}</dd>
        </dl>
      </div>
      <div className="card">
        <h2>Last 7 days</h2>
        <div className="rv-stats">
          <div className="stat"><div className="n">{l7.granted}</div><div className="l">granted</div></div>
          <div className="stat"><div className="n">{l7.active}</div><div className="l">still open</div></div>
          <div className="stat"><div className="n">{l7.confirmed}</div><div className="l">later confirmed</div></div>
          <div className="stat"><div className="n">{l7.revoked}</div><div className="l">revoked</div></div>
          <div className="stat"><div className="n">{l7.expired}</div><div className="l">ran out</div></div>
        </div>
        {l7.confirmed_pct !== null && <p className="rv-help">{l7.confirmed_pct}% of settled grants were later confirmed by a real payment.</p>}
      </div>
      <div className="card">
        <h2>Ending within 6 hours</h2>
        {r.expiring_soon.length === 0 ? (
          <div className="rv-inline-empty">Nothing is about to expire.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Ends</th><th className="num">Exposure</th><th>Claim</th></tr></thead>
              <tbody>
                {r.expiring_soon.map((g) => (
                  <tr key={g.grant_id}>
                    <td>{new Date(g.expires_at).toLocaleString()}</td>
                    <td className="num">{money(g.exposure_minor, g.currency)}</td>
                    <td><Link to={`/claims/${g.claim_id}`}>{g.claim_id.slice(0, 8)}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
