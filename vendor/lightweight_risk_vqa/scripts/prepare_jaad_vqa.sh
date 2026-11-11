#!/usr/bin/env bash
# Run inside vlpoint-inference; downloads the public JAAD videos once (~3.08 GB).
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo 'Usage: bash scripts/prepare_jaad_vqa.sh JAAD_SOURCE_DIR NEW_DATASET_DIR' >&2
  exit 2
fi
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_root="$1"
dataset_output="$2"
jaad_revision=d2feeac998366e47ecc54e5533c8a3fa61141dda
if [[ -e "$dataset_output" ]]; then
  echo "Output exists; choose a new version: $dataset_output" >&2
  exit 2
fi
if [[ ! -d "$source_root/.git" ]]; then
  git init "$source_root"
  git -C "$source_root" remote add origin https://github.com/ykotseruba/JAAD.git
  git -C "$source_root" fetch --depth 1 origin "$jaad_revision"
  git -C "$source_root" checkout --detach FETCH_HEAD
fi
if [[ "$(git -C "$source_root" rev-parse HEAD)" != "$jaad_revision" ]]; then
  echo "JAAD source must use pinned revision $jaad_revision; existing checkout was not changed." >&2
  exit 2
fi
if [[ ! -d "$source_root/JAAD_clips" ]]; then
  curl -fL --retry 3 --connect-timeout 20 --continue-at - \
    --output "$source_root/JAAD_clips.zip" \
    https://data.nvision2.eecs.yorku.ca/JAAD_dataset/data/JAAD_clips.zip
  printf '%s  %s\n' '1eeb511f74c2002907f06ec9c0a286ef85b7058f232595d4c28106c436d1582d' \
    "$source_root/JAAD_clips.zip" | sha256sum --check
  unzip -q -n "$source_root/JAAD_clips.zip" -d "$source_root"
fi
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
python3 -m lightweight_risk_vqa.prepare_jaad_vqa \
  --jaad-root "$source_root" --videos "$source_root/JAAD_clips" \
  --output "$dataset_output" --split-set all_videos \
  --steps 8 --sample-hz 2 --stride-s 1 --min-height 64 --seed 42
python3 -m lightweight_risk_vqa.audit_jaad_vqa \
  --dataset-root "$dataset_output" --jaad-root "$source_root"
cp "$source_root/LICENSE" "$dataset_output/LICENSE_JAAD_CODE.txt"
cp "$source_root/README.md" "$dataset_output/UPSTREAM_JAAD_README.md"
cp "$package_root/docs/TRAINING.md" "$dataset_output/README.md"
