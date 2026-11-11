ARG ROS_BASE_IMAGE=ros:humble-ros-base-jammy@sha256:d9bcf2e6f72df127fdee07048c2e87b1b4b9a59a74af0592f4489fc2844aa855
FROM ${ROS_BASE_IMAGE} AS common
WORKDIR /workspace

FROM common AS smoke
COPY docker/ros_smoke.py /opt/itri/ros_smoke.py
CMD ["python3", "/opt/itri/ros_smoke.py"]

FROM common AS dev
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build git curl ca-certificates less nano \
    ripgrep tmux gdb pkg-config \
    python3-colcon-common-extensions python3-vcstool python3-rosdep \
    python3-pip python3-pytest python3-yaml python3-numpy python3-opencv \
    ros-humble-rmw-cyclonedds-cpp ros-humble-xacro \
    ros-humble-ros2-control ros-humble-ros2-controllers \
    ros-humble-moveit ros-humble-navigation2 ros-humble-nav2-bringup \
    ros-humble-cv-bridge ros-humble-image-transport \
    ros-humble-vision-msgs ros-humble-tf2-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-realsense2-camera ros-humble-realsense2-description \
    ros-humble-image-transport-plugins ros-humble-joint-state-publisher \
    ros-humble-joint-state-publisher-gui \
    python3-pyqt5 python3-pil xauth x11-utils \
    imagemagick libgl1-mesa-dri mesa-utils \
    && rm -rf /var/lib/apt/lists/*

ARG USER_UID=1000
ARG USER_GID=1000
RUN if ! getent group "${USER_GID}" >/dev/null; then groupadd --gid "${USER_GID}" developer; fi \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash developer \
    && mkdir -p /workspace/ros2_ws/src \
    && chown -R "${USER_UID}:${USER_GID}" /workspace \
    && printf '\nsource /opt/itri/setup.bash\n' >> /home/developer/.bashrc
COPY docker/setup.bash /opt/itri/setup.bash
COPY docker/entrypoint.bash /opt/itri/entrypoint.bash
COPY docker/dev_check.bash /opt/itri/dev_check.bash
COPY docker/ros_smoke.py /opt/itri/ros_smoke.py
USER developer
WORKDIR /workspace/ros2_ws
ENTRYPOINT ["/bin/bash", "/opt/itri/entrypoint.bash"]
CMD ["bash"]

FROM dev AS model-tools
USER root
RUN python3 -m pip install --no-cache-dir usd-core==25.11
USER developer

FROM dev AS sim
USER root
COPY docker/requirements-perception.txt /opt/itri/requirements-perception.txt
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/perception \
    && /opt/perception/bin/pip install --no-cache-dir -r /opt/itri/requirements-perception.txt
ENV PATH=/opt/perception/bin:$PATH
COPY external/openarm_dev/ros2_ws/src/openarm_can /opt/retail_ws/src/vendor/openarm_can
COPY external/openarm_dev/ros2_ws/src/openarm_description /opt/retail_ws/src/vendor/openarm_description
COPY external/openarm_dev/ros2_ws/src/openarm_ros2/openarm_hardware /opt/retail_ws/src/vendor/openarm_hardware
COPY external/openarm_dev/ros2_ws/src/openarm_ros2/openarm_bringup /opt/retail_ws/src/vendor/openarm_bringup
COPY external/openarm_dev/ros2_ws/src/openarm_ros2/openarm_bimanual_moveit_config /opt/retail_ws/src/vendor/openarm_bimanual_moveit_config
COPY external/social-safety-decision/realtime_safety /opt/retail_upstream/safety/realtime_safety
COPY vendor/lightweight_risk_vqa /opt/retail_upstream/vqa
COPY external/tidybot_ros2_control /opt/retail_ws/src/vendor/tidybot_ros2_control
ENV PYTHONPATH=/opt/retail_upstream/safety:/opt/retail_upstream/vqa
COPY ros2_ws/src /opt/retail_ws/src/integration
WORKDIR /opt/retail_ws
RUN source /opt/ros/humble/setup.bash \
    && python3 /usr/bin/colcon build --packages-select openarm_can --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && source install/setup.bash \
    && python3 /usr/bin/colcon build --executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && chmod -R a+rX /opt/retail_ws
COPY docker/run_sim.bash /opt/itri/run_sim.bash
COPY scripts/open_rviz.py /opt/itri/open_rviz.py
COPY docker/cyclonedds-local.xml /opt/itri/cyclonedds-local.xml
USER developer
CMD ["bash", "/opt/itri/run_sim.bash"]
