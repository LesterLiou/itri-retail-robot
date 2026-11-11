"""Direct temporal reports and motion decisions, with no LLM dependency."""
from copy import deepcopy
import math

from .temporal import LABELS


def analyze_tracks(source, mode, confidence_threshold=0.6):
    if mode not in {'heuristic', 'lstm'}:
        raise ValueError('Direct analysis requires heuristic or trained lstm mode')
    if not isinstance(source, dict) or not isinstance(source.get('cameras'), dict):
        raise ValueError('Tracking report needs a cameras object')
    for camera in source['cameras'].values():
        stamp = camera.get('stamp_s')
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            raise ValueError('Camera timestamp must be finite')
    assessments, stamps = [], {}
    source_ready = source.get('availability') == 'READY' and set(source.get('cameras', {})) == {'left', 'right'}
    for side, camera in source.get('cameras', {}).items():
        stamps[side] = camera['stamp_s']
        for track in camera.get('tracks', []):
            if not track.get('visible', False):
                continue
            prediction = deepcopy(track.get('temporal_prediction', {}))
            label = prediction.get('future_motion', 'UNCERTAIN')
            ready = prediction.get('sequence_ready', False)
            confidence = prediction.get('confidence')
            aligned = (isinstance(prediction.get('stamp_s'), (float, int))
                       and math.isfinite(prediction['stamp_s'])
                       and abs(prediction['stamp_s'] - camera['stamp_s']) <= 1e-6)
            if prediction.get('source') != mode or not aligned or label not in LABELS:
                state = 'UNAVAILABLE'
            elif not ready:
                state = 'WARMING_UP'
            elif mode == 'lstm' and (not isinstance(confidence, (int, float))
                    or not math.isfinite(confidence) or not confidence_threshold <= confidence <= 1):
                state = 'LOW_CONFIDENCE'
            elif not prediction.get('reliable', False):
                state = 'LOW_CONFIDENCE'
            else:
                state = 'READY'
            assessments.append({
                'track_id': track['track_id'], 'camera': side, 'temporal_state': state,
                'current_observation': {'body_orientation': 'UNCERTAIN', 'body_posture': 'UNCERTAIN',
                    'current_activity': 'UNCERTAIN', 'observation_source': 'not_inferred'},
                'future_prediction': {'future_motion': label if state == 'READY' else 'UNCERTAIN',
                    'motion_source': mode, 'confidence': confidence,
                    'prediction_horizon_s': prediction.get('prediction_horizon_s')},
                'temporal_prediction': prediction})
    states = {item['temporal_state'] for item in assessments}
    availability = ('UNAVAILABLE' if not source_ready or 'UNAVAILABLE' in states else
                    'WARMING_UP' if 'WARMING_UP' in states else 'READY')
    return {'schema_version': 'lightweight_temporal_v1', 'availability': availability,
        'assessments': assessments, 'validation_errors': [],
        'metadata': {'analysis_mode': 'direct_temporal', 'motion_source': mode,
            'llm_used': False, 'activity_model_available': False, 'snapshot_stamps_s': stamps,
            'detector_backend': source.get('backend', 'unknown')}}


def temporal_decision(report, stamp_s, age_s, timeout_s=2.0):
    items = [] if report is None else report.get('assessments', [])
    source = None if report is None else report['metadata']['motion_source']
    critical = []
    if report is None:
        action, reason, explanation = 'DATA_LOSS', 'awaiting_temporal_tracks', 'Person tracking input is missing; unable to assess.'
    elif not math.isfinite(age_s) or age_s < 0 or age_s > timeout_s:
        action, reason, explanation = 'DATA_LOSS', 'temporal_tracks_stale', 'Person tracking input is stale; unable to assess.'
    elif report['availability'] == 'UNAVAILABLE':
        action, reason, explanation = 'DATA_LOSS', 'temporal_prediction_unavailable', 'Temporal prediction is missing, malformed, misaligned or from the wrong model; unable to assess.'
    elif not items:
        action, reason, explanation = 'NO_RESPONSE', 'no_person_detected', 'No currently visible person; LLM is disabled.'
    else:
        unavailable = [i for i in items if i['temporal_state'] in {'LOW_CONFIDENCE', 'UNAVAILABLE'}]
        warning = [i for i in items if (
            i['temporal_state'] == 'READY' and i['future_prediction']['future_motion'] in {'TOWARD_CAMERA', 'UNCERTAIN'})]
        warming = [i for i in items if i['temporal_state'] == 'WARMING_UP']
        if unavailable:
            action, reason = 'DATA_LOSS', 'temporal_prediction_unreliable'
            explanation = 'No reliable temporal prediction; unable to assess danger.'
            critical = [i['track_id'] for i in unavailable]
        elif warning:
            action, reason = 'DANGER_WARNING', 'temporal_approach_or_uncertainty'
            explanation = 'Direct temporal result: approaching camera or insufficient prediction confidence; no LLM request.'
            critical = [i['track_id'] for i in warning]
        elif warming:
            action, reason = 'OBSERVE', 'temporal_history_warming_up'
            explanation = 'Collecting continuous per-person history; no valid prediction yet. LLM is disabled.'
            critical = [i['track_id'] for i in warming]
        else:
            action, reason = 'CONTINUE', 'temporal_motion_clear'
            explanation = 'All current temporal directions are away, lateral or stationary; research motion rule only.'
    return {'stamp_s': stamp_s, 'action': action, 'reason_code': reason, 'explanation': explanation,
        'critical_tracks': critical, 'decision_mode': 'direct_temporal',
        'decision_authority': 'lightweight_temporal_motion_policy', 'prediction_source': source,
        'geometry_age_s': round(age_s, 3) if report and math.isfinite(age_s) else None, 'semantics_age_s': None,
        'llm_used': False, 'metric_coordinates': False, 'zone_logic_enabled': False,
        'data_available': action != 'DATA_LOSS',
        'priority': 'DATA_LOSS (unable to assess) > motion warning > warmup > continue; fresh no person -> no response'}


def camera_summary(report, side):
    mode = report.get('metadata', {}).get('motion_source', 'unknown')
    model = 'TRAINED LSTM' if mode == 'lstm' else 'BBOX RULE BASELINE (not LSTM)'
    items = [i for i in report.get('assessments', []) if i.get('camera') == side]
    lines = [model]
    if not items:
        lines.append('No valid track data' if report.get('availability') == 'UNAVAILABLE'
                     else 'No visible person')
    for item in items:
        state = item['temporal_state']
        prediction = item['future_prediction']
        confidence = prediction.get('confidence')
        detail = (f"NEXT: {prediction['future_motion']}" if state == 'READY' else state)
        if mode == 'lstm' and isinstance(confidence, (float, int)):
            detail += f' | score={confidence:.2f}'
        lines.append(f"{item['track_id']}: {detail}")
    lines.append('Posture/activity: not inferred by this model')
    if report.get('availability') == 'UNAVAILABLE':
        lines.append('DATA LOSS: unable to assess')
        level = 'unknown'
    elif any(i['temporal_state'] in {'UNAVAILABLE', 'LOW_CONFIDENCE'} for i in items):
        level = 'unknown'
    elif any(
           (i['temporal_state'] == 'READY' and i['future_prediction']['future_motion'] in {'TOWARD_CAMERA', 'UNCERTAIN'}) for i in items):
        level = 'warning'
    elif any(i['temporal_state'] == 'WARMING_UP' for i in items) or not items:
        level = 'unknown'
    else:
        level = 'clear'
    return '\n'.join(lines), level
