# Image to text

The console has a standalone converter that returns the plain text of one uploaded file. It is a
reading aid for reviewers, support staff and payers: it does **not** create a claim, store the
file or its text, and nothing it returns can approve, reject or allocate a payment. A claim's
own reading still runs through the field pipeline and its reader agreement rules.

Open **Image to text** in the console (`/image-to-text`), or call the API directly.

## API

`POST /image-to-text` — `multipart/form-data` with one `file` field.

Any authenticated caller may use it. The upload goes through exactly the same checks as evidence:

1. ClamAV malware scan (fail closed: `503` if the scanner is unreachable).
2. File-signature validation (the client's content type and extension are never trusted).
3. The configured size limit (`POPS_MAX_UPLOAD_BYTES`, 10 MB by default).
4. A per-caller rate limit (`POPS_RATE_LIMIT_TEXT_CONVERSIONS_PER_MINUTE`, 20 by default).

```bash
curl -s -X POST http://localhost:8000/image-to-text \
  -H "Authorization: Bearer $POPS_TOKEN" \
  -F "file=@receipt.png"
```

Response:

```json
{
  "name": "receipt.png",
  "content_type": "image/png",
  "page_count": 1,
  "width": 720,
  "height": 1280,
  "text": "EcoCash\nPayment successful\nAmount: USD 40.00",
  "character_count": 49,
  "pages": [
    {
      "page": 1,
      "text": "EcoCash\nPayment successful\nAmount: USD 40.00",
      "reader": "tesseract_ocr",
      "confidence": 0.91,
      "lines": 3
    }
  ],
  "reader_errors": [],
  "quality_issues": []
}
```

`text` joins the non-empty pages with a blank line. `pages` is always in order and includes a page
that produced no text (so the console can say which page was empty). `reader` names the reader
that produced that page's text. `confidence` is Tesseract's mean word confidence (0..1); it is
`null` when the reader does not report one. `quality_issues` uses the same codes as the claim
legibility check (`CAPTURE_TOO_SMALL`, `CAPTURE_BLURRY`).

Errors are `{code, message}` in `detail`:

| Status | Code | Meaning |
| --- | --- | --- |
| 422 | `MALWARE_DETECTED` | The scan found malware. |
| 422 | `UNSUPPORTED_TYPE`, `CORRUPT_FILE`, `TOO_LARGE`, ... | The file cannot be read as evidence. |
| 429 | `RATE_LIMITED` | Too many conversions; `Retry-After` says when to try again. |
| 503 | `SCANNER_UNAVAILABLE` | The malware scanner is down; nothing was parsed. |
| 503 | `TEXT_READER_UNAVAILABLE` | No configured reader could transcribe the file. |

## What reads the file

| Order | Reader | When |
| --- | --- | --- |
| 1 | The document's own text layer | A digital PDF, Word document, email body or plain text file. Exact, no character recognition. |
| 2 | Fixture text | Only in `POPS_VLM_BACKEND=fake` (tests and demos): a `pops-text` PNG chunk. |
| 3 | Tesseract OCR | Photos, screenshots and pages without a text layer. Language from `POPS_OCR_LANGUAGES`. |
| 4 | The configured Ollama vision model | Only when OCR is unavailable or returns nothing, and only if the organisation's guardrails allow the vision model to run. |

The vision-model fallback **obeys the same guardrails as a claim**: an AI kill switch, an
external-processing block and the model allowlist are read from the caller's organisation, so the
converter cannot be used to bypass an organisation that has turned external AI processing off.
The Donut reader is field-based, not a transcriber, so it is not used as the fallback.

If every reader fails and no page produced text, the endpoint returns `503` rather than an empty
`200`, so a misconfigured server is visible. A file that was read successfully but contains no
text (for example a blank image) returns `200` with `text: ""` when OCR itself is available.

## Privacy and data protection

* The file and the extracted text are not stored. They exist in memory for the request only.
* The extracted text is returned to the caller; it is not written to the database, the audit log,
  evidence storage or the claim tables.
* With the Ollama fallback, the page image is sent to the configured model provider exactly as a
  claim's images are. Use the same data-protection review and the same guardrails described in
  [`models.md`](models.md) and [`guardrails.md`](guardrails.md).
* The output is untrusted text. It is rendered as text in the console, never as HTML, and is never
  interpreted as an instruction.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `POPS_RATE_LIMIT_TEXT_CONVERSIONS_PER_MINUTE` | 20 | Per authenticated caller. |
| `POPS_MAX_UPLOAD_BYTES` | 10 MB | Shared with claim evidence. |
| `POPS_OCR_BACKEND`, `POPS_OCR_LANGUAGES` | `tesseract`, `eng` | The OCR reader. In the Docker image Tesseract is installed. |
| `POPS_VLM_BACKEND`, `POPS_OLLAMA_*` | `ollama` | The transcription fallback when OCR cannot read a page. |

## Tests

```bash
cd backend
pytest tests/test_image_to_text.py
```

The tests use the fake reader and fixture text (`meta={"pops-text": "..."}`), a digital PDF text
layer, malware-scan failure paths and the rate limit. A real-Tesseract check lives with the other
OCR tests in `tests/test_hardening.py`.
