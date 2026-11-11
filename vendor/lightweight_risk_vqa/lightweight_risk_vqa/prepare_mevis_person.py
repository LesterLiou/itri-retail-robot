"""Pinned MeViS person-subject clips with real masks and no future images/answers."""
import argparse
import json
from pathlib import Path
import re

from .prepare_person_state import write_jsonl

PERSON_SUBJECT = re.compile(
    r'^(?:(?:the|a|an|one|two|three|four|several|some|all|both|young|old|little|adult|'
    r'elderly|tall|short|small|group of)\s+)*'
    r'(?:person|people|man|men|woman|women|boy|boys|girl|girls|child|children|guy|guys|'
    r'lady|ladies|player|players|runner|runners|worker|workers|pedestrian|pedestrians)\b', re.I)


def person_subject(text):
    return bool(PERSON_SUBJECT.match(text.strip()))


def whole_prefix_indices(length, steps=8):
    if length < steps:
        raise ValueError('A real multi-frame video is required')
    return [round(i * (length - 1) / (steps - 1)) for i in range(steps)]


def main(argv=None):
    import numpy as np
    from PIL import Image
    from light_foundation_vlm.download_data import (
        download, hf_url, MEVIS_REV, extract_video_subset, select_expressions, decode_mask, sha256)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--train-videos', type=int, default=8)
    parser.add_argument('--val-videos', type=int, default=4)
    parser.add_argument('--expressions-per-video', type=int, default=8)
    args = parser.parse_args(argv)
    root = args.root.resolve() / 'data/mevis'
    if (root / 'READY').exists() or min(args.train_videos, args.val_videos, args.expressions_per_video) < 1:
        parser.error('Choose an incomplete/new export and positive limits')
    root.mkdir(parents=True, exist_ok=True)
    reports = {}
    group_sets = []
    for split, count in (('train', args.train_videos), ('valid_u', args.val_videos)):
        source = root / split
        for name in ('meta_expressions_v2.json', 'mask_dict.json'):
            download(hf_url('FudanCVL/MeViSv2', MEVIS_REV, f'{split}/{name}'), source / name)
        videos = json.loads((source / 'meta_expressions_v2.json').read_text())['videos']
        eligible = {}
        for video, info in videos.items():
            expressions = {key: exp for key, exp in info['expressions'].items() if person_subject(exp['exp'])}
            if expressions and len(info['frames']) >= 8:
                eligible[video] = {**info, 'expressions': expressions}
        if len(eligible) < count:
            raise ValueError('Not enough person-subject videos')
        print(json.dumps({'split': split, 'eligible_person_videos': len(eligible),
                          'extracting_videos': count}), flush=True)
        selected = extract_video_subset(hf_url('FudanCVL/MeViSv2', MEVIS_REV, f'{split}/JPEGImages.tar'),
                                        source / 'JPEGImages', eligible, count)
        masks = json.loads((source / 'mask_dict.json').read_text())
        records = []
        for video in selected:
            info = eligible[video]
            names = sorted(info['frames'])
            indices = whole_prefix_indices(len(names))
            paths = [source / 'JPEGImages' / video / f'{names[i]}.jpg' for i in indices]
            with Image.open(paths[-1]) as image:
                width, height = image.size
            expressions = select_expressions(info['expressions'], args.expressions_per_video, 'diverse')
            for key, exp in expressions:
                ann_ids = exp['anno_id'] if isinstance(exp['anno_id'], list) else [exp['anno_id']]
                mask = np.zeros((height, width), dtype=bool)
                for ann_id in ann_ids:
                    if str(ann_id) == '-1':
                        continue
                    annotation = masks[str(ann_id)][len(names) - 1]
                    if annotation is not None:
                        mask |= decode_mask(annotation, height, width)
                destination = source / 'person_masks' / f'{video}_{key}.png'
                destination.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(mask.astype(np.uint8) * 255).save(destination)
                records.append({'id': f'mevis_person_{video}_{key}', 'group_id': f'mevis_{video}',
                    'frames': [str(p) for p in paths], 'frame_indices': list(range(8)), 'time_unit': 'frames',
                    'source_frame_indices': indices, 'source_frame_names': [names[i] for i in indices],
                    'source_frame_count': len(names), 'physical_fps': None,
                    'query': exp['exp'], 'mask': str(destination), 'mask_encoding': 'binary_255',
                    'source_split': split, 'label_source': 'mevis_v2_human_expression_and_mask',
                    'person_filter': 'conservative_expression_subject_not_category_ground_truth',
                    'query_scope': 'whole_observed_video_prefix_ending_at_last_frame',
                    'eval_group': 'mevis', 'sampling_group': 'mevis',
                    'annotation_ids': ann_ids, 'augmentation_safe_flip': False})
        del masks
        write_jsonl(root / f'{split}_person.jsonl', records)
        reports[split] = {'videos': selected, 'clips': len(records),
                         'annotation_sha256': sha256(source / 'mask_dict.json')}
        group_sets.append(set(selected))
        print(json.dumps({'split': split, 'clips': len(records)}), flush=True)
    if group_sets[0] & group_sets[1]:
        raise ValueError('MeViS train/val video leakage')
    report = {'dataset': 'FudanCVL/MeViSv2', 'revision': MEVIS_REV, 'splits': reports,
              'selection': 'first complete person-subject videos in archive; small pilot, not representative benchmark',
              'time_contract': 'Eight samples span the complete observed prefix; original descriptions may refer to full-video motion. No future outside prefix, no future VQA answer synthesized. Frame steps are not seconds.'}
    (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    (root / 'READY').write_text('MeViS person-subject mask export complete.\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
