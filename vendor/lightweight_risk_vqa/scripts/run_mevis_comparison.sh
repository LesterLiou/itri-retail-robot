#!/usr/bin/env bash
# Controlled addition of MeViS; shared init/updates and common validation selection.
set -euo pipefail
if [[ $# -lt 4 || $# -gt 6 ]]; then
  echo 'Usage: bash scripts/run_mevis_comparison.sh BASE_DATASET MEVIS_DATASET INIT_BUNDLE NEW_EXPERIMENT [EPOCHS=2] [DRAWS=1200]' >&2
  exit 2
fi
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
base="$1"; mevis="$2"; init="$3"; experiment="$4"
epochs="${5:-2}"; draws="${6:-1200}"
[[ ! -e "$experiment" ]] || { echo 'Choose a NEW experiment directory.' >&2; exit 2; }
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$experiment"
python3 -m lightweight_risk_vqa.mix_mevis_person --dataset "$base" --mevis "$mevis" \
  --output "$experiment/dataset" --weight .2
for arm in without_mevis with_mevis; do
  data="$base"
  [[ "$arm" != with_mevis ]] || data="$experiment/dataset"
  bash "$package_root/scripts/train_person_state.sh" "$data" "$init" \
    "$experiment/$arm" "$epochs" "$draws" train > "$experiment/$arm.train.log" 2>&1
  bash "$package_root/scripts/evaluate_person_state.sh" "$experiment/$arm/best" \
    "$base" "$experiment/evaluation/$arm" > "$experiment/$arm.evaluate.log" 2>&1
  for history in full last repeat; do
    python3 -m lightweight_risk_vqa.evaluate_grounded_masks \
      --checkpoint "$experiment/$arm/best" --manifest "$experiment/dataset/mevis_val.jsonl" \
      --output "$experiment/evaluation/$arm/mevis_$history" --device cuda:0 --history "$history"
  done
done
python3 -m lightweight_risk_vqa.summarize_person_state --manifest "$base/state_val.jsonl" \
  --prediction "without_mevis=$experiment/evaluation/without_mevis/state.full.jsonl" \
  --prediction "with_mevis=$experiment/evaluation/with_mevis/state.full.jsonl" \
  --output "$experiment/evaluation/state_comparison.json"
python3 -m lightweight_risk_vqa.summarize_mevis_comparison \
  --experiment "$experiment" --results "$experiment/evaluation" \
  --output "$experiment/evaluation/comparison.json"
