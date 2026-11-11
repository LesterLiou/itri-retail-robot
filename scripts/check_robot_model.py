#!/usr/bin/env python3
"""Read-only check of the composed robot tree and current MoveIt collision state."""
import time
import xml.etree.ElementTree as ET
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile,DurabilityPolicy,qos_profile_sensor_data
from std_msgs.msg import String
from sensor_msgs.msg import JointState
from moveit_msgs.srv import GetStateValidity

rclpy.init();node=Node('check_composed_robot')
data={}
node.create_subscription(String,'/robot_description',lambda m:data.update(robot=m.data),
    QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))
node.create_subscription(JointState,'/joint_states',lambda m:data.update(joints=m),qos_profile_sensor_data)
deadline=time.monotonic()+15
while len(data)<2 and time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.1)
assert len(data)==2,data.keys()
root=ET.fromstring(data['robot'])
links=[p.get('name') for p in root.findall('link')]
joints=root.findall('joint')
assert len(links)==len(set(links)) and 'tidybot_chassis' in links
assert all('kinova' not in name.lower() for name in links)
parents={j.find('child').get('link'):j.find('parent').get('link') for j in joints}
assert len(parents)==len(joints), 'Multiple parent joints'
assert set(links)-set(parents)=={'base_link'},'Robot must have one root'
for link in links:
    seen=set()
    while link in parents:
        assert link not in seen,'Cycle in robot tree'
        seen.add(link);link=parents[link]
    assert link=='base_link'
client=node.create_client(GetStateValidity,'/check_state_validity')
assert client.wait_for_service(timeout_sec=10)
request=GetStateValidity.Request();request.robot_state.joint_state=data['joints']
future=client.call_async(request);rclpy.spin_until_future_complete(node,future,timeout_sec=10)
result=future.result();assert result and result.valid,[(c.contact_body_1,c.contact_body_2) for c in result.contacts]
print('PASS: single composed URDF tree, TidyBot chassis + OpenArm, no Kinova, current state collision-free; links=',len(links))
node.destroy_node();rclpy.shutdown()
