"""Generation metrics: malformed/missing predictions count as wrong."""


def classification_metrics(results, field, labels):
    labels = sorted(labels)
    rows = [row for row in results if isinstance(row.get('expected'), dict)
            and row['expected'].get(field) in labels]
    if not rows:
        return {'accuracy': None, 'macro_f1': None, 'per_class': {}, 'labeled_samples': 0}
    pairs = [(row['expected'][field],
              row['prediction'].get(field) if isinstance(row.get('prediction'), dict)
              and row.get('schema_valid', True) else None)
             for row in rows]
    per_class = {}
    for label in labels:
        tp = sum(actual == label and predicted == label for actual, predicted in pairs)
        fp = sum(actual != label and predicted == label for actual, predicted in pairs)
        fn = sum(actual == label and predicted != label for actual, predicted in pairs)
        support = sum(actual == label for actual, _ in pairs)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
        per_class[label] = {'precision': precision, 'recall': recall, 'f1': f1,
                            'support': support}
    supported = [row['f1'] for row in per_class.values() if row['support']]
    return {'accuracy': sum(actual == predicted for actual, predicted in pairs) / len(pairs),
            'macro_f1': sum(supported) / len(supported), 'per_class': per_class,
            'labeled_samples': len(pairs), 'macro_f1_scope': 'classes present in ground truth'}
