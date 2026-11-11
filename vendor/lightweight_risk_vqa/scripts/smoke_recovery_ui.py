"""Real Qt/video/model smoke with screenshot and capture-to-result timing records."""
import argparse
import json
from pathlib import Path
import sys
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['vqa-only','yolo-assisted','fast-head'],required=True)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--video',type=Path,required=True)
    p.add_argument('--yolo',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--robot-prompt',choices=['compatible','compact'],default='compatible')
    p.add_argument('--no-segmentation',action='store_true')
    p.add_argument('--seconds',type=float,default=16.)
    p.add_argument('--startup-timeout',type=float,default=90.)
    args=p.parse_args()
    if args.seconds<=0 or args.startup_timeout<=0:
        p.error('Positive measurement and startup durations required')
    if args.mode=='yolo-assisted' and (not args.yolo or not args.yolo.is_file()):
        p.error('yolo-assisted requires local --yolo weights')
    args.output.mkdir(parents=True,exist_ok=False)
    from lightweight_risk_vqa import light_foundation_live_ui as ui
    windows=[]
    class Capture(ui.Monitor):
        def __init__(self,pipeline):
            super().__init__(pipeline)
            self.records=[]
            self.visual_seen=set()
            self.answer_seen=set()
            self.ready_at=None
            self.started_at=time.monotonic()
            self.closing=False
            self.record_timer=ui.QTimer(self)
            self.record_timer.timeout.connect(self.collect)
            self.record_timer.start(50)
            windows.append(self)

        def collect(self):
            with self.pipeline.lock:
                state=dict(self.pipeline.state)
                metrics=dict(state.get('metrics',{}))
            for kind,index_key,evidence_key,publish_key,seen in [
                ('visual','segmentation_index','segmentation_evidence_at','visual_published_at',self.visual_seen),
                ('vqa','vqa_index','vqa_evidence_at','vqa_published_at',self.answer_seen)]:
                index=state.get(index_key)
                if index is None or index in seen or state.get(publish_key) is None:
                    continue
                seen.add(index)
                self.records.append({'kind':kind,'frame':index,
                    'capture_to_publish_ms':1000*(state[publish_key]-state[evidence_key]),
                    'model_visual_ms':metrics.get('visual_publish_latency_ms'),
                    'model_vqa_ms':state.get('vqa_latency_ms'),
                    'valid_vqa_schema':isinstance(state.get('vqa_parsed'),dict) and not state.get('vqa_error') if kind=='vqa' else None})
            now=time.monotonic()
            if self.ready_at is None and metrics.get('memory_steps_since_reset',0)>0:
                self.ready_at=now
            if not self.closing and ((self.ready_at is not None and now-self.ready_at>=args.seconds) or (self.ready_at is None and now-self.started_at>=args.startup_timeout)):
                self.closing=True
                self.close()

        def closeEvent(self,event):
            self.closing=True
            self.collect()
            self.grab().save(str(args.output/'ui.png'))
            super().closeEvent(event)
    ui.Monitor=Capture
    options=['--checkpoint',str(args.checkpoint),'--video',str(args.video),
             '--vqa-backend','embedded','--inference-mode','yolo-assisted' if args.mode=='yolo-assisted' else 'vqa-only',
             '--segmentation-hz','2','--vqa-hz','.5','--exit-after-s',str(args.startup_timeout+args.seconds+10)]
    options += ['--robot-prompt',args.robot_prompt]
    if args.no_segmentation:
        options.append('--no-segmentation')
    if args.mode=='fast-head':
        options.append('--fast-motion-head')
    if args.mode=='yolo-assisted' and args.yolo:
        options+=['--bbox-model',str(args.yolo)]
    code=ui.main(options)
    records=windows[0].records
    passed=any(r.get('valid_vqa_schema') for r in records)
    (args.output/'timings.json').write_text(json.dumps({'mode':args.mode,'passed':passed,'records':records},indent=2))
    if not passed:
        raise RuntimeError('UI smoke did not produce a valid VQA result; inspect its log/screenshot')
    return code


if __name__=='__main__':
    raise SystemExit(main())
