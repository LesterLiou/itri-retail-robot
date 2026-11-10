#!/usr/bin/env python3
"""Pause only the synthetic camera process and verify loss cannot clear motion."""
import os
from pathlib import Path
import signal
import time
import rclpy
from accept_sim import Acceptance


def main():
    rclpy.init()
    node=Acceptance()
    pid=None
    try:
        node.until(lambda:node.gate and node.gate.arms_stowed and node.gate.base_standstill)
        node.call('/mission/navigate')
        node.until(lambda:node.odom and abs(node.odom.twist.twist.linear.x)>0.02,timeout=20)
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                command=path.read_bytes().split(b'\0')
                if len(command)>1 and command[1].endswith(b'/retail_core/camera_source'):
                    pid=int(path.parent.name)
                    break
            except (OSError,ProcessLookupError):
                pass
        assert pid,'synthetic camera not found; refusing to signal any other process'
        os.kill(pid,signal.SIGSTOP)
        node.until(lambda:node.gate.hold and node.stopped(),timeout=5)
        os.kill(pid,signal.SIGCONT)
        pid=None
        node.until(lambda:node.risk and node.risk.valid,timeout=5)
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            rclpy.spin_once(node,timeout_sec=.05)
            assert node.gate.hold and not node.gate.enabled and node.stopped()
        print('PASS camera loss: stopped; restored stream cannot resume queued navigation',flush=True)
    finally:
        if pid: os.kill(pid,signal.SIGCONT)
        node.destroy_node()
        rclpy.try_shutdown()


if __name__=='__main__':
    main()

