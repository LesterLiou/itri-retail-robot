from copy import deepcopy

from lightweight_risk_vqa.direct_temporal import analyze_tracks, camera_summary, temporal_decision


def tracks(mode='lstm', motion='TOWARD_CAMERA', ready=True, reliable=True, confidence=0.9):
    return {'availability': 'READY', 'backend': 'yolo_bytetrack', 'cameras': {
        'left': {'stamp_s': 10.0, 'tracks': [{'track_id': 'L1', 'visible': True,
            'temporal_prediction': {'source': mode, 'stamp_s': 10.0,
                'future_motion': motion, 'sequence_ready': ready, 'reliable': reliable,
                'confidence': confidence, 'prediction_horizon_s': 1.0}}]},
        'right': {'stamp_s': 10.0, 'tracks': []}}}


def decide(source, mode='lstm', age=0.1):
    report = analyze_tracks(source, mode)
    return report, temporal_decision(report, 10.1, age)


def test_trained_lstm_decides_without_vqa_metadata_or_response():
    source = tracks()
    before = deepcopy(source)
    report, decision = decide(source)
    assert decision['action'] == 'DANGER_WARNING'
    assert decision['reason_code'] == 'temporal_approach_or_uncertainty'
    assert decision['prediction_source'] == 'lstm'
    assert not decision['llm_used'] and not report['metadata']['llm_used']
    assert source == before
    assert 'VQA' not in decision['explanation']


def test_clear_motion_continues_and_missing_history_only_observes():
    _, decision = decide(tracks(motion='AWAY_FROM_CAMERA'))
    assert decision['action'] == 'CONTINUE'
    report, decision = decide(tracks(ready=False, reliable=False, confidence=None))
    assert report['availability'] == 'WARMING_UP'
    assert decision['action'] == 'OBSERVE'
    assert decision['reason_code'] == 'temporal_history_warming_up'


def test_no_people_is_only_no_response_when_fresh():
    source = tracks()
    source['cameras']['left']['tracks'] = []
    _, decision = decide(source)
    assert decision['action'] == 'NO_RESPONSE'
    _, decision = decide(source, age=5.0)
    assert decision['reason_code'] == 'temporal_tracks_stale'
    assert decision['action'] == 'DATA_LOSS'
    assert decision['data_available'] is False


def test_low_confidence_and_wrong_model_cannot_claim_lstm_success():
    report, decision = decide(tracks(motion='STATIONARY', confidence=0.2))
    assert report['assessments'][0]['future_prediction']['future_motion'] == 'UNCERTAIN'
    assert decision['action'] == 'DATA_LOSS'
    report, decision = decide(tracks(mode='heuristic', motion='STATIONARY'))
    assert report['availability'] == 'UNAVAILABLE'
    assert decision['reason_code'] == 'temporal_prediction_unavailable'
    assert decision['action'] == 'DATA_LOSS'


def test_misaligned_prediction_and_partial_camera_report_are_unavailable():
    source = tracks()
    source['cameras']['left']['tracks'][0]['temporal_prediction']['stamp_s'] = 9.8
    report, _ = decide(source)
    assert report['availability'] == 'UNAVAILABLE'
    source = tracks()
    del source['cameras']['right']
    report, _ = decide(source)
    assert report['availability'] == 'UNAVAILABLE'


def test_ui_labels_rules_and_does_not_invent_activity_or_posture():
    report, decision = decide(tracks(mode='heuristic', confidence=None), mode='heuristic')
    text, _ = camera_summary(report, 'left')
    assert 'BBOX RULE BASELINE (not LSTM)' in text
    assert 'Posture/activity: not inferred' in text
    assert report['metadata']['activity_model_available'] is False
    report, _ = decide(tracks())
    text, _ = camera_summary(report, 'left')
    assert 'TRAINED LSTM' in text and 'TOWARD_CAMERA' in text
