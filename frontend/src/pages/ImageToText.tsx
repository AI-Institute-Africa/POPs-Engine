import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { post } from "../api";
import { useToast } from "../toast";

type PageText = {
  page: number;
  text: string;
  reader: string;
  confidence: number | null;
  lines: number;
};

type Conversion = {
  name: string;
  content_type: string;
  page_count: number;
  width: number;
  height: number;
  text: string;
  character_count: number;
  pages: PageText[];
  reader_errors: string[];
  quality_issues: string[];
};

const MAX_BYTES = 10 * 1024 * 1024;

const READER_LABELS: Record<string, string> = {
  text_layer: "the document's own text layer",
  tesseract_ocr: "Tesseract OCR",
  fixture_text: "the demo reader",
  ollama_vlm: "the vision model",
};

function readerLabel(reader: string): string {
  return READER_LABELS[reader] ?? reader.replace(/_/g, " ");
}

function downloadText(result: Conversion) {
  const blob = new Blob([result.text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stem = result.name.replace(/\.[^.]+$/, "") || "converted";
  a.href = url;
  a.download = `${stem}.txt`;
  a.click();
  URL.revokeObjectURL(url);
}

function PageTabs({
  pages,
  active,
  onChange,
}: {
  pages: PageText[];
  active: number;
  onChange: (index: number) => void;
}) {
  if (pages.length < 2) return null;
  return (
    <div className="page-tabs" role="tablist" aria-label="Pages">
      {pages.map((page, index) => (
        <button
          key={page.page}
          type="button"
          role="tab"
          aria-selected={index === active}
          className={`ghost small ${index === active ? "active" : ""}`}
          onClick={() => onChange(index)}
        >
          Page {page.page}
          {page.text ? "" : " (empty)"}
        </button>
      ))}
    </div>
  );
}

function ResultCard({ result }: { result: Conversion }) {
  const toast = useToast();
  const [active, setActive] = useState(0);
  const page = result.pages[active] ?? result.pages[0];

  useEffect(() => {
    setActive(0);
  }, [result]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(page?.text ?? result.text);
      toast("ok", "Text copied to the clipboard.");
    } catch {
      toast("err", "Your browser blocked the clipboard. Select the text and copy it.");
    }
  };

  return (
    <div className="card">
      <div className="toolbar">
        <h2>Extracted text</h2>
        <span className="badge">{result.character_count.toLocaleString()} characters</span>
        <span className="grow" />
        <button type="button" className="ghost small" onClick={copy} disabled={!result.text}>
          Copy
        </button>
        <button
          type="button"
          className="ghost small"
          onClick={() => downloadText(result)}
          disabled={!result.text}
        >
          Download .txt
        </button>
      </div>

      <PageTabs pages={result.pages} active={active} onChange={setActive} />

      {page?.text ? (
        <pre className="ocr-text">{page.text}</pre>
      ) : (
        <p className="muted">
          No readable text was found on this page. Try a sharper photo, more light, or the
          original file instead of a screenshot of a screenshot.
        </p>
      )}

      <p className="muted small">
        {page ? `Page ${page.page} of ${result.page_count}` : result.name}
        {page?.reader ? ` · read with ${readerLabel(page.reader)}` : ""}
        {page?.confidence !== null && page?.confidence !== undefined
          ? ` · ${Math.round(page.confidence * 100)}% confidence`
          : ""}
        {` · ${result.width} × ${result.height}`}
      </p>

      {result.quality_issues.length > 0 && (
        <p className="muted small">
          Quality warning: {result.quality_issues.join(", ").replace(/_/g, " ").toLowerCase()}.
          A clearer copy may read better.
        </p>
      )}

      {result.reader_errors.length > 0 && (
        <details className="reader-errors">
          <summary className="muted small">Reader notes</summary>
          <ul className="muted small">
            {result.reader_errors.map((error, index) => (
              <li key={index}>{error}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export default function ImageToText() {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Conversion | null>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const convert = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("file", file!);
      return post<Conversion>("/image-to-text", fd);
    },
    onSuccess: (body) => {
      setResult(body);
      setError(null);
      toast(
        "ok",
        body.text
          ? `Read ${body.character_count.toLocaleString()} characters.`
          : "The file was read, but it contains no readable text.",
      );
    },
    onError: (e) => {
      setResult(null);
      setError((e as Error).message);
    },
  });

  const pick = (list: FileList | null | undefined) => {
    const picked = list?.[0];
    if (!picked) return;
    if (picked.size > MAX_BYTES) {
      setError("That file is over 10 MB. Please choose a smaller file.");
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setFile(picked);
    setError(null);
    setResult(null);
    convert.reset();
    setPreviewUrl(picked.type.startsWith("image/") ? URL.createObjectURL(picked) : null);
  };

  const clear = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setFile(null);
    setPreviewUrl(null);
    setResult(null);
    setError(null);
    convert.reset();
  };

  return (
    <section>
      <div className="hero">
        <h1>Image to text</h1>
        <p className="muted">
          Turn a receipt, screenshot or scanned document into editable text. The file is read on
          the server, returned to you, and not saved. Extracted text is a reading aid only: it
          never approves, rejects or allocates a payment.
        </p>
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
                  <img src={previewUrl} alt="Selected file" className="thumb" />
                ) : (
                  <span aria-hidden className="file-icon">
                    {(file.name.split(".").pop() ?? "file").slice(0, 4).toUpperCase()}
                  </span>
                )}
                <div className="grow">
                  <strong>{file.name}</strong>
                  <div className="muted small">
                    {(file.size / 1024).toFixed(0)} KB · ready to convert
                  </div>
                </div>
                <button type="button" className="ghost small" onClick={clear}>
                  ✕
                </button>
              </div>
            ) : (
              <>
                <div className="drop-icon" aria-hidden>
                  <span className="drop-glyph">⇪</span>
                </div>
                <p>
                  <strong>Drag a photo or document here</strong> or choose how to add it:
                </p>
              </>
            )}

            <div className="capture-row">
              <button type="button" className="capture" onClick={() => cameraRef.current?.click()}>
                <span className="glyph" aria-hidden>
                  📷
                </span>
                Take a photo
                <span className="muted small">uses your camera</span>
              </button>
              <button type="button" className="capture" onClick={() => uploadRef.current?.click()}>
                <span className="glyph" aria-hidden>
                  📁
                </span>
                Upload file
                <span className="muted small">PNG, JPG, PDF, Word</span>
              </button>
            </div>

            <input
              ref={cameraRef}
              type="file"
              accept="image/*"
              capture="environment"
              hidden
              onChange={(e) => {
                pick(e.target.files);
                e.currentTarget.value = "";
              }}
            />
            <input
              ref={uploadRef}
              type="file"
              accept="image/*,.heic,.heif,application/pdf,.docx,.eml,message/rfc822,.txt,text/plain"
              hidden
              onChange={(e) => {
                pick(e.target.files);
                e.currentTarget.value = "";
              }}
            />
            <p className="muted small">
              Photos, screenshots, PDFs, Word files and forwarded emails. Up to 10 MB. Every file
              is malware-scanned before it is read.
            </p>
          </div>

          <div className="card">
            <button
              type="button"
              disabled={!file || convert.isPending}
              onClick={() => convert.mutate()}
            >
              {convert.isPending ? "Reading the file…" : "Convert to text"}
            </button>
            <p className="muted small">
              Digital PDFs and Word files use their own text layer, which is exact. Photos and
              screenshots are read with OCR.
            </p>
          </div>
        </div>

        <div className="stack">
          {error && <p className="error">{error}</p>}
          {convert.isPending && (
            <div className="card">
              <h2>Reading the file…</h2>
              <p className="muted small">
                The malware scan runs first, then the reader. Larger documents can take a few
                seconds.
              </p>
            </div>
          )}
          {result && <ResultCard result={result} />}
          {!result && !convert.isPending && !error && (
            <div className="card empty-state">
              <div className="empty-glyph" aria-hidden>
                🔤
              </div>
              <p className="muted">
                The text will appear here. Nothing is stored: close or reload the page and the
                text is gone.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
