import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, post } from "../api";
import { useToast } from "../toast";

type Out = { kyc_id: string; state: string; message: string | null; subject_type: string; subject_ref: string };
type Status = { verified: boolean; state: string | null; expires_at?: string | null };

const STATE_TEXT: Record<string, string> = {
  VERIFIED: "Verified automatically.",
  NEEDS_REVIEW: "Received. A reviewer will confirm it shortly.",
  NEEDS_INFORMATION: "We could not use the photos.",
  REJECTED: "We could not verify this identity.",
  PROCESSING: "Checking…",
  SUBMITTED: "Checking…",
};

// ID verification: upload the ID (front, optional back) and a selfie. The checks run on the server;
// a clear case is verified by the rules, everything else waits for a reviewer.
export default function Kyc() {
  const toast = useToast();
  const [subjectType, setSubjectType] = useState("payer");
  const [subjectRef, setSubjectRef] = useState("");
  const front = useRef<HTMLInputElement>(null);
  const back = useRef<HTMLInputElement>(null);
  const selfie = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<Out | null>(null);
  const [lookup, setLookup] = useState("");
  const status = useQuery({
    queryKey: ["kyc-status", subjectType, lookup],
    enabled: lookup.trim().length > 0,
    queryFn: () => api<Status>(`/kyc/subjects/${subjectType}/${encodeURIComponent(lookup.trim())}/status`),
  });
  const submit = useMutation({
    mutationFn: () => {
      const f = front.current?.files?.[0];
      const s = selfie.current?.files?.[0];
      if (!f || !s) throw new Error("Choose the ID photo and a selfie.");
      const fd = new FormData();
      fd.append("subject_type", subjectType);
      if (subjectRef.trim()) fd.append("subject_ref", subjectRef.trim());
      fd.append("front", f);
      fd.append("selfie", s);
      const b = back.current?.files?.[0];
      if (b) fd.append("back", b);
      return post<Out>("/kyc/verifications", fd);
    },
    onSuccess: (r) => {
      setResult(r);
      toast("ok", STATE_TEXT[r.state] ?? r.state);
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  return (
    <section>
      <div className="card">
        <h2>Verify an identity</h2>
        <p className="muted small">
          Photos of the ID (the page with the photo; the back is optional) and a clear selfie. Nothing is approved
          by the AI: it reads the document, and fixed rules or a reviewer decide.
        </p>
        <div className="row">
          <select value={subjectType} onChange={(e) => setSubjectType(e.target.value)} aria-label="Who is being verified">
            <option value="payer">Payer / learner</option>
            <option value="staff">Staff (me)</option>
            <option value="org">Organisation (admin)</option>
          </select>
          {subjectType !== "staff" && (
            <input placeholder="Learner / subject reference" value={subjectRef} onChange={(e) => setSubjectRef(e.target.value)} />
          )}
        </div>
        <label className="small">ID photo <input ref={front} type="file" accept="image/*" capture="environment" /></label>
        <label className="small">ID back (optional) <input ref={back} type="file" accept="image/*" capture="environment" /></label>
        <label className="small">Selfie <input ref={selfie} type="file" accept="image/*" capture="user" /></label>
        <button disabled={submit.isPending} onClick={() => submit.mutate()}>
          {submit.isPending ? "Checking…" : "Submit for verification"}
        </button>
        {result && (
          <p role="status">
            <strong>{STATE_TEXT[result.state] ?? result.state}</strong> {result.message}
          </p>
        )}
      </div>
      <div className="card">
        <h3>Is someone verified?</h3>
        <input placeholder="Subject reference" onBlur={(e) => setLookup(e.target.value)} />
        {status.data && (
          <p>
            {status.data.verified ? "Verified" : "Not verified"}
            {status.data.state && <span className="muted small"> · latest: {status.data.state}</span>}
            {status.data.expires_at && <span className="muted small"> · valid until {new Date(status.data.expires_at).toLocaleDateString()}</span>}
          </p>
        )}
      </div>
    </section>
  );
}
