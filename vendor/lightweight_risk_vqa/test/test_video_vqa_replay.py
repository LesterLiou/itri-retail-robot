import json

import pytest

from lightweight_risk_vqa.render_video_vqa import read_camera_records


def test_replay_rejects_same_track_ids_from_different_recordings(tmp_path):
    rows = [{'id': f'{group}_L1', 'track_id': 'L1', 'group_id': group, 'camera': 'left'}
            for group in ('store_A', 'store_B')]
    path = tmp_path / 'mixed.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(ValueError, match='single recording'):
        read_camera_records(path, 'left')


def test_replay_filters_camera_before_selecting_answers(tmp_path):
    rows = [{'id': 'left_person', 'group_id': 'store_A', 'camera': 'left'},
            {'id': 'right_person', 'group_id': 'store_A', 'camera': 'right'}]
    path = tmp_path / 'cameras.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert set(read_camera_records(path, 'left')) == {'left_person'}
