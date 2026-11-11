import csv
import json

import pytest

from lightweight_risk_vqa.apply_recovery_review import apply
from lightweight_risk_vqa.prepare_causal_motion import policy_answer
from lightweight_risk_vqa.recovery_data import write_rows,read_rows


def fixture_data(tmp_path):
    data=tmp_path/'data'
    (data/'retail').mkdir(parents=True)
    (data/'stages').mkdir()
    clips=[]
    for split in ['train','val']:
        key='raw_'+split
        clips.append({'id':key,'split':split})
        common={'group_id':split,'frames':[split+'.jpg'],'human_reviewed':False}
        write_rows(data/'stages'/f'c_{split}.jsonl',[
            {**common,'id':'causal_'+key,'mix_group':'weak_robot',
             'answer':policy_answer('STATIONARY','A person is visible.'),'event_label':3},
            {**common,'id':key+'_mask','mix_group':'weak_retail','task':'retail_mask','mask':'weak.png'}])
    write_rows(data/'retail/teacher.jsonl',clips)
    return data


def write_review(path,**overrides):
    row={'id':'raw_train','split':'train','status':'accept','reviewer':'test_reviewer',
         'motion_override':'TOWARD_CAMERA','collision_label':'','explanation_override':'',
         'observation_override':'A person is walking.'}
    row.update(overrides)
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def test_motion_review_never_upgrades_unreviewed_masks_or_intent(tmp_path):
    data=fixture_data(tmp_path)
    csv_path=tmp_path/'review.csv'
    write_review(csv_path)
    output=tmp_path/'new'
    apply(data,csv_path,output)
    robot,mask=read_rows(output/'stages/c_train.jsonl')
    assert json.loads(robot['answer'])['action']=='DANGER_WARNING'
    assert robot['event_label']==0
    assert set(robot['human_reviewed_fields'])=={'motion','observation'}
    assert not robot['human_reviewed'] and not mask['human_reviewed']
    assert 'human_reviewed_fields' not in mask
    assert read_rows(data/'stages/c_train.jsonl')[0]['event_label']==3


@pytest.mark.parametrize('overrides,error',[
    ({'status':'pending'},'No explicit'),
    ({'reviewer':''},'requires a reviewer'),
    ({'collision_label':'IMMINENT'},'explanation_override'),
    ({'split':'val'},'recording split')])
def test_review_failures_do_not_create_a_training_dataset(tmp_path,overrides,error):
    data=fixture_data(tmp_path)
    csv_path=tmp_path/'review.csv'
    write_review(csv_path,**overrides)
    output=tmp_path/'new'
    with pytest.raises(ValueError,match=error):
        apply(data,csv_path,output)
    assert not output.exists()
