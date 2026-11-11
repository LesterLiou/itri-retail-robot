import pytest

from lightweight_risk_vqa.vqa_metrics import classification_metrics


def test_invalid_json_counts_as_wrong_and_minority_class_is_visible():
    rows = [
        {'expected': {'motion': 'A'}, 'prediction': {'motion': 'A'}},
        {'expected': {'motion': 'A'}, 'prediction': '{invalid'},
        {'expected': {'motion': 'B'}, 'prediction': {'motion': 'A'}},
    ]
    metrics = classification_metrics(rows, 'motion', {'A', 'B'})
    assert metrics['labeled_samples'] == 3
    assert metrics['accuracy'] == pytest.approx(1 / 3)
    assert metrics['per_class']['B']['recall'] == 0
    assert metrics['macro_f1'] == pytest.approx(.25)


def test_unlabeled_samples_are_not_scored():
    assert classification_metrics([{'expected': None}], 'motion', {'A'})['accuracy'] is None


def test_schema_invalid_object_cannot_score_by_copying_one_correct_field():
    rows = [{'expected': {'motion': 'A'}, 'prediction': {'motion': 'A'}, 'schema_valid': False}]
    assert classification_metrics(rows, 'motion', {'A'})['accuracy'] == 0
