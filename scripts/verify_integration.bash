#!/usr/bin/env bash
# Host-side, isolated simulation acceptance. Never loads a physical driver.
set -euo pipefail
cd "$(dirname "$0")/.."
verification_dir="${1:-/tmp/retail-integration-verification}"
mkdir -p "$verification_dir"
compose=(docker compose --project-directory . -f docker/compose.yaml)
"${compose[@]}" --profile sim --profile camera stop cameras
fresh_sim() {
    VQA_BACKEND=fixture HAND_BACKEND=fixture CAMERA_SOURCE=synthetic \
    VIDEO_PATH="" RECONSTRUCTION_URL="" "${compose[@]}" up -d --force-recreate sim
}
check() {
    local check_name="$1"
    shift
    "${compose[@]}" exec -T sim bash -ec "source /opt/itri/setup.bash; $*" > "$verification_dir/$check_name.log" 2>&1
    cat "$verification_dir/$check_name.log"
}
fresh_sim
check packages "python3 -m pytest -p no:cacheprovider -q /workspace/ros2_ws/src/retail_core/test /workspace/external/tidybot_ros2_control/tests/test_docking_core.py"
check full "python3 /workspace/scripts/accept_sim.py full"
check faults "python3 /workspace/scripts/accept_sim.py faults"
fresh_sim
check restart "python3 /workspace/scripts/accept_sim.py restart"
check arm-cancel "python3 /workspace/scripts/accept_arm_cancel.py"
fresh_sim
check camera-loss "python3 /workspace/scripts/accept_camera_loss.py"
"${compose[@]}" run --rm --no-deps sim bash /workspace/scripts/accept_invalid_scene.bash > "$verification_dir/invalid-scene.log" 2>&1
cat "$verification_dir/invalid-scene.log"
fresh_sim
check final-idle "python3 /workspace/scripts/accept_sim.py restart"
echo "PASS: isolated integration acceptance; logs in $verification_dir"

