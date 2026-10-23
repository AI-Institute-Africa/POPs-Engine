Synthetic POP image-to-JSON training splits.
Each JSONL row has an image path, reference text path, and target using
backend/app/extraction/schema.py ReceiptFields keys. Paths are relative to
the dataset root (the parent of this folder).
Split is deterministic with seed 42 and stratified by category.
These generated, visibly watermarked samples are for pipeline practice only.
They do not represent real bank layouts or establish real-world accuracy.
For fine-tuning or evaluation, add reviewer-verified, consented examples and
keep a separate held-out set of real receipts.
