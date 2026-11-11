#!/usr/bin/env bash
# Continue an existing 5-class recovery bundle; all outputs go to a new experiment.
set -euo pipefail
if [[ $# -lt 3 || $# -gt 5 ]]; then
  echo 'Usage: continue_recovery.sh DATA_ROOT INIT_BUNDLE NEW_OUTPUT [EPOCHS=6] [DRAWS_PER_EPOCH=12000]' >&2
  exit 2
fi
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$package_root:${PYTHONPATH:-}"
data_root=$1
init_bundle=$2
new_output=$3
train_epochs=${4:-6}
epoch_draws=${5:-12000}
weights='{"count":0.1,"presence":0.05,"caption":0.1,"human_state":0.1,"human_mask":0.2,"mevis_mask":0.05,"weak_retail":0.15,"weak_robot:TOWARD_CAMERA":0.05,"weak_robot:AWAY_FROM_CAMERA":0.05,"weak_robot:LATERAL_ACROSS_VIEW":0.05,"weak_robot:STATIONARY":0.05,"weak_robot:UNCERTAIN":0.05}'
exec python3 -m light_foundation_vlm.train \
  --stage joint --init "$init_bundle" --output "$new_output" \
  --train-jsonl "$data_root/stages/c_train.jsonl" --val-jsonl "$data_root/stages/c_val.jsonl" \
  --device cuda:0 --dtype bfloat16 --time-unit frames --freeze-encoder \
  --lora-rank 8 --lora-alpha 16 --language-lr 0.00001 --lr 0.00002 \
  --event-classes 5 --event-label-names TOWARD_CAMERA,AWAY_FROM_CAMERA,LATERAL_ACROSS_VIEW,STATIONARY,UNCERTAIN \
  --sampling-group-key recovery_sampling_group --group-sampling-weights "$weights" \
  --eval-group-key mix_group --epochs "$train_epochs" --epoch-samples "$epoch_draws" \
  --patience 2 --save-best-only --log-every 100
