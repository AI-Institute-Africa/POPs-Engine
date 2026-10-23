import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, money, pingBackend, post, type Claim, type Obligation } from "../api";
import { useI18n } from "../i18n";
import { useToast } from "../toast";

type Recent = { claim_id: string; code: string; when: string };
type Preview = {
  readable: boolean;
  files: {
    name: string;
    width: number;
    height: number;
    fields: { name: string; value: string; confidence: number }[];
    quality_issues: string[];
    reader_errors: string[];
    readable: boolean;
  }[];
};

const RECENT_KEY = "validate-recent";

function loadRecent(): Recent[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as Recent[];
  } catch {
    return [];
  }
}

function saveRecent(r: Recent[]) {
  localStorage.setItem(RECENT_KEY, JSON.stringify(r.slice(0, 6)));
}

const PROCESSING = new Set(["received", "processing"]);

function tierClass(tier: string): string {
  if (tier === "green") return "tier-green";
  if (tier === "amber") return "tier-amber";
  if (tier === "red") return "tier-red";
  return "badge";
}

function prettyState(s: string): string {
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Auto-format the payment code as the user types: uppercase, groups split by a hyphen. */
function formatCode(raw: string): string {
  const clean = raw.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 9);
  if (clean.length <= 2) return clean;
  return `${clean.slice(0, 2)}-${clean.slice(2)}`;
}

function OfflineBanner() {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    let alive = true;
    const check = () => void pingBackend().then((ok) => alive && setOnline(ok));
    check();
    const id = window.setInterval(check, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);
  if (online) return null;
  return (
    <div className="offline-banner" role="alert">
      ⚠ Can't reach the validation service. Your upload will work again once it is back —
      nothing is lost.
    </div>
  );
}

function Skeleton({ lines = 2 }: { lines?: number }) {
  return (
    <div className="skeleton-card" aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton-line" style={{ width: `${88 - i * 18}%` }} />
      ))}
    </div>
  );
}

function notifyDone(claim: Claim, t: (k: string) => string) {
  const text = claim.messages[0] ?? `${t("result.title")}: ${prettyState(claim.state)}`;
  try {
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      new Notification("Validate", { body: text, tag: claim.claim_id });
    }
  } catch {
    /* notifications unavailable (e.g. insecure context) */
  }
}

function downloadSummary(claim: Claim, code: string, t: (k: string) => string) {
  const lines = [
    "Validate — payment check summary",
    "================================",
    `Claim:        ${claim.claim_id}`,
    `Payment code: ${code || "—"}`,
    `${t("result.status")}:     ${prettyState(claim.state)}`,
    `${t("result.outcome")}:    ${claim.outcome.replace(/_/g, " ")}`,
    `Tier:         ${claim.evidence_tier}`,
    ...(claim.declared_amount_minor !== null
      ? [`${t("result.declared")}:    ${money(claim.declared_amount_minor, claim.declared_currency)}`]
      : []),
    "",
    ...claim.messages.map((m) => `• ${m}`),
    "",
    new Date().toLocaleString(),
    "A receipt is a claim, not a confirmation.",
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `validate-${claim.claim_id.slice(0, 8)}.txt`;
  a.click();
  URL.revokeObjectURL(url);
}

function ResultCard({ claim, code }: { claim: Claim; code: string }) {
  const { t } = useI18n();
  const done = !PROCESSING.has(claim.state);
  const TIER_LABEL: Record<string, string> = {
    green: t("tier.green"),
    amber: t("tier.amber"),
    red: t("tier.red"),
  };
  return (
    <div className="card result">
      <div className="result-head">
        <div>
          <h2>{done ? t("result.title") : t("result.checking")}</h2>
          <p className="muted small">
            Claim <code>{claim.claim_id.slice(0, 8)}</code> · {prettyState(claim.state)}
          </p>
        </div>
        {claim.evidence_tier !== "none" && (
          <span className={`badge tier ${tierClass(claim.evidence_tier)}`}>
            {TIER_LABEL[claim.evidence_tier] ?? claim.evidence_tier}
          </span>
        )}
      </div>

      {!done && (
        <ol className="steps">
          <li className="done">{t("steps.uploaded")}</li>
          <li>{t("steps.quality")}</li>
          <li>{t("steps.reading")}</li>
          <li>{t("steps.matching")}</li>
        </ol>
      )}

      {claim.reason_codes.includes("TAMPER_SUSPECTED") && (
        <p className="risk-flag" role="status">
          <span aria-hidden>⚑</span> This receipt shows signs of editing, so a finance reviewer
          will look at it by hand. It is never rejected automatically for this reason alone.
        </p>
      )}

      {claim.messages.length > 0 && (
        <ul className="messages">
          {claim.messages.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      )}

      <dl className="meta">
        <dt>{t("result.status")}</dt>
        <dd className={`state state-${claim.state}`}>{prettyState(claim.state)}</dd>
        <dt>{t("result.outcome")}</dt>
        <dd>{claim.outcome.replace(/_/g, " ")}</dd>
        {claim.declared_amount_minor !== null && (
          <>
            <dt>{t("result.declared")}</dt>
            <dd>{money(claim.declared_amount_minor, claim.declared_currency)}</dd>
          </>
        )}
        <dt>{t("result.channel")}</dt>
        <dd>{claim.channel}</dd>
      </dl>

      {done && (
        <button type="button" className="ghost small" onClick={() => downloadSummary(claim, code, t)}>
          ⬇ Download summary
        </button>
      )}
      <p className="muted small">{t("result.green.note")}</p>
    </div>
  );
}

export default function Scan() {
  const { t } = useI18n();
  const toast = useToast();
  // several proofs for one payment (e.g. the bank-app screenshot and the SMS), up to 5
  const [files, setFiles] = useState<File[]>([]);
  const file = files[0] ?? null;
  const [pasted, setPasted] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [filePreview, setFilePreview] = useState<Preview | null>(null);
  const [fileVerified, setFileVerified] = useState(false);
  const [drag, setDrag] = useState(false);
  const [code, setCode] = useState("");
  const [claimantRef, setClaimantRef] = useState("");
  const [declaredAmount, setDeclaredAmount] = useState("");
  const [declaredReference, setDeclaredReference] = useState("");
  const [claimId, setClaimId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [recent, setRecent] = useState<Recent[]>(() => loadRecent());
  const cameraRef = useRef<HTMLInputElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const replaceUploadRef = useRef(false);
  const fileSelectionVersion = useRef(0);
  const notifiedRef = useRef<string | null>(null);

  const obligations = useQuery({
    queryKey: ["obligations"],
    queryFn: () => api<Obligation[]>("/obligations"),
  });

  const wanted = code.trim().toLowerCase();
  const obligation = obligations.data?.find((o) => o.payment_code.toLowerCase() === wanted) ?? null;

  const claim = useQuery({
    queryKey: ["claim", claimId],
    queryFn: () => api<Claim>(`/claims/${claimId}`),
    enabled: !!claimId,
    refetchInterval: (q) => (PROCESSING.has(q.state.data?.state ?? "") ? 2000 : false),
  });

  const wasProcessing = useRef(true);
  useEffect(() => {
    const c = claim.data;
    if (!c) return;
    if (PROCESSING.has(c.state)) {
      wasProcessing.current = true;
      return;
    }
    if (wasProcessing.current && notifiedRef.current !== c.claim_id) {
      wasProcessing.current = false;
      notifiedRef.current = c.claim_id;
      notifyDone(c, t);
      toast("ok", `${t("tier.green")}: ${c.messages[0] ?? prettyState(c.state)}`);
    }
  }, [claim.data, t, toast]);

  const submit = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.set("obligation_id", obligation!.obligation_id);
      fd.set("claimant_ref", claimantRef.trim());
      fd.set("channel", "validate-web");
      files.forEach((f) => fd.append("file", f));
      if (pasted.trim()) fd.set("text", pasted.trim());
      if (declaredAmount.trim()) fd.set("declared_amount", declaredAmount.trim());
      if (declaredReference.trim()) fd.set("declared_reference", declaredReference.trim());
      return post<Claim>("/claims", fd);
    },
    onSuccess: (c) => {
      setFormError(null);
      setClaimId(c.claim_id);
      wasProcessing.current = true;
      const next = [
        { claim_id: c.claim_id, code: code.trim().toUpperCase(), when: new Date().toISOString() },
        ...recent.filter((r) => r.claim_id !== c.claim_id),
      ];
      setRecent(next);
      saveRecent(next);
      toast("info", "Receipt uploaded — checking it now.");
      try {
        if (typeof Notification !== "undefined" && Notification.permission === "default") void Notification.requestPermission();
      } catch {
        /* ignore */
      }
    },
    onError: (e) => {
      setFormError((e as Error).message);
      toast("err", (e as Error).message);
    },
  });

  const readFiles = useMutation({
    mutationFn: async () => {
      const version = fileSelectionVersion.current;
      const fd = new FormData();
      files.forEach((f) => fd.append("file", f));
      return { version, preview: await post<Preview>("/claims/preview", fd) };
    },
    onSuccess: ({ version, preview }) => {
      if (version !== fileSelectionVersion.current) return;
      setFilePreview(preview);
      setFileVerified(false);
      setFormError(null);
    },
    onError: (e) => setFormError((e as Error).message),
  });

  const pick = (list: FileList | null | undefined, replace = false) => {
    const picked = Array.from(list ?? []);
    if (!picked.length) return;
    if (picked.some((f) => f.size > 20 * 1024 * 1024)) {
      setFormError("That file is over 20 MB. Please upload a smaller photo or PDF.");
      return;
    }
    setFormError(null);
    setFilePreview(null);
    setFileVerified(false);
    readFiles.reset();
    fileSelectionVersion.current += 1;
    const next = (replace ? picked : [...files, ...picked]).slice(0, 5);
    if (!replace && files.length + picked.length > 5) setFormError("Up to 5 files per payment.");
    setFiles(next);
    if (replace || !files.length) {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(next[0].type.startsWith("image/") ? URL.createObjectURL(next[0]) : null);
    }
  };

  const clearFile = () => {
    fileSelectionVersion.current += 1;
    setFiles([]);
    setFilePreview(null);
    setFileVerified(false);
    readFiles.reset();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
  };

  const canSubmit = (files.length > 0 || pasted.trim().length > 0) &&
    (!files.length || fileVerified) && !!obligation && claimantRef.trim().length >= 2 && !submit.isPending;

  return (
    <section className="scan">
      <OfflineBanner />
      <div className="hero">
        <h1>{t("hero.title")}</h1>
        <p className="muted">{t("hero.sub")}</p>
      </div>

      <div className="split scan-split">
        <div>
          <div
            className={`card dropzone ${drag ? "over" : ""} ${file ? "filled" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              pick(e.dataTransfer.files);
            }}
          >
            {file ? (
              <div className="file-row">
                {previewUrl ? (
                  <img src={previewUrl} alt="Selected receipt" className="thumb" />
                ) : (
                  <span aria-hidden className="file-icon">
                    {(file.name.split(".").pop() ?? "file").slice(0, 4).toUpperCase()}
                  </span>
                )}
                <div className="grow">
                  <strong>{file.name}</strong>
                  {files.length > 1 && <span className="muted small"> + {files.length - 1} more</span>}
                  <div className="muted small">
                    {(files.reduce((n, f) => n + f.size, 0) / 1024).toFixed(0)} KB · {t("drop.ready")}
                  </div>
                </div>
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => {
                    clearFile();
                    cameraRef.current?.click();
                  }}
                >
                  ↻ Retake
                </button>
                <button type="button" className="ghost small" onClick={() => { replaceUploadRef.current = true; uploadRef.current?.click(); }}>
                  Re-upload
                </button>
                <button type="button" className="ghost small" onClick={clearFile}>
                  ✕
                </button>
              </div>
            ) : (
              <>
                <div className="drop-icon" aria-hidden>
                  <span className="drop-glyph">⇪</span>
                </div>
                <p>
                  <strong>{t("drop.drag")}</strong> {t("drop.or")}
                </p>
              </>
            )}

            {files.length > 0 && !filePreview && (
              <div className="image-check">
                <p className="muted small">Read the attachment and check that its payment details are clear before sending.</p>
                <button type="button" className="secondary" disabled={readFiles.isPending} onClick={() => readFiles.mutate()}>
                  {readFiles.isPending ? "Reading attachment…" : "Read attachment"}
                </button>
              </div>
            )}
            {filePreview && (
              <div className={`image-check ${filePreview.readable ? "image-check-ok" : "image-check-needs-retake"}`}>
                {filePreview.readable ? (
                  <>
                    <strong>Check the details the reader found</strong>
                    {filePreview.files.map((item, index) => (
                      <div className="image-readout" key={`${item.name}-${index}`}>
                        <span className="muted small">{item.name} · {item.width} × {item.height}</span>
                        {item.fields.length > 0 ? (
                          <dl className="image-fields">
                            {item.fields.map((field) => (
                              <div key={field.name}><dt>{field.name.replace(/_/g, " ")}</dt><dd>{field.value}</dd></div>
                            ))}
                          </dl>
                        ) : <span className="muted small">No payment details detected in this attachment.</span>}
                      </div>
                    ))}
                    <button type="button" className={fileVerified ? "secondary verified" : "secondary"} onClick={() => setFileVerified((v) => !v)}>
                      {fileVerified ? "✓ Proof verified" : "Verify: preview and payment details are readable"}
                    </button>
                  </>
                ) : (
                  <>
                    <strong>This proof is hard to read. Please upload a clearer copy.</strong>
                    {filePreview.files.map((item, index) => (
                      <p className="muted small" key={`${item.name}-${index}`}>
                        {item.name}: {item.quality_issues.length ? item.quality_issues.join(", ") : "payment details weren’t detected"}
                      </p>
                    ))}
                    <div className="inline">
                      {files.some((f) => f.type.startsWith("image/")) && (
                        <button type="button" className="secondary" onClick={() => { clearFile(); cameraRef.current?.click(); }}>Retake photo</button>
                      )}
                      <button type="button" className="secondary" onClick={() => { replaceUploadRef.current = true; uploadRef.current?.click(); }}>Re-upload</button>
                    </div>
                  </>
                )}
              </div>
            )}

            <div className="capture-row">
              <button type="button" className="capture" onClick={() => cameraRef.current?.click()}>
                <span className="glyph" aria-hidden>📷</span>
                {t("capture.photo")}
                <span className="muted small">{t("capture.photo.sub")}</span>
              </button>
              <button type="button" className="capture" onClick={() => cameraRef.current?.click()}>
                <span className="glyph" aria-hidden>▤</span>
                {t("capture.scan")}
                <span className="muted small">{t("capture.scan.sub")}</span>
              </button>
              <button type="button" className="capture" onClick={() => uploadRef.current?.click()}>
                <span className="glyph" aria-hidden>📁</span>
                {t("capture.upload")}
                <span className="muted small">{t("capture.upload.sub")}</span>
              </button>
            </div>
            {/* capture="environment" opens the rear camera on phones; on desktops it is just a file picker */}
            <input
              ref={cameraRef}
              type="file"
              accept="image/*"
              capture="environment"
              hidden
              onChange={(e) => { pick(e.target.files); e.currentTarget.value = ""; }}
            />
            <input
              ref={uploadRef}
              type="file"
              multiple
              accept="image/*,.heic,.heif,application/pdf,.docx,.eml,message/rfc822,.txt,text/plain"
              hidden
              onChange={(e) => {
                const replace = replaceUploadRef.current;
                replaceUploadRef.current = false;
                pick(e.target.files, replace);
                e.currentTarget.value = "";
              }}
            />
            <p className="muted small">Photos, screenshots, PDFs, Word files and forwarded emails (.eml). Add up to 5 files for one payment.</p>
          </div>

          <form
            className="card"
            onSubmit={(e) => {
              e.preventDefault();
              if (canSubmit) submit.mutate();
            }}
          >
            <h2>{t("form.title")}</h2>
            <label>
              {t("form.code")}
              <input
                placeholder="CG-H4CC093"
                value={code}
                onChange={(e) => setCode(formatCode(e.target.value))}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <p className="muted small">
              {t("form.code.help")}{" "}
              {obligations.isFetching ? (
                <span className="muted">…</span>
              ) : (
                obligations.data && (
                  <>
                    {obligations.data.length} obligation{obligations.data.length === 1 ? "" : "s"} loaded.
                  </>
                )
              )}
            </p>
            {wanted.length >= 3 && obligations.isSuccess && !obligation && (
              <p className="error small">{t("form.code.nomatch")}</p>
            )}
            {obligation && (
              <div className="match">
                <strong>{obligation.description}</strong>
                <span className="muted small">
                  {money(obligation.amount_minor, obligation.currency)} · learner {obligation.learner_ref}
                </span>
              </div>
            )}

            <label>
              Or paste the payment SMS / message
              <textarea
                rows={3}
                placeholder="e.g. the EcoCash confirmation SMS"
                value={pasted}
                maxLength={20000}
                onChange={(e) => setPasted(e.target.value)}
              />
            </label>

            <label>
              {t("form.name")}
              <input placeholder={t("form.name.ph")} value={claimantRef} onChange={(e) => setClaimantRef(e.target.value)} />
            </label>

            <div className="inline-pair">
              <label>
                {t("form.amount")} <span className="muted">{t("form.amount.opt")}</span>
                <input placeholder="150.00" value={declaredAmount} onChange={(e) => setDeclaredAmount(e.target.value)} inputMode="decimal" />
              </label>
              <label>
                {t("form.ref")} <span className="muted">{t("form.ref.opt")}</span>
                <input placeholder="MP2506.1012.AB12CD" value={declaredReference} onChange={(e) => setDeclaredReference(e.target.value)} />
              </label>
            </div>

            {formError && <p className="error">{formError}</p>}
            <button type="submit" disabled={!canSubmit}>
              {submit.isPending ? t("form.submitting") : t("form.submit")}
            </button>
            <p className="muted small">{t("form.trust")}</p>
          </form>
        </div>

        <div className="stack">
          {claim.isFetching && !claim.data && <Skeleton lines={4} />}
          {claim.data && <ResultCard claim={claim.data} code={code} />}
          {claim.data && PROCESSING.has(claim.data.state) && (
            <p className="muted small">This page refreshes automatically every couple of seconds.</p>
          )}

          {recent.length > 0 && (
            <div className="card">
              <h2>{t("recent.title")}</h2>
              {recent.map((r) => (
                <button
                  key={r.claim_id}
                  type="button"
                  className={`recent ${r.claim_id === claimId ? "active" : ""}`}
                  onClick={() => setClaimId(r.claim_id)}
                >
                  <strong>{r.code || "Receipt"}</strong>
                  <span className="muted small">{new Date(r.when).toLocaleString()}</span>
                  <span className="muted small">{r.claim_id.slice(0, 8)}</span>
                </button>
              ))}
            </div>
          )}

          {recent.length === 0 && !claim.data && (
            <div className="card empty-state">
              <div className="empty-glyph" aria-hidden>
                🧾
              </div>
              <p className="muted">
                No checks yet. Your result will appear here as soon as you validate a payment.
              </p>
            </div>
          )}

          <div className="card tips">
            <h2>{t("tips.title")}</h2>
            <ul>
              <li>{t("tips.1")}</li>
              <li>{t("tips.2")}</li>
              <li>{t("tips.3")}</li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}
