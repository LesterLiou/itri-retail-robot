import math

import pytest

from lightweight_risk_vqa.camera_policy import (
    CameraPolicy, CameraPolicyConfig, PersonBoxDetector, camera_live_decision, mask_to_boxes,
)


SAFE = [.4, .2, .6, .65]
NEAR = [.4, .3, .6, .9]


def test_stationary_people_need_history_before_continue():
    policy = CameraPolicy()
    assert policy.update([SAFE], 0)['action'] == 'OBSERVE'
    assert policy.update([SAFE], .5)['action'] == 'OBSERVE'
    report = policy.update([SAFE], 1)
    assert report['action'] == 'CONTINUE'
    assert report['tracks'][0]['motion'] == 'STATIONARY'


def test_growing_box_warns_only_in_robot_corridor():
    for x, expected in ((.4, 'DANGER_WARNING'), (.01, 'CONTINUE')):
        policy = CameraPolicy()
        for i, height in enumerate((.3, .33, .36)):
            report = policy.update([[x, .1, x + .15, .1 + height]], i * .5)
        assert report['action'] == expected
        assert report['tracks'][0]['motion'] == 'TOWARD_CAMERA'


def test_receding_and_lateral_motion_are_not_danger_by_activity_alone():
    policy = CameraPolicy()
    for i, height in enumerate((.4, .36, .32)):
        report = policy.update([[.4, .1, .6, .1 + height]], i * .5)
    assert report['action'] == 'CONTINUE'
    assert report['tracks'][0]['motion'] == 'AWAY_FROM_CAMERA'
    policy.reset()
    for i, x in enumerate((.3, .34, .38)):
        report = policy.update([[x, .2, x + .2, .65]], i * .5)
    assert report['tracks'][0]['motion'] == 'LATERAL_ACROSS_VIEW'
    assert report['action'] == 'CONTINUE'


def test_stop_confirmation_priority_hold_and_empty_mask():
    policy = CameraPolicy()
    assert policy.update([NEAR, SAFE], 0)['action'] == 'OBSERVE'
    assert policy.update([NEAR, SAFE], .5)['action'] == 'STOP'
    assert policy.update([], .7)['action'] == 'STOP'
    assert policy.update([], 1.6)['action'] == 'OBSERVE'
    assert policy.update([NEAR], 2)['action'] == 'OBSERVE'
    assert policy.update([NEAR], 2.5)['action'] == 'STOP'


def test_large_person_outside_stop_roi_does_not_stop():
    policy = CameraPolicy()
    for i in range(3):
        report = policy.update([[.05, .1, .25, .95]], i * .5)
    assert report['action'] == 'CONTINUE'
    assert not report['tracks'][0]['in_stop_roi']


def test_missing_person_is_not_proof_of_clear_path_and_nonperson_query_is_not_a_person():
    policy = CameraPolicy()
    assert policy.update([], 1)['reason_code'] == 'no_reliable_person_box'
    assert policy.update([NEAR], 2, person_target=False)['action'] == 'OBSERVE'


def test_merge_and_split_do_not_create_fake_approach_history():
    policy = CameraPolicy()
    boxes = [[.3, .2, .45, .6], [.5, .2, .65, .6]]
    for i in range(3):
        policy.update(boxes, i * .5)
    merged = policy.update([[.3, .1, .65, .65]], 1.5)
    assert merged['tracks'][0]['samples'] == 1
    assert merged['action'] == 'OBSERVE'
    split = policy.update(boxes, 2)
    assert all(t['samples'] == 1 for t in split['tracks'])


def test_clock_gap_rewind_and_reset_discard_history():
    policy = CameraPolicy()
    policy.update([NEAR], 0)
    assert policy.update([NEAR], .5)['action'] == 'STOP'
    assert policy.update([NEAR], 10)['action'] == 'OBSERVE'
    assert policy.update([NEAR], 9)['action'] == 'OBSERVE'
    policy.reset()
    assert policy.update([SAFE], 11)['tracks'][0]['samples'] == 1
    assert policy.update([SAFE], math.nan)['action'] == 'DATA_LOSS'


def test_geometry_decision_overrides_observe_but_never_data_loss():
    geometry = CameraPolicy().update([NEAR], 9)
    geometry.update(action='STOP', reason_code='fixture_stop')
    state = {'frame_at': 10., 'vqa_evidence_at': 9., 'loop': 0,
             'requested_query': 'state', 'vqa_context': (0, 'state'),
             'segmentation_context': (0, 'state'), 'vqa_parsed': {'action': 'OBSERVE'},
             'camera_geometry': geometry}
    result = camera_live_decision(state, 10.1)
    assert result['action'] == 'STOP'
    assert result['decision_authority'] == 'camera_image_geometry'
    assert result['evidence']['tracks']
    for changes in ({'vqa_error': 'bad JSON'}, {'frame_at': 0}, {'loop': 1},
                    {'camera_geometry': None}, {'camera_geometry': {**geometry, 'evidence_at': 0}},
                    {'segmentation_context': (0, 'other')}):
        result = camera_live_decision({**state, **changes}, 10.1)
        assert result['action'] == 'DATA_LOSS'
        assert not result['data_available']
    assert camera_live_decision({**state, 'paused': True}, 10.1)['action'] == 'OBSERVE'


def test_mask_boxes_keep_separate_people_and_filter_specks():
    np = pytest.importorskip('numpy')
    pytest.importorskip('cv2')
    mask = np.zeros((100, 200), dtype=bool)
    mask[20:80, 20:50] = True
    mask[30:90, 130:160] = True
    mask[1, 1] = True
    boxes = mask_to_boxes(mask)
    assert len(boxes) == 2
    assert boxes[0] == pytest.approx([.1, .2, .25, .8])
    assert boxes[1] == pytest.approx([.65, .3, .8, .9])


@pytest.mark.parametrize('kwargs', [
    {'stop_roi': [.7, 0, .3, 1]}, {'corridor': [0, 2]}, {'approach_rate': math.nan},
    {'stop_confirmations': 1}, {'min_samples': 2.5}, {'history_s': .1}, {'min_box_height': 2},
])
def test_invalid_thresholds_are_rejected(kwargs):
    with pytest.raises(ValueError):
        CameraPolicyConfig(**kwargs)


def test_detector_requires_existing_weights_and_explicit_person_class(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    with pytest.raises(ValueError, match='missing'):
        PersonBoxDetector(tmp_path / 'missing.pt')
    weights = tmp_path / 'local.pt'
    weights.write_bytes(b'fixture')

    class Model:
        names = {0: 'person', 1: 'dog'}
        def __init__(self, path, task):
            assert path == str(weights) and task == 'detect'
        def predict(self, frame, **kwargs):
            assert kwargs['classes'] == [0] and kwargs['conf'] == .25
            assert kwargs['iou'] == .45
            assert kwargs['imgsz'] == 416
            return [SimpleNamespace(boxes=SimpleNamespace(xyxyn=SimpleNamespace(
                cpu=lambda: SimpleNamespace(tolist=lambda: [SAFE]))))]

    monkeypatch.setitem(sys.modules, 'ultralytics', SimpleNamespace(YOLO=Model))
    assert PersonBoxDetector(weights).detect(None) == [SAFE]
    Model.names = {0: 'dog'}
    with pytest.raises(ValueError, match='person class'):
        PersonBoxDetector(weights)
