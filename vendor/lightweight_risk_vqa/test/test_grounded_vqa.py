import json
from pathlib import Path

import pytest

from lightweight_risk_vqa.prepare_grounded_vqa import prepare


def make_sources(root):
    for source in ('mixed', 'masks'):
        (root / source).mkdir()
        for split in ('train', 'val'):
            image = root / source / f'{split}.jpg'
            image.write_bytes(b'fixture')
            if source == 'masks':
                mask = root / source / f'{split}.png'
                mask.write_bytes(b'fixture')
                rows = [{'id': f'mask_{split}', 'group_id': f'image_{split}',
                         'frames': [image.name], 'mask': mask.name, 'query': 'the person',
                         'timestamps_s': [0], 'mask_encoding': 'binary_255'}]
            else:
                rows = [{'id': f'{domain}_{split}', 'group_id': f'{domain}_{split}',
                         'frames': [image.name], 'time_unit': 'frames', 'frame_indices': [0],
                         'mix_group': domain, 'query': 'motion?',
                         'answer': json.dumps({'motion': 'STATIONARY'})}
                        for domain in ('jaad', 'store')]
            (root / source / f'{split}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))


def test_real_masks_keep_their_query_without_fake_temporal_frames(tmp_path):
    make_sources(tmp_path)
    report = prepare(tmp_path / 'mixed', tmp_path / 'masks', tmp_path / 'out')
    assert sum(report['sampling_weights'].values()) == pytest.approx(1)
    assert report['sampling_weights']['segmentation'] == .3
    row = json.loads((tmp_path / 'out/segmentation_train.jsonl').read_text())
    assert row['query'] == 'the person'
    assert row['frame_indices'] == [0]
    assert 'timestamps_s' not in row and 'answer' not in row
    assert Path(row['mask']).is_absolute()


def test_mask_image_cannot_leak_between_splits(tmp_path):
    make_sources(tmp_path)
    path = tmp_path / 'masks/val.jsonl'
    row = json.loads(path.read_text())
    row['frames'] = ['train.jpg']
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='leakage'):
        prepare(tmp_path / 'mixed', tmp_path / 'masks', tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
