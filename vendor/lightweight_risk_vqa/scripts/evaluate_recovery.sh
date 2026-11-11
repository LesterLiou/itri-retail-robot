#!/usr/bin/env bash
# Run serially on an idle GPU. Full masks/history generation are development validation.
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/workspace}
export PYTHONPATH="$TASK_ROOT/ros2_ws/src/lightweight_risk_vqa:${PYTHONPATH:-}"
DATA_ROOT="${RECOVERY_DATA_ROOT:-$TASK_ROOT/datasets/robot_vqa_recovery_v1}"
MODEL_ROOT="${RECOVERY_MODEL_ROOT:-$TASK_ROOT/checkpoints/robot_vqa_recovery_v1}"
RESULT_ROOT="${RECOVERY_EVAL_ROOT:-$TASK_ROOT/results/robot_vqa_recovery_v1/evaluation}"
mkdir -p "$RESULT_ROOT"
# Opt-in resume reuses only complete JSON newer than the model and source manifest.
audit() {
  local -a audit_args=("$@")
  local audit_output='' audit_bundle='' audit_manifest='' audit_index
  for ((audit_index=0; audit_index<${#audit_args[@]}-1; audit_index++)); do
    case "${audit_args[audit_index]}" in
      --output) audit_output=${audit_args[audit_index+1]} ;;
      --checkpoint) audit_bundle=${audit_args[audit_index+1]} ;;
      --manifest) audit_manifest=${audit_args[audit_index+1]} ;;
    esac
  done
  if [[ "${RECOVERY_RESUME:-0}" == 1 ]] && python3 - "$audit_output" "$audit_bundle" "$audit_manifest" <<'PY'
import json,sys
from pathlib import Path
try:
    output,bundle,manifest=map(Path,sys.argv[1:])
    data=json.loads(output.read_text())
    good=bool(data.get('summary')) and data.get('checkpoint')==str(bundle)
    good=good and output.stat().st_mtime>=max((bundle/'model.pt').stat().st_mtime,(bundle/'manifest.json').stat().st_mtime,manifest.stat().st_mtime)
except (OSError,ValueError):
    good=False
sys.exit(0 if good else 1)
PY
  then
    echo "Reused complete JSON: $audit_output"
    return
  fi
  python3 -m lightweight_risk_vqa.audit_visual_grounding "$@"
}
for variant in baseline a_lora a_last_layer b_temporal c_policy; do
  bundle="$MODEL_ROOT/$variant/best"
  if [[ "$variant" == baseline ]]; then
    bundle="$TASK_ROOT/checkpoints/person_state_mevis_ablation_v1/with_mevis/best"
  fi
  audit \
    --checkpoint "$bundle" --manifest "$DATA_ROOT/grounding/count_val.jsonl" \
    --samples 160 --conditions real --max-new-tokens 48 --output "$RESULT_ROOT/${variant}_count.json" \
    > "$RESULT_ROOT/${variant}_count.log" 2>&1
  audit \
    --checkpoint "$bundle" --manifest "$DATA_ROOT/grounding/presence_val.jsonl" \
    --samples 160 --conditions real --max-new-tokens 32 --output "$RESULT_ROOT/${variant}_presence.json" \
    > "$RESULT_ROOT/${variant}_presence.log" 2>&1
  audit \
    --checkpoint "$bundle" --manifest "$DATA_ROOT/grounding/count_val.jsonl" \
    --samples 24 --conditions blank,mismatch --max-new-tokens 48 --output "$RESULT_ROOT/${variant}_interventions.json" \
    > "$RESULT_ROOT/${variant}_interventions.log" 2>&1
  audit \
    --checkpoint "$bundle" --manifest "$DATA_ROOT/grounding/caption_val.jsonl" \
    --samples 12 --conditions real --decoders free --max-new-tokens 48 --output "$RESULT_ROOT/${variant}_caption.json" \
    > "$RESULT_ROOT/${variant}_caption.log" 2>&1
done
for variant in baseline b_temporal c_policy; do
  bundle="$MODEL_ROOT/$variant/best"
  if [[ "$variant" == baseline ]]; then
    bundle="$TASK_ROOT/checkpoints/person_state_mevis_ablation_v1/with_mevis/best"
  fi
  audit \
    --checkpoint "$bundle" --manifest "$DATA_ROOT/causal_motion/val.jsonl" \
    --samples 10000 --conditions real --decoders free,robot --max-new-tokens 192 \
    --output "$RESULT_ROOT/${variant}_robot.json" > "$RESULT_ROOT/${variant}_robot.log" 2>&1
  audit \
    --checkpoint "$bundle" --manifest "$DATA_ROOT/state_ablation_val.jsonl" \
    --samples 10000 --conditions real,shuffle,repeat,last --decoders free --max-new-tokens 96 \
    --output "$RESULT_ROOT/${variant}_state.json" > "$RESULT_ROOT/${variant}_state.log" 2>&1
  python3 -m lightweight_risk_vqa.evaluate_grounded_masks \
    --checkpoint "$bundle" --manifest "$TASK_ROOT/datasets/light_foundation_vqa/person_state_expanded_v1/segmentation_val.jsonl" \
    --output "$RESULT_ROOT/${variant}_masks" > "$RESULT_ROOT/${variant}_masks.log" 2>&1
  python3 -m lightweight_risk_vqa.evaluate_grounded_masks \
    --checkpoint "$bundle" --manifest "$TASK_ROOT/datasets/light_foundation_vqa/person_state_mevis_v2/mevis_val.jsonl" \
    --output "$RESULT_ROOT/${variant}_mevis_masks" > "$RESULT_ROOT/${variant}_mevis_masks.log" 2>&1
done
python3 -m lightweight_risk_vqa.evaluate_fast_motion \
  --checkpoint "$MODEL_ROOT/c_policy/best" --manifest "$DATA_ROOT/causal_motion/val.jsonl" \
  --output "$RESULT_ROOT/fast_motion.json" > "$RESULT_ROOT/fast_motion.log" 2>&1
python3 -m lightweight_risk_vqa.audit_streaming_memory \
  --checkpoint "$MODEL_ROOT/c_policy/best" --video "$TASK_ROOT/example/video/0610_take3n4.mp4" \
  --output "$RESULT_ROOT/streaming_memory.json" > "$RESULT_ROOT/streaming_memory.log" 2>&1
python3 -m lightweight_risk_vqa.render_recovery_report \
  --results "$RESULT_ROOT" --data "$DATA_ROOT" > "$RESULT_ROOT/render.log" 2>&1
