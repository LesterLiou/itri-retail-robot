"""Observed state labels; robot decisions and future motion are separate tasks."""
import json

ACTIVITIES = {'WALKING', 'STANDING', 'SITTING', 'BENDING', 'REACHING',
              'PICKING_ITEM', 'PLACING_ITEM', 'OTHER', 'UNCERTAIN'}
TRANSITIONS = {'STEADY', 'STARTED_WALKING', 'STOPPED_WALKING', 'UNCERTAIN'}
STATE_QUERY = (
    'The target person is outlined in green in chronological frames ending at NOW. '
    'Describe the observed activity at NOW and its transition across the sampled history. '
    'Return exactly one JSON object with keys current_activity, observed_transition, '
    'motion, action, explanation. current_activity: WALKING, STANDING, SITTING, BENDING, '
    'REACHING, PICKING_ITEM, PLACING_ITEM, OTHER, UNCERTAIN. observed_transition: STEADY, '
    'STARTED_WALKING, STOPPED_WALKING, UNCERTAIN. Set motion to UNCERTAIN and action '
    'to OBSERVE: this task does not assess future motion or robot safety.'
)


def activity_transition(labels):
    if not labels or any(label not in {'walking', 'standing'} for label in labels):
        return None
    changes = [b for a, b in zip(labels, labels[1:]) if a != b]
    if len(changes) > 1:
        return None
    transition = ('STEADY' if not changes else
                  'STARTED_WALKING' if changes[0] == 'walking' else 'STOPPED_WALKING')
    return labels[-1].upper(), transition


def state_answer(activity, transition, explanation=None):
    if activity not in ACTIVITIES or transition not in TRANSITIONS:
        raise ValueError('Invalid observed state label')
    return json.dumps({'current_activity': activity, 'observed_transition': transition,
        'motion': 'UNCERTAIN', 'action': 'OBSERVE',
        'explanation': explanation or f'The target is {activity.lower()}; sampled activity transition: {transition.lower()}.'},
        separators=(',', ':'))


def state_schema_error(parsed):
    if not isinstance(parsed,dict):
        return 'state response is not a JSON object'
    if parsed.get('current_activity') not in ACTIVITIES:
        return 'invalid current_activity'
    if parsed.get('observed_transition') not in TRANSITIONS:
        return 'invalid observed_transition'
    if parsed.get('motion') != 'UNCERTAIN' or parsed.get('action') != 'OBSERVE':
        return 'state-only task must not claim future motion or robot safety'
    return ''
