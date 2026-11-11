"""Paired old/new prompts on identical local clips; schema validity is not accuracy."""
import argparse
import json
from pathlib import Path
import statistics
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--previous-prompt', type=Path, help='Optional JSON query snapshot; defaults to the compatible query in source')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--samples', type=int, default=12)
    p.add_argument('--device', default='cuda:0')
    args = p.parse_args()
    if args.samples < 1 or args.output.exists():
        p.error('Use a positive sample count and a new output file')
    import torch
    from transformers import LogitsProcessorList, StoppingCriteriaList
    from light_foundation_vlm.checkpoint import load_bundle
    from .temporal_vqa_infer import load_frames, time_deltas
    from .robot_vqa import (ROBOT_QUERY, COMPACT_ROBOT_QUERY, PersonMotionHints, query_with_hints,
                            StopAfterJsonObject, robot_schema_error, json_object_end)
    from .robot_decoding import RobotJsonDecoder
    torch.set_num_threads(4)
    cuda = str(args.device).startswith('cuda')
    model, tokenizer, _ = load_bundle(args.checkpoint, torch.bfloat16 if cuda else torch.float32)
    model.to(args.device).eval()
    grammar = RobotJsonDecoder(tokenizer)
    old = json.loads(args.previous_prompt.read_text())['query'] if args.previous_prompt else ROBOT_QUERY
    source = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    n = min(args.samples, len(source))
    rows = [source[round(i*(len(source)-1)/max(n-1,1))] for i in range(n)]
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        tracker = PersonMotionHints()
        hints = None
        for step, boxes in enumerate(row.get('yolo_boxes', [])):
            hints = tracker.update(boxes, step*.5)
        queries = {'previous': old, 'compact': COMPACT_ROBOT_QUERY}
        if hints is not None:
            queries['previous'] += '\nSupporting YOLO history ending at this exact input frame:\n' + json.dumps(hints,separators=(',',':'),allow_nan=False)
            queries['compact'] = query_with_hints(COMPACT_ROBOT_QUERY,hints)
        frames = load_frames(row,args.manifest.parent,args.device)
        # Alternate order to avoid always giving one profile the cold first call.
        order = ('previous','compact') if len(results)%4 == 0 else ('compact','previous')
        for profile in order:
            ids = torch.tensor([tokenizer.encode(queries[profile]+'\n<seg>\n',add_special_tokens=False)],device=args.device)
            if cuda:
                torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode():
                context = model(frames,ids,dt=time_deltas(row,args.device),
                                compute_masks=False,return_language_context=True)
                generated = model.generate_from_context(context['language_inputs'],context['language_attention_mask'],
                    192,logits_processor=LogitsProcessorList([grammar.processor()]),
                    stopping_criteria=StoppingCriteriaList([StopAfterJsonObject(tokenizer)]))
            if cuda:
                torch.cuda.synchronize()
            elapsed = 1000*(time.perf_counter()-start)
            raw = tokenizer.decode(generated[0],skip_special_tokens=True).strip()
            try:
                end = json_object_end(raw)
                parsed = json.loads(raw[:end] if end is not None else raw)
            except ValueError:
                parsed = None
            record = dict(id=row['id'],profile=profile,raw=raw,parsed=parsed,
                          schema_error=robot_schema_error(parsed),prompt_tokens=int(ids.numel()),
                          output_tokens=int(generated.numel()),vision_prefill_generation_ms=elapsed,
                          hints=hints,frame_now=row['frames'][-1])
            results.append(record)
            print(json.dumps(record),flush=True)
            args.output.write_text(json.dumps({'results':results,'complete':False},indent=2))
    summary = {}
    for profile in ('previous','compact'):
        group = [r for r in results if r['profile']==profile]
        summary[profile] = dict(n=len(group),schema_valid=sum(not r['schema_error'] for r in group),
            prompt_tokens_median=statistics.median(r['prompt_tokens'] for r in group),
            output_tokens_median=statistics.median(r['output_tokens'] for r in group),
            warm_latency_p50_ms=statistics.median(r['vision_prefill_generation_ms'] for r in group[1:] or group),
            action_counts={a:sum((r['parsed'] or {}).get('action')==a for r in group)
                           for a in ('STOP','DANGER_WARNING','CONTINUE','OBSERVE')})
    args.output.write_text(json.dumps(dict(complete=True,checkpoint=str(args.checkpoint),
        manifest=str(args.manifest),queries={'previous':old,'compact':COMPACT_ROBOT_QUERY},summary=summary,results=results,
        limitations=['Same updated decoder for both prompts: isolates prompt/hint changes.',
                     'No human labels: do not infer motion/risk accuracy from schema or action counts.',
                     'Local development clips, including training footage; not a blind generalization test.']),indent=2))


if __name__ == '__main__':
    main()
