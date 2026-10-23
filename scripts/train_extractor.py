"""Fine-tune Donut to extract POP fields from the prepared synthetic image dataset."""

import argparse
import json
from pathlib import Path

import torch
from PIL import Image
from torch.utils.data import Dataset


class PopsDataset(Dataset):
    def __init__(self, split_file: Path, dataset_root: Path, processor, max_target_length: int):
        self.records = [
            json.loads(line) for line in split_file.read_text(encoding="utf-8").splitlines() if line
        ]
        self.dataset_root = dataset_root
        self.processor = processor
        self.max_target_length = max_target_length

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        record = self.records[index]
        image_path = self.dataset_root / record["image"]
        with Image.open(image_path) as source:
            image = source.convert("RGB")
            pixel_values = self.processor(image, return_tensors="pt").pixel_values.squeeze(0)

        target = json.dumps(record["target"], ensure_ascii=False, separators=(",", ":"))
        tokenized = self.processor.tokenizer(
            target,
            add_special_tokens=True,
            max_length=self.max_target_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        ).input_ids.squeeze(0)
        labels = tokenized.clone()
        labels[labels == self.processor.tokenizer.pad_token_id] = -100
        return {"pixel_values": pixel_values, "labels": labels}


def collate_batch(features: list[dict[str, torch.Tensor]]) -> dict[str, torch.Tensor]:
    """Stack the already padded image tensors and token labels for Trainer."""
    return {key: torch.stack([feature[key] for feature in features]) for key in features[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Folder with train/validation/test JSONL")
    parser.add_argument("--output-dir", type=Path, default=Path("var/models/donut-pops"))
    parser.add_argument("--model", default="naver-clova-ix/donut-base")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--max-target-length", type=int, default=256)
    parser.add_argument("--image-height", type=int, default=960)
    parser.add_argument("--image-width", type=int, default=640)
    parser.add_argument("--cpu-threads", type=int, default=0, help="0 uses PyTorch's default")
    parser.add_argument("--allow-cpu", action="store_true", help="Allow slow full fine-tuning on CPU")
    encoder_group = parser.add_mutually_exclusive_group()
    encoder_group.add_argument("--freeze-encoder", dest="freeze_encoder", action="store_true")
    encoder_group.add_argument("--train-encoder", dest="freeze_encoder", action="store_false")
    parser.set_defaults(freeze_encoder=None)
    args = parser.parse_args()

    if args.epochs < 1 or args.batch_size < 1 or args.max_target_length < 8:
        parser.error("epochs and batch-size must be positive; max-target-length must be at least 8")
    if not torch.cuda.is_available() and not args.allow_cpu:
        raise SystemExit(
            "No CUDA GPU is available. Donut fine-tuning on CPU is very slow; "
            "run on a CUDA machine or pass --allow-cpu to proceed anyway."
        )
    try:
        from transformers import (
            DonutProcessor,
            Trainer,
            TrainingArguments,
            VisionEncoderDecoderModel,
        )
    except ImportError as exc:
        raise SystemExit(
            f"Training dependencies are missing or incomplete: {exc}. "
            "Install the optional training dependencies from backend/pyproject.toml."
        ) from exc
    if args.cpu_threads:
        torch.set_num_threads(args.cpu_threads)

    dataset_root = args.data_dir.resolve().parent
    for split in ("train", "validation", "test"):
        if not (args.data_dir / f"{split}.jsonl").is_file():
            raise SystemExit(f"Missing split file: {args.data_dir / f'{split}.jsonl'}")

    print(f"Loading base checkpoint: {args.model}", flush=True)
    processor = DonutProcessor.from_pretrained(args.model)
    model = VisionEncoderDecoderModel.from_pretrained(args.model)
    processor.image_processor.size = {"height": args.image_height, "width": args.image_width}
    model.config.encoder.image_size = [args.image_height, args.image_width]
    model.encoder.config.image_size = [args.image_height, args.image_width]
    if getattr(model.config, "decoder_start_token_id", None) is None:
        decoder_bos = model.config.decoder.bos_token_id
        model.config.decoder_start_token_id = (
            decoder_bos if decoder_bos is not None else processor.tokenizer.bos_token_id
        )
    model.config.pad_token_id = processor.tokenizer.pad_token_id
    model.config.eos_token_id = processor.tokenizer.eos_token_id
    freeze_encoder = args.freeze_encoder
    if freeze_encoder is None:
        freeze_encoder = not torch.cuda.is_available()
    if freeze_encoder:
        model.encoder.requires_grad_(False)

    train_dataset = PopsDataset(
        args.data_dir / "train.jsonl", dataset_root, processor, args.max_target_length
    )
    validation_dataset = PopsDataset(
        args.data_dir / "validation.jsonl", dataset_root, processor, args.max_target_length
    )
    print(
        f"Training on {len(train_dataset)} records; validating on {len(validation_dataset)}; "
        f"device={'CUDA' if torch.cuda.is_available() else 'CPU'}; "
        f"input={args.image_width}x{args.image_height}; encoder_frozen={freeze_encoder}",
        flush=True,
    )

    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        logging_steps=10,
        fp16=torch.cuda.is_available(),
        report_to="none",
        remove_unused_columns=False,
        dataloader_num_workers=0,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        processing_class=processor.tokenizer,
        data_collator=collate_batch,
    )
    train_result = trainer.train()
    trainer.save_model(str(args.output_dir))
    processor.save_pretrained(str(args.output_dir))

    test_dataset = PopsDataset(args.data_dir / "test.jsonl", dataset_root, processor, args.max_target_length)
    metrics = trainer.evaluate(eval_dataset=test_dataset, metric_key_prefix="test")
    validation_losses = [
        entry["eval_loss"] for entry in trainer.state.log_history if "eval_loss" in entry
    ]
    summary = {
        "base_model": args.model,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "image_size": {"height": args.image_height, "width": args.image_width},
        "encoder_frozen": freeze_encoder,
        "records": {
            split: len((args.data_dir / f"{split}.jsonl").read_text(encoding="utf-8").splitlines())
            for split in ("train", "validation", "test")
        },
        "train_loss": float(train_result.metrics["train_loss"]),
        "validation_loss": float(validation_losses[-1]) if validation_losses else None,
        "test_loss": float(metrics["test_loss"]),
        "train_runtime_seconds": float(train_result.metrics["train_runtime"]),
    }
    (args.output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Test loss: {summary['test_loss']}")
    print(f"Model and processor saved to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
