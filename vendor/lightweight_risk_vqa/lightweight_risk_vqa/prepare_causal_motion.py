"""Causal whole-frame JAAD motion proxies from human boxes, with no future images/labels.

Boxes are human annotated; camera-relative direction and policy are geometric weak labels.
JAAD lacks camera-motion compensation and collision/intention GT. These are NOT safety GT.
"""
import argparse
from collections import defaultdict, Counter
import json
from pathlib import Path
import random

from .prepare_jaad_vqa import read_tracks
from .recovery_data import read_rows, write_rows, check_groups
from .robot_vqa import ROBOT_QUERY

CLASSES = ['TOWARD_CAMERA','AWAY_FROM_CAMERA','LATERAL_ACROSS_VIEW','STATIONARY','UNCERTAIN']


def box_motion(first,last,width,span_s):
    if span_s < .8 or first[3]-first[1] <= 0:
        return 'UNCERTAIN'
    growth = (last[3]-last[1])/(first[3]-first[1])-1
    dx = abs((last[0]+last[2]-first[0]-first[2])/2/width)
    if growth > .12:
        return 'TOWARD_CAMERA'
    if growth < -.12:
        return 'AWAY_FROM_CAMERA'
    if dx > .08:
        return 'LATERAL_ACROSS_VIEW'
    if abs(growth) < .06 and dx < .035:
        return 'STATIONARY'
    return 'UNCERTAIN'


def aggregate(motions):
    if 'TOWARD_CAMERA' in motions:
        return 'TOWARD_CAMERA'
    known = set(motions)-{'UNCERTAIN'}
    return next(iter(known)) if len(known)==1 and 'UNCERTAIN' not in motions else 'UNCERTAIN'


def policy_answer(motion,observation):
    toward = motion == 'TOWARD_CAMERA'
    unknown = motion == 'UNCERTAIN'
    intent = 'UNCERTAIN' if unknown else 'LIKELY' if toward else 'UNLIKELY'
    risk = 'UNCERTAIN' if unknown else 'ELEVATED' if toward else 'LOW'
    action = 'OBSERVE' if unknown else 'DANGER_WARNING' if toward else 'CONTINUE'
    explanation = ('Person scale increases across the sampled history; approach is a hypothesis.' if toward else
                   'Camera-relative motion is unclear; more temporal evidence is needed.' if unknown else
                   'The observed camera-relative motion does not indicate approach in this sampled history.')
    return json.dumps({'observation':observation,'motion':motion,'approach_intent':intent,
                       'collision_risk':risk,'action':action,'explanation':explanation},separators=(',',':'))


def main():
    import cv2
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--jaad',type=Path,required=True)
    p.add_argument('--retail',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--per-class-train',type=int,default=100)
    p.add_argument('--per-class-val',type=int,default=30)
    args=p.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    splits={}
    for split in ('train','val'):
        buckets=defaultdict(list)
        tracks_cache={}
        for row in read_rows(args.source/f'jaad_{split}.jsonl'):
            video=row['video_id']
            if video not in tracks_cache:
                tracks_cache[video]=read_tracks(args.jaad/'annotations'/f'{video}.xml')
            width,height,tracks=tracks_cache[video]
            indices=row['source_frame_indices']
            first,last=indices[0],indices[-1]
            motions=[]
            for person,annotations in tracks.items():
                if first in annotations and last in annotations and annotations[last]['attributes'].get('occlusion') != 'full':
                    motions.append(box_motion(annotations[first]['box'],annotations[last]['box'],width,(last-first)/row['source_fps']))
            motion=aggregate(motions)
            activity=row.get('now_human_attributes',{}).get('action','')
            observation='A person is walking in the scene.' if activity=='walking' else 'A person is standing in the scene.' if activity=='standing' else 'People are visible in the scene.'
            # Remove all future-derived fields, source answers, review panels and green image inputs.
            record={'id':'causal_'+row['id'],'group_id':row['group_id'],'video_id':video,
                    'source_frame_indices':indices,'frame_indices':list(range(8)),'time_unit':'frames',
                    'query':ROBOT_QUERY,'answer':policy_answer(motion,observation),
                    'event_label':CLASSES.index(motion),'motion_candidate':motion,
                    'label_source':'jaad_human_boxes_past_geometry_weak',
                    'action_source':'approach_warning_policy_proxy','human_reviewed':False,
                    'intent_source':'motion_policy_proxy_not_human_intent','collision_ground_truth':None,
                    'camera_motion':'unverified','eval_group':'weak_robot','sampling_group':motion,
                    'augmentation_safe_flip':False}
            buckets[motion].append(record)
        selected=[]
        rng=random.Random(42)
        for values in buckets.values():
            rng.shuffle(values)
            selected.extend(values[:args.per_class_train if split=='train' else args.per_class_val])
        by_video=defaultdict(list)
        for r in selected:
            by_video[r['video_id']].append(r)
        for video,records in by_video.items():
            folder=args.output/'frames'/video
            folder.mkdir(parents=True,exist_ok=True)
            wanted={index for r in records for index in r['source_frame_indices']}
            cap=cv2.VideoCapture(str(args.jaad/'JAAD_clips'/f'{video}.mp4'))
            for index in range(max(wanted)+1):
                okay,frame=cap.read()
                if not okay:
                    raise ValueError(f'Cannot decode {video}:{index}')
                if index in wanted:
                    cv2.imwrite(str(folder/f'{index:06d}.jpg'),cv2.resize(frame,(640,360)))
            cap.release()
            for r in records:
                r['frames']=[str(folder/f'{index:06d}.jpg') for index in r['source_frame_indices']]
            print(json.dumps({'split':split,'video':video,'clips':len(records)}),flush=True)
        for row in read_rows(args.retail/'teacher.jsonl'):
            if row['split'] != split or row['teacher_parsed'] is None:
                continue
            motion=row['motion_candidate']
            r={k:v for k,v in row.items() if k not in {'teacher_parsed','teacher_raw','yolo_hints','yolo_boxes','timestamps_s'}}
            r.update(id='causal_'+row['id'],query=ROBOT_QUERY,
                     answer=policy_answer(motion,row['teacher_parsed']['observation']),
                     event_label=CLASSES.index(motion),eval_group='weak_robot',sampling_group=motion,
                     action_source='approach_warning_policy_proxy',intent_source='motion_policy_proxy_not_human_intent')
            selected.append(r)
        splits[split]=selected
    check_groups(splits)
    for split,rows in splits.items():
        write_rows(args.output/f'{split}.jsonl',rows)
    summary={s:dict(Counter(r['motion_candidate'] for r in rows)) for s,rows in splits.items()}
    (args.output/'summary.json').write_text(json.dumps({'classes':CLASSES,'counts':summary,
        'note':'All motion/risk/intent/actions are weak proxy labels; no IMMINENT/STOP ground truth. No future labels in export.'},indent=2))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    main()
