Synthetic sample dataset for parser/OCR development.
All transactions and account details are randomly generated and are not real.
200 app-style payment screenshots: screenshots/text and screenshots/images.
100 mobile-wallet SMS examples: sms/text and sms/images.
200 varied, scan-style complex receipt images: complex/text and complex/images.
manifest.csv maps each record to its text, image and generated labels.
Run scripts/prepare_training_data.py to create deterministic train/validation/test JSONL.
