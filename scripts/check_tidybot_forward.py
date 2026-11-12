"""Exercise the forward client with simulated services in a network-isolated container."""

import subprocess
import sys
import time
from pathlib import Path

import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from retail_interfaces.msg import DriveState
from std_srvs.srv import SetBool, Trigger

if {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}:
    raise SystemExit('Run this test only inside Docker with --network none')

local, remote = Context(), Context()
rclpy.init(args=[], context=local, domain_id=0)
rclpy.init(args=[], context=remote, domain_id=84)
base, thor = Node('fixture_gate', context=local), Node('fixture_thor', context=remote)
exe0, exe84 = SingleThreadedExecutor(context=local), SingleThreadedExecutor(context=remote)
exe0.add_node(base)
exe84.add_node(thor)
publisher = base.create_publisher(DriveState, '/drive/state', 1)
calls = []
accept = True
started = None

def arm(req, res):
    calls.append(('arm', req.data))
    res.success = accept or not req.data
    res.message = 'accepted' if res.success else 'fixture interlock missing'
    return res

def straight(req, res):
    global started
    assert calls[-1] == ('arm', True)
    calls.append(('start', True))
    started = time.monotonic()
    res.success, res.message = True, 'fixture-task'
    return res

base.create_service(SetBool, '/drive/arm_auto', arm)
thor.create_service(Trigger, '/mission/straight_2m', straight)

def run():
    with subprocess.Popen([sys.executable, '/opt/itri/navigation/forward_2m.py']) as client:
        deadline = time.monotonic() + 15
        while client.poll() is None and time.monotonic() < deadline:
            state = DriveState(hardware=True, boot_id='fixture-boot', standstill=True, phase='IDLE')
            state.header.stamp = base.get_clock().now().to_msg()
            if started is not None:
                state.task_id = 'fixture-task'
                state.phase = 'ACTIVE'
                state.standstill, state.enabled = False, True
                if time.monotonic() - started > 0.8:
                    state.phase, state.distance = 'DONE', 2.0
                    state.standstill, state.enabled = True, False
            publisher.publish(state)
            exe0.spin_once(timeout_sec=0.03)
            exe84.spin_once(timeout_sec=0.03)
        if client.poll() is None:
            client.kill()
        return client.wait()

assert run() == 0
assert calls == [('arm', True), ('start', True), ('arm', False)], calls
accept, started = False, None
calls.clear()
assert run() == 1
assert calls == [('arm', True)], calls
print('PASS: program arms before goal, verifies DONE and standstill, disarms; refused interlock submits no task')
