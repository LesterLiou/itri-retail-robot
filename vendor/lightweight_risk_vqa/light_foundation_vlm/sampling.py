"""Domain-balanced sampling weights for heterogeneous manifest sizes."""
from collections import Counter
import math


def domain_weights(records, key, proportions):
    if not records or not key or any(key not in row for row in records):
        raise ValueError('Every training record must have the sampling group key')
    counts = Counter(row[key] for row in records)
    if set(counts) != set(proportions):
        raise ValueError(f'Sampling weights must name exactly these groups: {sorted(counts)}')
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in proportions.values()):
        raise ValueError('Group weights must be finite and nonnegative')
    total = sum(float(value) for value in proportions.values())
    if total <= 0:
        raise ValueError('At least one group weight must be positive')
    return [float(proportions[row[key]]) / total / counts[row[key]] for row in records]
