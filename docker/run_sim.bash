#!/usr/bin/env bash
set -eo pipefail
source /opt/itri/setup.bash
if [[ "${RVIZ_ENABLED:-0}" == 1 ]]; then
    if [[ -z "${DISPLAY:-}" ]] || ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        echo 'Cannot connect to the host display. Run make gui-setup from your Ubuntu desktop terminal, then recreate sim.' >&2
        exit 1
    fi
    rviz=true
else
    rviz=false
fi
launch_args=("rviz:=$rviz" "vqa_backend:=${VQA_BACKEND:-fixture}" \
    "vqa_mode:=${VQA_MODE:-vqa-only}" \
    "hand_backend:=${HAND_BACKEND:-fixture}" "camera_source:=${CAMERA_SOURCE:-synthetic}" \
    "cameras:=${VQA_CAMERAS:-chest}")
if [[ -n "${VIDEO_PATH:-}" ]]; then launch_args+=("video_path:=$VIDEO_PATH"); fi
if [[ -n "${RECONSTRUCTION_URL:-}" ]]; then launch_args+=("reconstruction_url:=$RECONSTRUCTION_URL"); fi
exec ros2 launch retail_bringup sim.launch.py "${launch_args[@]}"

