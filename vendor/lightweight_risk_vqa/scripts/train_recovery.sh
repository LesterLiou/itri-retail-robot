#!/usr/bin/env bash
# Reproducible pilot budgets; existing experiment directories are never overwritten.
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/workspace}
export PYTHONPATH="$TASK_ROOT/ros2_ws/src/lightweight_risk_vqa:${PYTHONPATH:-}"
DATA_ROOT="${RECOVERY_DATA_ROOT:-$TASK_ROOT/datasets/robot_vqa_recovery_v1}"
MODEL_ROOT="${RECOVERY_MODEL_ROOT:-$TASK_ROOT/checkpoints/robot_vqa_recovery_v1}"
RESULT_ROOT="${RECOVERY_RESULT_ROOT:-$TASK_ROOT/results/robot_vqa_recovery_v1}"
mkdir -p "$RESULT_ROOT"
COMMON=(--stage joint --device cuda:0 --dtype bfloat16 --time-unit frames --freeze-encoder
  --lr 0.0001 --language-lr 0.00005 --sampling-group-key mix_group --eval-group-key mix_group
  --save-best-only --log-every 100)
run_stage() {
  local stage_name=$1
  shift
  python3 -m light_foundation_vlm.train "${COMMON[@]}" "$@" \
    --output "$MODEL_ROOT/$stage_name" > "$RESULT_ROOT/$stage_name.log" 2>&1
}
case "${1:-all}" in
  a)
    run_stage a_lora --init "$TASK_ROOT/checkpoints/person_state_mevis_ablation_v1/with_mevis/best" \
      --train-jsonl "$DATA_ROOT/stages/a_train.jsonl" --val-jsonl "$DATA_ROOT/stages/a_val.jsonl" \
      --lora-rank 8 --freeze-memory --epochs 2 --epoch-samples 1600 \
      --group-sampling-weights '{"count":0.3,"presence":0.15,"caption":0.3,"human_state":0.05,"human_mask":0.2}'
    ;;
  control)
    run_stage a_last_layer --init "$TASK_ROOT/checkpoints/person_state_mevis_ablation_v1/with_mevis/best" \
      --train-jsonl "$DATA_ROOT/stages/a_train.jsonl" --val-jsonl "$DATA_ROOT/stages/a_val.jsonl" \
      --language-last-layers 1 --freeze-memory --epochs 2 --epoch-samples 1600 \
      --group-sampling-weights '{"count":0.3,"presence":0.15,"caption":0.3,"human_state":0.05,"human_mask":0.2}'
    ;;
  b)
    run_stage b_temporal --init "$MODEL_ROOT/a_lora/best" \
      --train-jsonl "$DATA_ROOT/stages/b_train.jsonl" --val-jsonl "$DATA_ROOT/stages/b_val.jsonl" \
      --lora-rank 8 --epochs 2 --epoch-samples 1600 \
      --group-sampling-weights '{"count":0.15,"presence":0.1,"caption":0.15,"human_state":0.3,"human_mask":0.25,"mevis_mask":0.05}'
    ;;
  balanced)
    run_stage b_balanced --init "$MODEL_ROOT/a_lora/best" \
      --train-jsonl "$DATA_ROOT/stages/b_balanced_train.jsonl" --val-jsonl "$DATA_ROOT/stages/b_balanced_val.jsonl" \
      --lora-rank 8 --epochs 2 --epoch-samples 1600 --sampling-group-key recovery_sampling_group \
      --group-sampling-weights '{"count":0.15,"presence":0.1,"caption":0.15,"human_state:WALKING:STEADY":0.075,"human_state:WALKING:STARTED_WALKING":0.075,"human_state:STANDING:STEADY":0.075,"human_state:STANDING:STOPPED_WALKING":0.075,"human_mask":0.25,"mevis_mask":0.05}'
    ;;
  c)
    run_stage c_policy --init "$MODEL_ROOT/b_temporal/best" \
      --train-jsonl "$DATA_ROOT/stages/c_train.jsonl" --val-jsonl "$DATA_ROOT/stages/c_val.jsonl" \
      --lora-rank 8 --attach-event-head --event-classes 5 --event-label-names TOWARD_CAMERA,AWAY_FROM_CAMERA,LATERAL_ACROSS_VIEW,STATIONARY,UNCERTAIN --epochs 2 --epoch-samples 1600 \
      --sampling-group-key recovery_sampling_group \
      --group-sampling-weights '{"count":0.1,"presence":0.05,"caption":0.1,"human_state":0.1,"human_mask":0.2,"mevis_mask":0.05,"weak_retail":0.15,"weak_robot:TOWARD_CAMERA":0.05,"weak_robot:AWAY_FROM_CAMERA":0.05,"weak_robot:LATERAL_ACROSS_VIEW":0.05,"weak_robot:STATIONARY":0.05,"weak_robot:UNCERTAIN":0.05}'
    ;;
  all)
    bash "$0" a
    bash "$0" control
    bash "$0" b
    bash "$0" c
    ;;
  *) echo 'Usage: train_recovery.sh [a|control|b|balanced|c|all]' >&2; exit 2 ;;
esac
