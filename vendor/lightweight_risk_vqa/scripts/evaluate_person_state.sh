#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 3 ]]; then
  echo 'Usage: bash scripts/evaluate_person_state.sh BUNDLE DATASET NEW_OUTPUT' >&2
  exit 2
fi
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ ! -e "$3" && -f "$2/READY" ]] || exit 2
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$3"
python3 -m lightweight_risk_vqa.evaluate_grounded_masks \
  --checkpoint "$1" --manifest "$2/segmentation_val.jsonl" --output "$3/masks" --device cuda:0
for history in full last repeat; do
  extra=()
  [[ "$history" != last ]] || extra+=(--last-frame-only)
  [[ "$history" != repeat ]] || extra+=(--repeat-last-frame)
  python3 -m lightweight_risk_vqa.temporal_vqa_infer \
    --checkpoint "$1" --manifest "$2/state_val.jsonl" --device cuda:0 \
    --max-new-tokens 128 "${extra[@]}" > "$3/state.$history.jsonl"
done
