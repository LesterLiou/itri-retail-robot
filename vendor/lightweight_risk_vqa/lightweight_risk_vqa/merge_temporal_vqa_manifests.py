"""Merge temporal-VQA manifests while preserving resolvable frame paths."""

import argparse
import json
from pathlib import Path


def merge_manifests(inputs, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite: {output}")
    rows = []
    sample_ids = set()
    for manifest_value in inputs:
        manifest = Path(manifest_value).resolve()
        with manifest.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                sample_id = row.get("id")
                if not sample_id or sample_id in sample_ids:
                    raise ValueError(
                        f"Missing or duplicate sample id {sample_id!r} at {manifest}:{line_number}"
                    )
                sample_ids.add(sample_id)
                absolute_frames = []
                for value in row.get("frames", []):
                    frame = Path(value)
                    frame = frame if frame.is_absolute() else manifest.parent / frame
                    frame = frame.resolve()
                    if not frame.is_file():
                        raise FileNotFoundError(f"Missing frame referenced by {sample_id}: {frame}")
                    absolute_frames.append(str(frame))
                if not absolute_frames:
                    raise ValueError(f"Sample {sample_id} has no frames")
                row["frames"] = absolute_frames
                rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"output": str(output.resolve()), "samples": len(rows), "inputs": len(inputs)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(merge_manifests(args.inputs, args.output), indent=2))


if __name__ == "__main__":
    main()
