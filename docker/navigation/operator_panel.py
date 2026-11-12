"""5070 control/status panel for the split-host navigation profile."""
import json
import time

from nav_msgs.msg import Odometry
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String
from std_srvs.srv import Trigger

from retail_interfaces.msg import DriveState, RiskAdvisory
from retail_core.common import now, seconds
from retail_core.drive_timing import remote_age

app = QApplication([])
rclpy.init()
node = Node('navigation_panel_5070')
samples = {}


def receive(key, msg):
    samples[key] = (msg, time.monotonic())


node.create_subscription(DriveState, '/drive/state', lambda m: receive('drive', m), qos_profile_sensor_data)
node.create_subscription(Odometry, '/odom', lambda m: receive('odom', m), qos_profile_sensor_data)
node.create_subscription(RiskAdvisory, '/perception/vqa/tidybot',
                         lambda m: receive('risk', m), qos_profile_sensor_data)
node.create_subscription(String, '/mission/state', lambda m: receive('mission', json.loads(m.data)), 5)
straight = node.create_client(Trigger, '/mission/straight_2m')
cancel = node.create_client(Trigger, '/mission/cancel')

window = QWidget()
window.setWindowTitle('TidyBot Navigation and VQA')
window.setMinimumWidth(440)
layout = QVBoxLayout(window)
labels = {name: QLabel('Waiting for data') for name in ('mission', 'risk', 'drive', 'odom', 'result')}
for label in labels.values():
    label.setWordWrap(True)
    layout.addWidget(label)
forward = QPushButton('Drive Forward 2 m')
stop = QPushButton('Cancel Mission')
layout.addWidget(forward)
layout.addWidget(stop)


def request(client):
    if client.service_is_ready():
        future = client.call_async(Trigger.Request())
        future.add_done_callback(lambda result: labels['result'].setText(result.result().message))


forward.clicked.connect(lambda: request(straight))
stop.clicked.connect(lambda: request(cancel))


def latest(key, ttl=0.5):
    msg, received = samples.get(key, (None, 0))
    return msg if time.monotonic()-received < ttl else None


def refresh():
    for _ in range(8):
        rclpy.spin_once(node, timeout_sec=0.0)
    mission = latest('mission')
    drive, risk, odom = latest('drive'), latest('risk', 2.0), latest('odom')
    age = remote_age(now(node), seconds(risk.header.stamp)) if risk else -1
    risk_valid = risk and risk.valid and 0 <= age <= min(2.0, seconds(risk.valid_for))
    last_rejection = mission.get('last_goal_rejection') if mission else None
    mission_text = f"{mission['phase']} | {mission['reason']}" if mission else 'Offline'
    if last_rejection:
        mission_text += f"\nLast goal rejected | {last_rejection['reason']}"
    labels['mission'].setText(f'THOR mission | {mission_text}')
    risk_text = f'{risk.action} | {age:.2f} s\n{risk.explanation}' if risk_valid else 'DATA_LOSS | Waiting for a valid advisory'
    if risk and risk.synthetic:
        risk_text += ' (synthetic test)'
    labels['risk'].setText(f'VQA | {risk_text}')
    labels['drive'].setText(
        f"{'Hardware interface' if drive.hardware else 'Fake base'} | {drive.phase} | {drive.owner}"
        f" | {'Standstill verified' if drive.standstill else 'Standstill unverified'}\n{drive.reason}"
        f'\nGate output vx {drive.output.linear.x:+.2f} m/s | Straight-line progress {drive.distance:.2f} m'
        if drive else 'TidyBot | Offline')
    if odom and 0 <= remote_age(now(node), seconds(odom.header.stamp)) < 0.5:
        twist = odom.twist.twist
        labels['odom'].setText(f'Measured odometry | vx {twist.linear.x:+.2f} m/s | vy {twist.linear.y:+.2f} m/s'
                               f' | wz {twist.angular.z:+.2f} rad/s')
    else:
        labels['odom'].setText('Odometry | Offline')
    forward.setEnabled(bool(straight.service_is_ready() and drive and drive.standstill
                            and drive.phase in ('IDLE', 'DONE')))
    stop.setEnabled(bool(cancel.service_is_ready() and mission and
                         mission['phase'] in ('STARTING', 'ACTIVE', 'FINISHING')))


timer = QTimer()
timer.timeout.connect(refresh)
timer.start(50)
window.show()
try:
    app.exec()
finally:
    node.destroy_node()
    rclpy.try_shutdown()
