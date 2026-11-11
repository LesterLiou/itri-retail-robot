"""Same-NOW longer-stream smoke and actual batch/stream numerical equivalence.

No human motion/collision labels are available: this measures stability, not accuracy.
"""
import argparse
import json
from pathlib import Path

import cv2
import torch


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--video',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='cuda:0')
    args=p.parse_args()
    from .light_foundation_live_ui import TemporalSegmenter
    from .robot_vqa import ROBOT_QUERY
    segmenter=TemporalSegmenter(args.checkpoint,args.device,ROBOT_QUERY,person_mask=True,collect_boxes=False)
    segmenter.robot_decoding='free'
    cap=cv2.VideoCapture(str(args.video))
    fps=cap.get(cv2.CAP_PROP_FPS)
    total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if not cap.isOpened() or fps<=0:
        raise ValueError('Unreadable source video')
    end=(total-1)/fps
    max_steps=min(32,int(end/.5)+1)
    if max_steps<8:
        raise ValueError('Need at least 3.5 seconds of video')
    times=[end-.5*(max_steps-1-i) for i in range(max_steps)]
    frames=[]
    for stamp in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES,min(total-1,round(stamp*fps)))
        okay,frame=cap.read()
        if not okay:
            raise ValueError('Failed to decode a sampled frame')
        frames.append(frame)
    cap.release()
    results=[]
    for length in sorted({8,min(16,max_steps),max_steps}):
        segmenter.reset()
        sampled=frames[-length:]
        for index,frame in enumerate(sampled):
            _,metrics,answer=segmenter.process(frame,generate_answer=index==length-1,
                                    max_new_tokens=192,expect_robot=True)
        tensor=torch.stack([segmenter.preprocess(frame) for frame in sampled])[None].to(args.device)
        with torch.inference_mode():
            _,batch_state=segmenter.model.encode_clip(tensor,dt=torch.ones(1,length,device=args.device))
        difference=max(float((a-b).abs().max()) for a,b in zip(segmenter.state,batch_state))
        results.append({'steps':length,'same_now_s':end,'sample_interval_s':.5,
                        'stream_batch_state_max_abs_difference':difference,'metrics':metrics,'answer':answer})
    report={'checkpoint':str(args.checkpoint),'video':str(args.video),'results':results,
            'note':'Same NOW, actual past samples only. Stability/equivalence is not temporal accuracy; no human labels.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
