"""Verify exported JAAD clips, official splits, review queue and image integrity."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

from .prepare_jaad_vqa import read_splits
from .temporal import LABELS


def audit(root, source_root):
    from PIL import Image

    root, source_root = Path(root), Path(source_root)
    summary = json.loads((root / 'summary.json').read_text())
    official = read_splits(source_root / 'split_ids' / summary['split_set'])
    all_ids, all_paths, all_groups = set(), set(), set()
    fingerprint_splits = {}
    results = {}
    for split in ('train', 'val', 'test'):
        manifest = root / 'manifests' / f'{split}.jsonl'
        records = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
        ids, paths, groups = set(), set(), set()
        counts = Counter()
        for row in records:
            assert row['id'] not in ids, 'Duplicate sample ID'
            ids.add(row['id'])
            groups.add(row['group_id'])
            assert row['video_id'] in official[split], 'Not in official source split'
            assert row['split'] == split
            assert row['human_reviewed'] is False
            assert row['label_source'] == 'jaad_human_boxes_future_geometry'
            answer = json.loads(row['answer'])
            assert set(answer) == {'motion', 'action', 'explanation'}
            assert answer['motion'] in LABELS
            counts[answer['motion']] += 1
            assert answer['action'] in ('CONTINUE', 'DANGER_WARNING')
            assert len(row['frames']) == summary['steps']
            assert row['frame_indices'] == list(range(summary['steps']))
            indices = row['source_frame_indices']
            assert len(indices) == summary['steps']
            assert all(a < b for a, b in zip(indices, indices[1:]))
            assert max(row['source_timestamps_s']) <= row['now_s'] + 1e-6
            assert row['label_future_frame_index'] > max(indices)
            horizon = (row['label_future_frame_index'] - indices[-1]) / row['source_fps']
            assert abs(horizon - 1.0) <= 1.0 / row['source_fps']
            for number, value in enumerate(row['frames']):
                path = Path(value).resolve()
                assert path.parent.parent == (root / 'frames').resolve()
                assert path.name == f'{number:02d}.jpg'
                assert path.is_file()
                with Image.open(path) as image:
                    assert image.size == (960, 540)
                    image.verify()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                fingerprint_splits.setdefault(digest, set()).add(split)
                paths.add(str(path))
            panel = root / row['review_panel']
            with Image.open(panel) as image:
                image.verify()
        assert not all_ids.intersection(ids), 'Cross-split duplicate sample'
        assert not all_paths.intersection(paths), 'Cross-split shared frame path'
        assert not all_groups.intersection(groups), 'Cross-split shared video'
        all_ids.update(ids)
        all_paths.update(paths)
        all_groups.update(groups)
        assert len(records) == summary['splits'][split]['samples']
        assert dict(counts) == summary['splits'][split]['motion_counts']
        results[split] = {'samples': len(records), 'input_images': len(paths),
                          'videos': len(groups), 'motion_counts': dict(counts),
                          'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    with (root / 'review_queue.csv').open(newline='') as stream:
        queue = list(csv.DictReader(stream))
    assert len(queue) == len(all_ids)
    assert {row['id'] for row in queue} == all_ids
    assert all(row['review_status'] == 'pending' and not row['reviewed_motion'] for row in queue)
    duplicates = sum(len(splits) > 1 for splits in fingerprint_splits.values())
    report = {
        'status': 'passed' if duplicates == 0 else 'cross_split_identical_images_require_review',
        'splits': results, 'total_samples': len(all_ids), 'total_input_images': len(all_paths),
        'official_split_membership': 'passed', 'cross_split_ids_groups_paths': 'disjoint',
        'cross_split_identical_image_hashes': duplicates, 'image_integrity': 'passed',
        'causal_timestamps_and_future_exclusion': 'passed', 'review_queue': 'all_pending',
        'label_quality': 'weak image-space motion; no human direction review',
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root', type=Path, required=True)
    parser.add_argument('--jaad-root', type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(args.dataset_root, args.jaad_root)
    (args.dataset_root / 'validation_report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if report['status'] != 'passed':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
