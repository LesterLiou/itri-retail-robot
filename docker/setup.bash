# Source in entrypoint and each interactive/exec shell.
source /opt/ros/humble/setup.bash
if [[ -f /opt/retail_ws/install/setup.bash ]]; then
    source /opt/retail_ws/install/setup.bash
fi
if [[ "${RETAIL_BAKED_ONLY:-0}" != 1 && -f /workspace/ros2_ws/install/setup.bash ]]; then
    source /workspace/ros2_ws/install/setup.bash
fi
