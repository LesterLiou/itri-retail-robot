#!/usr/bin/env bash
# Isolated fixture-only graph. Never use against an external robot or live camera.
set -eo pipefail
source /opt/itri/setup.bash
test "${CAMERA_SOURCE:-synthetic}" = synthetic
test "${VQA_BACKEND:-fixture}" = fixture
test "${CONTROL_MODE:-mission}" = mission
python3 -m pytest -p no:cacheprovider -q /workspace/ros2_ws/src/retail_core/test
bash /opt/itri/run_sim.bash >/tmp/navigation-launch.log 2>&1 &
navigation_launch=$!
cleanup() { kill -TERM "$navigation_launch" 2>/dev/null || true; wait "$navigation_launch" 2>/dev/null || true; }
trap cleanup EXIT
python3 /workspace/scripts/check_nav_goal.py
