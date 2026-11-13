import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { API, api, Candidate, Claim, Field, hasRole, money, post, ReviewDetail } from "../api";
import { humanize, stateInfo } from "../labels";
import { useToast } from "../toast";
import { Dialog } from "../components/Dialog";
import { compareRows, lowConfidenceCount, type CompareStatus } from "../lib/comparison";
import { REJECT_CONSEQUENCES, REJECT_PAYER_MESSAGE, REQUEST_INFO_NOTE } from "../lib/payerMessages";
import { cssFilter, DEFAULT_VIEW, isDefault, rotateBy, setLevel, zoomBy } from "../lib/viewer";
import { Chip, Loading, Reasons, StateChip } from "../ui";
import { agoText, EvidenceChip, TierBadge } from "./Queue";
import { RiskBadge, SignalList } from "./Fraud";
import "./review.css";

const REVIEWABLE = ["DOCUMENT_VERIFIED", "AWAITING_FINANCIAL_CONFIRMATION", "FINANCIALLY_CONFIRMED", "PENDING_REVIEW", "DUPLICATE_HOLD"];

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
  const [idx, setIdx] = useState(0);
  const [view, setView] = useState(DEFAULT_VIEW);
  const [tab, setTab] = useState<"image" | "json">("image");
  const ev = detail.evidence[idx] ?? detail.evidence[0];
  if (!ev) return <p className="muted">No evidence file.</p>;
  const latest = new Map<string, Field>();
  detail.fields.forEach((f) => latest.set(f.name, f));
  // the engine does not say which proof a field came from, so boxes are drawn on the first only
  const showBoxes = idx === 0;
  return (
    <div className="viewer">
      {detail.evidence.length > 1 && (
        <div className="rv-proofs" role="tablist" aria-label="Proofs submitted">
          {detail.evidence.map((e, i) => (
            <button key={e.evidence_id} type="button" role="tab" aria-selected={i === idx} className="rv-tab" onClick={() => setIdx(i)}>
              Proof {i + 1}
            </button>
          ))}
        </div>
      )}
      <div className="viewer-tools" role="toolbar" aria-label="Image tools">
        <button type="button" className="ghost small" onClick={() => setView(zoomBy(view, 0.25))} aria-label="Zoom in">＋</button>
        <button type="button" className="ghost small" onClick={() => setView(zoomBy(view, -0.25))} aria-label="Zoom out">－</button>
        <button type="button" className="ghost small" onClick={() => setView(rotateBy(view, 90))} aria-label="Rotate">⟳</button>
        <label className="small">Brightness
          <input type="range" min={0.5} max={2.5} step={0.1} value={view.brightness} onChange={(e) => setView(setLevel(view, "brightness", Number(e.target.value)))} />
        </label>
        <label className="small">Contrast
          <input type="range" min={0.5} max={2.5} step={0.1} value={view.contrast} onChange={(e) => setView(setLevel(view, "contrast", Number(e.target.value)))} />
        </label>
        {!isDefault(view) && <button type="button" className="ghost small" onClick={() => setView(DEFAULT_VIEW)}>Reset</button>}
        <button type="button" className="ghost small" onClick={() => setTab(tab === "image" ? "json" : "image")}>
          {tab === "image" ? "Raw data" : "Image"}
        </button>
      </div>
      {tab === "json" ? (
        <div>
          <pre className="rv-json">{JSON.stringify({ evidence: ev, fields: [...latest.values()] }, null, 2)}</pre>
          <button type="button" className="ghost small" onClick={() => void navigator.clipboard?.writeText([...latest.values()].map((f) => `${f.name}: ${f.raw_value ?? ""}`).join("\n"))}>
            Copy read text
          </button>
        </div>
      ) : (
      <div className="viewer-scroll">
      <div className="frame" style={{ transform: `scale(${view.zoom}) rotate(${view.rotate}deg)`, transformOrigin: "center top" }}>
        <img src={`${API}${ev.preview_url}`} alt={`Submitted proof of payment ${idx + 1}`} style={{ filter: cssFilter(view) }} />
        {showBoxes && [...latest.values()]
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
        {showBoxes && detail.tamper?.regions.map((r, i) => (
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
      </div>
      )}
      {!showBoxes && <p className="muted small">Field boxes are shown on Proof 1 only; the engine does not record which proof each value came from.</p>}
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

const CMP_TONE: Record<CompareStatus, "ok" | "bad" | "warn" | "neutral" | "info"> = {
  match: "ok",
  mismatch: "bad",
  missing: "warn",
  not_compared: "neutral",
  note: "info",
};
const CMP_LABEL: Record<CompareStatus, string> = {
  match: "Match",
  mismatch: "Mismatch",
  missing: "Not read",
  not_compared: "Not compared",
  note: "Check",
};

function Comparison({ detail, selected }: { detail: ReviewDetail; selected: number }) {
  const credit = detail.candidates[selected]?.credit ?? null;
  const { rows, notPerformed } = compareRows(detail.fields, detail.claim, credit);
  return (
    <div className="card">
      <h3>Receipt against the record</h3>
      <p className="rv-help">A mismatch is a reason to look closer, never a verdict. “Not compared” means that check was not done.</p>
      <table className="rv-compare">
        <thead><tr><th>Value</th><th>On the receipt</th><th>Expected</th><th /></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.label}</td>
              <td className="num">{r.observed ?? "—"}</td>
              <td className="num">{r.expected ?? "—"}</td>
              <td><Chip tone={CMP_TONE[r.status]} title={r.detail}>{CMP_LABEL[r.status]}</Chip></td>
            </tr>
          ))}
        </tbody>
      </table>
      {notPerformed.length > 0 && (
        <details>
          <summary className="small">Checks not performed ({notPerformed.length})</summary>
          <ul className="small muted">{notPerformed.map((n) => <li key={n}>{n}</li>)}</ul>
        </details>
      )}
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
      <h3>
        What was read from the receipt{" "}
        {lowConfidenceCount([...latest.values()]) > 0 && (
          <Chip tone="warn" title="Fields the readers disagreed on or were unsure about">
            {lowConfidenceCount([...latest.values()])} to check
          </Chip>
        )}
      </h3>
      <p className="rv-help">Hover a row to highlight where it was found on the image. Fix anything the readers got wrong; the claim is re-checked.</p>
      <div className="rv-fields">
        {[...latest.values()].map((f) => (
          <div key={f.field_id} className="rv-field" onMouseEnter={() => onHover(f.name)} onMouseLeave={() => onHover(null)}>
            <span className="k" title={f.name}>{humanize(f.name)}</span>
            <span className={`v ${f.agreed ? "" : "warn-text"}`}>
              {displayValue(f)}
              {f.raw_value && f.raw_value !== displayValue(f) && <span className="muted small"> ({f.raw_value})</span>}
            </span>
            <span className="src">
              {f.agreed ? <Chip tone="ok">Readers agree</Chip> : <Chip tone="warn">Check this</Chip>}
              {f.corrected_by ? `corrected: ${f.correction_reason}` : `${f.extractor} · ${Math.round(f.confidence * 100)}% sure`}
            </span>
            <span className="act">
              <button className="ghost small" onClick={() => { setEditing(f.name); setValue(f.raw_value ?? ""); setReason(""); }}>
                Correct
              </button>
            </span>
          </div>
        ))}
      </div>
      {editing && (
        <div className="rv-edit">
          <strong>Correct “{humanize(editing)}”</strong>
          <form className="inline" onSubmit={(e) => { e.preventDefault(); correct.mutate(); }}>
            <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Value as printed" aria-label="Value as printed" />
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (required)" aria-label="Reason for the correction" />
            <button disabled={reason.length < 3}>Save</button>
            <button type="button" className="ghost" onClick={() => setEditing(null)}>Cancel</button>
            {correct.error && <span className="error">{(correct.error as Error).message}</span>}
          </form>
        </div>
      )}
    </div>
  );
}

function CandidateCard({ c, selected, onSelect, index }: { c: Candidate; selected: boolean; onSelect: () => void; index: number }) {
  return (
    <div
      className={`candidate ${selected ? "selected" : ""} ${c.passed ? "" : "failed"}`}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(); } }}
    >
      <div className="row">
        <span className="kbd">{index + 1}</span>
        <strong>{money(c.credit.amount_minor, c.credit.currency)}</strong>
        <span>{c.credit.provider} · {c.credit.provider_txn_id}</span>
        <span className="muted">{new Date(c.credit.occurred_at).toLocaleString()}</span>
        <Chip tone={c.passed ? "ok" : "bad"}>{c.passed ? "Can be confirmed" : "Fails a check"}</Chip>
        {selected && <span className="pick">Selected</span>}
      </div>
      <div className="muted small">
        {c.credit.payer ?? "unknown payer"} · {c.credit.narrative ?? ""} · available{" "}
        {money(c.credit.available_minor, c.credit.currency)} · from {c.credit.source} · match score {c.score.toFixed(2)}
      </div>
      {!c.passed && c.near_miss && (
        <div className="warn-text small">
          Close match: the reference {c.near_miss.kind === "lookalike_characters" ? "differs only by look-alike characters (O/0, I/1)" : `differs by ${c.near_miss.distance} character${c.near_miss.distance === 1 ? "" : "s"}`}.
          It cannot be confirmed as it stands. If the receipt was misread, correct the transaction ID above and it will be matched.
        </div>
      )}
      <div className="gates">
        {c.gates.map((g) => (
          <span key={g.name} className={`chip ${g.passed ? "pass" : "fail"}`} title={g.detail}>
            {g.passed ? "✓" : "✗"} {humanize(g.name)}
          </span>
        ))}
      </div>
    </div>
  );
}

// What should the reviewer do next? Plain words, derived from what is already on screen.
function nextStep(d: ReviewDetail, selected: number, reviewable: boolean): { icon: string; tone: "ok" | "warn" | "bad" | "info"; text: string; jump?: boolean } {
  const c = d.claim;
  if (c.state === "APPROVED") return { icon: "✓", tone: "ok", text: "Approved and the invoice is paid. Nothing more to do. If it was wrong, reverse the allocation in the history below." };
  if (c.state === "REJECTED") return { icon: "✕", tone: "bad", text: "This claim was rejected. No further action." };
  if (c.state === "NEEDS_INFORMATION") return { icon: "…", tone: "warn", text: "Waiting for the payer to send a clearer photo. It comes back to you automatically." };
  if (!reviewable) return { icon: "i", tone: "info", text: `No decision is pending (${stateInfo(c.state).label.toLowerCase()}).` };
  if (c.reason_codes.includes("SUSPECTED_FAKE"))
    return { icon: "!", tone: "bad", text: "Suspected fake. An administrator has to rule on it under Suspected fakes before it can be confirmed." };
  if (c.state === "DUPLICATE_HOLD")
    return { icon: "!", tone: "bad", text: d.related_claims.length ? "The same proof was used before. Open the earlier claim, then reject this one if it is a repeat." : "The same proof was used before. Check the earlier use, then reject it if it is a repeat.", jump: true };
  if (d.candidates.length === 0)
    return { icon: "…", tone: "warn", text: "No bank credit found yet. Import a statement or wait for a payment alert. Do not approve on the receipt alone.", jump: true };
  const cand = d.candidates[selected];
  if (cand?.passed)
    return { icon: "→", tone: "info", text: `Confirm credit ${selected + 1} (${money(cand.credit.amount_minor, cand.credit.currency)}): look up the transaction ID at the source, not on the receipt, and type its last 4 characters.`, jump: true };
  if (d.candidates.some((x) => x.passed))
    return { icon: "→", tone: "info", text: "Credit " + (d.candidates.findIndex((x) => x.passed) + 1) + " passes every check. Select it, then confirm.", jump: true };
  return { icon: "!", tone: "warn", text: "No credit passes every check. Compare the candidates, correct anything the readers misread, or reject / ask the payer for more.", jump: true };
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
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reverseFor, setReverseFor] = useState<string | null>(null);
  const [reverseReason, setReverseReason] = useState("");
  const toast = useToast();
  const last4Ref = useRef<HTMLInputElement>(null);

  const q = useQuery({ queryKey: ["claim", id], queryFn: () => api<ReviewDetail>(`/review/claims/${id}`) });
  const queue = useQuery({ queryKey: ["queue", "", ""], queryFn: () => api<Claim[]>("/review/queue") });

  const done = (msg: string) => {
    toast("ok", msg);
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
    onSuccess: () => { setRejectOpen(false); done("Rejected"); },
  });
  const requestInfo = useMutation({
    mutationFn: () => post(`/review/claims/${id}/request-info`, { message: infoMsg }),
    onSuccess: () => done("Information requested from payer"),
  });
  const reverse = useMutation({
    mutationFn: (v: { allocationId: string; reason: string }) =>
      post(`/allocations/${v.allocationId}/reverse`, { reason: v.reason }),
    onSuccess: () => { setReverseFor(null); setReverseReason(""); done("Allocation reversed"); },
  });

  const nextId = queue.data?.find((c) => c.claim_id !== id)?.claim_id;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.target as HTMLElement).tagName.match(/INPUT|TEXTAREA|SELECT/)) return;
      if (e.key === "n" && nextId) nav(`/claims/${nextId}`);
      else if (e.key === "c") { e.preventDefault(); last4Ref.current?.focus(); }
      else if (e.key === "r" && q.data && REVIEWABLE.includes(q.data.claim.state)) { e.preventDefault(); setRejectOpen(true); }
      else if (/^[1-9]$/.test(e.key) && q.data && Number(e.key) <= q.data.candidates.length) setSelected(Number(e.key) - 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nextId, nav, q.data]);
  useEffect(() => { setSelected(0); }, [id]);

  if (q.isLoading) return <section className="rv"><Loading lines={6} /></section>;
  if (q.error) return <section className="rv"><p className="error">{(q.error as Error).message}</p></section>;
  const d = q.data!;
  const c = d.claim;
  const reviewable = REVIEWABLE.includes(c.state);
  const err = (confirm.error ?? reject.error ?? requestInfo.error ?? reverse.error) as Error | null;

  const step = nextStep(d, selected, reviewable);
  const tone = stateInfo(c.state).tone;
  const needsText = c.reason_codes.length
    ? null
    : reviewable
      ? "No problems recorded. It is here for a person to confirm the money."
      : "Nothing is blocking this claim.";
  const goDecision = () => document.getElementById("decision")?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <section className="rv">
      <div className="rv-topbar">
        <Link to="/queue">← Review queue</Link>
        <span className="rv-keys">
          Shortcuts: 1-9 pick a credit · c to confirm · r to reject · n for the next claim
          {nextId && <> · <Link to={`/claims/${nextId}`}>Next claim →</Link></>}
        </span>
      </div>

      <div className={`card rv-summary rv-tone-${tone}`}>
        <div className="rv-sum-top">
          <div>
            <div className="rv-eyebrow">Claim <span className="rv-code">{c.claim_id.slice(0, 8)}</span></div>
            <h1 className="rv-amount">{c.declared_amount_minor == null ? "No amount typed" : money(c.declared_amount_minor, c.declared_currency)}</h1>
            <div className="muted">
              declared by <strong>{c.claimant_ref}</strong> via {c.channel} · <span title={new Date(c.created_at).toLocaleString()}>{agoText(c.created_at)}</span>
            </div>
          </div>
          <div className="rv-chips">
            <StateChip state={c.state} />
            <TierBadge tier={c.approval_tier} />
            <EvidenceChip tier={c.evidence_tier} />
            <RiskBadge level={c.risk_level} score={c.risk_score} />
          </div>
        </div>

        <dl className="rv-facts">
          <div><dt>Invoice</dt><dd>{d.obligation.description || d.obligation.type}</dd></div>
          <div><dt>For</dt><dd>{d.obligation.learner_ref}</dd></div>
          <div>
            <dt>Still to pay</dt>
            <dd>
              {money(d.balance.outstanding_minor, d.balance.currency)} <span className="muted small">of {money(d.balance.amount_minor, d.balance.currency)} ({humanize(d.balance.payment_status).toLowerCase()})</span>
            </dd>
          </div>
          <div><dt>Payment code</dt><dd><code>{d.obligation.payment_code}</code></dd></div>
          <div><dt>Reference they gave</dt><dd>{c.declared_reference ?? "—"}</dd></div>
          {d.related_claims.length > 0 && (
            <div><dt>Same file in</dt><dd>{d.related_claims.map((r) => <Link key={r} to={`/claims/${r}`}>{r.slice(0, 8)} </Link>)}</dd></div>
          )}
        </dl>

        <div className="rv-sum-why">
          <h3>Why it needs a person</h3>
          {needsText ? <p className="muted small" style={{ margin: 0 }}>{needsText}</p> : <Reasons codes={c.reason_codes} />}
        </div>

        <div className={`rv-next rv-tone-${step.tone}`}>
          <span className="ico" aria-hidden>{step.icon}</span>
          <div className="body">
            <div className="lbl">Suggested next step</div>
            <p>{step.text}</p>
            {step.jump && reviewable && (
              <button type="button" className="secondary small go" onClick={goDecision}>Go to the decision</button>
            )}
            {c.reason_codes.includes("SUSPECTED_FAKE") && <p className="small"><Link to="/fraud">Open Suspected fakes</Link></p>}
          </div>
        </div>
      </div>

      {err && !reviewable && <p className="error" role="alert">{err.message}</p>}
      <div className="split">
        <EvidenceViewer detail={d} hover={hover} />
        <div className="stack">
          {c.risk_signals.length > 0 && (
            <div className={`card risk-card risk-${c.risk_level}`}>
              <h3>Fake-receipt risk: {c.risk_score}/100</h3>
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
          <Comparison detail={d} selected={selected} />
          <Fields detail={d} onHover={setHover} claimId={id} />
          <div className="card">
            <h3>Credits that could match</h3>
            <p className="rv-help">Money that arrived in the bank or merchant record. Pick the one that belongs to this payment; only a credit that passes every check can be confirmed.</p>
            {d.candidates.length === 0 && <p className="muted">No credit found yet. Import a statement or wait for the alert.</p>}
            {d.candidates.map((cand, i) => (
              <CandidateCard key={cand.credit.credit_id} c={cand} index={i} selected={i === selected} onSelect={() => setSelected(i)} />
            ))}
          </div>
          {d.shadow_proposals && d.shadow_proposals.length > 0 && (
            <div className="card">
              <h3>Advisory proposals</h3>
              <p className="muted small">Shadow mode: recorded for measurement, never acted on.</p>
              <ul className="history">
                {d.shadow_proposals.map((sp) => (
                  <li key={sp.proposal_id}>
                    <strong>{humanize(sp.proposed_outcome)}</strong> <span className="muted small">{sp.proposer} · confidence {sp.confidence.toFixed(2)}</span>{" "}
                    {sp.rationale}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {reviewable && (
            <div className="card actions" id="decision">
              <h3>Your decision</h3>
              {err && <p className="error" role="alert">{err.message}</p>}
              <div className="rv-decide">
                <div className="rv-action primary">
                  <h4>Confirm the money</h4>
                  <p className="rv-help">Look up the transaction ID at the source (bank app or merchant portal), not on the receipt, and type its last 4 characters.</p>
                  <form className="inline" onSubmit={(e) => { e.preventDefault(); confirm.mutate(); }}>
                    <input ref={last4Ref} value={last4} maxLength={4} onChange={(e) => setLast4(e.target.value)}
                      placeholder="Last 4 of txn ID (from source)" aria-label="Last four characters of transaction ID" />
                    <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Note" />
                    <button disabled={last4.length !== 4 || !d.candidates[selected]?.passed || confirm.isPending}>
                      Confirm credit {selected + 1}
                    </button>
                  </form>
                </div>
                <div className="rv-action danger">
                  <h4>Reject</h4>
                  <p className="rv-help">Declines this claim. The reason you pick goes in the audit trail.</p>
                  <div className="inline">
                    <select value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} aria-label="Reject reason">
                      {REJECT_REASONS.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
                    </select>
                    <button type="button" className="danger" onClick={() => setRejectOpen(true)}>Reject…</button>
                  </div>
                </div>
                <div className="rv-action">
                  <h4>Ask the payer for more</h4>
                  <p className="rv-help">For a blurry photo or a missing detail. {REQUEST_INFO_NOTE}</p>
                  <form className="inline" onSubmit={(e) => { e.preventDefault(); requestInfo.mutate(); }}>
                    <input value={infoMsg} onChange={(e) => setInfoMsg(e.target.value)} placeholder="Message to payer" aria-label="Message to payer" />
                    <button className="ghost" disabled={infoMsg.length < 3}>Request info</button>
                  </form>
                </div>
              </div>
            </div>
          )}
          <div className="card">
            <h3>Audit trail</h3>
            <ul className="rv-history">
              {d.decisions.map((x) => (
                <li key={x.decision_id}>
                  <div className="what">{humanize(x.outcome)} <span className="muted small" style={{ fontWeight: 400 }}>by {x.actor_id}</span></div>
                  <div className="when">{new Date(x.created_at).toLocaleString()} · policy {x.policy_version}</div>
                  {x.reason_codes.length > 0 && <Reasons codes={x.reason_codes} />}
                  {x.note && <div className="note">“{x.note}”</div>}
                </li>
              ))}
              {d.allocations.map((a) => (
                <li key={a.allocation_id}>
                  <div className="what">
                    {humanize(a.kind)} {money(a.amount_minor, a.currency)} <code className="rv-code">{a.allocation_id.slice(0, 8)}</code>
                  </div>
                  <div className="when">{new Date(a.created_at).toLocaleString()}</div>
                  {a.kind === "allocation" && !d.allocations.some((r) => r.reverses === a.allocation_id) && (
                    <button className="ghost small" style={{ marginTop: 6 }} disabled={!hasRole("approver") || reverse.isPending} onClick={() => { setReverseFor(a.allocation_id); setReverseReason(""); }}>
                      Reverse
                    </button>
                  )}
                </li>
              ))}
              {d.decisions.length === 0 && d.allocations.length === 0 && <li className="muted">Nothing recorded yet.</li>}
            </ul>
          </div>
        </div>
      </div>
      {reviewable && (
        <div className="decision-bar" role="region" aria-label="Decision bar">
          <span className="db-sum">
            {d.candidates[selected]
              ? `Credit ${selected + 1}: ${money(d.candidates[selected].credit.amount_minor, d.candidates[selected].credit.currency)}${d.candidates[selected].passed ? "" : " (fails a check)"}`
              : "No credit selected"}
          </span>
          <button type="button" onClick={() => { goDecision(); setTimeout(() => last4Ref.current?.focus(), 350); }}>Confirm…</button>
          <button type="button" className="danger" onClick={() => setRejectOpen(true)}>Reject…</button>
          {nextId && <Link className="btn secondary" to={`/claims/${nextId}`}>Next claim →</Link>}
        </div>
      )}

      {rejectOpen && (
        <Dialog title="Reject this claim?" onClose={() => setRejectOpen(false)}>
          <p>Reason (audit trail only): <strong>{humanize(rejectReason)}</strong></p>
          <label className="small">Note (optional)
            <input value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <p className="small"><strong>The payer will be told:</strong></p>
          <blockquote className="payer-msg">{REJECT_PAYER_MESSAGE}</blockquote>
          <ul className="small">{REJECT_CONSEQUENCES.map((x) => <li key={x}>{x}</li>)}</ul>
          {reject.error && <p className="error">{(reject.error as Error).message}</p>}
          <div className="dialog-actions">
            <button type="button" className="ghost" onClick={() => setRejectOpen(false)}>Cancel</button>
            <button type="button" className="danger" disabled={reject.isPending} onClick={() => reject.mutate()}>Reject claim</button>
          </div>
        </Dialog>
      )}

      {reverseFor && (
        <Dialog title="Reverse this allocation?" onClose={() => setReverseFor(null)}>
          <p className="small">This puts the money back: the invoice shows as unpaid again and the credit becomes available. It is recorded in the audit trail. Approvers only.</p>
          <label className="small">Reason (required)
            <input value={reverseReason} onChange={(e) => setReverseReason(e.target.value)} autoFocus />
          </label>
          {reverse.error && <p className="error">{(reverse.error as Error).message}</p>}
          <div className="dialog-actions">
            <button type="button" className="ghost" onClick={() => setReverseFor(null)}>Cancel</button>
            <button type="button" className="danger" disabled={reverseReason.trim().length < 3 || reverse.isPending} onClick={() => reverse.mutate({ allocationId: reverseFor, reason: reverseReason.trim() })}>Reverse allocation</button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
