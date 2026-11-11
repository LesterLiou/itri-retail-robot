"""Train a small future-motion LSTM with mandatory recording-session separation."""
import argparse
import json
from pathlib import Path
import random

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .lstm_model import MotionLSTM
from .temporal import FEATURE_NAMES, FEATURE_VERSION, LABELS


def load_datasets(paths):
    data, targets, sessions, sources = [], [], set(), set()
    contract = None
    for path in paths:
        with np.load(path, allow_pickle=False) as saved:
            features, labels = saved['features'], saved['labels']
            current = (features.shape[1] if features.ndim == 3 else 0,
                       float(saved['sample_hz']), float(saved['prediction_horizon_s']))
            if (str(saved['feature_version']) != FEATURE_VERSION
                    or saved['feature_names'].tolist() != list(FEATURE_NAMES)
                    or saved['label_names'].tolist() != list(LABELS)):
                raise ValueError('Incompatible dataset feature/label contract')
            if contract is not None and contract != current:
                raise ValueError('All datasets must have identical sampling and prediction horizon')
            contract = current
            if (features.ndim != 3 or features.shape[-1] != len(FEATURE_NAMES) or features.shape[0] == 0
                    or labels.shape != (features.shape[0],) or saved['sessions'].shape != labels.shape
                    or features.shape[1] < 2 or not np.isfinite(features).all()
                    or labels.min() < 0 or labels.max() >= len(LABELS)
                    or not np.issubdtype(labels.dtype, np.integer)):
                raise ValueError('Invalid or empty training arrays')
            data.append(features.astype(np.float32))
            targets.append(labels.astype(np.int64))
            sessions.update(map(str, saved['sessions'].tolist()))
            sources.add(str(saved['label_source']))
    return np.concatenate(data), np.concatenate(targets), sessions, contract, sources


def evaluate(model, features, labels):
    matrix = np.zeros((len(LABELS), len(LABELS)), dtype=np.int64)
    with torch.inference_mode():
        for start in range(0, len(labels), 256):
            predicted = model(torch.from_numpy(features[start:start + 256])).argmax(-1).numpy()
            for truth, guess in zip(labels[start:start + 256], predicted):
                matrix[int(truth), int(guess)] += 1
    f1 = []
    for i in range(len(LABELS)):
        denominator = matrix[i].sum() + matrix[:, i].sum()
        f1.append(float(2 * matrix[i, i] / denominator) if denominator else 0.0)
    return {'accuracy': float(matrix.trace() / matrix.sum()), 'macro_f1': float(np.mean(f1)),
            'per_class_f1': dict(zip(LABELS, f1)), 'confusion_matrix': matrix.tolist()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train', nargs='+', required=True)
    parser.add_argument('--val', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--hidden-size', type=int, default=32)
    parser.add_argument('--learning-rate', type=float, default=0.001)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args(argv)
    if min(args.epochs, args.batch_size, args.hidden_size) < 1 or args.learning_rate <= 0:
        parser.error('Training parameters must be positive')
    if args.output.exists():
        parser.error('Checkpoint exists; choose a new filename')
    train_x, train_y, train_sessions, contract, train_sources = load_datasets(args.train)
    val_x, val_y, val_sessions, val_contract, val_sources = load_datasets(args.val)
    if train_sessions & val_sessions:
        raise ValueError('Train/validation session overlap: split whole recordings, not adjacent windows')
    if val_contract != contract:
        raise ValueError('Train/validation temporal contracts differ')
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    model = MotionLSTM(args.hidden_size)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    counts = np.bincount(train_y, minlength=len(LABELS))
    weights = len(train_y) / (len(LABELS) * np.maximum(counts, 1))
    criterion = nn.CrossEntropyLoss(weight=torch.tensor(weights, dtype=torch.float32))
    loader = DataLoader(TensorDataset(torch.from_numpy(train_x), torch.from_numpy(train_y)),
                        batch_size=args.batch_size, shuffle=True)
    best, best_metrics, best_epoch = None, None, 0
    for epoch in range(args.epochs):
        model.train()
        loss_sum = 0.0
        for features, labels in loader:
            optimizer.zero_grad()
            loss = criterion(model(features), labels)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            loss_sum += float(loss.detach()) * len(labels)
        model.eval()
        metrics = evaluate(model, val_x, val_y)
        if best_metrics is None or metrics['macro_f1'] > best_metrics['macro_f1']:
            best = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            best_metrics, best_epoch = metrics, epoch + 1
        print(json.dumps({'epoch': epoch + 1, 'train_loss': loss_sum / len(train_y), **metrics}))
    steps, sample_hz, horizon_s = contract
    saved = {'trained': True, 'state_dict': best, 'hidden_size': args.hidden_size,
        'feature_version': FEATURE_VERSION, 'feature_names': list(FEATURE_NAMES), 'labels': list(LABELS),
        'sequence_length': steps, 'sample_hz': sample_hz, 'prediction_horizon_s': horizon_s,
        'train_sessions': sorted(train_sessions), 'val_sessions': sorted(val_sessions),
        'train_label_sources': sorted(train_sources), 'val_label_sources': sorted(val_sources),
        'train_windows': len(train_y), 'val_windows': len(val_y), 'best_epoch': best_epoch,
        'validation_metrics': best_metrics, 'seed': args.seed}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(saved, args.output)
    report = {key: value for key, value in saved.items() if key != 'state_dict'}
    report['parameter_count'] = sum(p.numel() for p in model.parameters())
    args.output.with_suffix('.metrics.json').write_text(json.dumps(report, indent=2))
    print('Saved trained checkpoint:', args.output)


if __name__ == '__main__':
    main()
