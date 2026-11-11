"""Small supervised model; no pretrained temporal weights are bundled."""
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .temporal import FEATURE_NAMES, FEATURE_VERSION, LABELS


class MotionLSTM(nn.Module):
    def __init__(self, hidden_size=32):
        super().__init__()
        self.lstm = nn.LSTM(len(FEATURE_NAMES), hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, len(LABELS))

    def forward(self, features):
        outputs, _ = self.lstm(features)
        return self.head(outputs[:, -1])


class TrainedPredictor:
    def __init__(self, checkpoint, steps, sample_hz):
        if not checkpoint or not Path(checkpoint).is_file():
            raise ValueError('lstm mode requires an existing trained checkpoint; use heuristic first')
        saved = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if isinstance(saved, dict) and 'memory.gates.weight' in saved:
            raise ValueError(
                'Checkpoint is a visual ConvLSTM segmentation/VLM state_dict, not the '
                'normalized_bbox_v1 MotionLSTM classifier. It has no five-class future-motion '
                'head and requires its matching Light Foundation deployment code.'
            )
        if not isinstance(saved, dict):
            raise ValueError('Checkpoint must contain a MotionLSTM training dictionary')
        if saved.get('trained') is not True or saved.get('feature_version') != FEATURE_VERSION:
            raise ValueError('Checkpoint is untrained or has an incompatible feature version')
        if saved.get('labels') != list(LABELS) or saved.get('feature_names') != list(FEATURE_NAMES):
            raise ValueError('Checkpoint labels/features do not match this package')
        if saved.get('sequence_length') != steps or abs(saved.get('sample_hz', 0) - sample_hz) > 1e-6:
            raise ValueError('Checkpoint sampling does not match runtime settings')
        self.horizon_s = float(saved['prediction_horizon_s'])
        if self.horizon_s <= 0:
            raise ValueError('Checkpoint horizon must be positive')
        self.model = MotionLSTM(int(saved['hidden_size']))
        self.model.load_state_dict(saved['state_dict'], strict=True)
        self.model.eval()

    def predict(self, sequence):
        with torch.inference_mode():
            probabilities = self.model(torch.from_numpy(np.asarray(sequence, dtype=np.float32))[None]).softmax(-1)[0]
        if not torch.isfinite(probabilities).all():
            raise ValueError('Non-finite LSTM output')
        index = int(probabilities.argmax())
        return LABELS[index], float(probabilities[index])
