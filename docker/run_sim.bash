#!/usr/bin/env bash
set -eo pipefail
source /opt/itri/setup.bash
children=()
cleanup() {
    trap - EXIT TERM INT
    for child in "${children[@]}"; do kill -TERM "$child" 2>/dev/null || true; done
}
trap cleanup EXIT TERM INT
if [[ "${RVIZ_BROWSER:-1}" == 1 ]]; then
    Xvfb :99 -screen 0 1600x1000x24 -ac -nolisten tcp >/tmp/xvfb.log 2>&1 &
    children+=("$!")
    export DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 QT_X11_NO_MITSHM=1
    for attempt in $(seq 1 50); do
        if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
        sleep 0.1
    done
    fluxbox >/tmp/fluxbox.log 2>&1 &
    children+=("$!")
    x11vnc -display :99 -forever -shared -nopw -listen 127.0.0.1 -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
    children+=("$!")
    websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &
    children+=("$!")
    rviz=true
else
    rviz=false
fi
launch_args=("rviz:=$rviz" "vqa_backend:=${VQA_BACKEND:-fixture}" \
    "hand_backend:=${HAND_BACKEND:-fixture}" "camera_source:=${CAMERA_SOURCE:-synthetic}" \
    "cameras:=${VQA_CAMERAS:-chest}")
if [[ -n "${VIDEO_PATH:-}" ]]; then launch_args+=("video_path:=$VIDEO_PATH"); fi
if [[ -n "${RECONSTRUCTION_URL:-}" ]]; then launch_args+=("reconstruction_url:=$RECONSTRUCTION_URL"); fi
ros2 launch retail_bringup sim.launch.py "${launch_args[@]}" &
children+=("$!")
wait "${children[-1]}"

