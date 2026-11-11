#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/workspace}
export PYTHONPATH="$TASK_ROOT/ros2_ws/src/lightweight_risk_vqa:${PYTHONPATH:-}"
DATA_ROOT="${RECOVERY_DATA_ROOT:-$TASK_ROOT/datasets/robot_vqa_recovery_v1}"
MODEL_ROOT="${RECOVERY_MODEL_ROOT:-$TASK_ROOT/checkpoints/robot_vqa_recovery_v1}"
RESULT_ROOT="${RECOVERY_EVAL_ROOT:-$TASK_ROOT/results/robot_vqa_recovery_v1/evaluation}"
bundle="$MODEL_ROOT/b_balanced/best"
for task in count presence; do
  python3 -m lightweight_risk_vqa.audit_visual_grounding --checkpoint "$bundle" \
    --manifest "$DATA_ROOT/grounding/${task}_val.jsonl" --samples 160 --conditions real \
    --max-new-tokens 48 --output "$RESULT_ROOT/b_balanced_${task}.json" \
    > "$RESULT_ROOT/b_balanced_${task}.log" 2>&1
done
python3 -m lightweight_risk_vqa.audit_visual_grounding --checkpoint "$bundle" \
  --manifest "$DATA_ROOT/state_ablation_val.jsonl" --samples 10000 \
  --conditions real,shuffle,repeat,last --decoders free --max-new-tokens 96 \
  --output "$RESULT_ROOT/b_balanced_state.json" > "$RESULT_ROOT/b_balanced_state.log" 2>&1
python3 -m lightweight_risk_vqa.evaluate_grounded_masks --checkpoint "$bundle" \
  --manifest "$TASK_ROOT/datasets/light_foundation_vqa/person_state_expanded_v1/segmentation_val.jsonl" \
  --output "$RESULT_ROOT/b_balanced_masks" > "$RESULT_ROOT/b_balanced_masks.log" 2>&1
python3 -m lightweight_risk_vqa.evaluate_grounded_masks --checkpoint "$bundle" \
  --manifest "$TASK_ROOT/datasets/light_foundation_vqa/person_state_mevis_v2/mevis_val.jsonl" \
  --output "$RESULT_ROOT/b_balanced_mevis_masks" > "$RESULT_ROOT/b_balanced_mevis_masks.log" 2>&1
python3 -m lightweight_risk_vqa.render_recovery_report --results "$RESULT_ROOT" --data "$DATA_ROOT"
