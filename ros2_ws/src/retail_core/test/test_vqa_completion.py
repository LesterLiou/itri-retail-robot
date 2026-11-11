"""Completion notification must preserve capture-time expiry, with no HTTP/GPU."""
from concurrent.futures import Future
import time
import numpy as np
import pytest
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from retail_interfaces.msg import RiskAdvisory
from retail_core.vqa import VQAAdapter
from retail_core.common import seconds


@pytest.mark.parametrize('capture_age,expected',[(.4,'CONTINUE'),(3.0,'DATA_LOSS')])
def test_completed_inference_wakes_executor_without_periodic_timer(capture_age,expected):
    rclpy.init();adapter=VQAAdapter();observer=Node('observe_completion')
    executor=SingleThreadedExecutor();executor.add_node(adapter);executor.add_node(observer)
    received=[]
    observer.create_subscription(RiskAdvisory,'/perception/vqa/chest',received.append,10)
    for timer in adapter.timers:timer.cancel()
    # Let DDS discover the actual publisher before triggering the result.
    deadline=time.monotonic()+2
    while adapter.outputs['chest'].get_subscription_count()==0 and time.monotonic()<deadline:executor.spin_once(timeout_sec=.02)
    image=adapter.bridge.cv2_to_imgmsg(np.zeros((8,8,3),dtype=np.uint8),'bgr8')
    stamp=adapter.get_clock().now().nanoseconds-int(capture_age*1e9)
    image.header.stamp.sec,image.header.stamp.nanosec=divmod(stamp,1_000_000_000)
    adapter.latest['chest']=image;adapter.processed['chest']=seconds(image.header.stamp)
    future=Future();adapter.pending=('chest',image,future)
    future.add_done_callback(adapter.inference_ready)
    future.set_result(dict(action='CONTINUE',valid=True,worker_id='mock'))
    try:
        deadline=time.monotonic()+2
        while not received and time.monotonic()<deadline:executor.spin_once(timeout_sec=.02)
        assert received,'No timer is running: only the completion guard can publish'
        assert received[0].action==expected
        assert received[0].header.stamp==image.header.stamp
        assert received[0].valid_for.sec==2
        assert received[0].valid is (expected=='CONTINUE')
    finally:
        executor.shutdown();adapter.destroy_node();observer.destroy_node();rclpy.shutdown()
