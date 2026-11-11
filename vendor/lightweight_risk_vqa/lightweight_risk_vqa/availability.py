"""Runtime data health is separate from the model's semantic danger prediction."""
import math


def live_decision(state, now, max_frame_age_s=2.0, max_result_age_s=8.0):
    def result(action, reason, available=False):
        return {'action': action, 'reason_code': reason, 'data_available': available,
                'decision_authority': 'runtime_data_health' if not available else 'model_research_hint'}

    if state.get('pipeline_error') or state.get('vqa_error'):
        return result('DATA_LOSS', state.get('pipeline_error') or state['vqa_error'])
    if state.get('paused') or state.get('capture_complete'):
        return result('OBSERVE', 'paused' if state.get('paused') else 'video_complete')
    if state.get('frame_at') is None:
        return result('DATA_LOSS', 'camera_frame_missing')
    age = now - state['frame_at']
    if not math.isfinite(age) or age < 0 or age > max_frame_age_s:
        return result('DATA_LOSS', 'camera_frame_stale')
    if not isinstance(state.get('vqa_parsed'), dict) or state.get('vqa_evidence_at') is None:
        return result('DATA_LOSS', 'valid_vqa_result_missing')
    if state.get('vqa_context') != (state.get('loop'), state.get('requested_query')):
        return result('DATA_LOSS', 'vqa_context_changed')
    age = now - state['vqa_evidence_at']
    if not math.isfinite(age) or age < 0 or age > max_result_age_s:
        return result('DATA_LOSS', 'vqa_evidence_stale')
    return result(state['vqa_parsed']['action'], 'fresh_model_result', True)
