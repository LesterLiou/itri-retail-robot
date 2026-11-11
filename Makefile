.DEFAULT_GOAL := help
COMPOSE := docker compose --project-directory . -f docker/compose.yaml
GUI ?= 1
ifneq ($(GUI),0)
COMPOSE += -f docker/compose.local.yaml
GUI_PREREQ := gui-setup
endif
EDIT := $(COMPOSE) -f docker/compose.dev.yaml
VQA := $(COMPOSE) -f docker/compose.vqa.yaml
VQA_EDIT := $(VQA) -f docker/compose.dev.yaml
UI := $(VQA) -f docker/compose.ui.yaml
CAMERA := $(VQA) --profile sim --profile camera
VIDEO ?= /data/video/0610_take3n4.mp4
export VIDEO

.PHONY: help build up shell dev dev-build dev-restart status logs stop vqa vqa-dev vqa-video vqa-build camera-list camera-logs camera-stop gui-setup
help:
	@printf '%s\n' \
	  'make build        Build the pinned-source simulation image' \
	  'make up           Start simulation + native RViz window on your desktop' \
	  'make gui-setup    Prepare current desktop Xauthority (never prints cookies)' \
	  'make up GUI=0     Start headless simulation without a display server' \
	  'make shell        Enter the running simulation container' \
	  'make rviz         Reopen RViz in the existing container; no robot restart' \
	  'make dev          Start editable simulation + enter its shell' \
	  'make dev-build    Build the five integration packages in editable mode' \
	  'make dev-restart  Restart editable simulation to load the built overlay' \
	  'make camera-list Discover RealSense devices using a camera-only container' \
	  'make vqa          Start RealSense RGB + real VQA + RViz (fake robot only)' \
	  'make vqa-dev      Restart RealSense/VQA with writable overlay + enter shell' \
	  'make vqa-video VIDEO=/data/video/file.mp4  Run VQA with recorded video' \
	  'make vqa-build    Build the optional GPU inference image' \
	  'make camera-logs  Follow RealSense driver logs' \
	  'make camera-stop Stop the camera driver' \
	  'make vqa-ui-build Build the original YOLO-assisted backend dependencies' \
	  'make vqa-ui       RealSense + original monitor layout as a ROS 2 node' \
	  'make vqa-ui-video VIDEO=/data/video/file.mp4  Video + ROS 2 VQA monitor' \
	  'make moveit       Exclusive fake-hardware MoveIt control and RViz panel' \
	  'make model-convert Rebuild the chassis URDF/STL from the supplied USD' \
	  'make nav-vqa      RealSense + VQA UI + Nav2 on the RViz test map' \
	  'make nav-demo     CPU Nav2 practice with synthetic perception (no camera/model)' \
	  'make floorplan    Use the dimensioned 6.6 x 4.4 m map with matched FAKE scan' \
	  'make floorplan-vqa Same map plus real camera/VQA; robot remains FAKE' \
	  'make floorplan-generate Regenerate the dimensioned map inside Docker' \
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
	$(EDIT) exec -T sim bash -ec 'test "$${RETAIL_BAKED_ONLY}" = 0 || { echo "Start editable mode with make dev first" >&2; exit 1; }; source /opt/itri/setup.bash; cd /workspace/ros2_ws; python3 /usr/bin/colcon build --symlink-install --packages-select retail_description retail_interfaces retail_core retail_rviz_plugins retail_bringup --event-handlers console_direct+'

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
	CAMERA_SOURCE=video VIDEO_PATH="$${VIDEO}" $(VQA) up -d --force-recreate sim

camera-logs:
	$(CAMERA) logs -f --tail=100 cameras

camera-stop:
	$(CAMERA) stop cameras

gui-setup:
	python3 scripts/prepare_gui.py

up dev dev-restart vqa vqa-dev vqa-video: $(GUI_PREREQ)

.PHONY: model-convert vqa-ui-build vqa-ui vqa-ui-video moveit
model-convert:
	$(COMPOSE) build model-tools
	$(COMPOSE) run --rm --no-deps model-tools

vqa-ui-build:
	$(UI) build vqa-worker

vqa-ui: $(GUI_PREREQ)
	$(CAMERA) stop cameras
	$(UI) up -d --wait --wait-timeout 120 vqa-worker
	CAMERA_SOURCE=external VIDEO_PATH= $(UI) up -d sim
	$(UI) --profile sim --profile camera up -d --no-deps cameras
	$(MAKE) rviz GUI=$(GUI)
	@printf '%s\n' 'Opening the native VQA monitor. This terminal stays attached until the window closes; use another terminal for make shell.'
	$(UI) exec sim bash -ec 'source /opt/itri/setup.bash; exec ros2 run retail_core vqa_ui'

vqa-ui-video: $(GUI_PREREQ)
	$(CAMERA) stop cameras
	$(UI) up -d --wait --wait-timeout 120 vqa-worker
	CAMERA_SOURCE=video VIDEO_PATH="$${VIDEO}" $(UI) up -d --force-recreate sim
	@printf '%s\n' 'Opening the native VQA monitor. This terminal stays attached until the window closes; use another terminal for make shell.'
	$(UI) exec sim bash -ec 'source /opt/itri/setup.bash; exec ros2 run retail_core vqa_ui'

moveit: $(GUI_PREREQ)
	$(CAMERA) stop cameras
	$(COMPOSE) -f docker/compose.moveit.yaml up -d --force-recreate sim

.PHONY: nav-vqa
nav-vqa: vqa-ui

.PHONY: nav-demo
nav-demo: up

.PHONY: floorplan floorplan-vqa floorplan-generate
floorplan-generate:
	docker compose --project-directory . -f docker/compose.yaml run --rm --no-deps dev python3 /workspace/scripts/generate_floorplan.py

floorplan: $(GUI_PREREQ)
	$(CAMERA) stop cameras
	VQA_BACKEND=fixture HAND_BACKEND=fixture CAMERA_SOURCE=synthetic VIDEO_PATH= $(COMPOSE) -f docker/compose.floorplan.yaml up -d --force-recreate sim

floorplan-vqa: $(GUI_PREREQ)
	$(CAMERA) stop cameras
	$(UI) up -d --wait --wait-timeout 120 vqa-worker
	CAMERA_SOURCE=external VIDEO_PATH= $(UI) -f docker/compose.floorplan.yaml up -d --force-recreate sim
	$(UI) --profile sim --profile camera up -d --no-deps cameras
	$(UI) exec sim bash -ec 'source /opt/itri/setup.bash; exec ros2 run retail_core vqa_ui'

.PHONY: rviz
rviz: $(GUI_PREREQ)
	$(COMPOSE) exec -d sim bash -ec 'source /opt/itri/setup.bash; exec python3 /opt/itri/open_rviz.py >>/tmp/retail-rviz.log 2>&1'
	@printf '%s\n' 'RViz requested on the local display. Check: docker compose exec sim tail -n 30 /tmp/retail-rviz.log'
