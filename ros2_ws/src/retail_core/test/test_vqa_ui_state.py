"""UI freshness and debug history; these callbacks never publish motion/input data."""
import json
import numpy as np
import pytest
import rclpy
from std_msgs.msg import String
from retail_interfaces.msg import RiskAdvisory
from retail_core.vqa_ui import RosPipeline


@pytest.fixture
def pipeline():
    rclpy.init()
    node=RosPipeline()
    yield node
    node.destroy_node();rclpy.shutdown()


def input_sample(node):
    image=node.bridge.cv2_to_imgmsg(np.zeros((8,8,3),dtype=np.uint8),'bgr8')
    image.header.stamp=node.get_clock().now().to_msg()
    image.header.frame_id='chest_color_optical_frame'
    risk=RiskAdvisory(header=image.header,action='OBSERVE',valid=True,source_id='video:test/worker',
        model_id='test-model',raw_response='{"observation":"test","action":"OBSERVE"}')
    risk.valid_for.sec=2
    stamp=image.header.stamp.sec+image.header.stamp.nanosec*1e-9
    detail=dict(stamp=stamp,source_id=risk.source_id,inference_mode='yolo-assisted',inference_ms=500,
        metrics=dict(bbox_source='yolo',segmentation_enabled=False,person_boxes=[[0.1,0.1,0.2,0.2]]))
    node.receive_image(image);node.receive_risk(risk);node.receive_details(String(data=json.dumps(detail)))
    return risk


def test_expiry_retains_raw_without_claiming_valid_data(pipeline):
    risk=input_sample(pipeline)
    state=pipeline.snapshot()
    assert state['decision']['data_available']
    original=state['vqa_raw']
    expired=RiskAdvisory(header=risk.header,action='DATA_LOSS',valid=False,source_id=risk.source_id,
                        reason='image/result expired')
    pipeline.receive_risk(expired)
    state=pipeline.snapshot()
    assert state['decision']['action']=='DATA_LOSS' and not state['decision']['data_available']
    assert state['vqa_raw']==original and state['vqa_model']=='test-model'


def test_other_source_epoch_cannot_supply_current_yolo_evidence(pipeline):
    risk=input_sample(pipeline)
    risk.source_id='video:new-epoch/new-worker'
    pipeline.receive_risk(risk)
    state=pipeline.snapshot()
    assert state['segmentation_context'] is None


def test_last_valid_history_does_not_release_current_data_loss(pipeline):
    risk=input_sample(pipeline)
    risk.action='CONTINUE';pipeline.receive_risk(risk)
    invalid=RiskAdvisory(header=risk.header,valid=False,action='DATA_LOSS',source_id=risk.source_id,
                        reason='schema rejected')
    pipeline.receive_risk(invalid)
    state=pipeline.snapshot()
    assert state['last_valid']['action']=='CONTINUE'
    assert state['decision']['action']=='DATA_LOSS'
    assert state['decision']['data_available'] is False


def test_new_stop_replaces_history_without_waiting_for_display_smoothing(pipeline):
    risk=input_sample(pipeline)
    risk.action='CONTINUE';pipeline.receive_risk(risk)
    risk.action='STOP';pipeline.receive_risk(risk)
    state=pipeline.snapshot()
    assert state['decision']['action']=='STOP'
    assert state['last_valid']['action']=='STOP'


def test_monitor_labels_retained_continue_as_history_not_current_permission(pipeline,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from retail_core.vqa_monitor_view import QApplication,Monitor
    app=QApplication.instance() or QApplication([])
    risk=input_sample(pipeline);risk.action='CONTINUE';pipeline.receive_risk(risk)
    pipeline.receive_risk(RiskAdvisory(header=risk.header,valid=False,action='DATA_LOSS',
        source_id=risk.source_id,reason='image/result expired'))
    window=Monitor(pipeline)
    try:
        window.refresh()
        assert 'LAST VALID: CONTINUE (history only)' in window.decision.text()
        assert 'CURRENT: DATA_LOSS' in window.decision.text()
        assert 'no motion permission' in window.history.text()
        assert set(window.action_choices)=={'STOP','DANGER_WARNING','CONTINUE','OBSERVE'}
        assert 'HISTORY ONLY' in window.action_choices['CONTINUE'].text()
        assert 'MODEL SELECTED' not in window.action_choices['CONTINUE'].text()
        assert 'DATA_LOSS' in window.health.text()
        assert pipeline.snapshot()['decision']['data_available'] is False
        risk.action='STOP';pipeline.receive_risk(risk);window.refresh()
        assert 'MODEL SELECTED' in window.action_choices['STOP'].text()
        assert window.action_choices['CONTINUE'].text().endswith('—')
    finally:
        window.timer.stop();window.close();app.processEvents()
