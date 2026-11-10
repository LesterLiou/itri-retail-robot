#!/usr/bin/env bash
set -eo pipefail
source /opt/itri/setup.bash
ros2 launch retail_bringup sim.launch.py scene_backend:=external navigation:=false >/tmp/no-scene.log 2>&1 &
stack_pid=$!
trap 'kill -TERM "$stack_pid" 2>/dev/null || true' EXIT
python3 /workspace/scripts/accept_sim.py invalid-scene
