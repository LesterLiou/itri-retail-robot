import pytest
torch=pytest.importorskip('torch')

from lightweight_risk_vqa.audit_visual_grounding import intervene, FiniteAnswers
from lightweight_risk_vqa.audit_visual_grounding import parse_answer
from lightweight_risk_vqa.fast_motion import fast_motion_decision,head_labels
from lightweight_risk_vqa.prepare_causal_motion import box_motion,aggregate,policy_answer,CLASSES
from lightweight_risk_vqa.prepare_retail_recovery import geometry_motion
from lightweight_risk_vqa.recovery_data import check_groups
from lightweight_risk_vqa.robot_vqa import robot_schema_error


def test_history_ablation_keeps_now_and_original_unchanged():
    row={'frames':list(range(8)),'frame_indices':list(range(8)),'time_unit':'frames'}
    assert intervene(row,'shuffle')['frames'][-1]==7
    assert intervene(row,'shuffle')['frames']!=row['frames']
    assert intervene(row,'repeat')['frames']==[7]*8
    assert intervene(row,'last')['frame_indices']==[7]
    assert row['frames']==list(range(8))


def test_proxy_labels_are_causal_and_do_not_invent_stop():
    assert box_motion([0,0,20,40],[0,0,22,50],640,3.5)=='TOWARD_CAMERA'
    assert box_motion([0,0,20,40],[100,0,120,40],640,3.5)=='LATERAL_ACROSS_VIEW'
    hints={'people':[{'history_s':3.5,'height_change_fraction':.2,'center_delta_xy':[.01,.01]}]}
    assert geometry_motion(hints)=='TOWARD_CAMERA'
    assert aggregate(['STATIONARY','AWAY_FROM_CAMERA'])=='UNCERTAIN'
    import json
    for motion in CLASSES:
        parsed=json.loads(policy_answer(motion,'A person is visible.'))
        assert not robot_schema_error(parsed)
        assert parsed['action']!='STOP'


def test_group_and_frame_leakage_detected():
    with pytest.raises(ValueError,match='leakage'):
        check_groups({'train':[{'group_id':'a','frames':['x']}], 'val':[{'group_id':'b','frames':['x']}]})


def test_fast_head_is_independent_of_vqa_failure_but_checks_own_health():
    state={'frame_at':10.,'segmentation_evidence_at':10.,'segmentation_context':(0,'question'),
           'loop':0,'requested_query':'question','vqa_error':'bad language',
           'metrics':{'memory_steps_since_reset':8,'fast_motion':{'motion':'TOWARD_CAMERA','probability_uncalibrated':.8}}}
    assert fast_motion_decision(state,10.1)['action']=='DANGER_WARNING'
    state['metrics']['fast_motion']['probability_uncalibrated']=.4
    assert fast_motion_decision(state,10.1)['action']=='OBSERVE'
    assert fast_motion_decision(state,14.)['action']=='DATA_LOSS'
    state['metrics']['memory_steps_since_reset']=2
    assert fast_motion_decision(state,10.1)['reason_code']=='motion_history_warmup'
    with pytest.raises(ValueError):
        head_labels({})


def test_finite_decoder_never_sees_expected_label():
    class Tok:
        eos_token_id=9
        def encode(self,text,**kw): return [ord(c)-97 for c in text]
    grammar=FiniteAnswers(Tok(),['ab','ac'])
    scores=torch.zeros(1,10)
    allowed=grammar(torch.tensor([[0]]),scores).isfinite()[0].nonzero().flatten().tolist()
    assert allowed==[1,2]
    assert grammar(torch.tensor([[0,1]]),scores).isfinite()[0].nonzero().flatten().tolist()==[9]


def test_count_evaluation_rejects_boolean_counts_and_inconsistent_presence():
    from lightweight_risk_vqa.person_state import state_schema_error
    assert state_schema_error(None)=='state response is not a JSON object'
    assert parse_answer('{"person_count":true,"person_present":true}','count') is None
    assert parse_answer('{"person_count":0,"person_present":true}','count') is None
    assert parse_answer('{"person_count":0,"person_present":0}','count') is None
    assert parse_answer('{"person_count":0,"person_present":false}','count') == {'person_count':0,'person_present':False}


def test_coco_grounding_excludes_crowds_and_keeps_human_split(tmp_path):
    import json
    from lightweight_risk_vqa.recovery_data import prepare_coco,write_rows,read_rows
    source=tmp_path/'base'
    source.mkdir()
    write_rows(source/'segmentation_train.jsonl',[
        {'group_id':'coco1','frames':['/images/000000000001.jpg']},
        {'group_id':'coco2','frames':['/images/000000000002.jpg']}])
    write_rows(source/'segmentation_val.jsonl',[{'group_id':'coco3','frames':['/images/000000000003.jpg']}])
    annotations=tmp_path/'instances.json'
    annotations.write_text(json.dumps({'annotations':[
        {'category_id':1,'image_id':1,'iscrowd':0},
        {'category_id':1,'image_id':2,'iscrowd':1}]}))
    captions=tmp_path/'captions.json'
    captions.write_text(json.dumps({'annotations':[{'image_id':i,'id':i,'caption':f'Scene {i}.'} for i in (1,2,3)]}))
    output=tmp_path/'new'
    report=prepare_coco(source,annotations,[captions],output,10,10)
    train,val=read_rows(output/'train.jsonl'),read_rows(output/'val.jsonl')
    assert len(train)==len(val)==3
    assert {r['coco_image_id'] for r in train}=={1}
    assert {r['person_count'] for r in train}=={1}
    assert {r['person_count'] for r in val}=={0}
    assert next(r['answer'] for r in train if r['task']=='caption')=='Scene 1.'


def test_retail_masks_keep_sam3_provenance_instead_of_caption_teacher(tmp_path):
    from types import SimpleNamespace
    from lightweight_risk_vqa.recovery_data import write_rows,read_rows
    from lightweight_risk_vqa.prepare_retail_recovery import assemble
    (tmp_path/'sam3').mkdir()
    row={'id':'raw_fixture','split':'train','frames':[str(tmp_path/'frames/now.jpg')],
         'group_id':'train_recording','time_unit':'frames','frame_indices':[0],
         'teacher_parsed':{'person_count':1,'observation':'A person is standing.'},
         'teacher_model':'Qwen3-VL','teacher_revision':'qwen_revision',
         'human_reviewed':False,'motion_candidate':'STATIONARY','yolo_boxes':[[[0,0,.5,1]]]}
    write_rows(tmp_path/'clips.jsonl',[row])
    write_rows(tmp_path/'teacher.jsonl',[row])
    write_rows(tmp_path/'sam3/candidates.jsonl',[{'id':'teacher_mask_raw_fixture',
        'mask':str(tmp_path/'mask.png'),'teacher_model':'facebook/sam3',
        'teacher_revision':'sam3_revision','teacher_threshold':.5}])
    assemble(SimpleNamespace(output=tmp_path))
    records=read_rows(tmp_path/'train.jsonl')
    mask=next(r for r in records if r['task']=='retail_mask')
    caption=next(r for r in records if r['task']=='retail_caption')
    assert mask['teacher_model']=='facebook/sam3' and mask['teacher_revision']=='sam3_revision'
    assert caption['teacher_model']=='Qwen3-VL' and not mask['human_reviewed']
