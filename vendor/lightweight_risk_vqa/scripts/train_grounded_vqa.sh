#!/usr/bin/env bash
# Same-init mask/VQA rehearsal control. Supply assets explicitly; never download implicitly.
set -euo pipefail
if [[ $# -lt 3 || $# -gt 6 ]]; then
  echo 'Usage: bash scripts/train_grounded_vqa.sh DATASET INIT_BUNDLE NEW_EXPERIMENT [EPOCHS=2] [DRAWS=240] [--dry-run]' >&2
  exit 2
fi
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
data="$1"
init="$2"
experiment="$3"
epochs="${4:-2}"
draws="${5:-240}"
dry_run="${6:-}"
if [[ -n "$dry_run" && "$dry_run" != --dry-run ]]; then
  echo 'Last argument must be --dry-run or omitted.' >&2
  exit 2
fi
if [[ -e "$experiment" ]]; then
  echo 'Experiment output exists; choose a new directory.' >&2
  exit 2
fi
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
weights="$(cat "$data/sampling_weights.json")"
checkpoint_root="$experiment/checkpoints"
result_root="$experiment/results"
run_command() {
  if [[ "$dry_run" == --dry-run ]]; then
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}
if [[ "$dry_run" != --dry-run ]]; then
  mkdir -p "$result_root"
  # Keep the exact VQA evaluation IDs with this experiment; mask examples excluded.
  cat "$data/jaad_val.jsonl" "$data/store_val.jsonl" > "$result_root/vqa_val.jsonl"
fi
for run in frozen_memory train_memory; do
  extra=()
  [[ "$run" != frozen_memory ]] || extra+=(--freeze-memory)
  command=(python3 -u -m light_foundation_vlm.train
    --stage joint --freeze-encoder "${extra[@]}" --init "$init"
    --train-jsonl "$data/train.jsonl" --val-jsonl "$data/val.jsonl"
    --output "$checkpoint_root/$run" --image-size 320 --max-frames 8 --time-unit frames
    --epochs "$epochs" --batch-size 1 --epoch-samples "$draws"
    --sampling-group-key sampling_group --group-sampling-weights "$weights"
    --eval-group-key eval_group --selection-groups jaad,store
    --lr 2e-5 --language-lr 2e-6 --language-last-layers 1
    --device cuda:0 --dtype bfloat16 --seed 42 --log-every 40 --save-best-only)
  if [[ "$dry_run" == --dry-run ]]; then
    run_command "${command[@]}"
  else
    "${command[@]}" 2>&1 | tee "$result_root/$run.train.log"
  fi
done
for run in baseline frozen_memory train_memory; do
  checkpoint="$checkpoint_root/$run/best"
  histories=(full last)
  if [[ "$run" == baseline ]]; then
    checkpoint="$init"
    histories=(full)
  fi
  for history in "${histories[@]}"; do
    extra=()
    [[ "$history" != last ]] || extra+=(--last-frame-only)
    command=(python3 -u -m lightweight_risk_vqa.temporal_vqa_infer
      --checkpoint "$checkpoint" --manifest "$result_root/vqa_val.jsonl"
      --device cuda:0 --max-new-tokens 64 "${extra[@]}")
    if [[ "$dry_run" == --dry-run ]]; then
      run_command "${command[@]}"
    else
      "${command[@]}" > "$result_root/$run.$history.val.jsonl"
    fi
  done
done
run_command python3 -m lightweight_risk_vqa.summarize_grounded_vqa \
  --results "$result_root" --checkpoints "$checkpoint_root" \
  --manifest "$result_root/vqa_val.jsonl" \
  --baseline-predictions "$result_root/baseline.full.val.jsonl"
