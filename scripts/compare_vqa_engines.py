#!/usr/bin/env python3
"""GPU offline paired check; never imports ROS or publishes motion commands."""
import base64
import contextlib
from collections import Counter
import json
import os
from pathlib import Path
import statistics
import sys
import time
import cv2
import torch
sys.path.insert(0,'/app')
from vqa_worker import Backend

torch.set_num_threads(2)
root=Path('/evaluation');frames=json.loads((root/'frames.json').read_text())
with contextlib.redirect_stdout(sys.stderr):
    backend=Backend(os.environ['VQA_CHECKPOINT'],os.environ.get('VQA_DEVICE','cuda:0'))
results={}
for profile in ('sentence_safe_hf','sentence_safe_cached'):
    backend.states.clear();backend.hints.clear();rows=[]
    for frame in frames:
        image=cv2.imread(str(root/frame['file']));ok,jpeg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,95]);assert ok
        with contextlib.redirect_stdout(sys.stderr):
            result=backend.infer(dict(camera_id='chest',stamp=frame['stamp'],source_epoch='offline:'+frame['group']+':'+profile,
                inference_mode='yolo-assisted',decoding_profile='sentence_safe',generation_engine=('hf' if profile.endswith('_hf') else 'grammar_cached'),jpeg=base64.b64encode(jpeg).decode()))
        result['file']=frame['file'];rows.append(result)
    results[profile]=rows
summary={profile:dict(valid=sum(row['valid'] for row in rows),count=len(rows),
    errors=dict(Counter(row['reason'] for row in rows if row['reason'])),
    median_ms=statistics.median(row['inference_ms'] for row in rows),max_ms=max(row['inference_ms'] for row in rows)) for profile,rows in results.items()}
changes=[]
for old,new in zip(results['sentence_safe_hf'],results['sentence_safe_cached']):
    if old['raw_action']!=new['raw_action']:changes.append(dict(file=old['file'],baseline=old['raw_action'],sentence_safe=new['raw_action']))
summary['action_changes']=changes
summary['raw_exact_matches']=sum(a['raw']==b['raw'] for a,b in zip(results['sentence_safe_hf'],results['sentence_safe_cached']))
print(json.dumps(dict(summary=summary,results=results,model_id=backend.model_id,prompt_id=backend.prompt_id),indent=2))
