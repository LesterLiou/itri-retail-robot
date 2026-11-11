"""Summarize real reloaded-bundle evaluations and show paired images/answers locally."""
import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path

from .recovery_data import read_rows
from .vqa_metrics import classification_metrics

VARIANTS=['baseline','a_lora','a_last_layer','b_temporal','b_balanced','c_policy']


def summarize(directory):
    report={}
    for variant in VARIANTS:
        data={}
        for task in ['count','presence','interventions','caption','state','robot']:
            path=directory/f'{variant}_{task}.json'
            if not path.exists():
                continue
            source=json.loads(path.read_text())
            data[task]=source.get('summary',{})
            if task in {'count','presence'}:
                scored=[r for r in source['results'] if r['condition']=='real' and r['decoder']=='free']
                targets=[json.dumps(r['expected'],sort_keys=True) for r in scored]
                data[task]['majority_accuracy']=max(Counter(targets).values())/len(targets)
                if task=='count':
                    by_count={}
                    for category in ['0','1','2','3+']:
                        subset=[r for r in scored if ('3+' if r['expected']['person_count']>=3 else str(r['expected']['person_count']))==category]
                        by_count[category]={'samples':len(subset),'accuracy':sum(r['correct'] for r in subset)/len(subset) if subset else None}
                    data[task]['free_accuracy_by_count']=by_count
            if task in {'state','robot'}:
                for key in data[task]:
                    rows=[r for r in source['results'] if r['condition']+':'+r['decoder']==key]
                    fields={'motion','action'} if task=='robot' else {'current_activity','observed_transition'}
                    for field in fields:
                        labels=sorted({r['expected'][field] for r in rows})
                        data[task][key][field+'_metrics']=classification_metrics(rows,field,labels)
                    data[task][key]['stop_outputs']=sum(isinstance(r['prediction'],dict) and r['prediction'].get('action')=='STOP' for r in rows)
                if task=='state':
                    full={r['id']:r for r in source['results'] if r['condition']=='real'}
                    repeated={r['id']:r for r in source['results'] if r['condition']=='repeat'}
                    shared=set(full)&set(repeated)
                    def transition(r):
                        return r['prediction'].get('observed_transition') if isinstance(r['prediction'],dict) and r.get('schema_valid') else None
                    events=[key for key in shared if full[key]['expected']['observed_transition']!='STEADY']
                    data[task]['paired_diagnostics']={'pairs':len(shared),
                        'transition_changed_full_vs_repeat':sum(transition(full[k])!=transition(repeated[k]) for k in shared),
                        'nonsteady_samples':len(events),
                        'nonsteady_full_correct':sum(transition(full[k])==full[k]['expected']['observed_transition'] for k in events),
                        'nonsteady_repeat_correct':sum(transition(repeated[k])==full[k]['expected']['observed_transition'] for k in events)}
        for task in ['masks','mevis_masks']:
            path=directory/f'{variant}_{task}'/'summary.json'
            if path.exists():
                data[task]=json.loads(path.read_text())
        report[variant]=data
    path=directory/'fast_motion.json'
    if path.exists():
        head=json.loads(path.read_text())
        report['fast_motion']={k:v for k,v in head.items() if k!='results'}
        report['fast_motion']['ablations']={k:v['metrics'] for k,v in head['results'].items()}
    path=directory.parent/'teacher_agreement.json'
    if path.exists():
        report['teacher_agreement']=json.loads(path.read_text())
    path=directory.parent/'ui_summary.json'
    if path.exists():
        report['ui_smoke']=json.loads(path.read_text())
    path=directory/'native_teacher.json'
    if path.exists():
        native=json.loads(path.read_text())
        report['native_teacher']={k:v for k,v in native.items() if k!='results'}
        counted=[r for r in native['results'] if r['task']=='count']
        report['native_teacher']['normalized_count_json_rate']=sum(isinstance(r['prediction'],dict) for r in counted)/len(counted)
    for key,file in [('streaming_memory','streaming_memory.json'),('streaming_before_layout_fix','streaming_memory_before_layout_fix.json')]:
        path=directory/file
        if path.exists():
            stream=json.loads(path.read_text())
            report[key]=[{'steps':r['steps'],'stream_batch_state_max_abs_difference':r['stream_batch_state_max_abs_difference'],
                          'schema_error':r['answer']['error']} for r in stream['results']]
    report['notes']=['Development validation, single seed; no new blind-test claim.',
        'Counts/presence use official COCO annotations. State subset is stratified 64-clip JAAD human labels.',
        'Motion/policy metrics measure weak geometry agreement, not human intention or collision accuracy.',
        'Blank intervention repeats one black input; those trials are not independent images.',
        'Finite/robot decoding enforces format/policy only. Check free generation separately.']
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True)
    args=p.parse_args()
    report=summarize(args.results)
    (args.results/'summary.json').write_text(json.dumps(report,indent=2))
    esc=html.escape
    def image_tag(path,width=250):
        return f'<img loading="lazy" width="{width}" src="{esc(os.path.relpath(path,args.results))}">'
    articles=['<!doctype html><meta charset="utf-8"><title>VQA recovery comparison</title>',
        '<style>body{font:16px sans-serif;max-width:1500px;margin:30px auto}article{border-top:1px solid #aaa;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}td{vertical-align:top;padding:10px;border:1px solid #ccc}table{border-collapse:collapse;width:100%}</style>',
        '<h1>Reloaded-bundle VQA recovery experiments</h1><p>Human COCO / JAAD state labels; weak geometry motion / policy labels. Format compliance is not accuracy. No collision GT.</p>',
        '<details><summary>Complete metrics</summary><pre>'+esc(json.dumps(report,indent=2))+'</pre></details>']
    if 'teacher_agreement' in report:
        retail={r['id']:r for r in read_rows(args.data/'retail/teacher.jsonl')}
        articles.append('<h2>Teacher count disagreements — human review pending</h2>')
        for entry in report['teacher_agreement']['comparisons']:
            if entry['agree']:
                continue
            row=retail[entry['id']]
            articles.append('<article><h3>'+esc(entry['id'])+'</h3>'+image_tag(row['frames'][-1],450)+
                            '<p>Qwen count='+str(entry['qwen_count'])+'; YOLO count='+str(entry['yolo_count'])+'</p><pre>'+esc(row['teacher_raw'])+'</pre></article>')
    for task,manifest in [('count',args.data/'grounding/count_val.jsonl'),('caption',args.data/'grounding/caption_val.jsonl'),('robot',args.data/'causal_motion/val.jsonl')]:
        rows={r['id']:r for r in read_rows(manifest)}
        predictions={}
        for variant in VARIANTS:
            path=args.results/f'{variant}_{task}.json'
            if path.exists():
                predictions[variant]={r['id']:r for r in json.loads(path.read_text())['results'] if r['decoder']=='free' and r['condition']=='real'}
        native=args.results/'native_teacher.json'
        if task in {'count','caption'} and native.exists():
            predictions['native_Qwen3VL2B_no_LSTM']={r['id']:r for r in json.loads(native.read_text())['results'] if r['task']==task}
        ids=list(next(iter(predictions.values()),{}))[:24 if task!='caption' else 12]
        if task=='robot':
            all_ids=list(next(iter(predictions.values()),{}))
            ids=all_ids[:12]+[key for key in all_ids if 'raw_0617' in key]
        articles.append(f'<h2>{task} — paired current images</h2>')
        for key in ids:
            row=rows[key]
            articles.append('<article><h3>'+esc(key)+'</h3>'+image_tag(row['frames'][-1],350)+
                            '<p>Reference '+('WEAK policy proxy' if task=='robot' else 'human annotation')+': '+esc(row['answer'])+'</p><table><tr>')
            for variant,preds in predictions.items():
                prediction=preds.get(key,{})
                raw=prediction.get('raw','missing')
                articles.append('<td><b>'+esc(variant)+'</b><pre>'+esc(raw[:700])+('</pre><p>correct='+str(prediction.get('correct'))+'</p>' if task=='count' else '</pre>')+'</td>')
            articles.append('</tr></table></article>')
    articles.append('<h2>Mask ground-truth comparisons</h2>')
    for variant in VARIANTS:
        for kind in ['masks','mevis_masks']:
            file=args.results/f'{variant}_{kind}'/'index.html'
            if file.exists():
                articles.append(f'<p><a href="{esc(os.path.relpath(file,args.results))}">{esc(variant+" "+kind)}</a></p>')
    articles.append('<h2>Actual Qt/video/model UI smoke screenshots</h2><p>Syntax and timing verification; no human motion/collision ground truth.</p>')
    for mode in ['vqa-only','yolo-assisted','fast-head']:
        screenshot=args.results.parent/f'ui_v3_{mode}'/'ui.png'
        if screenshot.exists():
            articles.append('<h3>'+esc(mode)+'</h3>'+image_tag(screenshot,1200))
    (args.results/'index.html').write_text(''.join(articles))
    print(json.dumps({'summary':str(args.results/'summary.json'),'report':str(args.results/'index.html')}))


if __name__=='__main__':
    main()
