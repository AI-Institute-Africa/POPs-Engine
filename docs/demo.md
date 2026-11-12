# The standalone demo

`python -m app.demo` runs Validate on your own machine as a self-contained app. It is meant for
showing what the product does, not for real verification.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e "backend[demo]"
cd backend && python -m app.demo
```

Options: `--port 8000`, `--no-browser`, `--fresh` (delete the demo data first), `--data-dir PATH`,
`--database-url postgresql+psycopg://...` (use your own PostgreSQL instead of the bundled one).

It needs Python 3.12+ and, for the first run only, Node 18+ to build the console (skipped if
`frontend/dist` already exists). The first start seeds a demo organisation, its rules and a dozen
sample cases in about 15 seconds. Data lives in `var/demo/` and survives restarts.

If you run it as `root`, the bundled PostgreSQL runs as a separate user and must be able to read its
own files; the launcher checks this and tells you what to change. A normal user needs nothing.

## What is simulated

| Real product | In the demo |
| --- | --- |
| A vision model reads receipts and ID cards | A stand-in that reads the facts embedded in the generated sample images. It cannot read anything else. |
| A face model compares the ID photo with the selfie | A stand-in tuned to the drawn faces. The scores show how the rules react; they say nothing about real-world accuracy. |
| Tesseract and a malware scanner | Off |
| Celery, Redis, S3 | Everything runs in one process with local files |

Everything the **rules, ledger, review queue, audit trail and permissions** do is the real code, and
so is the API. Documents are fictional ("Republic of Validia", `VDA`) and say SAMPLE on their face.

## The stories

Payments: *pay and get approved*, *receipt before the money* (then deliver it), *edited receipt*,
*same receipt twice*, *paid part of the amount*, *blurry photo*.
Identity: *verify an ID in seconds*, *passport with MRZ*, *someone else's selfie*, *expired ID*,
*edited passport*, *one ID, two people*, *no face in the selfie*.

Each is a short script in `backend/app/demo/scenarios.py` that drives the real API as a persona, and
**each declares the outcome and reason code it must produce**. `tests/test_demo.py` runs all of them,
so the demo cannot quietly drift from the engine.

On the **Verify a payment** and **Verify an ID** pages, *Try a sample* fills the form with a generated
case so you can submit it yourself.

## Personas

Four seats, switched from the top-right menu (no passwords; the demo has no login): a payer, a finance
reviewer, a finance approver and an administrator. The menu only shows pages the seat may use, and the
server enforces the same permissions.

## Safety

The demo binds to `127.0.0.1`. It has no login and can wipe its data, so do not expose it. The server
refuses to start in production mode with `POPS_DEMO=true` or the simulated face matcher.
