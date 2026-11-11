import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageEnhance, ImageOps
from torch.utils.data import Dataset


class ClipDataset(Dataset):
    """JSONL episodes; each target mask describes the final frame and one query."""

    def __init__(self, manifest, tokenizer, image_size=320, max_frames=8, max_text=512, augment=False):
        self.root = Path(manifest).resolve().parent
        self.records = [
            json.loads(line) for line in Path(manifest).read_text().splitlines() if line.strip()
        ]
        self.tokenizer = tokenizer
        self.image_size, self.max_frames, self.max_text = image_size, max_frames, max_text
        self.augment = augment
        if not self.records or image_size < 32 or image_size % 32 or max_frames < 1:
            raise ValueError(
                "Nonempty data, image size divisible by 32, and positive max_frames required"
            )
        seen = set()
        for record in self.records:
            if record["id"] in seen or not record["group_id"]:
                raise ValueError("IDs must be unique and group_id must identify source recording")
            seen.add(record["id"])
            if not 1 <= len(record["frames"]) <= max_frames:
                raise ValueError("Split into clips with 1..max_frames before training")
            unit = record.get("time_unit", "seconds")
            if unit not in ("seconds", "frames"):
                raise ValueError("time_unit must be seconds or frames")
            if unit == "frames" and "timestamps_s" in record:
                raise ValueError("Frame-index data must not claim physical timestamps")
            stamps = record["frame_indices" if unit == "frames" else "timestamps_s"]
            if (
                len(stamps) != len(record["frames"])
                or not np.isfinite(stamps).all()
                or any(b <= a for a, b in zip(stamps, stamps[1:]))
            ):
                raise ValueError("Each clip needs finite, strictly increasing frame timestamps")
            if unit == "frames" and any(value < 0 or int(value) != value for value in stamps):
                raise ValueError("frame_indices must be nonnegative integers")
            if "<seg>" in record["query"] or "<seg>" in record.get("answer", ""):
                raise ValueError("Dataset inserts the <seg> marker; do not include it in text")

    @property
    def time_units(self):
        return {record.get("time_unit", "seconds") for record in self.records}

    @property
    def groups(self):
        return {record["group_id"] for record in self.records}

    @property
    def frame_paths(self):
        return {
            (self.root / path).resolve() for record in self.records for path in record["frames"]
        }

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        record = self.records[index]
        # Opt-in per record: never flip arbitrary referring expressions or direction labels.
        augment = self.augment and record.get('augmentation_safe_flip', False)
        flip = augment and bool(torch.rand(()) < .5)
        brightness = float(.8 + .4 * torch.rand(())) if augment else 1.0
        contrast = float(.8 + .4 * torch.rand(())) if augment else 1.0
        frames, original_size = [], None
        for path in record["frames"]:
            with Image.open(self.root / path) as image:
                original_size = image.size
                rgb = image.convert('RGB')
                if flip:
                    rgb = ImageOps.mirror(rgb)
                if augment:
                    rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
                    rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
                resized = rgb.resize(
                    (self.image_size, self.image_size), Image.Resampling.BILINEAR
                )
                array = np.asarray(resized).copy()
            tensor = torch.from_numpy(array).permute(2, 0, 1).float() / 255
            tensor = (
                tensor - tensor.new_tensor([0.485, 0.456, 0.406])[:, None, None]
            ) / tensor.new_tensor([0.229, 0.224, 0.225])[:, None, None]
            frames.append(tensor)
        target = torch.full((1, self.image_size, self.image_size), 255.0)
        if record.get("mask"):
            with Image.open(self.root / record["mask"]) as image:
                if image.size != original_size:
                    raise ValueError("Mask must match the last original frame size")
                if flip:
                    image = ImageOps.mirror(image)
                array = np.asarray(
                    image.convert("L").resize(
                        (self.image_size, self.image_size), Image.Resampling.NEAREST
                    )
                ).copy()
            encoding = record.get("mask_encoding", "binary_255")
            if encoding == "binary_255":
                if not np.isin(array, [0, 255]).all():
                    raise ValueError("binary_255 masks contain only background=0/foreground=255")
                array = array // 255
            elif encoding != "indexed":
                raise ValueError("mask_encoding must be binary_255 or indexed (0/1/255-ignore)")
            if not np.isin(array, [0, 1, 255]).all():
                raise ValueError("Invalid mask values")
            target = torch.from_numpy(array).float()[None]
        prompt = self.tokenizer.encode(record["query"] + "\n<seg>\n", add_special_tokens=False)
        answer = (
            self.tokenizer.encode(record["answer"], add_special_tokens=False)
            + [self.tokenizer.eos_token_id]
            if record.get("answer")
            else []
        )
        if len(prompt) + len(answer) > self.max_text:
            raise ValueError(
                "Text too long; shorten query/answer instead of silently truncating labels"
            )
        unit = record.get("time_unit", "seconds")
        timestamps = record["frame_indices" if unit == "frames" else "timestamps_s"]
        dt = [1.0 if unit == "frames" else 0.1] + [
            b - a for a, b in zip(timestamps, timestamps[1:])
        ]
        return {
            "frames": torch.stack(frames),
            "input_ids": torch.tensor(prompt + answer),
            "labels": torch.tensor([-100] * len(prompt) + answer),
            "mask_targets": target,
            "dt": torch.tensor(dt),
            "event_targets": torch.tensor(record.get("event_label", -100)),
        }


def collate(samples, pad_token_id):
    batch = len(samples)
    steps = max(s["frames"].shape[0] for s in samples)
    length = max(len(s["input_ids"]) for s in samples)
    frames = samples[0]["frames"].new_zeros(batch, steps, *samples[0]["frames"].shape[1:])
    valid = torch.zeros(batch, steps, dtype=torch.bool)
    ids = torch.full((batch, length), pad_token_id, dtype=torch.long)
    attention = torch.zeros_like(ids)
    labels = torch.full_like(ids, -100)
    dt = torch.zeros(batch, steps)
    for index, sample in enumerate(samples):
        t, length = sample["frames"].shape[0], len(sample["input_ids"])
        frames[index, :t], valid[index, :t] = sample["frames"], True
        ids[index, :length], attention[index, :length] = sample["input_ids"], 1
        labels[index, :length], dt[index, :t] = sample["labels"], sample["dt"]
    return {
        "frames": frames,
        "frame_valid": valid,
        "input_ids": ids,
        "attention_mask": attention,
        "labels": labels,
        "dt": dt,
        "mask_targets": torch.stack([s["mask_targets"] for s in samples]),
        "event_targets": torch.stack([s["event_targets"] for s in samples]),
    }


def check_split(train, validation):
    if train.groups & validation.groups or train.frame_paths & validation.frame_paths:
        raise ValueError(
            "Train/validation leakage: split by source recording, not neighboring frames"
        )
