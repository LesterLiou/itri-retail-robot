#!/usr/bin/env bash
set -eo pipefail
source /opt/itri/setup.bash
children=()
cleanup() { for child in "${children[@]}"; do kill -TERM "$child" 2>/dev/null || true; done; }
trap cleanup EXIT
ros2 launch retail_bringup perception.launch.py vqa_backend:=http hand_backend:=mediapipe input_source:=video >/tmp/live-perception.log 2>&1 &
children+=("$!")
ros2 run retail_core camera_source --ros-args -p video_path:="${VIDEO_PATH:-/data/video/0610_take3n4.mp4}" >/tmp/video-source.log 2>&1 &
children+=("$!")
python3 /workspace/scripts/accept_sim.py live-vqa
cat /tmp/live-perception.log
