#!/usr/bin/env bash
# One-off compose.moveit.yaml container only: GenericSystem, no physical devices.
set -eo pipefail
source /opt/itri/setup.bash
test "${CONTROL_MODE}" = moveit
test "${RVIZ_ENABLED:-0}" = 0
bash /opt/itri/run_sim.bash >/tmp/rviz-manual-launch.log 2>&1 &
controls_launch=$!
cleanup() { kill -TERM "$controls_launch" 2>/dev/null || true; wait "$controls_launch" 2>/dev/null || true; }
trap cleanup EXIT
python3 /workspace/scripts/check_robot_model.py
QT_QPA_PLATFORM=offscreen ros2 run retail_rviz_plugins controls_smoke --manual-backend
