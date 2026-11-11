import torch
from torch import nn


class SpatialLSTM(nn.Module):
    """Causal ConvLSTM, preserving a stride-16 spatial grid and elapsed time.

    State belongs to the caller, never to a global camera cache. Reset on source
    changes, cuts and timestamp discontinuities. Detach at truncated BPTT boundaries.
    """

    def __init__(self, input_dim, hidden_dim):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.gates = nn.Conv2d(input_dim + hidden_dim + 1, 4 * hidden_dim, 3, padding=1)
        with torch.no_grad():
            self.gates.bias[hidden_dim : 2 * hidden_dim].fill_(1)

    def forward(self, feature, state=None, *, dt=None, reset=None):
        batch, _, height, width = feature.shape
        if state is None:
            h = feature.new_zeros(batch, self.hidden_dim, height, width)
            c = torch.zeros_like(h)
        else:
            h, c = state
            if h.shape != (batch, self.hidden_dim, height, width) or c.shape != h.shape:
                raise ValueError("Memory shape changed; reset state for the new stream/resolution")
        if reset is not None:
            keep = (~reset.bool()).to(feature.dtype).view(batch, 1, 1, 1)
            h, c = h * keep, c * keep
        time_channel = feature.new_zeros(batch, 1, height, width)
        if dt is not None:
            time_channel = dt.to(feature).view(batch, 1, 1, 1).expand_as(time_channel)
        i, f, g, o = self.gates(torch.cat([feature, h, time_channel], dim=1)).chunk(4, dim=1)
        c = f.sigmoid() * c + i.sigmoid() * g.tanh()
        h = o.sigmoid() * c.tanh()
        return h, c


def detach_state(state):
    return tuple(t.detach() for t in state)
