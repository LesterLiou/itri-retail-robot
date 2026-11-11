#!/usr/bin/env python3
"""CPU-only deterministic cache-boundary tests; no ROS and no model weights."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0,'/app')
import torch
from transformers import AutoTokenizer
from lightweight_risk_vqa.robot_decoding import RobotJsonDecoder
from vqa_worker import generate_grammar_cached

torch.set_num_threads(2)
tokenizer=AutoTokenizer.from_pretrained(Path(os.environ['VQA_CHECKPOINT'])/'tokenizer',local_files_only=True)
grammar=RobotJsonDecoder(tokenizer)
cases=[dict(observation='A person is approaching.',motion='TOWARD_CAMERA',approach_intent='LIKELY',collision_risk='ELEVATED',action='DANGER_WARNING',explanation='Person scale increases across frames.'),
       dict(observation='A person is directly ahead.',motion='TOWARD_CAMERA',approach_intent='LIKELY',collision_risk='IMMINENT',action='STOP',explanation='Immediate obstruction is visible.'),
       dict(observation='A person is stationary.',motion='STATIONARY',approach_intent='UNLIKELY',collision_risk='LOW',action='CONTINUE',explanation='No approach is visible in the history.'),
       dict(observation='Motion is unclear.',motion='UNCERTAIN',approach_intent='UNCERTAIN',collision_risk='UNCERTAIN',action='OBSERVE',explanation='Direction evidence is insufficient.')]
class Model:
    generation_config=SimpleNamespace(num_beams=None,repetition_penalty=None,no_repeat_ngram_size=None)
    def __init__(self,target):self.target=target;self.calls=0
    def __call__(self,inputs_embeds=None,input_ids=None,attention_mask=None,past_key_values=None,**kwargs):
        self.calls+=1
        if inputs_embeds is not None:count=0;prefix=inputs_embeds.shape[1]
        else:
            previous,prefix=past_key_values;values=input_ids[0].tolist()
            assert values==self.target[previous:previous+len(values)]
            count=previous+len(values)
        assert attention_mask.shape[1]==prefix+count
        scores=torch.zeros((1,1,len(tokenizer)),dtype=torch.float32)
        scores[0,0,self.target[count] if count<len(self.target) else tokenizer.eos_token_id]=100
        return SimpleNamespace(logits=scores,past_key_values=(count,prefix))
for case in cases:
    # Match the grammar's token boundaries; tokenizing the whole JSON merges
    # boundary punctuation differently and is not a legal reference stream.
    fields={key:case[key] for key in ('motion','approach_intent','collision_risk','action')}
    header=','+json.dumps(fields,separators=(',',':'))[1:-1]+',"explanation":"'
    target=(grammar.opening+tokenizer.encode(case['observation'],add_special_tokens=False)+[grammar.quote]
        +tokenizer.encode(header,add_special_tokens=False)+tokenizer.encode(case['explanation'],add_special_tokens=False)
        +[grammar.quote]+grammar.close)
    for limit in [1,7,len(target)-1,192]:
        model=Model(target)
        result=generate_grammar_cached(model,torch.zeros((1,3,4)),torch.ones((1,3),dtype=torch.long),limit,grammar.processor())
        assert result[0].tolist()==target[:limit],(limit,tokenizer.decode(result[0]))
        if limit==192:assert model.calls<len(target)//2
print('PASS: 16 CPU greedy/KV-cache boundary cases, exact forced/free tokens, truncation, all four actions; no model weights or ROS')
