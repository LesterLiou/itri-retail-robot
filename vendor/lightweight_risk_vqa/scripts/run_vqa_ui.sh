#!/usr/bin/env bash
# Region-free robot VQA modes plus the retained embedded/worker baselines.
set -euo pipefail
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backend="${1:-}"
if [[ "$backend" != embedded && "$backend" != worker && "$backend" != vqa-only && "$backend" != yolo-assisted ]]; then
  echo 'Usage: bash scripts/run_vqa_ui.sh vqa-only|yolo-assisted|embedded|worker --checkpoint BUNDLE --video VIDEO [UI options]' >&2
  exit 2
fi
shift
options=()
if [[ "$backend" == vqa-only || "$backend" == yolo-assisted ]]; then
  options+=(--inference-mode "$backend" --vqa-backend embedded --max-new-tokens 192)
  if [[ "$backend" == yolo-assisted ]]; then
    options+=(--bbox-model "$package_root/../../../yolov8n.pt")
  fi
else
  query='the people'
  if [[ "$backend" == embedded ]]; then
    query="The target person is outlined in green in chronological frames ending at NOW. Predict that target's camera-relative motion one second after NOW. Return exactly one JSON object with keys motion, action, and explanation. motion must be one of TOWARD_CAMERA, AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, STATIONARY, UNCERTAIN."
  fi
  options+=(--inference-mode legacy --vqa-backend "$backend" --query "$query" --max-new-tokens 64)
fi
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
export QT_X11_NO_MITSHM="${QT_X11_NO_MITSHM:-1}"
export QT_XCB_GL_INTEGRATION="${QT_XCB_GL_INTEGRATION:-none}"
exec python3 -m lightweight_risk_vqa.light_foundation_live_ui \
  "${options[@]}" --segmentation-hz 2 --vqa-hz 1 "$@"
