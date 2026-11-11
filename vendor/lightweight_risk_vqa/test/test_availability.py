import pytest

from lightweight_risk_vqa.availability import live_decision


def fresh():
    return {'frame_at': 10.0, 'vqa_evidence_at': 9.0, 'loop': 0,
            'requested_query': 'people', 'vqa_context': (0, 'people'),
            'vqa_parsed': {'action': 'DANGER_WARNING'}}


def test_semantic_danger_remains_separate_from_missing_data():
    assert live_decision(fresh(), 10.1)['action'] == 'DANGER_WARNING'
    assert live_decision({}, 10.1)['action'] == 'DATA_LOSS'


@pytest.mark.parametrize('changes', [
    {'vqa_error': 'model response is not a JSON object'},
    {'pipeline_error': 'MODEL ERROR'}, {'frame_at': 0},
    {'vqa_evidence_at': 0}, {'vqa_evidence_at': 11},
    {'vqa_evidence_at': float('nan')}, {'loop': 1},
    {'requested_query': 'different person'}, {'vqa_parsed': None},
])
def test_error_or_stale_evidence_never_reuses_old_warning(changes):
    state = {**fresh(), **changes}
    result = live_decision(state, 10.1)
    assert result['action'] == 'DATA_LOSS'
    assert not result['data_available']


def test_pause_and_end_are_observation_states():
    for key in ('paused', 'capture_complete'):
        assert live_decision({**fresh(), key: True}, 100)['action'] == 'OBSERVE'
