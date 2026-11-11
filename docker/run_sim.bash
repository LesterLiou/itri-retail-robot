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
launch_args+=("scene_backend:=${SCENE_BACKEND:-known_fixture}")
if [[ -n "${NAV_MAP_FILE:-}" ]]; then launch_args+=("map_yaml:=$NAV_MAP_FILE"); fi
if [[ -n "${SIM_WORLD_FILE:-}" ]]; then launch_args+=("world_file:=$SIM_WORLD_FILE"); fi
if [[ -n "${VIDEO_PATH:-}" ]]; then launch_args+=("video_path:=$VIDEO_PATH"); fi
if [[ "${CONTROL_MODE:-mission}" == moveit ]]; then
    launch_args+=("manual:=true" "navigation:=false")
elif [[ "${CONTROL_MODE:-mission}" != mission ]]; then
    echo 'Unknown CONTROL_MODE' >&2
    exit 1
fi
if [[ -n "${RECONSTRUCTION_URL:-}" ]]; then launch_args+=("reconstruction_url:=$RECONSTRUCTION_URL"); fi
exec ros2 launch retail_bringup sim.launch.py "${launch_args[@]}"

