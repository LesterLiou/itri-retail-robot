import numpy as np
import pytest

pytest.importorskip('PyQt5')
pytest.importorskip('torch')
pytest.importorskip('cv2')

from lightweight_risk_vqa.light_foundation_live_ui import (
    embedded_schema_error, make_vqa_panel, vicuna_image_prompt,
)


def test_vqa_panel_and_worker_prompt_contract():
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    overlay = frame.copy()
    overlay[20:40, 30:60, 2] = 255
    panel = make_vqa_panel(frame, overlay)
    assert panel.shape == (360, 1280, 3)
    assert vicuna_image_prompt().count('<image>') == 1
    assert 'Light Foundation temporal segmentation' in vicuna_image_prompt()


def test_stream_preprocessing_matches_training_contiguous_layout():
    from lightweight_risk_vqa.light_foundation_live_ui import TemporalSegmenter
    tensor=TemporalSegmenter.preprocess(np.zeros((90,160,3),dtype=np.uint8))
    assert tensor.is_contiguous() and tensor.shape==(3,320,320)


def test_preview_cap_does_not_slow_source_video_playback(monkeypatch):
    from types import SimpleNamespace
    import threading
    from lightweight_risk_vqa import light_foundation_live_ui as ui
    clock=SimpleNamespace(now=0.)
    class Stop:
        def is_set(self): return clock.now>=1.
        def wait(self,delay):
            clock.now+=max(delay,1e-6)
            return self.is_set()
    class Reader:
        position=0
        def isOpened(self): return True
        def get(self,key): return 30. if key==ui.cv2.CAP_PROP_FPS else 300.
        def grab(self):
            self.position+=1
            return True
        def read(self):
            self.position+=1
            return True,np.zeros((2,2,3),dtype=np.uint8)
        def release(self): pass
    monkeypatch.setattr(ui.time,'monotonic',lambda:clock.now)
    monkeypatch.setattr(ui.cv2,'VideoCapture',lambda _:Reader())
    pipeline=ui.LivePipeline.__new__(ui.LivePipeline)
    pipeline.args=SimpleNamespace(video='fixture.mp4',display_fps=15.,loop=False)
    pipeline.lock=threading.Lock()
    pipeline.stop_event=Stop()
    pipeline.paused=False
    pipeline.state={}
    pipeline._capture()
    assert 27<=pipeline.state['frame_index']<=30
    assert .9<=pipeline.state['video_time_s']<=1.


def test_embedded_schema_is_strict():
    assert embedded_schema_error({
        'motion': 'UNCERTAIN', 'action': 'DANGER_WARNING', 'explanation': 'insufficient'
    }) == ''
    assert 'invalid action' in embedded_schema_error({
        'motion': 'UNCERTAIN', 'action': 'UNKNOWN', 'explanation': 'insufficient'
    })


def test_monitor_hides_previous_semantic_warning_when_data_fails(monkeypatch):
    assert 'current_activity' in embedded_schema_error({
        'motion': 'UNCERTAIN', 'action': 'OBSERVE', 'explanation': 'missing state fields'
    }, expect_state=True)
    import json
    from types import SimpleNamespace
    from PyQt5.QtWidgets import QApplication
    from lightweight_risk_vqa.light_foundation_live_ui import Monitor
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])
    state = {'status': 'LIVE', 'frame': None, 'overlay': None, 'frame_index': 1, 'loop': 0,
             'segmentation_query': 'people', 'vqa_model': 'fixture',
             'vqa_parsed': {'action': 'DANGER_WARNING'}, 'vqa_raw': 'old danger',
             'vqa_error': 'invalid JSON',
             'decision': {'action': 'DATA_LOSS', 'reason_code': 'invalid JSON', 'data_available': False}}
    pipeline = SimpleNamespace(args=SimpleNamespace(query='people', vqa_backend='embedded'),
                               snapshot=lambda: state, stop=lambda: None)
    window = Monitor(pipeline)
    window.refresh()
    assert window.decision.text().startswith('DATA LOSS')
    assert json.loads(window.vqa.toPlainText())['parsed'] is None
    assert '#606b78' in window.decision.styleSheet()
    state['decision'] = {'action': 'OBSERVE', 'reason_code': 'fresh', 'data_available': True}
    state['vqa_parsed'] = {'current_activity': 'WALKING', 'observed_transition': 'STARTED_WALKING',
                           'explanation': 'The target started walking.'}
    window.refresh()
    assert 'WALKING' in window.answer_summary.text()
    assert 'STARTED_WALKING' in window.answer_summary.text()
    # A fresh motion-head hint does not make a stale/invalid Qwen answer current.
    state['vqa_health']={'data_available':False,'reason_code':'vqa_evidence_stale'}
    window.refresh()
    assert json.loads(window.vqa.toPlainText())['parsed'] is None
    assert 'vqa_evidence_stale' in window.answer_summary.text()
    window.close()
    app.processEvents()


def test_state_qa_reuses_one_visual_memory_update_with_a_separate_mask_query():
    import json
    import torch
    from types import SimpleNamespace
    from lightweight_risk_vqa.light_foundation_live_ui import TemporalSegmenter
    from lightweight_risk_vqa.person_state import STATE_QUERY, state_answer

    class Tokenizer:
        def encode(self, text, **kwargs):
            return [1, 7] if text.startswith('the people') else [2, 3, 4, 5, 7]
        def decode(self, tokens, **kwargs):
            return state_answer('WALKING', 'STEADY')

    class Model:
        config = SimpleNamespace(seg_token_id=7)
        language_model = SimpleNamespace(get_input_embeddings=lambda: torch.nn.Embedding(10, 4))
        seg_embedding = torch.zeros(4)
        calls = 0
        published = False
        def __call__(self, frames, ids, **kwargs):
            self.calls += 1
            assert ids.tolist() == [[1, 7]]
            return {'state': (torch.zeros(1, 2, 2, 2), torch.zeros(1, 2, 2, 2)),
                    'mask_logits': torch.zeros(1, 1, 32, 32), 'presence_logits': torch.zeros(1),
                    'visual_token_count': 2, 'language_inputs': torch.ones(1, 4, 4),
                    'language_attention_mask': torch.ones(1, 4, dtype=torch.long)}
        def generate_from_context(self, inputs, attention, max_new_tokens, **options):
            assert self.published  # Current visual result must be published before slow generation.
            assert inputs.shape == (1, 7, 4) and attention.shape == (1, 7)
            assert torch.equal(inputs[:, :2], torch.ones(1, 2, 4))
            return torch.tensor([[1]])

    segmenter = TemporalSegmenter.__new__(TemporalSegmenter)
    segmenter.device = torch.device('cpu')
    segmenter.model, segmenter.tokenizer = Model(), Tokenizer()
    segmenter.set_query(STATE_QUERY)
    def publish(overlay,metrics):
        assert segmenter.model.calls==1
        assert metrics['memory_steps_since_reset']==1
        segmenter.model.published=True
    _, metrics, answer = segmenter.process(np.zeros((32, 32, 3), dtype=np.uint8), True,visual_callback=publish)
    assert segmenter.model.calls == 1 and metrics['memory_steps_since_reset'] == 1
    assert segmenter.mask_query == 'the people' and not answer['error']
    assert json.loads(answer['raw'])['current_activity'] == 'WALKING'


def test_legacy_geometry_policy_keeps_actions_without_drawing_regions():
    import threading
    import time
    from types import SimpleNamespace
    from lightweight_risk_vqa.camera_policy import CameraPolicy
    from lightweight_risk_vqa.light_foundation_live_ui import LivePipeline

    now = time.monotonic()
    pipeline = LivePipeline.__new__(LivePipeline)
    pipeline.lock = threading.Lock()
    pipeline.paused = False
    pipeline.requested_query = 'state'
    pipeline.camera_policy = CameraPolicy()
    pipeline.args = SimpleNamespace(decision_policy='camera', max_frame_age_s=2, max_result_age_s=8)
    pipeline.camera_policy.update([[.4, .3, .6, .9]], now - .5)
    geometry = pipeline.camera_policy.update([[.4, .3, .6, .9]], now)
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    pipeline.state = {'frame': frame, 'overlay': frame, 'segmentation_frame': frame,
        'frame_at': now, 'vqa_evidence_at': now, 'loop': 0, 'vqa_context': (0, 'state'),
        'segmentation_context': (0, 'state'), 'vqa_parsed': {'action': 'OBSERVE'},
        'camera_geometry': geometry}
    snapshot = pipeline.snapshot()
    assert snapshot['decision']['action'] == 'STOP'
    assert snapshot['vqa_parsed']['action'] == 'OBSERVE'
    assert not snapshot['overlay'].any()  # No box/ROI drawing, including legacy snapshots.
    assert not pipeline.state['overlay'].any()
    pipeline.args.decision_policy = 'model'
    assert pipeline.snapshot()['decision']['action'] == 'OBSERVE'
    pipeline.args.decision_policy = 'camera'
    pipeline.state['vqa_error'] = 'invalid JSON'
    assert pipeline.snapshot()['decision']['action'] == 'DATA_LOSS'


def test_robot_yolo_hints_enter_the_same_single_frame_memory_context():
    import json
    import torch
    from types import SimpleNamespace
    from lightweight_risk_vqa.light_foundation_live_ui import TemporalSegmenter
    from lightweight_risk_vqa.robot_vqa import ROBOT_QUERY, query_with_hints

    encoded = []
    response = {'observation': 'A person is standing.', 'motion': 'STATIONARY',
                'approach_intent': 'UNLIKELY', 'collision_risk': 'LOW', 'action': 'CONTINUE',
                'explanation': 'No observed approach.'}
    hints = {'people_detected': 1, 'people': [{'bbox': [.2, .2, .4, .6]}]}
    query = query_with_hints(ROBOT_QUERY, hints)

    class Tokenizer:
        def encode(self, text, **kwargs):
            encoded.append(text)
            return [1, 7] if text.startswith('the people') else [2, 3, 7]
        def decode(self, tokens, **kwargs):
            return json.dumps(response)

    class Model:
        config = SimpleNamespace(seg_token_id=7)
        language_model = SimpleNamespace(get_input_embeddings=lambda: torch.nn.Embedding(10, 4))
        seg_embedding = torch.zeros(4)
        calls = 0
        def __call__(self, frames, ids, **kwargs):
            self.calls += 1
            assert frames.shape[:2] == (1, 1)
            return {'state': (torch.zeros(1, 2, 2, 2), torch.zeros(1, 2, 2, 2)),
                    'mask_logits': torch.zeros(1, 1, 32, 32), 'presence_logits': torch.zeros(1),
                    'visual_token_count': 2, 'language_inputs': torch.ones(1, 4, 4),
                    'language_attention_mask': torch.ones(1, 4, dtype=torch.long)}
        def generate_from_context(self, inputs, attention, max_new_tokens, **options):
            assert options['stopping_criteria']
            assert torch.equal(inputs[:, :2], torch.ones(1, 2, 4))
            return torch.tensor([[1]])

    segmenter = TemporalSegmenter.__new__(TemporalSegmenter)
    segmenter.device = torch.device('cpu')
    segmenter.model, segmenter.tokenizer = Model(), Tokenizer()
    segmenter.person_mask, segmenter.collect_boxes = True, False
    segmenter.robot_decoding = 'free'  # This test checks prefix reuse, not the tokenizer grammar.
    segmenter.set_query(ROBOT_QUERY)
    _, metrics, result = segmenter.process(np.zeros((32, 32, 3), dtype=np.uint8), True,
                                         answer_query=query, expect_robot=True)
    assert segmenter.model.calls == 1 and metrics['memory_steps_since_reset'] == 1
    assert metrics['person_boxes'] == []  # No mask-to-bbox policy hidden in robot mode.
    assert encoded[-1] == query + '\n<seg>\n'
    assert not result['error'] and result['parsed']['action'] == 'CONTINUE'


@pytest.mark.parametrize('mode', ['vqa-only', 'yolo-assisted'])
def test_robot_snapshot_never_uses_old_region_action(mode):
    import threading
    import time
    from types import SimpleNamespace
    from lightweight_risk_vqa.light_foundation_live_ui import LivePipeline

    now = time.monotonic()
    pipeline = LivePipeline.__new__(LivePipeline)
    pipeline.lock = threading.Lock()
    pipeline.paused, pipeline.requested_query = False, 'robot'
    pipeline.args = SimpleNamespace(inference_mode=mode, decision_policy='model',
                                    max_frame_age_s=2, max_result_age_s=8)
    pipeline.state = {'frame': None, 'overlay': None, 'segmentation_frame': None,
        'frame_at': now, 'vqa_evidence_at': now, 'loop': 0, 'vqa_context': (0, 'robot'),
        'vqa_parsed': {'observation': 'A person walks away.', 'motion': 'AWAY_FROM_CAMERA',
                       'approach_intent': 'UNLIKELY', 'collision_risk': 'LOW',
                       'action': 'CONTINUE', 'explanation': 'The person moves away.'},
        'camera_geometry': {'action': 'STOP'}}
    snapshot = pipeline.snapshot()
    assert snapshot['decision']['action'] == 'CONTINUE'
    assert snapshot['decision']['decision_authority'].startswith('embedded_vqa')


def test_monitor_dashboard_counts_sources_and_preserves_full_raw(monkeypatch):
    from types import SimpleNamespace
    import time
    from PyQt5.QtWidgets import QApplication
    from lightweight_risk_vqa.light_foundation_live_ui import Monitor
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])
    raw = '```json\n{"explanation":"exact raw response"}\n```\n' + 'x' * 3000
    state = dict(status='LIVE', frame=None, overlay=None, frame_index=5, loop=0,
                 requested_query='people', segmentation_query='people', vqa_model='fixture',
                 segmentation_context=(0, 'people'), segmentation_evidence_at=time.monotonic(),
                 vqa_raw=raw, vqa_error='', vqa_latency_ms=321.5,
                 vqa_parsed={'observation':'Two people walking.', 'motion':'TOWARD_CAMERA',
                             'explanation':'A person approaches.', 'collision_risk':'ELEVATED'},
                 metrics={'bbox_source':'yolo', 'person_boxes':[{}, {}], 'segmentation_ms':24.2,
                          'bbox_detector_ms':5.5},
                 decision={'action':'DANGER_WARNING','reason_code':'vqa_elevated',
                           'data_available':True,'decision_authority':'embedded_vqa'})
    pipeline = SimpleNamespace(args=SimpleNamespace(query='people',vqa_backend='embedded'),
                               snapshot=lambda:state, stop=lambda:None)
    window = Monitor(pipeline)
    window.refresh()
    assert window.decision.text() == 'DANGER_WARNING'
    assert window.people.text().startswith('2 people')
    assert 'VQA 322 ms' in window.latency.text() and 'Seg 24 ms' in window.latency.text()
    assert 'A person approaches.' in window.answer_summary.text()
    assert window.raw_answer.toPlainText() == raw
    assert window.details.isHidden()
    state['metrics']['bbox_source'] = 'none'
    window.refresh()
    assert window.people.text().startswith('Not provided')
    state['metrics']['bbox_source'] = 'mask'
    window.refresh()
    assert 'regions' in window.people.text() and 'not people IDs' in window.people.text()
    state['segmentation_context'] = (1, 'people')
    state['vqa_health'] = {'data_available':False, 'reason_code':'vqa_evidence_stale'}
    window.refresh()
    assert window.people.text().startswith('Unavailable')
    assert 'A person approaches.' not in window.answer_summary.text()
    assert window.raw_answer.toPlainText() == raw
    window.close()
    app.processEvents()


def test_no_segmentation_keeps_memory_and_answer_without_mask_output():
    import torch
    from types import SimpleNamespace
    from lightweight_risk_vqa.light_foundation_live_ui import TemporalSegmenter
    class Model:
        config = SimpleNamespace(seg_token_id=7)
        language_model = SimpleNamespace(get_input_embeddings=lambda:torch.nn.Embedding(10,4))
        seg_embedding = torch.zeros(4)
        def __call__(self, frames, ids, **kwargs):
            assert kwargs['compute_masks'] is False
            return dict(state=(torch.ones(1,2,2,2),torch.ones(1,2,2,2)),
                        mask_logits=None,presence_logits=None,visual_token_count=2,
                        language_inputs=torch.ones(1,4,4),
                        language_attention_mask=torch.ones(1,4,dtype=torch.long))
        def generate_from_context(self, inputs, attention, max_new_tokens, **kwargs):
            return torch.tensor([[1]])
    class Tokenizer:
        def encode(self,*args,**kwargs): return [1,7]
        def decode(self,*args,**kwargs):
            return '{"motion":"UNCERTAIN","action":"OBSERVE","explanation":"Not enough evidence"}'
    segmenter = TemporalSegmenter.__new__(TemporalSegmenter)
    segmenter.device = torch.device('cpu')
    segmenter.model,segmenter.tokenizer = Model(),Tokenizer()
    segmenter.segmentation_enabled = False
    segmenter.set_query('the people')
    overlay,metrics,answer = segmenter.process(np.zeros((32,32,3),dtype=np.uint8),True)
    assert overlay is None and not answer['error']
    assert metrics['segmentation_ms'] is None and metrics['mask_area_fraction'] is None
    assert metrics['segmentation_enabled'] is False and metrics['visual_memory_ms'] >= 0
    assert metrics['memory_steps_since_reset'] == 1 and metrics['person_boxes'] == []


def test_single_frame_fits_available_dashboard_height_without_cropping(monkeypatch):
    from PyQt5.QtWidgets import QApplication
    from lightweight_risk_vqa.light_foundation_live_ui import to_pixmap
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])
    pixmap = to_pixmap(np.zeros((720,1280,3),dtype=np.uint8), 1450, 350)
    assert pixmap.height() <= 350 and pixmap.width() <= 1450
    assert abs(pixmap.width()/pixmap.height()-1280/720) < .01
