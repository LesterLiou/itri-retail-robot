"""Optional ConvLSTM motion readout. Qwen remains the VQA provider.

This is an image-motion research hint trained on weak geometry, never a collision detector.
"""
import math

from .prepare_causal_motion import CLASSES


def head_labels(manifest):
    text = manifest.get('metadata',{}).get('training_args',{}).get('event_label_names','')
    labels = text.split(',') if text else []
    if labels != CLASSES:
        raise ValueError('Fast head requires the explicit ordered weak-motion label contract')
    return labels


def fast_motion_decision(state,now,threshold=.7,max_frame_age_s=2.,max_result_age_s=2.):
    base={'data_available':False,'decision_authority':'temporal_motion_head_weak_proxy'}
    def result(action,reason,available=False,**extra):
        return {**base,'action':action,'reason_code':reason,'data_available':available,**extra}
    if state.get('pipeline_error'):
        return result('DATA_LOSS',state['pipeline_error'])
    if state.get('paused') or state.get('capture_complete'):
        return result('OBSERVE','paused_or_complete')
    for key,limit in [('frame_at',max_frame_age_s),('segmentation_evidence_at',max_result_age_s)]:
        stamp=state.get(key)
        if stamp is None or not math.isfinite(now-stamp) or not 0 <= now-stamp <= limit:
            return result('DATA_LOSS',key+'_missing_or_stale')
    if state.get('segmentation_context') != (state.get('loop'),state.get('requested_query')):
        return result('DATA_LOSS','motion_context_changed')
    metrics=state.get('metrics',{})
    head=metrics.get('fast_motion')
    if not head:
        return result('DATA_LOSS','fast_motion_head_missing')
    if metrics.get('memory_steps_since_reset',0) < 8:
        return result('OBSERVE','motion_history_warmup',True)
    confidence=head.get('probability_uncalibrated',0)
    motion=head.get('motion')
    if not math.isfinite(confidence) or not 0 <= confidence <= 1 or motion not in CLASSES:
        return result('DATA_LOSS','invalid_motion_head_output')
    if confidence < threshold or motion=='UNCERTAIN':
        return result('OBSERVE','motion_head_uncertain',True,motion=motion)
    action='DANGER_WARNING' if motion=='TOWARD_CAMERA' else 'CONTINUE'
    return result(action,'weak_motion_policy_proxy',True,motion=motion,
                  probability_uncalibrated=confidence,
                  limitation='No collision/intention GT; STOP cannot be inferred by this head')
