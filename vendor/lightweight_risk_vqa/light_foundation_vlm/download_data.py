"""Download pinned public weights/data and emit the trainer's real-mask manifests."""

import argparse
import hashlib
import json
import random
import shutil
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

GREF_REV = "81eede59b3ac070049f597d023c0ff08d1fb80e9"
MEVIS_REV = "ed94977691d86cb670c47815488db6f46709a40d"
QWEN_REV = "c1899de289a04d12100db370d81485cdf75e47ca"


def hf_url(repo, revision, name, kind="datasets"):
    prefix = "datasets/" if kind == "datasets" else ""
    return f"https://huggingface.co/{prefix}{repo}/resolve/{revision}/{quote(name)}"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, path):
    """Retry atomic downloads; never treat an interrupted .part file as complete."""
    path = Path(path)
    metadata = path.with_suffix(path.suffix + ".download.json")
    if path.exists() and metadata.exists():
        info = json.loads(metadata.read_text())
        if info["url"] == url and info["sha256"] == sha256(path):
            return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    for attempt in range(4):
        try:
            with (
                urlopen(
                    Request(url, headers={"User-Agent": "light-foundation-vlm/0.1"}), timeout=90
                ) as response,
                partial.open("wb") as target,
            ):
                shutil.copyfileobj(response, target, 1024 * 1024)
                expected = response.headers.get("Content-Length")
            if expected is not None and partial.stat().st_size != int(expected):
                raise OSError("Incomplete download")
            partial.replace(path)
            write_json(metadata, {"url": url, "bytes": path.stat().st_size, "sha256": sha256(path)})
            print(f"Downloaded {path.name}: {path.stat().st_size / 1e6:.1f} MB", flush=True)
            return path
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def decode_mask(segmentation, height, width):
    from pycocotools import mask as coco_mask

    if not segmentation:
        return np.zeros((height, width), dtype=bool)
    rle = segmentation
    if isinstance(rle, list):
        rle = coco_mask.merge(coco_mask.frPyObjects(rle, height, width))
    elif isinstance(rle["counts"], list):
        rle = coco_mask.frPyObjects(rle, height, width)
    decoded = coco_mask.decode(rle)
    if decoded.ndim == 3:
        decoded = decoded.any(axis=2)
    if decoded.shape != (height, width):
        raise ValueError("Annotation and image dimensions disagree")
    return decoded.astype(bool)


def save_mask(path, mask):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def write_manifest(path, records):
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
    tmp.replace(path)


def weights(args):
    root = args.root.resolve() / "weights"
    names = [
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "model.safetensors",
        "LICENSE",
        "README.md",
    ]
    for name in names:
        download(hf_url("Qwen/Qwen3-0.6B", QWEN_REV, name, "models"), root / "qwen3-0.6b" / name)
    download(
        "https://download.pytorch.org/models/mobilenet_v3_large-5c1a4163.pth",
        root / "mobilenet_v3_large.pth",
    )
    write_json(
        root / "sources.json",
        {
            "qwen": {"repo": "Qwen/Qwen3-0.6B", "revision": QWEN_REV},
            "vision": "torchvision MobileNet_V3_Large_Weights.IMAGENET1K_V2",
        },
    )


def select_refs(refs, split, limit, seed):
    candidates = [ref for ref in refs if ref["split"] == split]
    # Keep positives and no-target examples in the pilot, approximately in source proportions.
    positive = [ref for ref in candidates if not ref["no_target"]]
    negative = [ref for ref in candidates if ref["no_target"]]
    rng = random.Random(seed)
    rng.shuffle(positive)
    rng.shuffle(negative)
    if limit and limit < len(candidates):
        n = round(limit * len(negative) / len(candidates))
        selected = positive[: limit - n] + negative[:n]
    else:
        selected = positive + negative
    rng.shuffle(selected)
    return selected


def grefcoco(args):
    root = args.root.resolve() / "data" / "grefcoco"
    source = root / "source"
    for name in ["grefs(unc).json", "instances.json", "README.md"]:
        download(hf_url("FudanCVL/gRefCOCO", GREF_REV, name), source / name)
    refs = json.loads((source / "grefs(unc).json").read_text())
    coco = json.loads((source / "instances.json").read_text())
    images = {row["id"]: row for row in coco["images"]}
    anns = {row["id"]: row for row in coco["annotations"]}
    splits = {
        "train": select_refs(refs, "train", args.train_refs, args.seed),
        "val": select_refs(refs, "val", args.val_refs, args.seed),
    }
    if {r["image_id"] for r in splits["train"]} & {r["image_id"] for r in splits["val"]}:
        raise ValueError("Official selected image groups overlap across splits")
    image_ids = sorted({ref["image_id"] for rows in splits.values() for ref in rows})

    def get_image(image_id):
        item = images[image_id]
        target = root / "images" / f"{image_id:012d}.jpg"
        local = args.coco_images / f"{image_id:012d}.jpg" if args.coco_images else None
        if local and local.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.symlink_to(local.resolve())
        else:
            # Path-style official COCO S3 bucket avoids the dotted bucket TLS mismatch.
            download(
                f"https://s3.amazonaws.com/images.cocodataset.org/train2014/COCO_train2014_{image_id:012d}.jpg",
                target,
            )
        with Image.open(target) as image:
            if image.size != (item["width"], item["height"]):
                raise ValueError(f"Image dimensions mismatch: {image_id}")
            image.verify()
        return image_id, str(target)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        paths = dict(executor.map(get_image, image_ids))
    report = {
        "dataset": "FudanCVL/gRefCOCO",
        "revision": GREF_REV,
        "seed": args.seed,
        "selection": "official splits, stratified references, first sentence per reference",
        "unique_images": len(paths),
        "splits": {},
    }
    for split, selected in splits.items():
        records = []
        for ref in selected:
            item = images[ref["image_id"]]
            mask = np.zeros((item["height"], item["width"]), dtype=bool)
            if not ref["no_target"]:
                ann_ids = ref["ann_id"] if isinstance(ref["ann_id"], list) else [ref["ann_id"]]
                for ann_id in ann_ids:
                    ann = anns[ann_id]
                    if ann["image_id"] != ref["image_id"]:
                        raise ValueError("Reference points to annotation in another image")
                    mask |= decode_mask(ann["segmentation"], *mask.shape)
                if not mask.any():
                    raise ValueError("Positive reference has no foreground")
            mask_path = root / "masks" / f"{ref['ref_id']}.png"
            save_mask(mask_path, mask)
            records.append(
                {
                    "id": f"gref_{ref['ref_id']}",
                    "group_id": f"coco_image_{ref['image_id']}",
                    "frames": [paths[ref["image_id"]]],
                    "timestamps_s": [0.0],
                    "query": ref["sentences"][0]["sent"],
                    "mask": str(mask_path),
                    "mask_encoding": "binary_255",
                    "no_target": bool(ref["no_target"]),
                    "source_split": split,
                }
            )
        write_manifest(root / f"{split}.jsonl", records)
        report["splits"][split] = {
            "samples": len(records),
            "negative": sum(r["no_target"] for r in records),
        }
    write_json(root / "report.json", report)
    print(json.dumps(report, indent=2), flush=True)


def extract_video_subset(url, destination, videos, count):
    """Stream only a pilot subset; no 15 GB archive or unsafe extractall on disk.

    Selection follows archive order (documented pilot bias). All frames of each selected
    video must be present before stopping. Existing complete selections are reusable.
    """
    marker = destination / "selected_videos.json"
    if marker.exists():
        previous = json.loads(marker.read_text())
        if previous["url"] == url and previous["count"] == count:
            selected = previous["videos"]
            if all(
                (destination / v / f"{f}.jpg").is_file()
                for v in selected
                for f in videos[v]["frames"]
            ):
                return selected
    destination.mkdir(parents=True, exist_ok=True)
    selected, found = [], {}
    with (
        urlopen(url, timeout=90) as response,
        tarfile.open(fileobj=response, mode="r|*") as archive,
    ):
        for member in archive:
            parts = Path(member.name).parts
            if not member.isfile() or len(parts) < 2 or not parts[-1].endswith(".jpg"):
                continue
            video, filename = parts[-2:]
            if video not in videos or Path(filename).stem not in videos[video]["frames"]:
                continue
            if video not in selected:
                if count and len(selected) >= count:
                    continue
                selected.append(video)
                found[video] = set()
            target = destination / video / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with (
                archive.extractfile(member) as source,
                target.with_suffix(".part").open("wb") as output,
            ):
                shutil.copyfileobj(source, output)
            target.with_suffix(".part").replace(target)
            found[video].add(Path(filename).stem)
            if (
                count
                and len(selected) == count
                and all(found[v] == set(videos[v]["frames"]) for v in selected)
            ):
                break
    if not selected or any(found[v] != set(videos[v]["frames"]) for v in selected):
        raise ValueError("Incomplete videos in remote archive")
    write_json(marker, {"url": url, "count": count, "videos": selected})
    return selected


def select_expressions(expressions, limit, policy="first", seed=42):
    items = list(expressions.items())
    if policy == "first" or not limit:
        return items[:limit] if limit else items
    # Round-robin distinct annotated target sets before taking paraphrases.
    buckets = {}
    for item in items:
        ids = item[1]["anno_id"]
        ids = ids if isinstance(ids, list) else [ids]
        key = tuple(sorted(str(value) for value in ids if str(value) != "-1"))
        buckets.setdefault(key, []).append(item)
    rng = random.Random(seed)
    keys = list(buckets)
    rng.shuffle(keys)
    # Include an official no-target query when available, even with a small limit.
    if () in keys:
        keys.remove(())
        keys.insert(0, ())
    for bucket in buckets.values():
        rng.shuffle(bucket)
    selected = []
    while len(selected) < min(limit, len(items)):
        for key in keys:
            if buckets[key] and len(selected) < limit:
                selected.append(buckets[key].pop())
    return selected


def mevis(args):
    root = args.root.resolve() / "data" / "mevis"
    report = {
        "dataset": "FudanCVL/MeViSv2",
        "revision": MEVIS_REV,
        "time_unit": "frames",
        "fps": None,
        "selection": "first complete videos in archive, not a representative benchmark",
        "expression_selection": args.expression_policy,
        "clip_length": args.clip_length,
        "clip_stride": args.clip_stride,
        "splits": {},
    }
    for split, count in [("train", args.train_videos), ("valid_u", args.val_videos)]:
        source = root / split
        for name in ["meta_expressions_v2.json", "mask_dict.json"]:
            download(hf_url("FudanCVL/MeViSv2", MEVIS_REV, f"{split}/{name}"), source / name)
        videos = json.loads((source / "meta_expressions_v2.json").read_text())["videos"]
        masks = json.loads((source / "mask_dict.json").read_text())
        selected = extract_video_subset(
            hf_url("FudanCVL/MeViSv2", MEVIS_REV, f"{split}/JPEGImages.tar"),
            source / "JPEGImages",
            videos,
            count,
        )
        records = []
        for video in selected:
            info = videos[video]
            frames = sorted(info["frames"])
            expressions = select_expressions(
                info["expressions"], args.expressions_per_video, args.expression_policy, args.seed
            )
            for exp_id, expression in expressions:
                for end in range(args.clip_length - 1, len(frames), args.clip_stride):
                    indices = list(range(end + 1 - args.clip_length, end + 1))
                    paths = [source / "JPEGImages" / video / f"{frames[i]}.jpg" for i in indices]
                    with Image.open(paths[-1]) as image:
                        width, height = image.size
                    mask = np.zeros((height, width), dtype=bool)
                    ann_ids = expression["anno_id"]
                    if not isinstance(ann_ids, list):
                        ann_ids = [ann_ids]
                    # v2 explicit no-target annotations use [-1]; unknown IDs must fail.
                    for ann_id in ann_ids:
                        if str(ann_id) == "-1":
                            continue
                        annotation = masks[str(ann_id)][end]
                        if annotation is not None:
                            mask |= decode_mask(annotation, height, width)
                    key = f"{video}_{exp_id}_{end:05d}"
                    mask_path = source / "masks" / f"{key}.png"
                    save_mask(mask_path, mask)
                    records.append(
                        {
                            "id": f"mevis_{key}",
                            "group_id": f"mevis_{video}",
                            "frames": [str(p) for p in paths],
                            "frame_indices": indices,
                            "time_unit": "frames",
                            "query": expression["exp"],
                            "mask": str(mask_path),
                            "mask_encoding": "binary_255",
                            "source_split": split,
                            "no_target": not any(str(x) != "-1" for x in ann_ids),
                            "last_frame_empty": not bool(mask.any()),
                        }
                    )
        if not records:
            raise ValueError("No temporal clips produced")
        write_manifest(root / f"{split}.jsonl", records)
        report["splits"][split] = {
            "videos": selected,
            "clips": len(records),
            "no_target_queries": sum(r["no_target"] for r in records),
            "empty_last_frame": sum(r["last_frame_empty"] for r in records),
        }
        del masks
    if set(report["splits"]["train"]["videos"]) & set(report["splits"]["valid_u"]["videos"]):
        raise ValueError("Train/validation video overlap")
    write_json(root / "report.json", report)
    print(json.dumps(report, indent=2), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, required=True, help="Separate writable runtime directory"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("weights")
    gref = sub.add_parser("grefcoco")
    gref.add_argument("--train-refs", type=int, default=256, help="0 = all")
    gref.add_argument("--val-refs", type=int, default=32, help="0 = all")
    gref.add_argument("--seed", type=int, default=42)
    gref.add_argument("--workers", type=int, default=16, help="Concurrent COCO image requests")
    gref.add_argument(
        "--coco-images", type=Path, help="Reuse existing COCO 2017 images by image ID"
    )
    video = sub.add_parser("mevis")
    video.add_argument("--train-videos", type=int, default=4, help="0 = all (large)")
    video.add_argument("--val-videos", type=int, default=2, help="0 = all")
    video.add_argument("--expressions-per-video", type=int, default=4, help="0 = all")
    video.add_argument("--clip-length", type=int, default=4)
    video.add_argument("--clip-stride", type=int, default=8)
    video.add_argument("--expression-policy", choices=["first", "diverse"], default="first")
    video.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    if args.command == "grefcoco" and not 1 <= args.workers <= 32:
        parser.error("workers must be between 1 and 32")
    for key, value in vars(args).items():
        if (
            key in {"train_refs", "val_refs", "train_videos", "val_videos", "expressions_per_video"}
            and value < 0
        ):
            parser.error(f"{key} must be nonnegative")
    if args.command == "mevis" and (args.clip_length < 2 or args.clip_stride < 1):
        parser.error("clip-length >= 2 and clip-stride >= 1 required")
    {"weights": weights, "grefcoco": grefcoco, "mevis": mevis}[args.command](args)


if __name__ == "__main__":
    main()
