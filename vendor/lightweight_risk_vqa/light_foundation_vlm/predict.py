"""Local image/ordered-clip inference and visual inspection of trained bundles."""

import argparse
import json
import os
import socket
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageDraw

from .checkpoint import load_bundle
from .data import ClipDataset, collate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="Inspect an existing annotated sample")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--images", type=Path, nargs="+", help="Chronological consecutive frames")
    parser.add_argument("--timestamps", type=float, nargs="+")
    parser.add_argument("--query")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args(argv)
    # Protect this HPC's login nodes while allowing ordinary local Linux deployment.
    host = socket.gethostname().split(".")[0]
    if host.startswith("cbi-lgn") and not os.environ.get("SLURM_JOB_ID"):
        parser.error("HPC login node detected; use srun or submit_preview.sh")
    if bool(args.manifest) == bool(args.images):
        parser.error("Choose --manifest or --images")
    torch.set_num_threads(4)
    dtype = torch.bfloat16 if args.device.startswith("cuda") else torch.float32
    model, tokenizer, manifest = load_bundle(args.checkpoint, dtype)
    settings = manifest["metadata"].get("training_args", {})
    size, steps = settings.get("image_size", 320), settings.get("max_frames", 8)
    if args.manifest:
        records = [
            json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()
        ]
        if not 0 <= args.index < len(records):
            parser.error("Sample index is out of range")
        record = records[args.index].copy()
        record["frames"] = [
            str((args.manifest.resolve().parent / p).resolve()) for p in record["frames"]
        ]
        if record.get("mask"):
            record["mask"] = str((args.manifest.resolve().parent / record["mask"]).resolve())
        if args.query:
            parser.error(
                "Do not override an annotated sample's query: its ground truth would differ"
            )
    else:
        if not args.query:
            parser.error("--images requires --query")
        if not model.use_temporal and len(args.images) != 1:
            parser.error("Use one --images input with the segmentation checkpoint")
        record = {
            "id": "custom",
            "group_id": "custom",
            "frames": [str(p.resolve()) for p in args.images],
            "query": args.query,
        }
        if model.use_temporal and model.config.time_unit == "frames":
            if args.timestamps is not None:
                parser.error("Frame-step checkpoint does not interpret physical --timestamps")
            record.update(time_unit="frames", frame_indices=list(range(len(args.images))))
        else:
            if len(args.images) > 1 and args.timestamps is None:
                parser.error("This checkpoint requires real --timestamps for a multi-frame clip")
            record["timestamps_s"] = args.timestamps if args.timestamps is not None else [0.0]
    if not model.use_temporal:
        record["frames"] = record["frames"][-1:]
        record.pop("frame_indices", None)
        record.update(time_unit="seconds", timestamps_s=[0.0])
    elif record.get("time_unit", "seconds") != model.config.time_unit:
        parser.error("Input time units must match checkpoint time_unit")
    args.output.mkdir(parents=True, exist_ok=False)
    input_path = args.output / "input.jsonl"
    input_path.write_text(json.dumps(record, ensure_ascii=False) + "\n")
    data = ClipDataset(input_path, tokenizer, size, steps if model.use_temporal else 1)
    batch = collate([data[0]], tokenizer.pad_token_id)
    inputs = {
        k: v.to(args.device)
        for k, v in batch.items()
        if k not in {"labels", "mask_targets", "event_targets"}
    }
    model.to(args.device).eval()
    with torch.no_grad():
        output = model(**inputs)
    with Image.open(record["frames"][-1]) as original:
        original = original.convert("RGB")
    logits = output["mask_logits"].float()
    if not torch.isfinite(logits).all():
        raise ValueError("Non-finite prediction")
    probability = (
        F.interpolate(
            logits, size=(original.height, original.width), mode="bilinear", align_corners=False
        )
        .sigmoid()[0, 0]
        .cpu()
        .numpy()
    )
    predicted = probability > 0.5
    Image.fromarray(predicted.astype(np.uint8) * 255).save(args.output / "mask.png")
    np.save(args.output / "probability.npy", probability)
    pixels = np.asarray(original).copy()
    pixels[predicted] = (pixels[predicted] * 0.55 + np.array([255, 50, 50]) * 0.45).astype(np.uint8)
    overlay = Image.fromarray(pixels)
    original.save(args.output / "original.png")
    overlay.save(args.output / "overlay.png")
    panels = [("Original", original), ("Prediction (>0.5)", overlay)]
    if record.get("mask"):
        with Image.open(record["mask"]) as target:
            foreground_value = (
                255 if record.get("mask_encoding", "binary_255") == "binary_255" else 1
            )
            target = np.asarray(target.convert("L")) == foreground_value
        truth = np.asarray(original).copy()
        truth[target] = (truth[target] * 0.55 + np.array([30, 255, 80]) * 0.45).astype(np.uint8)
        panels.append(("Ground truth", Image.fromarray(truth)))
    canvas = Image.new("RGB", (original.width * len(panels), original.height + 30), "white")
    for index, (title, panel) in enumerate(panels):
        canvas.paste(panel, (index * original.width, 30))
        ImageDraw.Draw(canvas).text((index * original.width + 5, 8), title, fill="black")
    canvas.save(args.output / "comparison.png")
    result = {
        "checkpoint": str(args.checkpoint.resolve()),
        "query": record["query"],
        "sample_id": record["id"],
        "frames": len(record["frames"]),
        "temporal": model.use_temporal,
        "presence_score_uncalibrated": float(output["presence_logits"].sigmoid()[0]),
        "threshold": 0.5,
        "predicted_area_fraction": float(predicted.mean()),
        "note": "Raw mask threshold; presence score is not used to suppress the mask.",
    }
    (args.output / "prediction.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
