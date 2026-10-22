import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { API, api, Candidate, Claim, Field, money, post, ReviewDetail } from "../api";
import { StateBadge, TierBadge } from "./Queue";
import { RiskBadge, SignalList } from "./Fraud";

const REJECT_REASONS = [
  "PAYMENT_NOT_RECEIVED",
  "DUPLICATE_CONFIRMED",
  "PAYMENT_FAILED_OR_REVERSED",
  "WRONG_BENEFICIARY",
  "AMOUNT_MISMATCH",
  "CURRENCY_MISMATCH",
  "TAMPERED_DOCUMENT",
  "WRONG_OBLIGATION",
  "OTHER",
];

function displayValue(f: Field): string {
  if (f.name === "amount" && f.normalised_value) return (Number(f.normalised_value) / 100).toFixed(2);
  return f.normalised_value ?? f.raw_value ?? "—";
}

function EvidenceViewer({ detail, hover }: { detail: ReviewDetail; hover: string | null }) {
  const ev = detail.evidence[0];
  if (!ev) return <p className="muted">No evidence file.</p>;
  const latest = new Map<string, Field>();
  detail.fields.forEach((f) => latest.set(f.name, f));
  return (
    <div className="viewer">
      <div className="frame">
        <img src={`${API}${ev.preview_url}`} alt="Submitted proof of payment" />
        {[...latest.values()]
          .filter((f) => f.region)
          .map((f) => (
            <div
              key={f.field_id}
              className={`box ${f.agreed ? "ok" : "warn"} ${hover === f.name ? "hot" : ""}`}
              style={{
                left: `${f.region!.x * 100}%`,
                top: `${f.region!.y * 100}%`,
                width: `${f.region!.w * 100}%`,
                height: `${f.region!.h * 100}%`,
              }}
              title={`${f.name}: ${displayValue(f)}`}
            />
          ))}
        {detail.tamper?.regions.map((r, i) => (
          <div
            key={`tamper-${i}`}
            className="box tamper"
            style={{
              left: `${r.x * 100}%`,
              top: `${r.y * 100}%`,
              width: `${r.w * 100}%`,
              height: `${r.h * 100}%`,
            }}
            title={`Possible manipulation (score ${r.score.toFixed(2)}) — a risk flag, not a verdict`}
          />
        ))}
      </div>
      {detail.tamper && (
        <p className="muted small">
          Manipulation risk score <strong>{detail.tamper.score.toFixed(2)}</strong>
          {detail.tamper.skipped ? ` (check skipped: ${detail.tamper.skipped})` : ""}
          {detail.tamper.regions.length > 0 && ` · ${detail.tamper.regions.length} region(s) highlighted in red`}
          . A flag only routes the claim to review; it can never approve or reject it.
        </p>
      )}
      <div className="muted small">
        <a href={`${API}${ev.original_url}`} target="_blank" rel="noreferrer">Open original</a> · sha256{" "}
        {ev.sha256.slice(0, 12)}… · {ev.content_type}
        {ev.meta.exif ? <> · EXIF: {JSON.stringify(ev.meta.exif)}</> : null}
      </div>
    </div>
  );
}

function Fields({ detail, onHover, claimId }: { detail: ReviewDetail; onHover: (n: string | null) => void; claimId: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const correct = useMutation({
    mutationFn: () => post(`/review/claims/${claimId}/fields`, { name: editing, value, reason }),
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["claim", claimId] });
    },
  });
  const latest = new Map<string, Field>();
  detail.fields.forEach((f) => latest.set(f.name, f));
  return (
    <div className="card">
      <h3>Extracted fields</h3>
      <table className="fields">
        <tbody>
          {[...latest.values()].map((f) => (
            <tr key={f.field_id} onMouseEnter={() => onHover(f.name)} onMouseLeave={() => onHover(null)}>
              <th>{f.name}</th>
              <td className={f.agreed ? "" : "warn-text"}>
                {displayValue(f)}
                {f.raw_value && f.raw_value !== displayValue(f) && <span className="muted small"> ({f.raw_value})</span>}
              </td>
              <td className="small muted">
                {f.corrected_by ? `corrected: ${f.correction_reason}` : `${f.extractor} · ${Math.round(f.confidence * 100)}%`}
              </td>
              <td>{f.agreed ? "✓" : <span className="badge tier-amber">check</span>}</td>
              <td>
                <button className="ghost small" onClick={() => { setEditing(f.name); setValue(f.raw_value ?? ""); setReason(""); }}>
                  Correct
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <form className="inline" onSubmit={(e) => { e.preventDefault(); correct.mutate(); }}>
          <strong>{editing}</strong>
          <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Value as printed" />
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (required)" />
          <button disabled={reason.length < 3}>Save</button>
          <button type="button" className="ghost" onClick={() => setEditing(null)}>Cancel</button>
          {correct.error && <span className="error">{(correct.error as Error).message}</span>}
        </form>
      )}
    </div>
  );
}

function CandidateCard({ c, selected, onSelect, index }: { c: Candidate; selected: boolean; onSelect: () => void; index: number }) {
  return (
    <div className={`candidate ${selected ? "selected" : ""} ${c.passed ? "" : "failed"}`} onClick={onSelect}>
      <div className="row">
        <span className="kbd">{index + 1}</span>
        <strong>{money(c.credit.amount_minor, c.credit.currency)}</strong>
        <span>{c.credit.provider} · {c.credit.provider_txn_id}</span>
        <span className="muted">{new Date(c.credit.occurred_at).toLocaleString()}</span>
        <span className="badge">{c.credit.source}</span>
        <span className="muted">score {c.score.toFixed(2)}</span>
      </div>
      <div className="muted small">
        {c.credit.payer ?? "unknown payer"} · {c.credit.narrative ?? ""} · available{" "}
        {money(c.credit.available_minor, c.credit.currency)}
      </div>
      <div className="gates">
        {c.gates.map((g) => (
          <span key={g.name} className={`chip ${g.passed ? "pass" : "fail"}`} title={g.detail}>
            {g.passed ? "✓" : "✗"} {g.name}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function ClaimReview() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [hover, setHover] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [last4, setLast4] = useState("");
  const [note, setNote] = useState("");
  const [rejectReason, setRejectReason] = useState(REJECT_REASONS[0]);
  const [infoMsg, setInfoMsg] = useState("");
  const [flash, setFlash] = useState<string | null>(null);
  const last4Ref = useRef<HTMLInputElement>(null);

  const q = useQuery({ queryKey: ["claim", id], queryFn: () => api<ReviewDetail>(`/review/claims/${id}`) });
  const queue = useQuery({ queryKey: ["queue", "", ""], queryFn: () => api<Claim[]>("/review/queue") });

  const done = (msg: string) => {
    setFlash(msg);
    setLast4("");
    qc.invalidateQueries({ queryKey: ["claim", id] });
    qc.invalidateQueries({ queryKey: ["queue"] });
  };
  const confirm = useMutation({
    mutationFn: () =>
      post<{ needs_second_approval: boolean; claim: Claim }>(`/review/claims/${id}/confirm`, {
        credit_id: q.data!.candidates[selected].credit.credit_id,
        txn_last4: last4,
        note: note || null,
      }),
    onSuccess: (r) => done(r.needs_second_approval ? "First approval recorded; a different approver must confirm." : `Confirmed: ${r.claim.state}`),
  });
  const reject = useMutation({
    mutationFn: () => post(`/review/claims/${id}/reject`, { reason: rejectReason, note: note || null }),
    onSuccess: () => done("Rejected"),
  });
  const requestInfo = useMutation({
    mutationFn: () => post(`/review/claims/${id}/request-info`, { message: infoMsg }),
    onSuccess: () => done("Information requested from payer"),
  });

  const nextId = queue.data?.find((c) => c.claim_id !== id)?.claim_id;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.target as HTMLElement).tagName.match(/INPUT|TEXTAREA|SELECT/)) return;
      if (e.key === "n" && nextId) nav(`/claims/${nextId}`);
      else if (e.key === "c") { e.preventDefault(); last4Ref.current?.focus(); }
      else if (/^[1-9]$/.test(e.key) && q.data && Number(e.key) <= q.data.candidates.length) setSelected(Number(e.key) - 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nextId, nav, q.data]);
  useEffect(() => { setSelected(0); setFlash(null); }, [id]);

  if (q.isLoading) return <p>Loading…</p>;
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  const d = q.data!;
  const c = d.claim;
  const reviewable = ["DOCUMENT_VERIFIED", "AWAITING_FINANCIAL_CONFIRMATION", "FINANCIALLY_CONFIRMED", "PENDING_REVIEW", "DUPLICATE_HOLD"].includes(c.state);
  const err = (confirm.error ?? reject.error ?? requestInfo.error) as Error | null;

  return (
    <section>
      <div className="toolbar">
        <Link to="/queue">← Queue</Link>
        <h2>Claim {c.claim_id.slice(0, 8)}</h2>
        <StateBadge state={c.state} />
        <TierBadge tier={c.approval_tier} />
        <span className="badge">{c.evidence_tier}</span>
        <RiskBadge level={c.risk_level} score={c.risk_score} />
        <span className="muted small">keys: 1-9 pick credit · c confirm · n next</span>
      </div>
      {flash && <p className="flash">{flash}</p>}
      {err && <p className="error">{err.message}</p>}
      <div className="split">
        <EvidenceViewer detail={d} hover={hover} />
        <div className="stack">
          <div className="card">
            <h3>Claim</h3>
            <dl>
              <dt>Obligation</dt>
              <dd>{d.obligation.description || d.obligation.type} · {d.obligation.learner_ref} · code <code>{d.obligation.payment_code}</code></dd>
              <dt>Balance</dt>
              <dd>{money(d.balance.paid_minor, d.balance.currency)} paid of {money(d.balance.amount_minor, d.balance.currency)} ({d.balance.payment_status})</dd>
              <dt>Declared</dt>
              <dd>{money(c.declared_amount_minor, c.declared_currency)} · ref {c.declared_reference ?? "—"} · {c.channel}</dd>
              <dt>Reasons</dt>
              <dd>{c.reason_codes.length ? c.reason_codes.join(", ") : "none"}</dd>
              {d.related_claims.length > 0 && (
                <>
                  <dt>Same file in</dt>
                  <dd>{d.related_claims.map((r) => <Link key={r} to={`/claims/${r}`}>{r.slice(0, 8)} </Link>)}</dd>
                </>
              )}
            </dl>
          </div>
          {c.risk_signals.length > 0 && (
            <div className={`card risk-card risk-${c.risk_level}`}>
              <h3>Fake-POP risk: {c.risk_score}/100</h3>
              {c.reason_codes.includes("SUSPECTED_FAKE") && (
                <p className="error small">
                  Suspected fake: an admin must decide under <Link to="/fraud">Suspected fakes</Link> before this claim can be confirmed.
                </p>
              )}
              <SignalList signals={c.risk_signals} />
              {d.evidence[0]?.forensics_url && (
                <a href={`${API}${d.evidence[0].forensics_url}`} target="_blank" rel="noreferrer">Open forensic heatmap (ELA)</a>
              )}
            </div>
          )}
          <Fields detail={d} onHover={setHover} claimId={id} />
          <div className="card">
            <h3>Candidate credits</h3>
            {d.candidates.length === 0 && <p className="muted">No credit found yet. Import a statement or wait for the alert.</p>}
            {d.candidates.map((cand, i) => (
              <CandidateCard key={cand.credit.credit_id} c={cand} index={i} selected={i === selected} onSelect={() => setSelected(i)} />
            ))}
          </div>
          {reviewable && (
            <div className="card actions">
              <h3>Decision</h3>
              <form className="inline" onSubmit={(e) => { e.preventDefault(); confirm.mutate(); }}>
                <input ref={last4Ref} value={last4} maxLength={4} onChange={(e) => setLast4(e.target.value)}
                  placeholder="Last 4 of txn ID (from source)" aria-label="Last four characters of transaction ID" />
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" />
                <button disabled={last4.length !== 4 || !d.candidates[selected]?.passed || confirm.isPending}>
                  Confirm credit {selected + 1}
                </button>
              </form>
              <form className="inline" onSubmit={(e) => { e.preventDefault(); reject.mutate(); }}>
                <select value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} aria-label="Reject reason">
                  {REJECT_REASONS.map((r) => <option key={r}>{r}</option>)}
                </select>
                <button className="danger">Reject</button>
              </form>
              <form className="inline" onSubmit={(e) => { e.preventDefault(); requestInfo.mutate(); }}>
                <input value={infoMsg} onChange={(e) => setInfoMsg(e.target.value)} placeholder="Message to payer" />
                <button className="ghost" disabled={infoMsg.length < 3}>Request info</button>
              </form>
            </div>
          )}
          <div className="card">
            <h3>History</h3>
            <ul className="history">
              {d.decisions.map((x) => (
                <li key={x.decision_id}>
                  <span className="muted small">{new Date(x.created_at).toLocaleString()}</span> <strong>{x.outcome}</strong> by {x.actor_id}{" "}
                  <span className="muted small">policy {x.policy_version}</span> {x.reason_codes.join(", ")} {x.note && `— ${x.note}`}
                </li>
              ))}
              {d.allocations.map((a) => (
                <li key={a.allocation_id}>
                  <strong>{a.kind}</strong> {money(a.amount_minor, a.currency)} <span className="muted small">{a.allocation_id.slice(0, 8)}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}
