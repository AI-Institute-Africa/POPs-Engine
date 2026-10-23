# Vision models for the POPs reader

The engine's primary reader is a vision-language model (VLM) served through the Ollama API
(`backend/app/extraction/readers/ollama_vlm.py`). Everything model-specific is configuration:

| Setting | Ollama Cloud | Self-hosted Ollama |
| --- | --- | --- |
| `POPS_OLLAMA_URL` | `https://ollama.com` | `http://ollama:11434` |
| `POPS_OLLAMA_API_KEY` | key from ollama.com → Settings → Keys | empty |
| `POPS_OLLAMA_MODEL` | e.g. `qwen3-vl:235b` | e.g. `qwen2.5vl:7b`, `pops-reader` |

The VLM never decides anything. It fills a strict JSON schema, Tesseract and the provider
templates read the image independently, and a critical field counts only when both readers agree
(blueprint §11, layer 2). A weaker model therefore causes more reviewer work, not wrong credits.

> **Verify before relying on this.** Ollama's site was unreachable from the build environment, and
> third-party lists of its free cloud models disagree and change often. Check the current
> vision-capable cloud models at `ollama.com/search?c=vision&c=cloud` and confirm the free-tier
> limits on your account.

## v1: Ollama Cloud models (free tier)

| Rank | Model | Why | Caveats |
| --- | --- | --- | --- |
| 1 | **`qwen3-vl:235b`** (Qwen3-VL, cloud) | Strongest open VLM family for OCR-heavy documents: multilingual OCR, small and rotated text, long receipts and screenshots. Also exists in small sizes you can self-host and fine-tune later, so v1 and v2 stay in one family. | Large, so expect higher latency than small models. Free-tier quotas are session/weekly and not published. |
| 2 | **`gemma4:31b`** or the current Gemma cloud model | Reported on the free roster in late 2026; Gemma 3 and later accept images. A good second opinion for the benchmark. | Vision support on the free tier is not confirmed. Check the model card. |
| — | `qwen2.5vl` (local only) | Baseline to benchmark against cloud models on the same set. | Older generation. |

Ollama model names can differ between cloud and local use: a local daemon usually appends
`-cloud`, e.g. `qwen3-vl:235b-cloud`. Use the exact name your account lists.

**Data protection.** With Ollama Cloud, unredacted receipts (names, phone numbers, account
numbers) leave your infrastructure. Blueprint §4.3 requires a Cyber and Data Protection Act review
before that happens with real payer data. Until it is done, use Cloud only for synthetic or consented
Phase 0 samples, or self-host.

**Pick by measurement, not by this table.** Collect 300+ labelled local POPs (Phase 0), then run:

```bash
cd backend
POPS_OLLAMA_API_KEY=... python scripts/benchmark_reader.py ./phase0-set qwen3-vl:235b gemma4:31b
```

It prints per-field accuracy (amount, transaction ID, date, currency, beneficiary, status) and
p50/p95 latency for each model. The targets are 98% on known templates and 90% on unseen layouts
(blueprint §2).

## v2: open-weights models to download, fine-tune and plug in

### Prepare the synthetic sample set

The repository includes 500 generated examples (200 app-style screenshots, 100 mobile-wallet SMS
examples and 200 complex receipt images). Rebuild the images and create deterministic, category-
stratified 80/10/10 JSONL splits with:

```bash
python scripts/generate_mock_pops.py
python scripts/prepare_training_data.py
```

The resulting `sample_data/mock_pops/training_splits/{train,validation,test}.jsonl` rows point to
images relative to `sample_data/mock_pops` and carry `target` objects using the exact
`ReceiptFields` keys. The current split is 400/50/50. Use these examples to validate a trainer and
its input pipeline; the records are synthetic, visibly watermarked and generated from a small set
of layouts. They are not a measure of real receipt accuracy and should not be used to train a fraud
detector. Before fine-tuning or making accuracy claims, add reviewer-verified, consented receipts
and reserve a separate held-out set of real layouts.

The Donut fine-tuning entry point is `scripts/train_extractor.py`. From the `backend` folder,
install its optional dependencies with `pip install -e ".[training]"`, then run from the repository
root:

```bash
python scripts/train_extractor.py \
  --data-dir sample_data/mock_pops/training_splits \
  --output-dir var/models/donut-pops \
  --epochs 3 --batch-size 1
```

This downloads `naver-clova-ix/donut-base` from Hugging Face the first time it runs. A CUDA GPU is
strongly recommended; `--allow-cpu` permits CPU training but can take a very long time. On CPU the
script uses 640×960 images and freezes the vision encoder by default; `--train-encoder` opts into
full fine-tuning. The script saves the model, processor and `run_summary.json` under the output
directory and reports held-out test loss.

When to fine-tune at all is set by blueprint §12.1: a high-volume channel is below target, you have
a few hundred reviewer-corrected examples per major layout, and the tuned model beats the best
pretrained one on the held-out set.

| Model | Size | Licence (check before use) | Role in POPs Engine |
| --- | --- | --- | --- |
| **Qwen3-VL (small dense variants, e.g. 4B/8B)** | 4–8B | Apache 2.0 | **Primary v2 reader.** Same family as the v1 cloud model, so prompts and the schema carry over. LoRA fine-tune for EcoCash, InnBucks, OneMoney and local bank layouts. |
| **Qwen2.5-VL-7B / 3B** | 3–7B | Apache 2.0 (3B: check) | The most mature fine-tuning tooling (Transformers + PEFT, LLaMA-Factory, Unsloth); already on Ollama as `qwen2.5vl`. Safe fallback if Qwen3-VL tooling lags. |
| **Gemma 3 (4B / 12B)** | 4–12B | Gemma terms | Good small multimodal model, supported by Unsloth. Use it as an alternative reader in the benchmark. |
| **MiniCPM-V** | ~8B | Model licence; commercial use needs registration | Strong OCR on phone screenshots, on Ollama as `minicpm-v`. Check the licence first. |
| **PaddleOCR (PP-OCRv5) / PaddleOCR-VL** | <1B | Apache 2.0 | **Replace Tesseract as the second reader.** Much better on low-light phone photos and screenshots, and still independent of the VLM. |
| **Florence-2 (base/large)** | 0.2–0.8B | MIT | Fast **document-type classifier** (blueprint §12 says to train a small one), and a cheap pre-screen before the VLM. |
| Small CNN/ViT (MobileNetV3, EfficientNet) | <30M | Apache 2.0 | Alternative classifier, and later the tamper-flag model trained on confirmed fakes (escalates only, never approves). |

Avoid LayoutLMv3 (non-commercial weights). Donut works but has been overtaken by the VLMs above.

### Fine-tune → serve → plug in

1. **Labels come from the console.** Every reviewer correction is stored as an `extracted_fields`
   row with `corrected_by` set, next to the original reading. Export those rows with their
   evidence images (mask personal data the model does not need), and keep a held-out test set that is
   never trained on.
2. **Train a LoRA adapter** on the chosen open model with Unsloth or Hugging Face PEFT. Use the
   exact `ReceiptFields` JSON schema as the target output, and track runs in MLflow with DVC for data
   versions.
3. **Export to GGUF**, then register it with Ollama:
   ```
   # Modelfile
   FROM ./pops-reader-q4_k_m.gguf
   PARAMETER temperature 0
   ```
   `ollama create pops-reader -f Modelfile`
4. **Plug in:** `POPS_OLLAMA_URL=http://ollama:11434`, `POPS_OLLAMA_MODEL=pops-reader`. The reader
   version (`ollama:pops-reader`) is recorded on every verification run, so decisions stay
   replayable and you can roll back.
5. **Gate it:** rerun `benchmark_reader.py` on the held-out set. Ship only if it beats the current
   reader by a margin worth the upkeep, then run it in shadow mode (Phase 4) before it counts.
