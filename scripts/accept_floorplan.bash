#!/usr/bin/env bash
set -eo pipefail
source /opt/itri/setup.bash
test -n "${SIM_WORLD_FILE}"
test "${RVIZ_ENABLED:-0}" = 0
python3 -m pytest -p no:cacheprovider -q /workspace/ros2_ws/src/retail_core/test
bash /opt/itri/run_sim.bash >/tmp/floorplan-launch.log 2>&1 &
floorplan_launch=$!
cleanup() { kill -TERM "$floorplan_launch" 2>/dev/null || true; wait "$floorplan_launch" 2>/dev/null || true; }
trap cleanup EXIT
if ! python3 /workspace/scripts/check_floorplan_nav.py; then
    tail -n 100 /tmp/floorplan-launch.log
    exit 1
fi
