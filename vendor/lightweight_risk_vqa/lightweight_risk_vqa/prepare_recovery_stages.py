"""Build reproducible multi-task recovery curricula, preserving recording and image splits."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
from collections import defaultdict

from .recovery_data import read_rows, write_rows, check_groups


def audit_data(root):
    from .prepare_causal_motion import CLASSES
    from .robot_vqa import robot_schema_error
    summary={}
    for stage in 'abc':
        splits={s:read_rows(root/'stages'/f'{stage}_{s}.jsonl') for s in ('train','val')}
        check_groups(splits)
        for split,rows in splits.items():
            ids=[r['id'] for r in rows]
            if len(ids)!=len(set(ids)):
                raise ValueError('Duplicate training IDs')
            for r in rows:
                if any(not Path(path).is_file() for path in r['frames']) or (r.get('mask') and not Path(r['mask']).is_file()):
                    raise ValueError('Missing image/mask asset: '+r['id'])
                if r['time_unit']=='frames' and 'timestamps_s' in r:
                    raise ValueError('Mixed physical and step timestamps')
                if r['mix_group']=='weak_robot':
                    answer=json.loads(r['answer'])
                    if robot_schema_error(answer) or answer['motion']!=CLASSES[r['event_label']]:
                        raise ValueError('Invalid weak policy target')
                    if r.get('human_reviewed') or answer['action']=='STOP' or any('future' in key for key in r):
                        raise ValueError('Unsupported human/future/collision claim')
            summary[f'{stage}_{split}']={'rows':len(rows),'unique_frames':len({p for r in rows for p in r['frames']}),
                         'groups':len({r['group_id'] for r in rows}),
                         'tasks':dict(Counter(r['mix_group'] for r in rows))}
    summary['status']='passed: assets, IDs, split isolation, frame-step units and weak-policy contracts'
    summary['limitations']='Does not establish label correctness. No human retail motion/collision review.'
    (root/'dataset_audit.json').write_text(json.dumps(summary,indent=2))
    return summary


def state_ablation(root,source):
    buckets=defaultdict(list)
    for row in read_rows(source/'state_val.jsonl'):
        answer=json.loads(row['answer'])
        buckets[(answer['current_activity'],answer['observed_transition'])].append(row)
    rng=random.Random(42)
    for values in buckets.values():
        rng.shuffle(values)
    selected=[]
    while len(selected)<64 and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(selected)<64:
                selected.append(buckets[key].pop())
    write_rows(root/'state_ablation_val.jsonl',selected)


def balanced_state(root):
    splits={}
    for split in ('train','val'):
        rows=read_rows(root/'stages'/f'b_{split}.jsonl')
        for row in rows:
            key=row['mix_group']
            if key=='human_state':
                answer=json.loads(row['answer'])
                key+=':'+answer['current_activity']+':'+answer['observed_transition']
            row['recovery_sampling_group']=key
        splits[split]=rows
        path=root/'stages'/f'b_balanced_{split}.jsonl'
        if path.exists():
            raise FileExistsError(path)
        write_rows(path,rows)
    check_groups(splits)
    summary={s:dict(Counter(r['recovery_sampling_group'] for r in rows)) for s,rows in splits.items()}
    (root/'stages/b_balanced_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


def prepare(root, source):
    output = root/'stages'
    if output.exists():
        raise FileExistsError(output)
    output.mkdir()
    reports = {}
    for stage in ('a','b','c'):
        splits = {}
        for split in ('train','val'):
            rows = read_rows(root/'grounding'/f'{split}.jsonl')
            rows = [{**r,'mix_group':r['task']} for r in rows]
            states = read_rows(source/f'state_{split}.jsonl')
            masks = read_rows(source/f'segmentation_{split}.jsonl')
            # Stage A keeps the runtime 80-token temporal architecture while memory is frozen.
            # A little human temporal data retains its existing input contract.
            if stage == 'a':
                states = states[:64 if split=='train' else 32]
                masks = masks[:160 if split=='train' else 40]
            rows += [{**r,'mix_group':'human_state'} for r in states]
            rows += [{**r,'mix_group':'human_mask'} for r in masks if r.get('label_source') != 'sam3_person_union_candidate']
            if stage != 'a':
                mevis = read_rows(source/f'mevis_{split}.jsonl')
                rows += [{**r,'mix_group':'mevis_mask'} for r in mevis]
            if stage == 'c':
                for r in read_rows(root/'retail'/f'{split}.jsonl'):
                    r = dict(r)
                    if 'timestamps_s' in r:
                        r['capture_timestamps_s'] = r.pop('timestamps_s')
                    r['mix_group'] = 'weak_retail'
                    rows.append(r)
                rows += [{**r,'mix_group':'weak_robot'} for r in read_rows(root/'causal_motion'/f'{split}.jsonl')]
                for r in rows:
                    r['recovery_sampling_group']=('weak_robot:'+r['motion_candidate'] if r['mix_group']=='weak_robot' else r['mix_group'])
            splits[split] = rows
        check_groups(splits)
        for split, rows in splits.items():
            write_rows(output/f'{stage}_{split}.jsonl',rows)
        reports[stage] = {s:dict(Counter(r['mix_group'] for r in rows)) for s,rows in splits.items()}
    (output/'summary.json').write_text(json.dumps(reports,indent=2))
    print(json.dumps(reports,indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--add-causal',action='store_true',help='Add newly exported causal tasks to existing stage C once')
    p.add_argument('--audit-only',action='store_true')
    p.add_argument('--balanced-state',action='store_true',help='Prepare a matched B curriculum with balanced human activity/transition sampling')
    args=p.parse_args()
    if args.balanced_state:
        balanced_state(args.root)
        return
    if args.audit_only:
        print(json.dumps(audit_data(args.root),indent=2))
        return
    state_ablation(args.root,args.source)
    if args.add_causal:
        for split in ('train','val'):
            path=args.root/'stages'/f'c_{split}.jsonl'
            rows=[r for r in read_rows(path) if r.get('mix_group')!='weak_robot']
            rows += [{**r,'mix_group':'weak_robot'} for r in read_rows(args.root/'causal_motion'/f'{split}.jsonl')]
            for r in rows:
                r['recovery_sampling_group']=('weak_robot:'+r['motion_candidate'] if r['mix_group']=='weak_robot' else r['mix_group'])
            write_rows(path,rows)
        check_groups({s:read_rows(args.root/'stages'/f'c_{s}.jsonl') for s in ('train','val')})
        return
    prepare(args.root,args.source)


if __name__=='__main__':
    main()
