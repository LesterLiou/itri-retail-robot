from copy import deepcopy

from lightweight_risk_vqa.fusion import PredictionHistory, fuse_report


def tracks(stamp, source='lstm', reliable=True):
    return {'cameras': {'left': {'stamp_s': stamp, 'tracks': [{
        'track_id': 'L1', 'visible': True, 'temporal_prediction': {
            'source': source, 'reliable': reliable, 'future_motion': 'TOWARD_CAMERA', 'stamp_s': stamp}}]}}}


def semantics():
    return {'availability': 'READY', 'validation_errors': [],
        'metadata': {'snapshot_stamps_s': {'left': 10.0}},
        'assessments': [{'track_id': 'L1', 'camera': 'left',
            'current_observation': {'body_posture': 'STANDING', 'current_activity': 'WORKING'},
            'future_prediction': {'future_motion': 'STATIONARY'}}]}


def test_lstm_fusion_preserves_prompt_semantics_and_raw_future():
    history = PredictionHistory()
    history.add(tracks(9.8))
    report = semantics()
    before = deepcopy(report)
    result = fuse_report(report, history, 'lstm_future')
    future = result['assessments'][0]['future_prediction']
    assert future['future_motion'] == 'TOWARD_CAMERA'
    assert future['vlm_future_motion'] == 'STATIONARY'
    assert result['assessments'][0]['current_observation'] == before['assessments'][0]['current_observation']
    assert report == before


def test_future_or_stale_observations_are_never_used_for_old_vqa():
    for stamp in (10.1, 1.0):
        history = PredictionHistory()
        history.add(tracks(stamp))
        result = fuse_report(semantics(), history, 'lstm_future')
        assert result['assessments'][0]['future_prediction']['future_motion'] == 'UNCERTAIN'


def test_heuristic_or_unreliable_prediction_cannot_be_presented_as_trained_lstm():
    for source, reliable in [('heuristic', True), ('lstm', False)]:
        history = PredictionHistory()
        history.add(tracks(10.0, source, reliable))
        result = fuse_report(semantics(), history, 'lstm_future')
        assert result['assessments'][0]['future_prediction']['future_motion'] == 'UNCERTAIN'


def test_diagnostic_does_not_override_and_invalid_vqa_is_not_repaired_by_lstm():
    history = PredictionHistory()
    history.add(tracks(10.0))
    original = semantics()
    assert fuse_report(original, history)['assessments'][0]['future_prediction']['future_motion'] == 'STATIONARY'
    original['validation_errors'] = ['missing person']
    result = fuse_report(original, history, 'lstm_future')
    assert result['validation_errors'] == ['missing person']
    assert result['assessments'][0]['future_prediction']['future_motion'] == 'STATIONARY'


def test_other_camera_identity_is_never_used():
    history = PredictionHistory()
    history.add(tracks(10.0))
    report = semantics()
    report['assessments'][0]['track_id'] = 'R1'
    report['assessments'][0]['camera'] = 'right'
    report['metadata']['snapshot_stamps_s']['right'] = 10.0
    assert fuse_report(report, history, 'lstm_future')['assessments'][0]['future_prediction']['future_motion'] == 'UNCERTAIN'
