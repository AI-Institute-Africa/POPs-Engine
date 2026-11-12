import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, post } from "../api";
import { demoInfo, prepareKyc, sampleFile } from "../demo";
import { DOC_TYPES } from "../labels";
import { useToast } from "../toast";
import { Chip, PageHeader, StateChip } from "../ui";

type Out = { kyc_id: string; state: string; message: string | null; subject_type: string; subject_ref: string };
type Status = { verified: boolean; state: string | null; expires_at?: string | null };

const SAMPLES: { id: string; label: string; hint: string }[] = [
  { id: "clear", label: "Valid ID card", hint: "Should verify automatically" },
  { id: "passport", label: "Passport (MRZ)", hint: "Check digits validate" },
  { id: "mismatch", label: "Wrong selfie", hint: "Face does not match" },
  { id: "expired", label: "Expired ID", hint: "Needs a reviewer" },
  { id: "edited", label: "Edited passport", hint: "Printed vs checked zone" },
  { id: "noface", label: "No face in selfie", hint: "Asked to retake" },
];

const NEXT: Record<string, string> = {
  VERIFIED: "Verified automatically. This identity now counts as checked everywhere that asks for it.",
  NEEDS_REVIEW: "Received. A reviewer will look at it. Nothing is rejected automatically.",
  NEEDS_INFORMATION: "We could not use the photos. Retake them using the tips on this page and submit again.",
  REJECTED: "We could not verify this identity.",
};

function Drop({ label, hint, file, onFile, optional }: { label: string; hint: string; file: File | null; onFile: (f: File | null) => void; optional?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file) return setUrl(null);
    const u = URL.createObjectURL(file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);
  return (
    <div
      className={`dz${file ? " has" : ""}${over ? " over" : ""}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files?.[0] ?? null); }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
    >
      <span className="ph">{url ? <img src={url} alt="" /> : "📷"}</span>
      <span style={{ flex: 1 }}>
        <b>{label} {optional && <span className="muted small">(optional)</span>}</b>
        <span className="muted small">{file ? file.name : hint}</span>
      </span>
      {file && (
        <button type="button" className="ghost small" onClick={(e) => { e.stopPropagation(); onFile(null); }}>
          Remove
        </button>
      )}
      <input ref={input} type="file" accept="image/*" hidden onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
    </div>
  );
}

export default function Kyc() {
  const toast = useToast();
  const demo = demoInfo();
  const [subjectType, setSubjectType] = useState("payer");
  const [subjectRef, setSubjectRef] = useState("");
  const [front, setFront] = useState<File | null>(null);
  const [back, setBack] = useState<File | null>(null);
  const [selfie, setSelfie] = useState<File | null>(null);
  const [result, setResult] = useState<Out | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [lookup, setLookup] = useState("");

  const sample = useMutation({
    mutationFn: async (variant: string) => {
      const s = await prepareKyc(variant);
      const [f, sf] = await Promise.all([sampleFile(s.front_url, "id-front.png"), sampleFile(s.selfie_url, "selfie.png")]);
      return { s, f, sf };
    },
    onSuccess: ({ s, f, sf }) => {
      setSubjectType("payer");
      setSubjectRef(s.subject_ref);
      setFront(f);
      setSelfie(sf);
      setBack(null);
      setNote(s.note);
      setResult(null);
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  const status = useQuery({
    queryKey: ["kyc-status", subjectType, lookup],
    enabled: lookup.trim().length > 0,
    queryFn: () => api<Status>(`/kyc/subjects/${subjectType}/${encodeURIComponent(lookup.trim())}/status`),
  });

  const submit = useMutation({
    mutationFn: () => {
      if (!front || !selfie) throw new Error("Add a photo of the ID and a selfie.");
      const fd = new FormData();
      fd.append("subject_type", subjectType);
      if (subjectRef.trim()) fd.append("subject_ref", subjectRef.trim());
      fd.append("front", front);
      fd.append("selfie", selfie);
      if (back) fd.append("back", back);
      return post<Out>("/kyc/verifications", fd);
    },
    onSuccess: (r) => {
      setResult(r);
      setLookup(r.subject_ref);
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  return (
    <div>
      <PageHeader
        title="Verify an identity"
        sub="Photograph the ID and take a selfie. The AI only reads the document; fixed rules, or a person, decide."
      />
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <div className="card">
          {demo && (
            <>
              <h3>Try a sample</h3>
              <div className="sample-row">
                {SAMPLES.map((s) => (
                  <button key={s.id} type="button" className="secondary" title={s.hint} disabled={sample.isPending} onClick={() => sample.mutate(s.id)}>
                    {s.label}
                  </button>
                ))}
              </div>
              {note && <p className="learn" style={{ marginTop: 0 }}><b>This sample:</b> {note}</p>}
            </>
          )}
          <h3>Who is being verified</h3>
          <div className="inline-pair" style={{ marginBottom: 10 }}>
            <select value={subjectType} onChange={(e) => setSubjectType(e.target.value)} aria-label="Who is being verified">
              <option value="payer">A payer / learner</option>
              <option value="staff">Me (staff)</option>
              <option value="org">An organisation (admin)</option>
            </select>
            {subjectType !== "staff" && (
              <input placeholder="Learner reference" value={subjectRef} onChange={(e) => setSubjectRef(e.target.value)} aria-label="Reference" />
            )}
          </div>
          <Drop label="ID or passport photo page" hint="The side with the photo. Flat, well lit, all four corners visible." file={front} onFile={setFront} />
          <Drop label="Back of the ID" hint="Only if the card has a second side." file={back} onFile={setBack} optional />
          <Drop label="Selfie" hint="Your face, facing the camera, good light, no one else in the shot." file={selfie} onFile={setSelfie} />
          <button type="button" className="lg" disabled={submit.isPending || !front || !selfie} onClick={() => submit.mutate()}>
            {submit.isPending ? "Checking…" : "Submit for verification"}
          </button>
        </div>

        <div>
          <div className="card" aria-live="polite">
            <h3>Result</h3>
            {!result && !submit.isPending && <p className="muted">Your result appears here, usually within a few seconds.</p>}
            {submit.isPending && (
              <ul className="checks">
                {["Finding the card in the photo", "Reading the document twice, independently", "Comparing the ID photo with the selfie", "Running the rules"].map((t, i) => (
                  <li key={t} className="story" style={{ animationDelay: `${i * 500}ms`, display: "flex" }}>
                    <span className="mk na">…</span>
                    {t}
                  </li>
                ))}
              </ul>
            )}
            {result && (
              <>
                <p><StateChip state={result.state} /></p>
                <p>{NEXT[result.state] ?? result.state}</p>
                {result.message && <div className="bubble">{result.message}</div>}
              </>
            )}
          </div>
          <div className="card">
            <h3>Photo tips</h3>
            <ul className="small muted">
              <li>Put the card on a plain surface. Avoid glare on the plastic.</li>
              <li>Show the whole card, then the whole page for a passport.</li>
              <li>For the selfie: just you, face visible, no filters, not a photo of a photo.</li>
              <li>Accepted: {Object.values(DOC_TYPES).join(", ")}.</li>
            </ul>
          </div>
          <div className="card">
            <h3>Is someone verified?</h3>
            <input placeholder="Learner reference" value={lookup} onChange={(e) => setLookup(e.target.value)} aria-label="Reference to look up" />
            {status.data && (
              <p style={{ marginTop: 10 }}>
                {status.data.verified ? <Chip tone="ok">Verified</Chip> : <Chip tone="neutral">Not verified</Chip>}
                {status.data.state && <span className="muted small"> latest check: {status.data.state.toLowerCase().replace(/_/g, " ")}</span>}
                {status.data.expires_at && <span className="muted small"> · valid until {new Date(status.data.expires_at).toLocaleDateString()}</span>}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
