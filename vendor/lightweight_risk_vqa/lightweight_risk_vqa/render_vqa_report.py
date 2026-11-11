"""Render generated VQA answers beside the actual historical input images."""
import argparse
import html
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--predictions', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    records = {row['id']: row for row in
               (json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip())}
    generated = [json.loads(line) for line in args.predictions.read_text().splitlines() if line.strip()]
    summary = next((row['summary'] for row in generated if 'summary' in row), {})
    cards = []
    for row in generated:
        if 'id' not in row:
            continue
        record = records[row['id']]
        tiles = []
        for index, value in enumerate(record['frames']):
            path = Path(value)
            if not path.is_absolute():
                path = args.manifest.resolve().parent / path
            relative = os.path.relpath(path.resolve(), args.output.resolve().parent)
            tiles.append(f'<figure><img loading="lazy" src="{html.escape(relative, quote=True)}">'
                         f'<figcaption>{"NOW" if index == len(record["frames"]) - 1 else f"HISTORY {index + 1}"}'
                         '</figcaption></figure>')
        prediction = html.escape(json.dumps(row['prediction'], ensure_ascii=False, indent=2))
        expected = html.escape(json.dumps(row.get('expected'), ensure_ascii=False, indent=2))
        correct = row.get('motion_correct') is True
        cards.append(f'<article class="{"correct" if correct else "wrong"}">'
                     f'<h2>{html.escape(row["id"])}</h2>'
                     f'<p>motion_correct={correct} · schema_valid={row["schema_valid"]} · '
                     f'latency={row["latency_ms"]} ms</p>'
                     f'<div class="frames">{"".join(tiles)}</div>'
                     f'<div class="answers"><section><h3>Prediction</h3><pre>{prediction}</pre></section>'
                     f'<section><h3>Expected weak label</h3><pre>{expected}</pre></section></div></article>')
    document = (
        '<!doctype html><meta charset="utf-8"><title>Temporal VQA inference report</title>'
        '<style>body{font:15px sans-serif;max-width:1550px;margin:24px auto;padding:12px}'
        'article{border:2px solid #bbb;padding:14px;margin:20px 0}article.correct{border-color:#288b44}'
        'article.wrong{border-color:#c03d32}.frames{display:flex;flex-wrap:wrap}'
        'figure{width:23%;margin:1%}img{width:100%}.answers{display:flex;gap:24px}'
        'section{flex:1;min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eee;padding:12px}'
        'h2{font-size:18px}</style><h1>ConvLSTM + Qwen: generated answers</h1>'
        '<p>Historical input images only. Expected answers are weak labels, not human risk ground truth. '
        'Green border = motion matches label; red = mismatch or invalid response.</p>'
        f'<details><summary>Metrics</summary><pre>{html.escape(json.dumps(summary, indent=2))}</pre></details>'
        + ''.join(cards))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document)
    print(f'Wrote {len(cards)} inference examples to {args.output}')


if __name__ == '__main__':
    main()
