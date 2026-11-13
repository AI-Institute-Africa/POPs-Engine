import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Claim, money } from "../api";
import { reasonText, type Tone } from "../labels";
import { Chip, EmptyState, Loading, PageHeader, StateChip } from "../ui";
import { bucketOf, slaOf, type Bucket } from "../lib/triage";
import { RiskBadge } from "./Fraud";
import "./review.css";

// [state filter value, label]. The label says what the reviewer should do, not what the code is called.
const FILTERS: [string, string][] = [
  ["", "Everything waiting"],
  ["FINANCIALLY_CONFIRMED", "Money confirmed"],
  ["PENDING_REVIEW", "Needs a person"],
  ["AWAITING_FINANCIAL_CONFIRMATION", "Waiting for the money"],
  ["DUPLICATE_HOLD", "Possible duplicates"],
  ["DOCUMENT_VERIFIED", "Document read"],
];

const TIERS: Record<string, { label: string; tone: Tone; hint: string }> = {
  GREEN: { label: "Green", tone: "ok", hint: "Green: evidence is strong and nothing is wrong. Quick to approve." },
  AMBER: { label: "Amber", tone: "warn", hint: "Amber: something is missing or unusual. Look before approving." },
  RED: { label: "Red", tone: "bad", hint: "Red: a hard stop such as a duplicate or wrong amount. Do not approve as it stands." },
};

const EVIDENCE: Record<string, { label: string; hint: string }> = {
  E0: { label: "Claim only", hint: "E0: the payer says they paid; nothing backs it up yet." },
  E1: { label: "Receipt only", hint: "E1: the receipt reads consistently, but a receipt alone is never proof." },
  E2: { label: "Alert confirmed", hint: "E2: backed by a payment alert or an attested manual record." },
  E3: { label: "Bank confirmed", hint: "E3: matched to a settled statement line or gateway record." },
};

export function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return null;
  const t = TIERS[tier.toUpperCase()] ?? { label: tier, tone: "neutral" as Tone, hint: "" };
  return <Chip tone={t.tone} title={t.hint}>{t.label}</Chip>;
}

export function StateBadge({ state }: { state: string }) {
  return <StateChip state={state} />;
}

export function EvidenceChip({ tier, compact }: { tier: string; compact?: boolean }) {
  const e = EVIDENCE[tier];
  return (
    <Chip tone={tier === "E3" || tier === "E2" ? "ok" : "neutral"} title={e?.hint ?? tier}>
      {e ? e.label : tier}{!compact && <span className="rv-code">{tier}</span>}
    </Chip>
  );
}

// "5 min ago", "3 h ago", "2 d ago". Exact time stays in the title.
export function agoText(iso: string): string {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (!(mins >= 1)) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)} h ago`;
  return `${Math.floor(mins / 1440)} d ago`;
}

export default function Queue() {
  const nav = useNavigate();
  const [state, setState] = useState("");
  const [channel, setChannel] = useState("");
  const [bucket, setBucket] = useState<Bucket>("needs_review");
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  if (channel.trim()) params.set("channel", channel.trim());
  const q = useQuery({
    queryKey: ["queue", state, channel],
    queryFn: () => api<Claim[]>(`/review/queue?${params}`),
    refetchInterval: 15000,
  });
  const filtered = !!state || !!channel.trim();
  const all = q.data;
  const counts = {
    needs_review: all?.filter((c) => bucketOf(c) === "needs_review").length ?? 0,
    flagged: all?.filter((c) => bucketOf(c) === "flagged").length ?? 0,
  };
  const rows = all?.filter((c) => bucketOf(c) === bucket);

  return (
    <section className="rv">
      <PageHeader
        title="Review queue"
        sub="Claims waiting for a person. The oldest are first. Click a row to open the receipt, the matching bank credit and the decision."
      />
      <div className="rv-tabs" role="tablist" aria-label="Triage">
        <button type="button" role="tab" aria-selected={bucket === "needs_review"} className="rv-tab" onClick={() => setBucket("needs_review")}>
          Needs review <span className="rv-tabcount">{counts.needs_review}</span>
        </button>
        <button type="button" role="tab" aria-selected={bucket === "flagged"} className="rv-tab flagged" onClick={() => setBucket("flagged")}>
          Flagged / high risk <span className="rv-tabcount">{counts.flagged}</span>
        </button>
        <Link className="rv-tab" to="/auto">Auto-verified (audit) →</Link>
      </div>
      <div className="rv-filters">
        <div className="rv-pills" role="group" aria-label="Show claims">
          {FILTERS.map(([v, label]) => (
            <button key={v} type="button" className="rv-pill" aria-pressed={state === v} onClick={() => setState(v)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="rv-filters">
        <input
          className="rv-search"
          type="search"
          placeholder="Channel, e.g. whatsapp"
          aria-label="Filter by channel"
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
        />
        {filtered && (
          <button type="button" className="ghost small" onClick={() => { setState(""); setChannel(""); }}>
            Clear filters
          </button>
        )}
        <span className="rv-count">
          {rows ? `${rows.length} claim${rows.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {q.isLoading && <Loading lines={5} />}
      {rows?.length === 0 && (
        filtered ? (
          <EmptyState icon="🔍" title="Nothing matches these filters" action={<button type="button" className="secondary" onClick={() => { setState(""); setChannel(""); }}>Show everything</button>}>
            Try a different status or clear the channel box.
          </EmptyState>
        ) : (
          <EmptyState icon="✓" title="The queue is clear" action={<Link className="btn secondary" to="/">Go to Home</Link>}>
            Nothing needs a person right now. Run a story from Home to fill this.
          </EmptyState>
        )
      )}
      {!!rows?.length && (
        <div className="card rv-flush">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Waiting</th>
                  <th>Status</th>
                  <th>Claimant</th>
                  <th className="num">Declared</th>
                  <th>Evidence</th>
                  <th title="Level: how careful to be. Risk: fake-receipt signals.">Level and risk</th>
                  <th>Why it is here</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr
                    key={c.claim_id}
                    className="rv-click"
                    onClick={(e) => { if (!(e.target as HTMLElement).closest("a,button")) nav(`/claims/${c.claim_id}`); }}
                  >
                    <td>
                      <Link className="rv-age" to={`/claims/${c.claim_id}`} title={new Date(c.created_at).toLocaleString()}>
                        {agoText(c.created_at)}
                      </Link>
                      {slaOf(c.created_at, Date.now()).level > 0 && (
                        <Chip tone={slaOf(c.created_at, Date.now()).tone} title="Waiting longer than the reminder thresholds">
                          overdue
                        </Chip>
                      )}
                    </td>
                    <td><StateChip state={c.state} /></td>
                    <td className="nowrap">
                      {c.claimant_ref}
                      <span className="rv-sub">{c.channel}</span>
                    </td>
                    <td className="num">{money(c.declared_amount_minor, c.declared_currency)}</td>
                    <td><EvidenceChip tier={c.evidence_tier} compact /></td>
                    <td><span className="rv-chips"><TierBadge tier={c.approval_tier} /><RiskBadge level={c.risk_level} score={c.risk_score} /></span></td>
                    <td className="rv-why">
                      {c.reason_codes.length === 0 ? (
                        <span className="muted">No problems recorded</span>
                      ) : (
                        <>
                          {reasonText(c.reason_codes[0])}
                          <div className="rv-codes">
                            {c.reason_codes.map((r) => (
                              <code key={r} className="rv-code" title={reasonText(r)}>{r}</code>
                            ))}
                          </div>
                        </>
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
