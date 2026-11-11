import json
import math

import pytest

from lightweight_risk_vqa.robot_vqa import (
    PersonMotionHints, query_with_hints, robot_live_decision, robot_schema_error,
    StopAfterJsonObject, json_object_end,
)


def answer(risk='ELEVATED', action='DANGER_WARNING'):
    return {'observation': 'A person walks toward the robot.', 'motion': 'TOWARD_CAMERA',
            'approach_intent': 'LIKELY', 'collision_risk': risk, 'action': action,
            'explanation': 'The person moves closer over the observed history.'}


def state(parsed=None):
    return {'frame_at': 10., 'vqa_evidence_at': 9., 'loop': 0,
            'requested_query': 'robot', 'vqa_context': (0, 'robot'),
            'vqa_parsed': answer() if parsed is None else parsed}


@pytest.mark.parametrize('risk,action', [('IMMINENT', 'STOP'), ('ELEVATED', 'DANGER_WARNING'),
                                      ('LOW', 'CONTINUE'), ('UNCERTAIN', 'OBSERVE')])
def test_vqa_itself_supplies_all_four_risk_actions(risk, action):
    parsed = answer(risk, action)
    if risk in {'LOW', 'UNCERTAIN'}:
        parsed.update(motion='STATIONARY', approach_intent='UNLIKELY')
    assert robot_schema_error(parsed) == ''
    result = robot_live_decision(state(parsed), 10.1, 'vqa-only')
    assert result['action'] == action
    assert result['decision_authority'] == 'embedded_vqa'


def test_state_only_or_contradictory_model_is_not_promoted_to_robot_decision():
    for parsed in ({'action': 'OBSERVE', 'current_activity': 'WALKING'},
                   answer('LOW', 'STOP'), {**answer(), 'approach_intent': None},
                   {**answer(), 'observation': ''}, {**answer(), 'motion': 'FORWARD'}):
        assert robot_schema_error(parsed)
        assert robot_live_decision(state(parsed), 10.1, 'vqa-only')['action'] == 'DATA_LOSS'
    assert robot_schema_error({**answer(), 'observation': 'brief current visible activity'})
    parsed = answer()
    del parsed['action']
    assert robot_live_decision(state(parsed), 10.1, 'vqa-only')['action'] == 'DATA_LOSS'


def test_approach_low_and_enum_observation_are_invalid_even_in_free_mode():
    for parsed in (answer('LOW', 'CONTINUE'), {**answer(), 'observation': 'TOWARD_CAMERA'}):
        assert robot_schema_error(parsed)
        assert robot_live_decision(state(parsed), 10.1, 'vqa-only')['action'] == 'DATA_LOSS'


def test_yolo_is_evidence_and_never_overrides_vqa_action():
    hints = PersonMotionHints()
    hints.update([[.1, .1, .3, .4]], 8.)
    evidence = hints.update([[.1, .1, .3, .6]], 9.)
    parsed = {**answer('UNCERTAIN', 'OBSERVE'), 'motion': 'UNCERTAIN', 'approach_intent': 'UNCERTAIN'}
    current = {**state(parsed), 'vqa_yolo_hints': evidence}
    result = robot_live_decision(current, 10.1, 'yolo-assisted')
    assert result['action'] == 'OBSERVE'
    assert result['decision_authority'] == 'embedded_vqa_with_yolo_hints'
    assert result['auxiliary_evidence_used'] == evidence
    assert 'action' not in evidence and 'approach_intent' not in evidence
    for changes in ({'vqa_error': 'bad JSON'}, {'frame_at': 0}, {'vqa_evidence_at': 0},
                    {'loop': 1}, {'requested_query': 'new query'}):
        result = robot_live_decision({**current, **changes}, 10.1, 'yolo-assisted')
        assert result['action'] == 'DATA_LOSS'
        assert not result['data_available']


def test_hints_use_caused_past_to_current_changes_without_zones_or_intentions():
    for x in (.01, .4, .8):
        hints = PersonMotionHints()
        hints.update([[x, .1, x + .15, .4]], 1.)
        report = hints.update([[x, .1, x + .15, .55]], 2.)
        person = report['people'][0]
        assert person['history_s'] == 1.
        assert person['samples'] == 2
        assert person['height_change_fraction'] == .5
        assert person['center_delta_xy'] == [0., .075]
        assert report['people_detected'] == 1
        text = query_with_hints('question', report)
        compact = json.loads(text.split('\n')[-1])
        assert compact == {k:report[k] for k in ('people_detected','people_omitted','people')}
        assert len(text) < len('question' + json.dumps(report))
        for word in ('stop_roi', 'corridor', 'collision_risk', 'action'):
            assert f'"{word}":' not in text
    assert query_with_hints('question', None) == 'question'


def test_missing_merge_split_clock_gap_and_reset_clear_motion_evidence():
    hints = PersonMotionHints()
    boxes = [[.3, .2, .45, .6], [.5, .2, .65, .6]]
    hints.update(boxes, 0.)
    merged = hints.update([[.3, .1, .65, .65]], .5)
    assert merged['people'][0]['samples'] == 1
    split = hints.update(boxes, 1.)
    assert all(p['samples'] == 1 for p in split['people'])
    assert hints.update([], 1.5)['people_detected'] == 0
    assert hints.update(boxes, 2.)['people'][0]['samples'] == 1
    assert hints.update(boxes, 10.)['people'][0]['samples'] == 1
    assert hints.update(boxes, 9.)['people'][0]['samples'] == 1
    hints.reset()
    assert hints.update(boxes, 11.)['people'][0]['samples'] == 1
    with pytest.raises(ValueError):
        hints.update(boxes, math.nan)


def test_hint_prompt_budget_and_invalid_boxes_are_explicit():
    hints = PersonMotionHints(max_people=1)
    report = hints.update([[.1, .1, .2, .4], [.5, .1, .8, .7]], 1.)
    assert report['people_detected'] == 2 and report['people_omitted'] == 1
    assert len(report['people']) == 1
    for box in ([.1, .1, .1, .9], [.5, .1, .3, .8], [0., 0., 1., math.nan]):
        with pytest.raises(ValueError):
            hints.update([box], 2.)


def test_json_completion_uses_real_json_boundaries_and_never_repairs_answers():
    obj = json.dumps({'explanation': 'A brace } and an escaped "quote" are text.', 'nested': {'a': 1}})
    for prefix in ('', ' \n'):
        assert json_object_end(prefix + obj + '{"unwanted":1}') == len(prefix + obj)
    for invalid in ('{"observation":', '{"text":"unfinished}', 'Not JSON', '[{}]', ''):
        assert json_object_end(invalid) is None
    assert robot_schema_error(json.loads(obj))  # A complete object still needs the full schema.
    tokenizer = type('Tokenizer', (), {'decode': lambda self, value, **kwargs: value})()
    stop = StopAfterJsonObject(tokenizer)
    assert stop([obj], None)
    assert not stop(['{"observation":'], None)


@pytest.mark.parametrize('reason', [
    'Insufficient temporal evidence to determine direction.',
    'More evidence is needed to confirm approach.',
    'Cannot determine the direction.',
    'Direction is uncertain.',
])
def test_low_with_explicit_missing_evidence_is_rejected_without_rewriting_raw(reason):
    parsed = {**answer('LOW','CONTINUE'), 'motion':'STATIONARY',
              'approach_intent':'UNLIKELY', 'explanation':reason}
    original = dict(parsed)
    result = robot_live_decision(state(parsed),10.1,'vqa-only')
    assert result['action'] == 'DATA_LOSS'
    assert 'insufficient motion evidence' in result['reason_code']
    assert parsed == original
    parsed.update(motion='UNCERTAIN',approach_intent='UNCERTAIN',
                  collision_risk='UNCERTAIN',action='OBSERVE')
    assert robot_schema_error(parsed) == ''


def test_non_approach_is_not_confused_with_missing_evidence():
    from lightweight_risk_vqa.robot_vqa import has_insufficient_motion_evidence, COMPACT_ROBOT_QUERY
    assert not has_insufficient_motion_evidence('No observed approach over the history.')
    assert not has_insufficient_motion_evidence('No more evidence is needed.')
    assert 'highest-risk visible person' in COMPACT_ROBOT_QUERY
    assert 'never call missing evidence LOW' in COMPACT_ROBOT_QUERY


def test_schema_text_leaks_and_punctuation_loops_are_unavailable_not_valid_observations():
    for bad in ("A person.','motion':'STATIONARY'", "A person." + "'."*8):
        parsed = {**answer(), 'observation':bad}
        assert robot_schema_error(parsed)
        assert robot_live_decision(state(parsed),10.1,'vqa-only')['action'] == 'DATA_LOSS'
