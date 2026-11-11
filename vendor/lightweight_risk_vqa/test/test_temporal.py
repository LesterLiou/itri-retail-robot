import numpy as np
import pytest

from lightweight_risk_vqa.temporal import TemporalEstimator, box_motion, causal_sequence


def test_sequence_never_reads_future_observations():
    box = np.array([0.5, 0.5, 0.2, 0.4])
    history = [(i / 5, box.copy(), 0.9) for i in range(30)]
    history[20] = (4.0, np.array([99.0, 99.0, 99.0, 99.0]), 0.9)
    features = causal_sequence(history, 3.0)
    assert features.shape == (16, 10)
    assert np.max(features[:, :4]) < 1


def test_missing_history_and_long_internal_gap_do_not_produce_predictions():
    box = np.array([0.5, 0.5, 0.2, 0.4])
    assert causal_sequence([(3.0, box, 0.9)], 3.0) is None
    history = [(i / 5, box, 0.9) for i in range(16) if i not in (4, 5, 6, 7)]
    assert causal_sequence(history, 3.0) is None


def test_clock_reset_restarts_history_and_never_claims_ready():
    estimator = TemporalEstimator()
    for i in range(16):
        result = estimator.update('L1', i / 5, [10, 10, 50, 90], 0.9, 100, 100)
    assert result['sequence_ready']
    assert result['future_motion'] == 'STATIONARY'
    assert result['confidence'] is None  # Do not invent calibrated confidence for rules.
    result = estimator.update('L1', 0.0, [10, 10, 50, 90], 0.9, 100, 100)
    assert not result['sequence_ready']


def test_lstm_never_silently_runs_without_trained_weights():
    pytest.importorskip('torch')
    with pytest.raises(ValueError, match='trained checkpoint'):
        TemporalEstimator(mode='lstm', checkpoint='')


def test_visual_convlstm_checkpoint_has_actionable_incompatibility(tmp_path):
    torch = pytest.importorskip('torch')
    checkpoint = tmp_path / 'visual_convlstm.pt'
    torch.save({'memory.gates.weight': torch.zeros(256, 193, 3, 3)}, checkpoint)
    with pytest.raises(ValueError, match='visual ConvLSTM segmentation/VLM'):
        TemporalEstimator(mode='lstm', checkpoint=str(checkpoint))




def test_motion_classes_and_camera_local_history():
    first = np.array([0.5, 0.5, 0.2, 0.4])
    assert box_motion(first, np.array([0.5, 0.5, 0.2, 0.5])) == 'TOWARD_CAMERA'
    assert box_motion(first, np.array([0.5, 0.5, 0.2, 0.3])) == 'AWAY_FROM_CAMERA'
    assert box_motion(first, np.array([0.7, 0.5, 0.2, 0.4])) == 'LATERAL_ACROSS_VIEW'
    estimator = TemporalEstimator()
    for i in range(16):
        estimator.update('L1', i / 5, [10, 10, 50, 90], 0.9, 100, 100)
    result = estimator.update('R1', 3.0, [10, 10, 50, 90], 0.9, 100, 100)
    assert not result['sequence_ready']
