"""Native pretrained VLM reference on identical resized still images, not a temporal model.

Also measures the student full model call on 24 identical count inputs. Timings exclude
disk/processor work, include vision and text generation, run serially after warmup.
"""
import argparse
import gc
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch

from .audit_visual_grounding import parse_answer
from .recovery_data import read_rows
from .temporal_vqa_infer import load_frames,time_deltas


def main():
    from transformers import AutoProcessor,Qwen3VLForConditionalGeneration
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--teacher',type=Path,required=True)
    p.add_argument('--student',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='cuda:0')
    args=p.parse_args()
    model=Qwen3VLForConditionalGeneration.from_pretrained(args.teacher,local_files_only=True,
              dtype=torch.bfloat16,attn_implementation='sdpa').to(args.device).eval()
    processor=AutoProcessor.from_pretrained(args.teacher,local_files_only=True)
    results=[]
    for task in ['count','caption']:
        rows=read_rows(args.data/'grounding'/f'{task}_val.jsonl')
        if task=='caption':
            rows=rows[:12]
        for row in rows:
            image=Image.open(row['frames'][-1]).convert('RGB').resize((320,320),Image.Resampling.BILINEAR)
            messages=[{'role':'user','content':[{'type':'image','image':image},{'type':'text','text':row['query']}]}]
            inputs=processor.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,
                         return_dict=True,return_tensors='pt').to(args.device)
            torch.cuda.synchronize()
            started=time.perf_counter()
            with torch.inference_mode():
                generated=model.generate(**inputs,max_new_tokens=64,do_sample=False)
            torch.cuda.synchronize()
            elapsed=1000*(time.perf_counter()-started)
            raw=processor.decode(generated[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True).strip()
            normalized=raw.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            parsed=parse_answer(normalized,task)
            expected=parse_answer(row['answer'],task)
            record={'id':row['id'],'task':task,'raw':raw,'prediction':parsed,'expected':expected,
                    'generated_tokens':int(generated.shape[1]-inputs['input_ids'].shape[1]),
                    'strict_format_valid':parse_answer(raw,task) is not None,
                    'correct':parsed==expected if task=='count' else None,'model_total_ms':elapsed}
            results.append(record)
            print(json.dumps(record),flush=True)
    del model,processor,inputs,generated
    gc.collect()
    torch.cuda.empty_cache()
    from light_foundation_vlm.checkpoint import load_bundle
    student,tokenizer,_=load_bundle(args.student,torch.bfloat16)
    student.to(args.device).eval()
    student_times=[]
    student_lengths=[]
    for row in read_rows(args.data/'grounding/count_val.jsonl')[:24]:
        frames=load_frames(row,args.data,args.device)
        ids=torch.tensor([tokenizer.encode(row['query']+'\n<seg>\n',add_special_tokens=False)],device=args.device)
        torch.cuda.synchronize()
        started=time.perf_counter()
        with torch.inference_mode():
            encoded=student(frames,ids,dt=time_deltas(row,args.device),return_language_context=True)
            answer=student.generate_from_context(encoded['language_inputs'],encoded['language_attention_mask'],48)
        torch.cuda.synchronize()
        student_times.append(1000*(time.perf_counter()-started))
        student_lengths.append(int(answer.shape[1]))
    counted=[r for r in results if r['task']=='count']
    report={'teacher':str(args.teacher),'student':str(args.student),'image_size':320,
        'teacher_count_accuracy':sum(r['correct'] for r in counted)/len(counted),
        'teacher_strict_count_json_rate':sum(r['strict_format_valid'] for r in counted)/len(counted),
        'teacher_model_total_p50_ms':float(np.median([r['model_total_ms'] for r in counted[1:24]])),
        'student_model_total_p50_ms':float(np.median(student_times[1:])),
        'teacher_count_tokens_p50':float(np.median([r['generated_tokens'] for r in counted[1:24]])),
        'student_count_tokens_p50':float(np.median(student_lengths[1:])),
        'timing_note':'Same first24 count images, discard first call. Includes encoder/prefill/generation, excludes CPU loading/processing. Teacher native chat vs student trained plain prompt; different architectures/token counts/capacity.',
        'accuracy_note':'Teacher count accuracy trims only optional markdown fences; strict JSON rate also reported. Caption correctness is not automatically scored.',
        'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='results'}),flush=True)


if __name__=='__main__':
    main()
