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
