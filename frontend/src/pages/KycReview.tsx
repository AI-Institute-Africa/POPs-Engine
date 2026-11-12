import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, blobUrl, hasRole, post } from "../api";
import { useToast } from "../toast";

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

function Img({ id, role }: { id: string; role: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    blobUrl(`/kyc/verifications/${id}/evidence/${role}`).then((u) => { url = u; setSrc(u); }).catch(() => setSrc(null));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id, role]);
  return src ? <img src={src} alt={role} style={{ maxWidth: "100%", maxHeight: 280, borderRadius: 6 }} /> : <span className="muted small">{role}: not available</span>;
}

// Reviewer view: everything the readers and the face matcher produced, side by side with the images.
// The reviewer decides; a clear mismatch between sources is shown, never hidden.
function Review({ id, onDone }: { id: string; onDone: () => void }) {
  const toast = useToast();
  const q = useQuery({ queryKey: ["kyc", id], queryFn: () => api<Detail>(`/kyc/verifications/${id}`) });
  const decide = useMutation({
    mutationFn: (v: { outcome: string; note: string | null }) => post(`/kyc/review/${id}/decision`, v),
    onSuccess: () => { toast("ok", "Decision recorded."); onDone(); },
    onError: (e) => toast("err", (e as Error).message),
  });
  const ask = (outcome: string) => {
    const note = outcome === "VERIFIED" ? window.prompt("Note (optional)") ?? "" : window.prompt("Reason (required)");
    if (note === null || (outcome !== "VERIFIED" && note.trim().length < 3)) return;
    decide.mutate({ outcome, note: note.trim() || null });
  };
  if (!q.data) return <p>Loading…</p>;
  const d = q.data;
  return (
    <div className="card">
      <h3>{d.subject_type}: {d.subject_ref}</h3>
      <p className="muted small">Reasons it needs a person: {d.reason_codes.join(", ") || "—"}</p>
      <div className="row">
        <Img id={id} role="front" />
        <Img id={id} role="selfie" />
      </div>
      <p>
        Face match <strong>{d.scores.face?.toFixed(2) ?? "n/a"}</strong> · liveness <strong>{d.scores.liveness?.toFixed(2) ?? "n/a"}</strong> ·
        sources agree on <strong>{d.scores.ocr_agreement !== null ? `${Math.round(d.scores.ocr_agreement * 100)}%` : "n/a"}</strong> of key fields ·
        MRZ {d.scores.mrz_valid === null ? "not found" : d.scores.mrz_valid ? "valid" : "check digits FAIL"}
      </p>
      <table>
        <thead><tr><th>Field</th><th>Chosen</th><th>Agreed</th><th>Each source read</th></tr></thead>
        <tbody>
          {Object.entries(d.fields.values).map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td>{v ?? "—"}</td>
              <td>{d.fields.agreed[k] ? "✓" : "—"}</td>
              <td className="small muted">{Object.entries(d.fields.sources[k] ?? {}).map(([s, x]) => `${s}: ${x}`).join(" · ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="small">
        {d.checks.map((c) => <li key={c.name}>{c.ok === null ? "•" : c.ok ? "✓" : "✗"} {c.name}</li>)}
      </ul>
      {hasRole("reviewer") && (
        <div className="row">
          <button disabled={decide.isPending} onClick={() => ask("VERIFIED")}>Verify</button>
          <button className="ghost" disabled={decide.isPending} onClick={() => ask("NEEDS_INFORMATION")}>Ask for new photos</button>
          <button className="danger" disabled={decide.isPending} onClick={() => ask("REJECTED")}>Reject</button>
        </div>
      )}
    </div>
  );
}

export default function KycReview() {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState<string | null>(null);
  const queue = useQuery({ queryKey: ["kyc-queue"], queryFn: () => api<QueueRow[]>("/kyc/review/queue"), refetchInterval: 30000 });
  const auto = useQuery({ queryKey: ["kyc-auto"], queryFn: () => api<Auto>("/kyc/review/auto-verified") });
  const verdict = useMutation({
    mutationFn: (v: { id: string; verdict: "ok" | "overturned"; note: string | null }) =>
      post(`/kyc/review/${v.id}/audit-verdict`, { verdict: v.verdict, note: v.note }),
    onSuccess: () => { toast("ok", "Verdict recorded. Revoke the verification separately if it was wrong."); qc.invalidateQueries({ queryKey: ["kyc-auto"] }); },
    onError: (e) => toast("err", (e as Error).message),
  });
  const done = () => { setOpen(null); qc.invalidateQueries({ queryKey: ["kyc-queue"] }); qc.invalidateQueries({ queryKey: ["kyc-auto"] }); };
  const a = auto.data;
  return (
    <section>
      <div className="card">
        <h2>Identity checks waiting for a person</h2>
        {queue.error && <p className="error">{(queue.error as Error).message}</p>}
        <table>
          <thead><tr><th>Submitted</th><th>Subject</th><th>Document</th><th>Face</th><th>Why</th></tr></thead>
          <tbody>
            {queue.data?.length === 0 && <tr><td colSpan={5} className="muted">Nothing waiting.</td></tr>}
            {queue.data?.map((r) => (
              <tr key={r.kyc_id} onClick={() => setOpen(r.kyc_id)} style={{ cursor: "pointer" }}>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.subject_type}: {r.subject_ref}</td>
                <td>{r.doc_type ?? "?"} {r.issuing_country ?? ""}</td>
                <td>{r.face_score?.toFixed(2) ?? "n/a"}</td>
                <td className="small muted">{r.reason_codes.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && <Review id={open} onDone={done} />}
      {a && (
        <div className="card">
          <h3>Automatic verifications</h3>
          <p className="muted small">
            Mode: <strong>{a.mode}</strong>{a.mode === "shadow" && " (recording what the rules would do; nothing is verified automatically)"}. {a.audit_pct}% are sampled for a reviewer.
          </p>
          <p>
            {a.summary.verified} verified · {a.summary.sampled} sampled · {a.summary.audited} audited · {a.summary.overturned} overturned
            {a.summary.overturn_rate !== null && <> · overturn rate <strong>{(a.summary.overturn_rate * 100).toFixed(1)}%</strong></>}
          </p>
          <table>
            <thead><tr><th>Verified</th><th>Subject</th><th>Face</th><th>Audit</th><th /></tr></thead>
            <tbody>
              {a.verifications.map((v) => (
                <tr key={v.kyc_id}>
                  <td>{new Date(v.verified_at).toLocaleString()}</td>
                  <td>{v.subject_type}: {v.subject_ref}</td>
                  <td>{v.face_score?.toFixed(2) ?? "n/a"}</td>
                  <td>{v.verdict ? `${v.verdict.outcome === "KYC_AUDIT_OK" ? "Correct" : "Overturned"} by ${v.verdict.by}` : v.sampled ? "In the sample: please check" : "Not sampled"}</td>
                  <td>
                    {!v.verdict && hasRole("reviewer") && (
                      <>
                        <button className="ghost small" onClick={() => verdict.mutate({ id: v.kyc_id, verdict: "ok", note: null })}>Correct</button>{" "}
                        <button className="danger small" onClick={() => { const n = window.prompt("What was wrong?"); if (n && n.trim().length >= 3) verdict.mutate({ id: v.kyc_id, verdict: "overturned", note: n.trim() }); }}>Overturn</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
