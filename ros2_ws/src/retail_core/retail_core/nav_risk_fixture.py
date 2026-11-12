"""Synthetic advisory stream for isolated Compose acceptance only."""
from rclpy.node import Node
from std_msgs.msg import Header, String

from retail_interfaces.msg import RiskAdvisory
from .common import duration, run


class RiskFixture(Node):
    def __init__(self):
        super().__init__('nav_risk_fixture')
        self.action = 'CONTINUE'
        self.output = self.create_publisher(RiskAdvisory, '/perception/vqa/tidybot', 10)
        self.create_subscription(String, '/test/vqa_action', self.update, 1)
        self.create_timer(0.1, self.tick)

    def update(self, msg):
        self.action = msg.data

    def tick(self):
        if self.action != 'OFFLINE':
            self.output.publish(RiskAdvisory(
                header=Header(stamp=self.get_clock().now().to_msg()), valid_for=duration(2),
                camera_id='tidybot', source_id='compose_fixture', action=self.action,
                valid=True, synthetic=True, explanation='Synthetic acceptance fixture'))


def main():
    run(RiskFixture)
