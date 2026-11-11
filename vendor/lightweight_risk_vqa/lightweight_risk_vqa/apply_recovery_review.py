"""Apply explicit per-field retail reviews to a NEW continuation dataset.

Pending machine labels stay weak. Accepting motion never upgrades masks/counts/intent
to human ground truth. Rejected clips are removed from both robot and retail tasks.
"""
import argparse
import csv
import json
from pathlib import Path

from .prepare_causal_motion import CLASSES,policy_answer
from .recovery_data import read_rows,write_rows,check_groups
from .robot_vqa import robot_schema_error,RISK_ACTIONS


def apply(data,reviews,output):
    data,reviews,output=map(Path,(data,reviews,output))
    if output.exists():
        raise FileExistsError(output)
    clips={r['id']:r for r in read_rows(data/'retail/teacher.jsonl')}
    decisions={}
    with reviews.open(newline='') as stream:
        for review in csv.DictReader(stream):
            key=review['id']
            if key not in clips or key in decisions:
                raise ValueError('Unknown or duplicate review ID: '+key)
            status=review['status'].strip().lower()
            review['status']=status
            review['motion_override']=review.get('motion_override','').strip()
            if status not in {'pending','accept','reject'}:
                raise ValueError('Review status must be pending, accept or reject')
            if review.get('split')!=clips[key]['split']:
                raise ValueError('Review cannot change a recording split')
            if status in {'accept','reject'} and not review.get('reviewer','').strip():
                raise ValueError('A completed review requires a reviewer')
            if status=='accept':
                if not review.get('reviewer','').strip() or review.get('motion_override') not in CLASSES:
                    raise ValueError('Accepted review requires reviewer and explicit motion_override')
                if review.get('collision_label') and (review['collision_label'] not in RISK_ACTIONS or not review.get('explanation_override','').strip()):
                    raise ValueError('An explicit collision label requires a valid risk and explanation_override')
                count=review.get('person_count_override','').strip()
                if count and (not count.isdigit() or int(count)>50):
                    raise ValueError('person_count_override must be an integer in 0..50')
            decisions[key]=review
    if not any(r['status'].strip().lower() in {'accept','reject'} for r in decisions.values()):
        raise ValueError('No explicit accepted/rejected reviews; no dataset created')
    splits={}
    for split in ('train','val'):
        rows=[]
        for source in read_rows(data/'stages'/f'c_{split}.jsonl'):
            row=dict(source)
            key=row['id'].removeprefix('causal_') if row.get('mix_group')=='weak_robot' else row['id'].rsplit('_',1)[0]
            review=decisions.get(key)
            if not review or review['status'].strip().lower()=='pending':
                rows.append(row)
                continue
            if review['status'].strip().lower()=='reject':
                continue
            motion=review['motion_override']
            observation=review.get('observation_override','').strip()
            count=review.get('person_count_override','').strip()
            fields=[]
            if row.get('mix_group')=='weak_robot':
                previous=json.loads(row['answer'])
                answer=json.loads(policy_answer(motion,observation or previous['observation']))
                if review.get('collision_label'):
                    answer.update(collision_risk=review['collision_label'],action=RISK_ACTIONS[review['collision_label']],
                                  explanation=review['explanation_override'].strip())
                    fields+=['collision_risk','action','explanation']
                if robot_schema_error(answer):
                    raise ValueError('Reviewed answer violates declared policy: '+key)
                row.update(answer=json.dumps(answer,separators=(',',':')),event_label=CLASSES.index(motion),
                    motion_candidate=motion,recovery_sampling_group='weak_robot:'+motion)
                fields+=['motion']+(['observation'] if observation else [])
            elif row.get('task')=='retail_caption' and observation:
                row['answer']=observation
                fields+=['observation']
            elif row.get('task')=='retail_count' and count:
                row['answer']=json.dumps({'person_count':int(count),'person_present':int(count)>0},separators=(',',':'))
                fields+=['person_count','person_present']
            if fields:
                row.update(human_reviewed_fields=fields,reviewer=review['reviewer'],
                    review_source=str(reviews.resolve()),human_reviewed=False,
                    supervision_note='Only human_reviewed_fields are reviewed; other fields/masks remain weak or policy proxies')
            rows.append(row)
        splits[split]=rows
    check_groups(splits)
    (output/'stages').mkdir(parents=True)
    for split,rows in splits.items():
        write_rows(output/'stages'/f'c_{split}.jsonl',rows)
    summary={'reviews':str(reviews),'accepted':sum(r['status'].lower()=='accept' for r in decisions.values()),
             'rejected':sum(r['status'].lower()=='reject' for r in decisions.values()),
             'rows':{s:len(r) for s,r in splits.items()},'note':'Copy-free continuation manifests; original datasets retained. Per-field review provenance only.'}
    (output/'review_audit.json').write_text(json.dumps(summary,indent=2))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for field in ['data','reviews','output']:
        p.add_argument('--'+field,type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(apply(args.data,args.reviews,args.output),indent=2))


if __name__=='__main__':
    main()
