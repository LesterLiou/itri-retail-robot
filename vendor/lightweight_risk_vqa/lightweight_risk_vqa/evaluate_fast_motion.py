"""Held-out weak-motion head ablations and serial warmed latency, without Qwen calls."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

import numpy as np
import torch

from .audit_visual_grounding import intervene
from .fast_motion import head_labels
from .recovery_data import read_rows
from .temporal_vqa_infer import load_frames,time_deltas
from .vqa_metrics import classification_metrics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='cuda:0')
    args=p.parse_args()
    using_cuda=torch.device(args.device).type=='cuda'
    def synchronize():
        if using_cuda:
            torch.cuda.synchronize()
    from light_foundation_vlm.checkpoint import load_bundle
    model,_,manifest=load_bundle(args.checkpoint,torch.bfloat16 if using_cuda else torch.float32)
    model.to(args.device).eval()
    labels=head_labels(manifest)
    rows=read_rows(args.manifest)
    results={}
    for condition in ['real','shuffle','repeat','last']:
        predictions=[]
        for row in rows:
            r=intervene(row,condition)
            frames=load_frames(r,args.manifest.parent,args.device)
            synchronize()
            start=time.perf_counter()
            output=model.predict_events(frames,dt=time_deltas(r,args.device))
            synchronize()
            latency=1000*(time.perf_counter()-start)
            prob=output['event_logits'].float().softmax(-1)[0]
            index=int(prob.argmax())
            predictions.append({'id':row['id'],'prediction':{'motion':labels[index]},
                'expected':{'motion':row['motion_candidate']},'probability_uncalibrated':float(prob[index]),
                'latency_ms':latency})
        metrics=classification_metrics(predictions,'motion',labels)
        covered=[r for r in predictions if r['probability_uncalibrated']>=.7 and r['prediction']['motion']!='UNCERTAIN']
        metrics.update(coverage_at_07=len(covered)/len(predictions),
                       accuracy_when_covered=(sum(r['prediction']==r['expected'] for r in covered)/len(covered) if covered else None),
                       label_distribution=dict(Counter(r['prediction']['motion'] for r in predictions)))
        results[condition]={'metrics':metrics,'predictions':predictions}
    # Benchmark one new frame with an existing h,c, identical 8-step state each trial.
    r=rows[0]
    frames=load_frames(r,args.manifest.parent,args.device)
    initial=model.predict_events(frames[:,:-1],dt=time_deltas(r,args.device)[:,:-1])['state']
    times=[]
    for i in range(55):
        synchronize()
        start=time.perf_counter()
        model.predict_events(frames[:,-1:],state=initial,dt=torch.ones(1,1,device=args.device))
        synchronize()
        if i>=5:
            times.append(1000*(time.perf_counter()-start))
    report={'checkpoint':str(args.checkpoint),'supervision':'weak geometry; not human motion/collision accuracy',
            'results':results,'incremental_latency':{'samples':len(times),'p50_ms':float(np.percentile(times,50)),
            'p95_ms':float(np.percentile(times,95))},'majority_accuracy':max(Counter(r['motion_candidate'] for r in rows).values())/len(rows)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='results'}),flush=True)
    print(json.dumps({k:v['metrics'] for k,v in results.items()}),flush=True)


if __name__=='__main__':
    main()
