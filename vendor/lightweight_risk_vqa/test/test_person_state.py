import csv
import json

import pytest

from lightweight_risk_vqa.person_state import activity_transition, state_answer
from lightweight_risk_vqa.prepare_person_state import state_record, finalize
from lightweight_risk_vqa.apply_person_state_review import apply
from lightweight_risk_vqa.mix_person_teacher import prepare as mix_teacher
from lightweight_risk_vqa.expand_person_masks import select_images


def test_human_activity_transition_does_not_invent_unknown_labels():
    assert activity_transition(['standing', 'walking', 'walking']) == ('WALKING', 'STARTED_WALKING')
    assert activity_transition(['walking', 'standing']) == ('STANDING', 'STOPPED_WALKING')
    assert activity_transition(['walking'] * 8) == ('WALKING', 'STEADY')
    assert activity_transition(['standing', None, 'walking']) is None
    assert activity_transition(['standing', 'walking', 'standing']) is None


def test_repeat_last_control_preserves_steps_but_removes_motion_evidence():
    pytest.importorskip('torch')
    from lightweight_risk_vqa.temporal_vqa_infer import history_ablation
    row = {'frames': ['past.jpg', 'now.jpg'], 'frame_indices': [0, 1], 'time_unit': 'frames'}
    repeated = history_ablation(row, repeat_last=True)
    assert repeated['frames'] == ['now.jpg', 'now.jpg']
    assert repeated['frame_indices'] == [0, 1]
    assert row['frames'] == ['past.jpg', 'now.jpg']
    last = history_ablation(row, last_only=True)
    assert last['frames'] == ['now.jpg'] and last['frame_indices'] == [1]


def test_extra_coco_images_respect_official_split_and_prior_images():
    class COCO:
        imgs = {i: {} for i in range(1, 15)}
        def getCatIds(self, **kwargs):
            return [1]
        def getImgIds(self, **kwargs):
            return [1, 2, 3, 4, 5, 6, 9, 10, 11, 12]
    refs = [{'image_id': i, 'split': 'train' if i <= 8 else 'val'} for i in range(1, 15)]
    chosen = select_images(refs, COCO(), {1}, 'train', 5, 42)
    assert len(chosen) == 5 and 1 not in chosen
    assert set(chosen) <= set(range(2, 9))
    assert len(set(chosen) & {2, 3, 4, 5, 6}) == 4


def test_state_record_uses_now_history_and_not_future_safety_templates():
    annotation = {0: {'attributes': {'action': 'standing'}},
                  15: {'attributes': {'action': 'walking'}},
                  30: {'attributes': {'action': 'standing'}}}
    row = state_record('v', 'p', [0, 15], ['0.jpg', '15.jpg'], annotation)
    answer = json.loads(row['answer'])
    assert answer['current_activity'] == 'WALKING'
    assert answer['action'] == 'OBSERVE' and answer['motion'] == 'UNCERTAIN'
    assert 'future_human_attributes' not in row
    assert row['source_frame_indices'] == [0, 15]


def test_new_dataset_refuses_recording_leakage(tmp_path):
    masks = {s: [{'id': 'mask_' + s, 'group_id': 'same_image',
                  'frames': [s + '.jpg'], 'sampling_group': 'mask:positive'}] for s in ('train', 'val')}
    states = {s: [{'id': 'state_' + s, 'group_id': s, 'frames': [s + '_state.jpg'],
                   'frames_reused': False, 'sampling_group': 'state:WALKING:STEADY'}] for s in ('train', 'val')}
    with pytest.raises(ValueError, match='leakage'):
        finalize(tmp_path, masks, states, [], 0)


def test_pending_retail_suggestions_are_excluded(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'READY').touch()
    (data / 'store_pending.jsonl').write_text(json.dumps({'id': 'store1'}) + '\n')
    reviews = tmp_path / 'review.csv'
    with reviews.open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=['id', 'review_status'])
        writer.writeheader()
        writer.writerow({'id': 'store1', 'review_status': 'pending'})
    with pytest.raises(ValueError, match='No accepted'):
        apply(data, reviews, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_teacher_variant_never_uses_teacher_validation_labels(tmp_path):
    dataset, teacher = tmp_path / 'data', tmp_path / 'teacher'
    dataset.mkdir()
    teacher.mkdir()
    for root in (dataset, teacher):
        (root / 'READY').touch()
    for split in ('train', 'val'):
        row = {'id': split, 'group_id': split, 'frames': [split + '.jpg'], 'mask': split + '.png'}
        (dataset / f'{split}.jsonl').write_text(json.dumps(row) + '\n')
    (dataset / 'sampling_weights.json').write_text('{"mask:positive":1}')
    candidates = [{'id': 'teacher_' + split, 'group_id': 'store_' + split,
                   'frames': [split + '_store.jpg'], 'split': split, 'mask': 'teacher.png',
                   'human_reviewed': False} for split in ('train', 'val')]
    (teacher / 'candidates.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in candidates))
    output = tmp_path / 'out'
    report = mix_teacher(dataset, teacher, output)
    assert (output / 'val.jsonl').read_bytes() == (dataset / 'val.jsonl').read_bytes()
    assert report['weak_train_mask_clips'] == 1
    assert report['teacher_val_clips_excluded'] == 1
    train = list(map(json.loads, (output / 'train.jsonl').read_text().splitlines()))
    assert not train[-1]['human_reviewed']
    assert sum(json.loads((output / 'sampling_weights.json').read_text()).values()) == pytest.approx(1)
