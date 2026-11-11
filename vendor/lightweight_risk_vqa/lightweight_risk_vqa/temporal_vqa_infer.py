"""Run causal ConvLSTM + embedded Qwen VQA generation on a JSONL manifest."""

import argparse
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch

from .vqa_metrics import classification_metrics
from .person_state import ACTIVITIES, TRANSITIONS, state_schema_error

MOTIONS = {"TOWARD_CAMERA", "AWAY_FROM_CAMERA", "LATERAL_ACROSS_VIEW", "STATIONARY", "UNCERTAIN"}
ACTIONS = {"STOP", "DANGER_WARNING", "CONTINUE", "NO_RESPONSE", "OBSERVE"}


def load_frames(record, root, device, image_size=320):
    tensors = []
    for value in record["frames"]:
        path = Path(value)
        path = path if path.is_absolute() else root / path
        with Image.open(path) as image:
            array = np.asarray(
                image.convert("RGB").resize((image_size, image_size), Image.Resampling.BILINEAR)
            ).copy()
        tensor = torch.from_numpy(array).permute(2, 0, 1).float().div(255)
        tensor = (tensor - tensor.new_tensor((0.485, 0.456, 0.406))[:, None, None])
        tensor = tensor / tensor.new_tensor((0.229, 0.224, 0.225))[:, None, None]
        tensors.append(tensor)
    return torch.stack(tensors)[None].to(device)


def time_deltas(record, device):
    unit = record.get("time_unit", "seconds")
    values = record["frame_indices" if unit == "frames" else "timestamps_s"]
    first = 1.0 if unit == "frames" else 0.1
    return torch.tensor([[first] + [b - a for a, b in zip(values, values[1:])]],
                        dtype=torch.float32, device=device)


def history_ablation(record, last_only=False, repeat_last=False):
    record = dict(record)
    if last_only:
        record['frames'] = record['frames'][-1:]
        key = 'frame_indices' if record.get('time_unit', 'seconds') == 'frames' else 'timestamps_s'
        record[key] = record[key][-1:]
    elif repeat_last:
        record['frames'] = record['frames'][-1:] * len(record['frames'])
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-new-tokens", type=int, default=96)
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--last-frame-only", action="store_true",
                        help="Ablation: reset per sample and provide only its final image")
    parser.add_argument('--repeat-last-frame', action='store_true',
                        help='Ablation: retain sequence length but repeat NOW, removing motion evidence')
    args = parser.parse_args(argv)
    if args.last_frame_only and args.repeat_last_frame:
        parser.error('Choose only one history ablation')
    if args.max_new_tokens < 1 or args.max_samples < 0:
        parser.error("Token/sample limits must be positive or zero for all samples")
    from light_foundation_vlm.checkpoint import load_bundle

    device = torch.device(args.device)
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    model, tokenizer, bundle = load_bundle(args.checkpoint, dtype)
    if not model.use_temporal:
        raise ValueError("Checkpoint has no temporal memory")
    model.to(device).eval()
    records = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    if args.max_samples:
        records = records[:args.max_samples]
    if not records:
        raise ValueError("Manifest contains no samples")
    results = []
    for record in records:
        record = history_ablation(record, args.last_frame_only, args.repeat_last_frame)
        prompt = tokenizer.encode(record["query"] + "\n<seg>\n", add_special_tokens=False)
        input_ids = torch.tensor(prompt, dtype=torch.long, device=device)[None]
        frames = load_frames(record, args.manifest.resolve().parent, device)
        valid = torch.ones(frames.shape[:2], dtype=torch.bool, device=device)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        started = time.perf_counter()
        with torch.inference_mode():
            output = model.generate_answer(
                frames, input_ids, attention_mask=torch.ones_like(input_ids),
                frame_valid=valid, dt=time_deltas(record, device),
                max_new_tokens=args.max_new_tokens,
            )
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        raw = tokenizer.decode(output["generated_ids"][0], skip_special_tokens=True).strip()
        try:
            parsed = json.loads(raw)
            valid_json = isinstance(parsed, dict)
        except (json.JSONDecodeError, TypeError):
            parsed, valid_json = None, False
        expected = None
        if record.get("answer"):
            try:
                expected = json.loads(record["answer"])
            except json.JSONDecodeError:
                expected = record["answer"]
        schema_valid = bool(
            valid_json
            and parsed.get("motion") in MOTIONS
            and parsed.get("action") in ACTIONS
            and isinstance(parsed.get("explanation"), str)
        )
        if record.get('state_task'):
            schema_valid = schema_valid and not state_schema_error(parsed)
        result = {
            "id": record["id"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "history_frames": int(frames.shape[1]),
            "visual_memory_tokens": int(output["visual_token_count"]),
            "valid_json": valid_json,
            "schema_valid": schema_valid,
            "prediction": parsed if valid_json else raw,
            "expected": expected,
        }
        if isinstance(expected, dict):
            result["motion_correct"] = bool(valid_json and parsed.get("motion") == expected.get("motion"))
            result["action_correct"] = bool(valid_json and parsed.get("action") == expected.get("action"))
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    motion_metrics = classification_metrics(results, 'motion', MOTIONS)
    action_metrics = classification_metrics(results, 'action', ACTIONS)
    latencies = sorted(row["latency_ms"] for row in results)
    summary = {
        "samples": len(results),
        "valid_json_rate": sum(row["valid_json"] for row in results) / len(results),
        "schema_valid_rate": sum(row["schema_valid"] for row in results) / len(results),
        "motion_accuracy": motion_metrics['accuracy'],
        "action_accuracy": action_metrics['accuracy'],
        "motion_metrics": motion_metrics,
        "action_metrics": action_metrics,
        "activity_metrics": classification_metrics(results, 'current_activity', ACTIVITIES),
        "transition_metrics": classification_metrics(results, 'observed_transition', TRANSITIONS),
        "mean_latency_ms": round(sum(row["latency_ms"] for row in results) / len(results), 2),
        "median_latency_ms": round(float(np.median(latencies)), 2),
        "warm_latency_p50_ms": (round(float(np.percentile([row['latency_ms'] for row in results[1:]], 50)), 2)
                                if len(results) > 1 else None),
        "warm_latency_p95_ms": (round(float(np.percentile([row['latency_ms'] for row in results[1:]], 95)), 2)
                                if len(results) > 1 else None),
        "history_mode": ('last_frame_only' if args.last_frame_only else
                         'repeat_last_frame' if args.repeat_last_frame else 'full_history'),
        "checkpoint": str(args.checkpoint),
        "checkpoint_stage": bundle.get("metadata", {}).get("training_args", {}).get("stage"),
        "checkpoint_epoch": bundle.get('metadata', {}).get('metrics', {}).get('epoch'),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
