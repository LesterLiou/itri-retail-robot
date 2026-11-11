import json
import csv

import numpy as np
import pytest

from lightweight_risk_vqa.prepare_sequences import main as prepare
from lightweight_risk_vqa.make_label_template import main as make_label_template
from lightweight_risk_vqa.temporal import FEATURE_NAMES, FEATURE_VERSION, LABELS
from lightweight_risk_vqa.label_with_llm import parse_label, find_pair, teacher_prompt


def test_export_uses_future_only_for_labels_and_keeps_past_features(tmp_path):
    path = tmp_path / 'tracks.jsonl'
    with path.open('w') as stream:
        for i in range(30):
            stream.write(json.dumps({'cameras': {'left': {'stamp_s': i / 5,
                'frame_width': 100, 'frame_height': 100, 'tracks': [{
                    'track_id': 'L1', 'visible': True, 'confidence': 0.9,
                    'bbox_xyxy': [30, 10, 60, 50 + i]}]}}}) + '\n')
    output = tmp_path / 'dataset.npz'
    prepare(['--tracks', str(path), '--session', 'recording_a', '--pseudo-labels', '--output', str(output)])
    with np.load(output, allow_pickle=False) as saved:
        assert saved['features'].shape[1:] == (16, 10)
        assert set(saved['sessions']) == {'recording_a'}
        assert str(saved['label_source']) == 'future_bbox_pseudo'
        # The final feature of each window is NOW's height, not the future label height.
        expected = (40 + saved['stamps'] * 5) / 100
        np.testing.assert_allclose(saved['features'][:, -1, 3], expected, atol=1e-6)


def test_label_template_lists_reviewable_now_and_future_without_guessing_labels(tmp_path):
    path = tmp_path / 'tracks.jsonl'
    with path.open('w') as stream:
        for i in range(35):
            stream.write(json.dumps({'cameras': {'left': {'stamp_s': i / 5,
                'frame_width': 100, 'frame_height': 100, 'tracks': [{
                    'track_id': 'L1', 'visible': True, 'confidence': 0.9,
                    'bbox_xyxy': [30, 10, 60, 50 + i]}]}}}) + '\n')
    labels = tmp_path / 'labels.csv'
    make_label_template(['--tracks', str(path), '--session', 'store_day1',
                         '--output', str(labels)])
    with labels.open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert rows and all(row['label'] == '' for row in rows)
    assert min(float(row['start_s']) for row in rows) >= 3.0
    assert all(float(row['review_until_s']) - float(row['start_s']) == pytest.approx(1.0)
               for row in rows)
    for row in rows:
        row['label'] = 'TOWARD_CAMERA'
    with labels.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    output = tmp_path / 'manual.npz'
    prepare(['--tracks', str(path), '--session', 'store_day1',
             '--labels', str(labels), '--output', str(output)])
    with np.load(output, allow_pickle=False) as saved:
        assert set(saved['labels']) == {LABELS.index('TOWARD_CAMERA')}


def test_label_template_explains_missing_tracks_file(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        make_label_template(['--tracks', str(tmp_path / 'missing.jsonl'),
                             '--session', 'day1', '--output', str(tmp_path / 'labels.csv')])
    assert exc.value.code == 2
    assert 'Run video_benchmark or set record_tracks_path first' in capsys.readouterr().err


def test_llm_teacher_requires_unambiguous_answer_and_continuous_future():
    assert teacher_prompt().count('<image>') == 1
    assert parse_label('L2: TOWARD_CAMERA') == 'TOWARD_CAMERA'
    assert parse_label('AWAY_FROM_CAMERA') == 'AWAY_FROM_CAMERA'
    assert parse_label('probably TOWARD_CAMERA') is None
    assert parse_label('TOWARD_CAMERA or AWAY_FROM_CAMERA') is None
    rows = [{'stamp_s': 3.0}, {'stamp_s': 3.2}, {'stamp_s': 4.0}]
    assert find_pair(rows, 3.0, 4.0, 0.6) is None
    rows.insert(2, {'stamp_s': 3.6})
    assert find_pair(rows, 3.0, 4.0, 0.6) is not None


def test_teacher_provenance_is_preserved_in_export(tmp_path):
    path = tmp_path / 'tracks.jsonl'
    with path.open('w') as stream:
        for i in range(30):
            stream.write(json.dumps({'cameras': {'left': {'stamp_s': i / 5,
                'frame_width': 100, 'frame_height': 100, 'tracks': [{
                    'track_id': 'L1', 'visible': True, 'confidence': 0.9,
                    'bbox_xyxy': [30, 10, 60, 50 + i]}]}}}) + '\n')
    labels = tmp_path / 'teacher.csv'
    labels.write_text('session,camera,track_id,start_s,end_s,label,review_until_s\n'
                      'day1,left,L1,3.0,3.0,TOWARD_CAMERA,4.0\n')
    output = tmp_path / 'teacher.npz'
    prepare(['--tracks', str(path), '--session', 'day1', '--labels', str(labels),
             '--label-source', 'llm_teacher_bbox_agreed', '--output', str(output)])
    with np.load(output, allow_pickle=False) as saved:
        assert str(saved['label_source']) == 'llm_teacher_bbox_agreed'
        assert len(saved['labels']) == 1


def write_dataset(path, session):
    rng = np.random.default_rng(42)
    np.savez(path, features=rng.normal(size=(10, 16, 10)).astype(np.float32),
        labels=np.arange(10, dtype=np.int64) % len(LABELS), sessions=np.full(10, session),
        feature_version=np.asarray(FEATURE_VERSION), feature_names=np.asarray(FEATURE_NAMES),
        label_names=np.asarray(LABELS), sample_hz=np.asarray(5.0),
        prediction_horizon_s=np.asarray(1.0), label_source=np.asarray('manual'))


def test_training_rejects_session_leakage_and_saved_checkpoint_is_loadable(tmp_path):
    pytest.importorskip('torch')
    from lightweight_risk_vqa.train_lstm import main as train
    from lightweight_risk_vqa.lstm_model import TrainedPredictor
    first, second, checkpoint = tmp_path / 'train.npz', tmp_path / 'val.npz', tmp_path / 'motion.pt'
    write_dataset(first, 'session_a')
    write_dataset(second, 'session_a')
    with pytest.raises(ValueError, match='session overlap'):
        train(['--train', str(first), '--val', str(second), '--output', str(checkpoint), '--epochs', '1'])
    write_dataset(second, 'session_b')
    train(['--train', str(first), '--val', str(second), '--output', str(checkpoint), '--epochs', '1'])
    predictor = TrainedPredictor(str(checkpoint), 16, 5.0)
    label, confidence = predictor.predict(np.zeros((16, 10), dtype=np.float32))
    assert label in LABELS and 0 <= confidence <= 1
    # Exercise the deployed temporal estimator, not only the standalone network.
    from lightweight_risk_vqa.temporal import TemporalEstimator
    estimator = TemporalEstimator(mode='lstm', checkpoint=str(checkpoint))
    for i in range(16):
        prediction = estimator.update('L1', i / 5, [10, 10, 50, 90], 0.9, 100, 100)
    assert prediction['source'] == 'lstm' and prediction['sequence_ready']
    assert isinstance(prediction['confidence'], float)
    with pytest.raises(ValueError, match='sampling'):
        TrainedPredictor(str(checkpoint), 8, 5.0)
