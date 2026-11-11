#!/usr/bin/env bash
# Only the isolated compose.moveit.yaml fake robot; never launches physical hardware.
set -eo pipefail
source /opt/itri/setup.bash
test "${CONTROL_MODE}" = moveit
bash /opt/itri/run_sim.bash >/tmp/manual-launch.log 2>&1 &
manual_launch=$!
cleanup() { kill -TERM "$manual_launch" 2>/dev/null || true; wait "$manual_launch" 2>/dev/null || true; }
trap cleanup EXIT
python3 /workspace/scripts/check_robot_model.py
if [[ "${1:-normal}" == normal ]]; then
    ros2 run retail_core manual_control move --group right_arm --joints 0 0 0 0.3 0 0 0
    ros2 run retail_core manual_control move --group right_arm --joints 0 0 0 0 0 0 0
elif [[ "$1" == cancel ]]; then
    python3 /workspace/scripts/check_manual_cancel.py
elif [[ "$1" == loss ]]; then
    python3 /workspace/scripts/check_manual_cancel.py --loss
elif [[ "$1" == prepare ]]; then
    ros2 run retail_core manual_control prepare
    ros2 run retail_core manual_control move --group right_arm --joints 0 0 0 0.3 0 0 0
else
    exit 2
fi
