#!/usr/bin/env bash
# Evaluate a supplied temporal bundle on real masks and VQA; no training or test selection.
set -euo pipefail
if [[ $# -ne 4 ]]; then
  echo 'Usage: bash scripts/evaluate_vqa.sh BUNDLE MASK_MANIFEST VQA_MANIFEST NEW_OUTPUT' >&2
  exit 2
fi
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -e "$4" ]]; then
  echo 'Output exists; choose a new directory.' >&2
  exit 2
fi
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$4"
python3 -m lightweight_risk_vqa.evaluate_grounded_masks \
  --checkpoint "$1" --manifest "$2" --output "$4/masks" --device cuda:0
python3 -m lightweight_risk_vqa.temporal_vqa_infer \
  --checkpoint "$1" --manifest "$3" --device cuda:0 --max-new-tokens 64 > "$4/vqa.jsonl"
python3 -m lightweight_risk_vqa.render_vqa_report \
  --manifest "$3" --predictions "$4/vqa.jsonl" --output "$4/vqa.html"
