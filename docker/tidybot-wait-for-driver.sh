#!/usr/bin/env bash
set -euo pipefail

if [[ "${ROS_DOMAIN_ID:-}" != 0 || "$#" != 1 || ( "$1" != true && "$1" != false ) ]]; then
    echo 'TidyBot standby requires local domain 0 and an explicit map setting' >&2
    exit 1
fi

cd /opt/itri/navigation
echo 'Waiting for the remapped TidyBot driver and exclusive control endpoints.'
until python3 check_hardware_graph.py; do
    sleep 2
done

# The existing entrypoint rechecks the graph before creating any publishers.
# After handoff, the gate owns fault handling and will require fresh local arming.
exec ./hardware-drive "$1"
