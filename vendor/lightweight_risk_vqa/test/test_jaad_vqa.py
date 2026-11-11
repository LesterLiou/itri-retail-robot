import json
import csv

import pytest

from lightweight_risk_vqa.prepare_jaad_vqa import (
    answer_for, candidates_for_track, read_splits, read_tracks,
)


def dense_track():
    return {i: {'box': (100., 100., 300., 300. + i),
                'attributes': {'occlusion': 'none'}, 'occluded': False}
            for i in range(181)}


def test_sampling_keeps_future_out_of_history():
    records = candidates_for_track(dense_track(), 30, 1000, 1000)
    assert records
    for row in records:
        assert len(row['indices']) == 8
        assert max(row['indices']) == row['now']
        assert row['future'] - row['now'] == 30
        assert row['future'] not in row['indices']
        assert row['now'] - row['indices'][0] == 105


def test_missing_or_fully_occluded_future_is_not_a_weak_label():
    rows = dense_track()
    rows.pop(125)
    assert not candidates_for_track(rows, 30, 1000, 1000)
    rows = dense_track()
    rows[125]['attributes']['occlusion'] = 'full'
    assert not candidates_for_track(rows, 30, 1000, 1000)


def test_split_overlap_is_rejected(tmp_path):
    for name in ('train', 'val', 'test'):
        (tmp_path / f'{name}.txt').write_text('video_0001\n')
    with pytest.raises(ValueError, match='overlapping'):
        read_splits(tmp_path)


def test_xml_excludes_groups_and_outside_boxes(tmp_path):
    path = tmp_path / 'annotation.xml'
    path.write_text('''<annotations><meta><task><original_size><width>1920</width>
      <height>1080</height></original_size></task></meta>
      <track label="pedestrian"><box frame="0" xtl="10" ytl="20" xbr="100" ybr="200">
        <attribute name="id">person_1</attribute></box>
      <box frame="1" outside="1" xtl="10" ytl="20" xbr="100" ybr="200">
        <attribute name="id">person_1</attribute></box></track>
      <track label="people"><box frame="0" xtl="10" ytl="20" xbr="100" ybr="200">
        <attribute name="id">group_1</attribute></box></track></annotations>''')
    width, height, tracks = read_tracks(path)
    assert (width, height) == (1920, 1080)
    assert set(tracks) == {'person_1'}
    assert set(tracks['person_1']) == {0}


def test_answer_does_not_train_provenance_or_unknown_action():
    answer = json.loads(answer_for('TOWARD_CAMERA'))
    assert set(answer) == {'motion', 'action', 'explanation'}
    assert answer['action'] == 'DANGER_WARNING'


def test_review_requires_explicit_human_approval(tmp_path):
    from lightweight_risk_vqa.apply_jaad_review import apply_review
    root = tmp_path / 'dataset'
    (root / 'manifests').mkdir(parents=True)
    frame = tmp_path / 'example.jpg'
    frame.write_bytes(b'placeholder')
    reviews = []
    for split in ('train', 'val', 'test'):
        row = {'id': split, 'split': split, 'frames': [str(frame)],
               'answer': answer_for('STATIONARY'), 'human_reviewed': False}
        (root / 'manifests' / f'{split}.jsonl').write_text(json.dumps(row) + '\n')
        reviews.append({'id': split, 'split': split, 'review_status': 'pending',
                        'reviewed_motion': '', 'notes': ''})
    path = tmp_path / 'review.csv'

    def write_reviews():
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(reviews[0]))
            writer.writeheader()
            writer.writerows(reviews)

    write_reviews()
    with pytest.raises(ValueError, match='Approve at least one'):
        apply_review(root, path, tmp_path / 'pending_output')
    assert not (tmp_path / 'pending_output').exists()
    for row in reviews:
        row.update(review_status='approved', reviewed_motion='AWAY_FROM_CAMERA')
    write_reviews()
    summary = apply_review(root, path, tmp_path / 'reviewed_output')
    assert summary['splits']['train']['samples'] == 1
    result = json.loads((tmp_path / 'reviewed_output' / 'train.jsonl').read_text())
    assert result['human_reviewed'] is True
    assert json.loads(result['answer'])['motion'] == 'AWAY_FROM_CAMERA'
    assert json.loads(result['original_weak_answer'])['motion'] == 'STATIONARY'
