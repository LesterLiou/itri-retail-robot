.DEFAULT_GOAL := help
COMPOSE := docker compose --project-directory . -f docker/compose.yaml
EDIT := $(COMPOSE) -f docker/compose.dev.yaml
VQA := $(COMPOSE) -f docker/compose.vqa.yaml
VQA_EDIT := $(VQA) -f docker/compose.dev.yaml
CAMERA := $(VQA) --profile sim --profile camera
export VIDEO

.PHONY: help build up shell dev dev-build dev-restart status logs stop vqa vqa-dev vqa-video vqa-build camera-list camera-logs camera-stop
help:
	@printf '%s\n' \
	  'make build        Build the pinned-source simulation image' \
	  'make up           Start baked simulation + RViz desktop (localhost:6080)' \
	  'make shell        Enter the running simulation container' \
	  'make dev          Start editable simulation + enter its shell' \
	  'make dev-build    Build the three integration packages in editable mode' \
	  'make dev-restart  Restart editable simulation to load the built overlay' \
	  'make camera-list Discover RealSense devices using a camera-only container' \
	  'make vqa          Start RealSense RGB + real VQA + RViz (fake robot only)' \
	  'make vqa-dev      Restart RealSense/VQA with writable overlay + enter shell' \
	  'make vqa-video VIDEO=/data/video/file.mp4  Run VQA with recorded video' \
	  'make vqa-build    Build the optional GPU inference image' \
	  'make camera-logs  Follow RealSense driver logs' \
	  'make camera-stop Stop the camera driver' \
	  'make status       Show project containers' \
	  'make logs         Follow simulation logs' \
	  'make stop         Stop this project simulation/dev/VQA services'

build:
	$(COMPOSE) build sim

up:
	$(CAMERA) stop cameras
	$(COMPOSE) up -d sim

shell:
	$(COMPOSE) exec sim bash

dev:
	$(CAMERA) stop cameras
	$(EDIT) up -d sim
	$(EDIT) exec sim bash

dev-build:
	$(EDIT) exec -T sim bash -ec 'test "$${RETAIL_BAKED_ONLY}" = 0 || { echo "Start editable mode with make dev first" >&2; exit 1; }; source /opt/itri/setup.bash; cd /workspace/ros2_ws; python3 /usr/bin/colcon build --symlink-install --packages-select retail_interfaces retail_core retail_bringup --event-handlers console_direct+'

dev-restart:
	@test -z "$$($(CAMERA) ps --status running -q cameras)" || { echo 'Camera is active; use make vqa-dev to restart camera and sim together' >&2; exit 1; }
	$(EDIT) exec -T sim bash -ec 'test "$${RETAIL_BAKED_ONLY}" = 0; test -f /workspace/ros2_ws/install/setup.bash'
	$(EDIT) restart sim

status:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100 sim

stop:
	$(CAMERA) stop cameras
	$(COMPOSE) stop sim dev vqa-worker

vqa-build:
	$(COMPOSE) -f docker/compose.vqa-build.yaml build vqa-worker

camera-list:
	$(CAMERA) run --rm --no-deps camera-probe

vqa:
	$(CAMERA) stop cameras
	$(COMPOSE) up -d --wait --wait-timeout 120 vqa-worker
	CAMERA_SOURCE=external VIDEO_PATH= $(VQA) up -d sim
	$(CAMERA) up -d --no-deps cameras

vqa-dev:
	$(CAMERA) stop cameras
	$(COMPOSE) up -d --wait --wait-timeout 120 vqa-worker
	CAMERA_SOURCE=external VIDEO_PATH= $(VQA_EDIT) up -d --force-recreate sim
	$(CAMERA) up -d --no-deps cameras
	$(VQA_EDIT) exec sim bash

vqa-video:
	@test -n "$${VIDEO}" || { echo 'Usage: make vqa-video VIDEO=/data/video/file.mp4' >&2; exit 1; }
	$(CAMERA) stop cameras
	$(COMPOSE) up -d --wait --wait-timeout 120 vqa-worker
	CAMERA_SOURCE=video VIDEO_PATH="$${VIDEO}" $(VQA) up -d sim

camera-logs:
	$(CAMERA) logs -f --tail=100 cameras

camera-stop:
	$(CAMERA) stop cameras
