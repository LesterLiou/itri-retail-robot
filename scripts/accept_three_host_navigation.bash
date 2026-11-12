#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Keep this test independent of the interactive fleet on domains 85 and 86.
export NAV_DOMAIN_ID=105 FAKE_BASE_DOMAIN_ID=106 NAV_PEERS=''
compose=(docker compose --project-directory . -p itri-navigation-acceptance
         -f docker/compose.navigation.yaml --profile fake)
cleanup() {
  "${compose[@]}" down
}
trap cleanup EXIT
"${compose[@]}" up -d --build
entry=/opt/itri/navigation/entrypoint
remote() { "${compose[@]}" exec -T thor-nav "$entry" "$@"; }
local_drive() { "${compose[@]}" exec -T fake-drive "$entry" "$@"; }
reset() { local_drive ros2 service call /drive/reset_hold std_srvs/srv/Trigger '{}'; }
start_straight() {
  remote ros2 service call /mission/straight_2m std_srvs/srv/Trigger '{}'
  local_drive python3 /opt/itri/navigation/accept_local.py active
}
for scenario in map straight risk loss cancel conflict; do
  remote python3 /opt/itri/navigation/accept.py "$scenario"
  reset
done
start_straight
local_drive python3 /opt/itri/navigation/accept_local.py handoff
reset
start_straight
"${compose[@]}" restart fake-drive
local_drive python3 /opt/itri/navigation/accept_local.py restarted
start_straight
"${compose[@]}" stop thor-nav
local_drive python3 /opt/itri/navigation/accept_local.py disconnected
