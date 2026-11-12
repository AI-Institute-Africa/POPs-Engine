import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, blobUrl, hasRole, post } from "../api";
import { CHECKS, DOC_TYPES, FIELD_LABELS } from "../labels";
import { useToast } from "../toast";
import { EmptyState, Loading, PageHeader, Reasons, StateChip } from "../ui";

type QueueRow = {
  kyc_id: string;
  subject_type: string;
  subject_ref: string;
  created_at: string;
  doc_type: string | null;
  issuing_country: string | null;
  reason_codes: string[];
  face_score: number | null;
};
type Detail = QueueRow & {
  state: string;
  fields: { values: Record<string, string | null>; agreed: Record<string, boolean>; sources: Record<string, Record<string, string | null>> };
  checks: { name: string; ok: boolean | null; [k: string]: unknown }[];
  scores: { ocr_agreement: number | null; mrz_valid: boolean | null; face: number | null; liveness: number | null };
  evidence: { role: string; purged: boolean }[];
};
type Auto = {
  mode: string;
  enabled: boolean;
  audit_pct: number;
  summary: { verified: number; sampled: number; audited: number; overturned: number; overturn_rate: number | null };
  verifications: {
    kyc_id: string;
    verified_at: string;
    subject_type: string | null;
    subject_ref: string | null;
    doc_type: string | null;
    face_score: number | null;
    sampled: boolean;
    verdict: { outcome: string; by: string; note: string | null } | null;
  }[];
};

function Img({ id, role, label }: { id: string; role: string; label: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    blobUrl(`/kyc/verifications/${id}/evidence/${role}`)
      .then((u) => { url = u; setSrc(u); })
      .catch(() => setFailed(true));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id, role]);
  return (
    <figure>
      {src ? <img src={src} alt={label} /> : <div className="skeleton-line" style={{ height: 180, borderRadius: 10 }}>{failed ? "Image not available" : ""}</div>}
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function Meter({ label, value, good, fmt }: { label: string; value: number | null; good: number; fmt?: (v: number) => string }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(1, value)) * 100;
  const ok = value !== null && value >= good;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span>{label}</span>
        <b>{value === null ? "n/a" : fmt ? fmt(value) : value.toFixed(2)}</b>
      </div>
      <div className="meter" aria-hidden>
        <i style={{ width: `${pct}%`, background: value === null ? "var(--muted)" : ok ? "var(--ok)" : "var(--warn)" }} />
      </div>
    </div>
  );
}

// Reviewer view: everything the readers and the face matcher produced, beside the images.
// A disagreement between sources is shown, never hidden. The reviewer decides.
function Review({ id, onDone }: { id: string; onDone: () => void }) {
  const toast = useToast();
  const q = useQuery({ queryKey: ["kyc", id], queryFn: () => api<Detail>(`/kyc/verifications/${id}`) });
  const decide = useMutation({
    mutationFn: (v: { outcome: string; note: string | null }) => post(`/kyc/review/${id}/decision`, v),
    onSuccess: () => { toast("ok", "Decision recorded."); onDone(); },
    onError: (e) => toast("err", (e as Error).message),
  });
  const ask = (outcome: string) => {
    const note = outcome === "VERIFIED" ? window.prompt("Note (optional)") ?? "" : window.prompt("Reason (required, shown in the audit trail)");
    if (note === null || (outcome !== "VERIFIED" && note.trim().length < 3)) return;
    decide.mutate({ outcome, note: note.trim() || null });
  };
  if (q.isLoading) return <Loading lines={5} />;
  if (!q.data) return <p className="error">{(q.error as Error)?.message ?? "Not found"}</p>;
  const d = q.data;
  const values = d.fields?.values ?? {};
  // only fields that were actually read: a field that is not on this document is not a failure
  const rows = Object.keys(FIELD_LABELS).filter((k) => values[k] != null);
  const open = d.state === "NEEDS_REVIEW";
  return (
    <div className="card">
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>
          {d.subject_type}: {d.subject_ref}
        </h2>
        <StateChip state={d.state} />
      </div>
      <p className="muted small">
        {d.doc_type ? DOC_TYPES[d.doc_type] ?? d.doc_type : "Unknown document"}
        {d.issuing_country ? ` · issued by ${d.issuing_country}` : ""}
      </p>
      <h3>Why it needs a person</h3>
      <Reasons codes={d.reason_codes} empty="No blockers recorded." />
      <div className="compare" style={{ marginTop: 14 }}>
        <Img id={id} role="front" label="The document" />
        <Img id={id} role="selfie" label="The selfie" />
      </div>
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <div>
          <h3>Scores</h3>
          <Meter label="Face match" value={d.scores.face} good={0.6} />
          <Meter label="Looks like a live capture" value={d.scores.liveness} good={0.7} />
          <Meter label="Readers agree on key fields" value={d.scores.ocr_agreement} good={1} fmt={(v) => `${Math.round(v * 100)}%`} />
          <p className="small muted">
            Machine-readable zone: {d.scores.mrz_valid === null ? "not on this document" : d.scores.mrz_valid ? "check digits valid" : "check digits FAILED"}
          </p>
        </div>
        <div>
          <h3>Checks run</h3>
          <ul className="checks">
            {d.checks.map((c) => (
              <li key={c.name}>
                <span className={`mk ${c.ok === null ? "na" : c.ok ? "ok" : "bad"}`} aria-hidden>{c.ok === null ? "–" : c.ok ? "✓" : "✕"}</span>
                {CHECKS[c.name] ?? c.name}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <h3 style={{ marginTop: 14 }}>What was read</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Field</th><th>Chosen value</th><th>Two sources agree</th><th>Each source read</th></tr>
          </thead>
          <tbody>
            {rows.map((k) => (
              <tr key={k}>
                <td>{FIELD_LABELS[k]}</td>
                <td><b>{values[k] ?? "—"}</b></td>
                <td>{d.fields.agreed[k] ? <span className="chip2 tone-ok">Yes</span> : <span className="chip2 tone-warn">No</span>}</td>
                <td className="small muted">{Object.entries(d.fields.sources[k] ?? {}).map(([s, x]) => `${s.replace(/^fake_id_/, "").replace(/^id_/, "")}: ${x}`).join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && hasRole("reviewer") && (
        <div className="scn-actions" style={{ marginTop: 16 }}>
          <button type="button" disabled={decide.isPending} onClick={() => ask("VERIFIED")}>Verify</button>
          <button type="button" className="secondary" disabled={decide.isPending} onClick={() => ask("NEEDS_INFORMATION")}>Ask for new photos</button>
          <button type="button" className="danger" disabled={decide.isPending} onClick={() => ask("REJECTED")}>Reject</button>
        </div>
      )}
      {!open && <p className="muted small" style={{ marginTop: 12 }}>This check is {d.state.toLowerCase().replace(/_/g, " ")}; no decision is pending.</p>}
    </div>
  );
}

export default function KycReview() {
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const open = params.get("open");
  const setOpen = (id: string | null) => setParams(id ? { open: id } : {}, { replace: true });
  const queue = useQuery({ queryKey: ["kyc-queue"], queryFn: () => api<QueueRow[]>("/kyc/review/queue"), refetchInterval: 20000 });
  const auto = useQuery({ queryKey: ["kyc-auto"], queryFn: () => api<Auto>("/kyc/review/auto-verified") });
  const verdict = useMutation({
    mutationFn: (v: { id: string; verdict: "ok" | "overturned"; note: string | null }) =>
      post(`/kyc/review/${v.id}/audit-verdict`, { verdict: v.verdict, note: v.note }),
    onSuccess: () => { toast("ok", "Verdict recorded. Revoke the verification separately if it was wrong."); qc.invalidateQueries({ queryKey: ["kyc-auto"] }); },
    onError: (e) => toast("err", (e as Error).message),
  });
  const done = () => { setOpen(null); qc.invalidateQueries({ queryKey: ["kyc-queue"] }); qc.invalidateQueries({ queryKey: ["kyc-auto"] }); qc.invalidateQueries({ queryKey: ["kyc-count"] }); };
  const a = auto.data;
  return (
    <div>
      <PageHeader title="Identity checks" sub="Cases the rules did not clear on their own. Open one to see the documents, every score and the reason." />
      {queue.error && <p className="error">{(queue.error as Error).message}</p>}
      {queue.isLoading && <Loading />}
      {queue.data?.length === 0 && (
        <EmptyState icon="✓" title="Nothing waiting">Every identity check has been decided. New ones appear here as people submit them.</EmptyState>
      )}
      {!!queue.data?.length && (
        <div className="card" style={{ padding: 0 }}>
          <div className="table-wrap">
            <table style={{ border: 0 }}>
              <thead>
                <tr><th>Submitted</th><th>Subject</th><th>Document</th><th>Face match</th><th>Why</th></tr>
              </thead>
              <tbody>
                {queue.data.map((r) => (
                  <tr key={r.kyc_id} onClick={() => setOpen(r.kyc_id)} style={{ cursor: "pointer", background: open === r.kyc_id ? "color-mix(in srgb, var(--accent) 8%, transparent)" : undefined }}>
                    <td>{new Date(r.created_at).toLocaleString()}</td>
                    <td>{r.subject_type}: {r.subject_ref}</td>
                    <td>{r.doc_type ? DOC_TYPES[r.doc_type] ?? r.doc_type : "?"}</td>
                    <td>{r.face_score?.toFixed(2) ?? "n/a"}</td>
                    <td className="small muted">{r.reason_codes.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {open && <Review key={open} id={open} onDone={done} />}
      {a && (
        <div className="card">
          <h2>Verified automatically</h2>
          <p className="muted small">
            Mode: <b>{a.mode}</b>
            {a.mode === "shadow" && " (recording what the rules would do; nothing is verified automatically)"}. {a.audit_pct}% are sampled
            for a person to check. The overturn rate decides whether the rules can be widened.
          </p>
          <p>
            {a.summary.verified} verified · {a.summary.sampled} sampled · {a.summary.audited} audited · {a.summary.overturned} overturned
            {a.summary.overturn_rate !== null && <> · overturn rate <b>{(a.summary.overturn_rate * 100).toFixed(1)}%</b></>}
          </p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Verified</th><th>Subject</th><th>Face match</th><th>Audit</th><th /></tr></thead>
              <tbody>
                {a.verifications.length === 0 && <tr><td colSpan={5} className="muted">None yet.</td></tr>}
                {a.verifications.map((v) => (
                  <tr key={v.kyc_id}>
                    <td><button type="button" className="link" onClick={() => setOpen(v.kyc_id)}>{new Date(v.verified_at).toLocaleString()}</button></td>
                    <td>{v.subject_type}: {v.subject_ref}</td>
                    <td>{v.face_score?.toFixed(2) ?? "n/a"}</td>
                    <td>{v.verdict ? `${v.verdict.outcome === "KYC_AUDIT_OK" ? "Correct" : "Overturned"} by ${v.verdict.by}` : v.sampled ? "In the sample: please check" : "Not sampled"}</td>
                    <td>
                      {!v.verdict && hasRole("reviewer") && (
                        <>
                          <button type="button" className="secondary small" onClick={() => verdict.mutate({ id: v.kyc_id, verdict: "ok", note: null })}>Correct</button>{" "}
                          <button type="button" className="danger small" onClick={() => { const n = window.prompt("What was wrong?"); if (n && n.trim().length >= 3) verdict.mutate({ id: v.kyc_id, verdict: "overturned", note: n.trim() }); }}>Overturn</button>
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
    </div>
  );
}
