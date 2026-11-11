"""Local YOLO/SAM3/Qwen3-VL weak labels on unmarked, causal whole-frame retail clips.

Machine labels are usable by explicit user authorization, but are never human GT.
No collision, metric distance, or private psychological intent labels are inferred.
"""
import argparse
from collections import Counter
import csv
import gc
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import torch

from .recovery_data import write_rows, read_rows, CAPTION_QUERY, COUNT_QUERY
from .robot_vqa import PersonMotionHints


def geometry_motion(hints):
    reliable = [t for t in hints['people'] if t.get('history_s',0) >= 1.0]
    motions = []
    for track in reliable:
        # Relative height growth is a noisy approach cue, not intent or collision GT.
        growth = track.get('height_change_fraction') or 0
        lateral = abs((track.get('center_delta_xy') or [0,0])[0])
        if growth > .12:
            motions.append('TOWARD_CAMERA')
        elif growth < -.12:
            motions.append('AWAY_FROM_CAMERA')
        elif lateral > .08:
            motions.append('LATERAL_ACROSS_VIEW')
        elif abs(growth) < .06 and lateral < .035:
            motions.append('STATIONARY')
        else:
            motions.append('UNCERTAIN')
    if 'TOWARD_CAMERA' in motions:
        return 'TOWARD_CAMERA'
    nonunknown = set(motions)-{'UNCERTAIN'}
    return next(iter(nonunknown)) if len(nonunknown)==1 and 'UNCERTAIN' not in motions else 'UNCERTAIN'


def extract(args):
    import cv2
    from .camera_policy import PersonBoxDetector
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    detector = PersonBoxDetector(args.yolo,args.device)
    rows = []
    videos = [('0610_take1n2.mp4','train'),('0610_take3n4.mp4','train'),
              ('0616x9.mp4','train'),('0617x9.mp4','val')]
    for name, split in videos:
        video = args.videos/name
        cap = cv2.VideoCapture(str(video))
        fps, count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or fps <= 0 or count < 8:
            raise ValueError(f'Unreadable video: {video}')
        duration = (count-1)/fps
        if duration < 3.5:
            raise ValueError(f'Video needs at least 3.5 seconds for an 8-step clip: {video}')
        ends = np.linspace(3.5,duration,min(args.clips_per_video,max(1,int((duration-3.5)/2))))
        folder = args.output/'frames'/video.stem
        folder.mkdir(parents=True)
        cache = {}
        for index, end in enumerate(ends):
            times = np.linspace(end-3.5,end,8).tolist()
            paths, boxes = [], []
            hints = PersonMotionHints()
            for at in times:
                frame_id = min(count-1,round(at*fps))
                if frame_id not in cache:
                    cap.set(cv2.CAP_PROP_POS_FRAMES,frame_id)
                    okay, frame = cap.read()
                    if not okay:
                        raise ValueError(f'Cannot decode {name}:{frame_id}')
                    # Preserve aspect ratio and reduce disk without visible target markers.
                    frame = cv2.resize(frame,(768,round(frame.shape[0]*768/frame.shape[1])))
                    path = folder/f'{frame_id:08d}.jpg'
                    cv2.imwrite(str(path),frame)
                    cache[frame_id] = (str(path),detector.detect(frame))
                path, detections = cache[frame_id]
                paths.append(path)
                boxes.append(detections)
                report = hints.update(detections,at)
            motion = geometry_motion(report)
            row = {'id':f'raw_{video.stem}_{index:03d}','group_id':f'recording_{name[:4]}',
                   'video':str(video),'split':split,'frames':paths,'time_unit':'frames',
                   'frame_indices':list(range(8)),'capture_timestamps_s':times,'source_fps':fps,
                   'query':'the people','motion_candidate':motion,'yolo_boxes':boxes,
                   'yolo_hints':report,'label_source':'yolov8n_image_geometry_weak',
                   'human_reviewed':False,'camera_motion':'unverified',
                   'collision_ground_truth':None,'augmentation_safe_flip':False}
            rows.append(row)
            print(json.dumps({'extracted':row['id'],'motion':motion}),flush=True)
        cap.release()
    write_rows(args.output/'clips.jsonl',rows)
    write_rows(args.output/'store_pending.jsonl',rows)


def teacher(args):
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
    model = Qwen3VLForConditionalGeneration.from_pretrained(args.teacher,local_files_only=True,
                  dtype=torch.bfloat16,attn_implementation='sdpa').to(args.device).eval()
    processor = AutoProcessor.from_pretrained(args.teacher,local_files_only=True)
    rows = read_rows(args.output/'clips.jsonl')
    records = []
    for row in rows:
        # Single current frame describes NOW; no future frames can enter this teacher task.
        image = Image.open(row['frames'][-1]).convert('RGB')
        query = ('Describe only this current photograph. Return JSON with person_count (integer), '
                 'observation (one short sentence about the visible people and their current activity). '
                 'Do not infer movement direction, intent, collision risk, gender, or future events from a still image.')
        messages = [{'role':'user','content':[{'type':'image','image':image},{'type':'text','text':query}]}]
        inputs = processor.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,
                  return_dict=True,return_tensors='pt').to(args.device)
        with torch.inference_mode():
            generated = model.generate(**inputs,max_new_tokens=160,do_sample=False)
        raw = processor.decode(generated[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True).strip()
        try:
            parsed = json.loads(raw.removeprefix('```json').removesuffix('```').strip())
            assert isinstance(parsed['person_count'],int) and not isinstance(parsed['person_count'],bool) and 0 <= parsed['person_count'] < 50
            assert isinstance(parsed['observation'],str) and len(parsed['observation']) > 8
        except (ValueError,KeyError,TypeError,AssertionError):
            parsed = None
        records.append({**row,'teacher_raw':raw,'teacher_parsed':parsed,
                        'teacher_model':str(args.teacher),'teacher_revision':args.teacher_revision,
                        'label_source':'qwen3_vl_2b_and_yolo_weak','review_status':'pending'})
        write_rows(args.output/'teacher.jsonl',records)
        print(json.dumps({'teacher':row['id'],'parsed':parsed}),flush=True)
    del model, processor
    gc.collect()
    torch.cuda.empty_cache()


def assemble(args):
    rows = read_rows(args.output/'teacher.jsonl')
    if {r['id'] for r in rows} != {r['id'] for r in read_rows(args.output/'clips.jsonl')}:
        raise ValueError('Teacher export incomplete; finish labeling before assembly')
    masks = {r['id'].removeprefix('teacher_mask_'):r for r in read_rows(args.output/'sam3/candidates.jsonl')} if (args.output/'sam3/candidates.jsonl').is_file() else {}
    tasks = {'train':[],'val':[]}
    review = []
    for row in rows:
        parsed = row['teacher_parsed']
        review.append({'id':row['id'],'split':row['split'],'status':'pending','reviewer':'',
                       'motion_candidate':row['motion_candidate'],'motion_override':'',
                       'collision_label':'','observation_override':''})
        common = {k:v for k,v in row.items() if k not in {'teacher_raw','teacher_parsed','yolo_boxes','yolo_hints'}}
        if 'timestamps_s' in common:
            common['capture_timestamps_s']=common.pop('timestamps_s')
        if parsed:
            tasks[row['split']].append({**common,'id':row['id']+'_caption','query':CAPTION_QUERY,
                        'answer':parsed['observation'],'task':'retail_caption','eval_group':'weak_retail',
                        'sampling_group':'retail_caption'})
            tasks[row['split']].append({**common,'id':row['id']+'_count','query':COUNT_QUERY,
                        'answer':json.dumps({'person_count':parsed['person_count'],'person_present':parsed['person_count']>0},separators=(',',':')),
                        'task':'retail_count','eval_group':'weak_retail','sampling_group':'retail_count'})
        if row['id'] in masks:
            mask = masks[row['id']]
            tasks[row['split']].append({**common,'id':row['id']+'_mask','query':'the people',
                        'mask':mask['mask'],'mask_encoding':'binary_255','task':'retail_mask',
                        'eval_group':'weak_retail_mask','sampling_group':'retail_mask',
                        'label_source':'sam3_person_union_weak','teacher_model':mask['teacher_model'],
                        'teacher_revision':mask.get('teacher_revision'),'teacher_threshold':mask['teacher_threshold']})
    for split, records in tasks.items():
        write_rows(args.output/f'{split}.jsonl',records)
    with (args.output/'review.csv').open('w',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=list(review[0]))
        writer.writeheader()
        writer.writerows(review)
    articles = []
    for row in rows:
        images = ''.join(f'<img loading="lazy" width="220" src="{html.escape(str(Path(p).relative_to(args.output)))}">' for p in row['frames'])
        yolo_count=len(row['yolo_boxes'][-1])
        articles.append(f'<article><h2>{html.escape(row["id"])}</h2>{images}<pre>{html.escape(json.dumps(row["teacher_parsed"],ensure_ascii=False))}\nYOLO NOW count: {yolo_count}\nYOLO weak motion: {row["motion_candidate"]}</pre></article>')
    (args.output/'review.html').write_text('<!doctype html><meta charset="utf-8"><h1>Machine weak labels — human review pending</h1>'+''.join(articles))
    summary = {'clips':len(rows),'splits':dict(Counter(r['split'] for r in rows)),
               'valid_teacher':sum(r['teacher_parsed'] is not None for r in rows),
               'motion_candidates':dict(Counter(r['motion_candidate'] for r in rows)),
               'tasks':{s:dict(Counter(r['task'] for r in v)) for s,v in tasks.items()},
               'human_reviewed':0,'note':'Recording-day disjoint; 0626 untouched. No collision GT; motion from YOLO geometry is ego-motion sensitive. Machine-only validation is agreement, not accuracy.'}
    comparable=[r for r in rows if r['teacher_parsed'] is not None]
    summary['qwen_yolo_count_agreement']={'matches':sum(r['teacher_parsed']['person_count']==len(r['yolo_boxes'][-1]) for r in comparable),'samples':len(comparable)}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['extract','teacher','assemble'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--videos',type=Path,default=Path('/workspace/example/video'))
    parser.add_argument('--yolo',type=Path,default=Path('/workspace/yolov8n.pt'))
    parser.add_argument('--teacher',type=Path,default=Path('/workspace/models/Qwen3-VL-2B-Instruct'))
    parser.add_argument('--teacher-revision',default='89644892e4d85e24eaac8bacfd4f463576704203')
    parser.add_argument('--clips-per-video',type=int,default=24)
    parser.add_argument('--device',default='cuda:0')
    args = parser.parse_args()
    {'extract':extract,'teacher':teacher,'assemble':assemble}[args.phase](args)


if __name__ == '__main__':
    main()
