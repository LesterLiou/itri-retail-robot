import json
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest
torch = pytest.importorskip('torch')

from light_foundation_vlm.data import ClipDataset
from lightweight_risk_vqa.evaluate_grounded_masks import mask_evaluation_records


class Tokenizer:
    eos_token_id = 1

    def encode(self, text, **kwargs):
        return [2, 3]


def test_paired_mask_evaluation_never_feeds_ground_truth_answer_to_model():
    row = {'id': 'x', 'frames': ['now.jpg'], 'mask': 'target.png',
           'query': 'Describe the green target', 'answer': 'ground truth activity'}
    clean = mask_evaluation_records([row])[0]
    assert 'answer' not in clean and row['answer'] == 'ground truth activity'
    assert clean['query'] == row['query'] and clean['mask'] == row['mask']


def test_clip_transform_keeps_mask_aligned_and_validation_unchanged(tmp_path):
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[:, :8] = 255
    mask = np.zeros((32, 32), dtype=np.uint8)
    mask[:, :8] = 255
    Image.fromarray(image).save(tmp_path / 'image.png')
    Image.fromarray(mask).save(tmp_path / 'mask.png')
    row = {'id': 'x', 'group_id': 'x', 'frames': ['image.png', 'image.png'],
           'frame_indices': [0, 1], 'time_unit': 'frames', 'query': 'the people',
           'mask': 'mask.png', 'augmentation_safe_flip': True}
    path = tmp_path / 'train.jsonl'
    path.write_text(json.dumps(row) + '\n')
    with patch('torch.rand', side_effect=[torch.tensor(0.), torch.tensor(.5), torch.tensor(.5)]):
        sample = ClipDataset(path, Tokenizer(), image_size=32, augment=True)[0]
    assert sample['mask_targets'][0, :, -8:].all()
    assert not sample['mask_targets'][0, :, :24].any()
    torch.testing.assert_close(sample['frames'][0], sample['frames'][1])
    original = ClipDataset(path, Tokenizer(), image_size=32)[0]
    assert original['mask_targets'][0, :, :8].all()
