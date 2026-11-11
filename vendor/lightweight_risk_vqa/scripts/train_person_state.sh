#!/usr/bin/env bash
# Joint person segmentation/state training; matched frozen-memory control optional.
set -euo pipefail
if [[ $# -lt 3 || $# -gt 7 ]]; then
  echo 'Usage: bash scripts/train_person_state.sh DATASET INIT_BUNDLE NEW_RUN [EPOCHS=3] [DRAWS=1200] [train|frozen] [--dry-run]' >&2
  exit 2
fi
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
data="$1"; init="$2"; run="$3"
epochs="${4:-3}"; draws="${5:-1200}"; memory="${6:-train}"; dry_run="${7:-}"
[[ -f "$data/READY" && -f "$init/manifest.json" && ! -e "$run" ]] || {
  echo 'Require a complete dataset, full initial bundle, and a NEW run directory.' >&2; exit 2;
}
[[ "$memory" == train || "$memory" == frozen ]] || exit 2
[[ -z "$dry_run" || "$dry_run" == --dry-run ]] || exit 2
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
extra=()
[[ "$memory" != frozen ]] || extra+=(--freeze-memory)
weights="$(cat "$data/sampling_weights.json")"
command=(python3 -u -m light_foundation_vlm.train
  --stage joint --freeze-encoder --augment "${extra[@]}" --init "$init"
  --train-jsonl "$data/train.jsonl" --val-jsonl "$data/val.jsonl"
  --output "$run" --image-size 320 --max-frames 8 --time-unit frames
  --epochs "$epochs" --batch-size 1 --epoch-samples "$draws"
  --sampling-group-key sampling_group --group-sampling-weights "$weights"
  --eval-group-key eval_group --selection-groups segmentation,state
  --lr 2e-5 --language-lr 2e-6 --language-last-layers 1
  --device cuda:0 --dtype bfloat16 --seed 42 --log-every 40
  --save-best-only --eval-history-ablation)
if [[ "$dry_run" == --dry-run ]]; then
  printf '%q ' "${command[@]}"; printf '\n'
else
  "${command[@]}"
fi
