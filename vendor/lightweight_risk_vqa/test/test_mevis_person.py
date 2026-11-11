import json

import pytest

from lightweight_risk_vqa.prepare_mevis_person import person_subject, whole_prefix_indices
from lightweight_risk_vqa.mix_mevis_person import prepare


def test_person_filter_does_not_match_an_animal_with_a_person_in_the_background():
    assert person_subject('The man walking toward the camera')
    assert person_subject('two women running')
    assert not person_subject('The dog walking toward the man')
    assert not person_subject('Panda rolling around')


def test_mevis_observed_prefix_has_no_unobserved_later_frames():
    indices = whole_prefix_indices(100)
    assert indices[0] == 0 and indices[-1] == 99
    assert len(set(indices)) == 8
    assert indices == sorted(indices)
    with pytest.raises(ValueError):
        whole_prefix_indices(3)


def test_mevis_mixing_keeps_labels_and_checkpoint_selection_contract(tmp_path):
    dataset, mevis = tmp_path / 'base', tmp_path / 'mevis'
    for root in (dataset, mevis):
        root.mkdir()
        (root / 'READY').touch()
    for split, source in (('train', 'train'), ('val', 'valid_u')):
        base = {'id': split, 'group_id': split, 'frames': [split + '.jpg']}
        (dataset / f'{split}.jsonl').write_text(json.dumps(base) + '\n')
        row = {'id': 'mevis_' + split, 'group_id': 'mevis_' + split,
               'frames': [f'{split}_{i}.jpg' for i in range(8)], 'query': 'The man walking',
               'mask': split + '.png', 'eval_group': 'mevis', 'sampling_group': 'mevis'}
        (mevis / f'{source}_person.jsonl').write_text(json.dumps(row) + '\n')
    (dataset / 'sampling_weights.json').write_text('{"state":1}')
    report = prepare(dataset, mevis, tmp_path / 'out')
    rows = list(map(json.loads, (tmp_path / 'out/train.jsonl').read_text().splitlines()))
    assert rows[-1]['query'] == 'The man walking' and 'answer' not in rows[-1]
    assert report['selection_groups'] == ['segmentation', 'state']
    assert json.loads((tmp_path / 'out/sampling_weights.json').read_text()) == {'state': .8, 'mevis': .2}
