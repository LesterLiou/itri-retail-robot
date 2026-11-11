#!/usr/bin/env python3
"""Cancel only a verified isolated manual fake-hardware MoveIt session."""
import signal
import argparse
import subprocess
import sys
import time
import rclpy
from std_msgs.msg import String
from retail_core.manual_control import Control

parser=argparse.ArgumentParser();parser.add_argument('--loss',action='store_true');args=parser.parse_args()
rclpy.init();node=Control();node.check_profile()
state=['']
node.create_subscription(String,'/arm/gate_state',lambda m:state.__setitem__(0,m.data),10)
process=subprocess.Popen([sys.executable,'-c','from retail_core.manual_control import main;raise SystemExit(main())',
    'move','--group','right_arm','--joints','0','0','0','0.8','0','0','0'],
    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
try:
    node.until(lambda:'active=True' in state[0],timeout=15)
    try:node.set_lease(True)
    except RuntimeError as exc:assert 'Lease refused' in str(exc),str(exc)
    else:raise AssertionError('Conflicting manual owner was granted a lease')
    print('PASS: conflicting lease rejected without cancelling the current owner')
    process.send_signal(signal.SIGKILL if args.loss else signal.SIGINT)
    node.until(lambda:process.poll() is not None,timeout=15)
    output=process.communicate(timeout=2)[0]
    assert process.returncode!=0,output
    node.until(lambda:'hold=True' in state[0] and 'active=False' in state[0] and node.stopped())
    previous=dict(zip(node.joints.name,node.joints.position))
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.05)
        assert node.gate and not node.gate.enabled
        assert all(abs(previous[n]-p)<0.005 for n,p in zip(node.joints.name,node.joints.position))
    print('PASS: manual MoveIt '+('owner loss' if args.loss else 'cancellation')+' verified by joint standstill; HOLD remains; no queued goal replay')
    print(output)
finally:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=15)
    node.destroy_node();rclpy.shutdown()
