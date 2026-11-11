import pytest

rclpy = pytest.importorskip('rclpy')
from std_msgs.msg import String
from lightweight_risk_vqa.temporal_analysis_node import TemporalAnalysisNode
from lightweight_risk_vqa.direct_temporal import temporal_decision


@pytest.mark.parametrize('payload', ['bad JSON', '[]', '{"cameras":{"left":{"stamp_s":null}}}'])
def test_malformed_tracking_input_publishes_unavailable_without_crashing(payload):
    rclpy.init()
    node = TemporalAnalysisNode()
    try:
        node._tracks(String(data=payload))
        assert node.report['availability'] == 'UNAVAILABLE'
        assert node.report['validation_errors']
        assert temporal_decision(node.report, 10, .1)['action'] == 'DATA_LOSS'
        node._heartbeat()
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_original_vqa_policy_expires_empty_scene_and_invalidates_bad_json():
    import json
    from future_risk_vqa.future_policy_node import FuturePolicyNode
    rclpy.init()
    node = FuturePolicyNode()
    clock = [10.0]
    node._now = lambda: clock[0]
    try:
        node._geometry(String(data=json.dumps({'stamp_s': 10, 'tracked_agents': []})))
        node._tick()
        assert node.last_decision.action == 'NO_RESPONSE'
        clock[0] = 15.0
        node._tick()
        assert node.last_decision.action == 'DATA_LOSS'
        node._geometry(String(data=json.dumps({'stamp_s': 15, 'tracked_agents': [{'track_id': 'L1'}]})))
        node._semantics(String(data=json.dumps({'assessments': [{'track_id': 'L1',
            'future_prediction': {'future_motion': 'AWAY_FROM_CAMERA'}}]})))
        node._tick()
        assert node.last_decision.action == 'CONTINUE'
        node._semantics(String(data='malformed JSON'))
        node._tick()
        assert node.last_decision.action == 'DATA_LOSS'
        node._geometry(String(data='{}'))
        node._tick()
        assert node.last_decision.action == 'DATA_LOSS'
    finally:
        node.destroy_node()
        rclpy.shutdown()
