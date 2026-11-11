"""Paired image interventions and history ablations; never infer grounding from JSON validity."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import time

import numpy as np
import torch

from .recovery_data import read_rows
from .temporal_vqa_infer import load_frames, time_deltas


class FiniteAnswers:
    """Finite answer trie. It restricts format, without consulting expected labels."""
    def __init__(self, tokenizer, texts):
        self.root = {}
        self.eos = tokenizer.eos_token_id
        for text in texts:
            node = self.root
            for token in tokenizer.encode(text, add_special_tokens=False):
                node = node.setdefault(token, {})
            node[self.eos] = {}

    def __call__(self, ids, scores):
        # HF inputs_embeds-only generation starts with an empty generated sequence.
        node = self.root
        for token in ids[0].tolist():
            if token == self.eos:
                node = {self.eos: {}}
                break
            node = node.get(token, {})
        if not node:
            raise ValueError('Generation left finite answer trie')
        out = torch.full_like(scores, -torch.inf)
        allowed = list(node)
        out[:, allowed] = scores[:, allowed]
        return out


def intervene(row, condition, peer=None):
    row = dict(row)
    if condition == 'mismatch':
        row.update(frames=peer['frames'], frame_indices=peer['frame_indices'],
                   person_count=peer['person_count'], answer=peer['answer'])
    elif condition == 'last':
        row['frames'] = row['frames'][-1:]
        key = 'frame_indices' if row.get('time_unit') == 'frames' else 'timestamps_s'
        row[key] = row[key][-1:]
    elif condition == 'repeat':
        row['frames'] = row['frames'][-1:] * len(row['frames'])
    elif condition == 'shuffle':
        past = row['frames'][:-1].copy()
        random.Random(42).shuffle(past)
        row['frames'] = past + row['frames'][-1:]
    return row


def parse_answer(raw, task):
    if task == 'caption':
        return raw.strip() if len(raw.strip())>3 else None
    if task == 'presence':
        value = raw.strip().lower().rstrip('.')
        return value if value in {'yes', 'no'} else None
    try:
        value = json.loads(raw)
        if task=='count' and (not isinstance(value,dict) or type(value.get('person_count')) is not int or value['person_count']<0 or type(value.get('person_present')) is not bool or value['person_present']!=(value['person_count']>0)):
            return None
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=24)
    parser.add_argument('--conditions', default='real,blank,mismatch')
    parser.add_argument('--decoders', default='free,finite')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--max-new-tokens',type=int,default=96)
    args = parser.parse_args()
    if args.samples<1 or args.max_new_tokens<1:
        parser.error('Positive sample and token limits required')
    if set(args.conditions.split(','))-{'real','blank','mismatch','shuffle','repeat','last'} or set(args.decoders.split(','))-{'free','finite','robot'}:
        parser.error('Unknown image/history intervention or decoder')
    using_cuda=torch.device(args.device).type=='cuda'
    from light_foundation_vlm.checkpoint import load_bundle
    from transformers import LogitsProcessorList,StoppingCriteriaList
    from .robot_vqa import StopAfterJsonObject,robot_schema_error
    model, tokenizer, metadata = load_bundle(args.checkpoint, torch.bfloat16 if using_cuda else torch.float32)
    model.to(args.device).eval()
    robot_decoder=None
    if 'robot' in args.decoders.split(','):
        from .robot_decoding import RobotJsonDecoder
        robot_decoder=RobotJsonDecoder(tokenizer)
    source = read_rows(args.manifest)
    rows = source[:args.samples]
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for row_index,row in enumerate(rows):
        task = row.get('task', 'temporal')
        peer = next((source[(row_index+offset)%len(source)] for offset in range(1,len(source))
                     if source[(row_index+offset)%len(source)].get('person_count') != row.get('person_count')), None)
        if task == 'presence':
            texts = ['yes', 'no']
        elif task == 'count':
            texts = [json.dumps({'person_count': n, 'person_present': n > 0}, separators=(',', ':')) for n in range(21)]
        else:
            texts = None
        for condition in args.conditions.split(','):
            current = intervene(row, condition, peer)
            frames = load_frames(current, args.manifest.parent, args.device)
            if condition == 'blank':
                frames[:] = -frames.new_tensor((.485/.229, .456/.224, .406/.225))[None,None,:,None,None]
                current['person_count'] = 0
                current['answer'] = 'no' if task == 'presence' else json.dumps({'person_count':0, 'person_present':False})
            ids = torch.tensor([tokenizer.encode(current['query']+'\n<seg>\n',add_special_tokens=False)],device=args.device)
            with torch.inference_mode():
                encoded = model(frames, ids, attention_mask=torch.ones_like(ids), dt=time_deltas(current,args.device), return_language_context=True)
            for decoder in args.decoders.split(','):
                if decoder == 'finite' and texts is None:
                    continue
                processor = LogitsProcessorList([FiniteAnswers(tokenizer,texts)]) if decoder == 'finite' else None
                if decoder=='robot':
                    processor=LogitsProcessorList([robot_decoder.processor()])
                stops=StoppingCriteriaList([StopAfterJsonObject(tokenizer)]) if task not in {'caption','presence'} else None
                if using_cuda:
                    torch.cuda.synchronize()
                start = time.perf_counter()
                with torch.inference_mode():
                    generated = model.generate_from_context(encoded['language_inputs'],encoded['language_attention_mask'],max_new_tokens=args.max_new_tokens,logits_processor=processor,stopping_criteria=stops)
                if using_cuda:
                    torch.cuda.synchronize()
                raw = tokenizer.decode(generated[0],skip_special_tokens=True).strip()
                parsed = parse_answer(raw, task)
                expected = parse_answer(current.get('answer',''),task)
                correct = (parsed == expected) if task == 'presence' else (parsed is not None and expected is not None and all(parsed.get(k)==v for k,v in expected.items())) if task == 'count' else None
                result = {'id':row['id'],'condition':condition,'decoder':decoder,'task':task,
                          'input_source_id':peer['id'] if condition=='mismatch' else 'black_image' if condition=='blank' else row['id'],
                          'raw':raw,'prediction':parsed,'expected':expected,'correct':correct,
                          'valid_format':parsed is not None,'latency_ms':1000*(time.perf_counter()-start)}
                if task == 'temporal' and isinstance(expected,dict):
                    result['field_correct'] = {k: isinstance(parsed,dict) and parsed.get(k)==v for k,v in expected.items() if k in {'motion','current_activity','observed_transition','action'}}
                    if 'collision_risk' in expected:
                        result['schema_error']=robot_schema_error(parsed)
                        result['schema_valid']=not result['schema_error']
                    elif row.get('state_task'):
                        from .person_state import state_schema_error
                        result['schema_error']=state_schema_error(parsed)
                        result['schema_valid']=not result['schema_error']
                results.append(result)
                print(json.dumps(result),flush=True)
                args.output.write_text(json.dumps({'checkpoint':str(args.checkpoint),'results':results},indent=2))
    grouped = defaultdict(list)
    for result in results:
        grouped[result['condition']+':'+result['decoder']].append(result)
    summary = {}
    for key, group in grouped.items():
        scored = [r for r in group if r['correct'] is not None]
        fields = sorted({k for r in group for k in r.get('field_correct',{})})
        summary[key] = {'n':len(group),'format_rate':np.mean([r['valid_format'] for r in group]),
                        'schema_rate':np.mean([r.get('schema_valid',r['valid_format']) for r in group]),
                        'accuracy':np.mean([r['correct'] for r in scored]) if scored else None,
                        'field_accuracy':{k:np.mean([r['field_correct'][k] for r in group if k in r.get('field_correct',{})]) for k in fields},
                        'latency_p50_ms':float(np.median([r['latency_ms'] for r in group]))}
    args.output.write_text(json.dumps({'checkpoint':str(args.checkpoint),'summary':summary,'results':results},indent=2))
    print(json.dumps({'summary':summary}),flush=True)


if __name__ == '__main__':
    main()
