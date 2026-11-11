"""Region-free robot VQA contract and optional causal YOLO measurements.

Measurements are model inputs, never a second source of robot actions.
"""
from collections import deque
import json
import math
import re

from .availability import live_decision
from .camera_policy import _iou

# Keep the checkpoint's training query as the default: paired testing did not
# establish a quality improvement from the shorter candidate.
ROBOT_QUERY = ('This is a robot forward camera. Use the current image and visual memory to assess people '
 'moving relative to the robot. Describe visible movement briefly; omit gender and clothing '
 'unless clearly supported by the original image. Mask colors are annotations. Return one JSON '
 'object in this order: observation, motion, approach_intent, collision_risk, action, '
 'explanation. Use short observation and explanation phrases consistent with the choices. '
 'motion: TOWARD_CAMERA, AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, STATIONARY, or UNCERTAIN. '
 'approach_intent: LIKELY, UNLIKELY, or UNCERTAIN. collision_risk: IMMINENT, ELEVATED, LOW, or '
 'UNCERTAIN. Decision policy: if motion is TOWARD_CAMERA OR approach_intent is LIKELY, set '
 'collision_risk to ELEVATED and action to DANGER_WARNING. Use IMMINENT and STOP instead only '
 'when visible evidence supports an imminent collision. LOW and CONTINUE are allowed only for '
 'AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, or STATIONARY with UNLIKELY approach intent and no '
 'visible hazard. Otherwise use UNCERTAIN and OBSERVE. Explain the same motion and decision '
 'you selected. Infer intent cautiously; size alone does not prove intent. Consider camera '
 'motion and occlusion. YOLO measurements, if present, are noisy supporting evidence. Use no '
 'fixed zones. Invent no distances or future observations.')

COMPACT_ROBOT_QUERY = (
    'This is a robot forward camera. Use the current image and visual memory to assess '
    'people moving relative to the robot. Return one JSON object in this order: '
    'observation, motion, approach_intent, collision_risk, action, explanation. '
    'Use one short sentence each for observation and explanation; describe evidence, not instructions. '
    'Assess the highest-risk visible person; identify left/center/right when possible. '
    'Do not combine different people into one motion. '
    'motion: TOWARD_CAMERA, AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, STATIONARY, or UNCERTAIN. '
    'approach_intent: LIKELY, UNLIKELY, or UNCERTAIN. '
    'collision_risk: IMMINENT, ELEVATED, LOW, or UNCERTAIN. '
    'Observed approach requires ELEVATED and DANGER_WARNING; clear imminent collision requires IMMINENT and STOP. '
    'LOW and CONTINUE require confirmed non-approach and no visible hazard. '
    'If direction evidence is insufficient, use UNCERTAIN for motion, approach_intent and collision_risk, '
    'and OBSERVE for action; never call missing evidence LOW. '
    'Use past-to-now changes. YOLO with fewer than two samples cannot establish direction. '
    'Account for camera motion and occlusion. Do not guess intention, distance, clothing or future facts.'
)

RISK_ACTIONS = {'IMMINENT': 'STOP', 'ELEVATED': 'DANGER_WARNING',
                'LOW': 'CONTINUE', 'UNCERTAIN': 'OBSERVE'}
MOTIONS = {'TOWARD_CAMERA', 'AWAY_FROM_CAMERA', 'LATERAL_ACROSS_VIEW', 'STATIONARY', 'UNCERTAIN'}


def json_object_end(text):
    """Return the first complete top-level object boundary, without guessing fields."""
    start = len(text) - len(text.lstrip())
    if start == len(text) or text[start] != '{':
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return None
    return end if isinstance(value, dict) else None


class StopAfterJsonObject:
    """HF generation stopping callable; input IDs are generated tokens only."""
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def __call__(self, input_ids, scores, **kwargs):
        return all(json_object_end(self.tokenizer.decode(row, skip_special_tokens=True)) is not None
                   for row in input_ids)



def has_insufficient_motion_evidence(text):
    """Narrow contradiction check, not a motion classifier or a replacement action."""
    normalized = ' '.join(str(text).lower().split())
    return bool(re.search(
        r'\b(?:insufficient|not enough) (?:temporal |visual |motion )?evidence\b'
        r'|(?<!no )\bmore (?:temporal |visual |motion )?evidence (?:is )?(?:needed|required)\b'
        r'|\b(?:cannot|unable to) (?:determine|confirm|assess) (?:the )?(?:motion|direction|approach)\b'
        r'|\b(?:motion|direction) (?:is |remains )?(?:unclear|unknown|uncertain)\b'
        r'|\b(?:approach|motion|direction) (?:is )?not established\b'
        r'|\bmore (?:recent |temporal )?(?:samples|history) (?:are |is )?(?:needed|required|not available)\b',
        normalized))

def robot_schema_error(parsed):
    if not isinstance(parsed, dict):
        return 'robot VQA response is not a JSON object'
    for key in ('observation', 'explanation'):
        if not isinstance(parsed.get(key), str) or not parsed[key].strip():
            return f'robot VQA missing {key}'
        if parsed[key].strip().lower() in {'brief current visible activity', 'brief visible temporal evidence',
                                                   'at most 8 words about visible activity', 'at most 12 words of evidence'}:
            return f'robot VQA copied schema placeholder in {key}'
        if re.search(r"['\"](?:observation|motion|approach_intent|collision_risk|action|explanation)['\"]\s*:", parsed[key]):
            return f'robot VQA copied JSON fields inside {key}'
        if re.search(r"([^a-zA-Z0-9\s]{1,4})\1{4,}", parsed[key]):
            return f'robot VQA repetitive punctuation in {key}'
        if parsed[key].strip() in MOTIONS | set(RISK_ACTIONS) | set(RISK_ACTIONS.values()) | {'LIKELY', 'UNLIKELY'}:
            return f'robot VQA {key} must be descriptive text, not an enum label'
    if parsed.get('motion') not in MOTIONS:
        return 'robot VQA invalid motion'
    if parsed.get('approach_intent') not in {'LIKELY', 'UNLIKELY', 'UNCERTAIN'}:
        return 'robot VQA missing or invalid approach_intent; state-only checkpoint is not sufficient'
    risk = parsed.get('collision_risk')
    if risk not in RISK_ACTIONS:
        return 'robot VQA missing or invalid collision_risk'
    if parsed.get('action') != RISK_ACTIONS[risk]:
        return 'robot VQA action contradicts collision_risk'
    approaching = parsed['motion'] == 'TOWARD_CAMERA' or parsed['approach_intent'] == 'LIKELY'
    if approaching and risk not in {'ELEVATED', 'IMMINENT'}:
        return 'robot VQA approaching person requires at least ELEVATED risk'
    if risk == 'LOW' and any(has_insufficient_motion_evidence(parsed[key]) for key in ('observation', 'explanation')):
        return 'robot VQA LOW risk contradicts insufficient motion evidence in explanation'
    if risk == 'LOW' and (parsed['motion'] == 'UNCERTAIN' or parsed['approach_intent'] != 'UNLIKELY'):
        return 'robot VQA LOW risk requires known motion and UNLIKELY approach intent'
    return ''


def robot_live_decision(state, now, mode, max_frame_age_s=2., max_result_age_s=8.):
    if mode not in {'vqa-only', 'yolo-assisted'}:
        raise ValueError('Unsupported robot VQA mode')
    # Never index an unvalidated action while checking input freshness.
    if isinstance(state.get('vqa_parsed'), dict) and 'action' not in state['vqa_parsed']:
        state = {**state, 'vqa_error': state.get('vqa_error') or robot_schema_error(state['vqa_parsed'])}
    result = live_decision(state, now, max_frame_age_s, max_result_age_s)
    if not result['data_available']:
        return result
    parsed = state['vqa_parsed']
    error = robot_schema_error(parsed)
    if error:
        return {'action': 'DATA_LOSS', 'reason_code': error, 'data_available': False,
                'decision_authority': 'runtime_data_health'}
    return {**result, 'reason_code': 'vqa_' + parsed['collision_risk'].lower(),
            'decision_authority': 'embedded_vqa' if mode == 'vqa-only' else 'embedded_vqa_with_yolo_hints',
            'motion': parsed['motion'], 'approach_intent': parsed['approach_intent'],
            'collision_risk': parsed['collision_risk'],
            'decoding': state.get('vqa_decoding', 'free'),
            'action_constraint': ('risk_action_pair_and_approach_warning'
                                  if state.get('vqa_decoding') == 'constrained' else None),
            'auxiliary_evidence_used': state.get('vqa_yolo_hints')}


def query_with_hints(query, hints):
    if hints is None:
        return query
    # Retain interpretable numeric names; omit repeated metadata and warnings
    # already stated once in ROBOT_QUERY. Full hints stay in the runtime report.
    compact = {key: hints[key] for key in ('people_detected', 'people_omitted', 'people') if key in hints}
    return query + '\nYOLO past->NOW (normalized xyxy):\n' + json.dumps(
        compact, separators=(',', ':'), allow_nan=False)



class PersonMotionHints:
    """Small IoU-linked histories. No image zones, action rules or intention labels."""
    def __init__(self, history_s=4., max_gap_s=2.5, max_people=6):
        if (not math.isfinite(history_s) or history_s <= 0 or
                not math.isfinite(max_gap_s) or max_gap_s <= 0 or
                not isinstance(max_people, int) or isinstance(max_people, bool) or max_people <= 0):
            raise ValueError('Hint history, gap and maximum people must be positive')
        self.history_s, self.max_gap_s, self.max_people = history_s, max_gap_s, max_people
        self.reset()

    def reset(self):
        self.tracks, self.next_id, self.last_at = {}, 1, None

    def update(self, boxes, timestamp):
        if not math.isfinite(timestamp):
            raise ValueError('Invalid YOLO capture timestamp')
        if self.last_at is not None and (timestamp <= self.last_at or timestamp - self.last_at > self.max_gap_s):
            self.reset()
        self.last_at = timestamp
        for box in boxes:
            if (len(box) != 4 or any(not math.isfinite(v) for v in box)
                    or not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1):
                raise ValueError('Expected normalized YOLO xyxy boxes')
        ambiguous_new = {i for i, box in enumerate(boxes)
                         if sum(_iou(t[-1][1], box) >= .15 for t in self.tracks.values()) > 1}
        ambiguous_old = {tid for tid, t in self.tracks.items()
                         if sum(_iou(t[-1][1], box) >= .15 for box in boxes) > 1}
        pairs = sorted(((_iou(t[-1][1], box), tid, i) for tid, t in self.tracks.items()
                        for i, box in enumerate(boxes)), reverse=True)
        matches, used = {}, set()
        for score, tid, i in pairs:
            if score >= .15 and i not in matches and tid not in used and i not in ambiguous_new and tid not in ambiguous_old:
                matches[i] = tid
                used.add(tid)
        current, people = {}, []
        for i, box in enumerate(boxes):
            tid = matches.get(i)
            if tid is None:
                tid, self.next_id = self.next_id, self.next_id + 1
                history = deque()
            else:
                history = self.tracks[tid]
                ratio = (box[3] - box[1]) / (history[-1][1][3] - history[-1][1][1])
                if not .55 <= ratio <= 1.8:
                    history = deque()
            history.append((timestamp, list(box)))
            while history and timestamp - history[0][0] > self.history_s:
                history.popleft()
            current[tid] = history
            span = timestamp - history[0][0]
            first = history[0][1]
            delta = [((box[j] + box[j + 2]) - (first[j] + first[j + 2])) / 2 for j in (0, 1)]
            growth = (box[3] - box[1]) / (first[3] - first[1]) - 1
            people.append({'id': tid, 'bbox': [round(v, 3) for v in box],
                           'samples': len(history), 'history_s': round(span, 3),
                           'center_delta_xy': [round(v, 3) for v in delta] if span > 0 else None,
                           'height_change_fraction': round(growth, 3) if span > 0 else None})
        self.tracks = current
        people.sort(key=lambda p: (p['bbox'][2] - p['bbox'][0]) * (p['bbox'][3] - p['bbox'][1]), reverse=True)
        return {'source': 'yolo', 'coordinates': 'normalized_image_xyxy',
                'people_detected': len(boxes), 'people_omitted': max(0, len(boxes) - self.max_people),
                'people': people[:self.max_people], 'warning': 'No depth, calibrated confidence or intention labels; camera motion may affect deltas.'}
