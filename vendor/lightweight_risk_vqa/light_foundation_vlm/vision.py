"""A replaceable multi-scale encoder; no detector outputs or NMS in this model."""

import torch
import torch.nn.functional as F
from torch import nn


class MobileNetEncoder(nn.Module):
    channels = (24, 40, 112, 960)
    strides = (4, 8, 16, 32)

    def __init__(self, checkpoint=None):
        super().__init__()
        from torchvision.models import mobilenet_v3_large

        network = mobilenet_v3_large(weights=None)
        if checkpoint:
            # Exact official torchvision full classifier state dict, loaded locally.
            network.load_state_dict(
                torch.load(checkpoint, map_location="cpu", weights_only=True), strict=True
            )
        self.features = network.features

    def forward(self, images):
        outputs = []
        for index, layer in enumerate(self.features):
            images = layer(images)
            if index in (3, 6, 12, 16):
                outputs.append(images)
        return outputs


class LightFPN(nn.Module):
    def __init__(self, channels, dim):
        super().__init__()
        self.lateral = nn.ModuleList(nn.Conv2d(c, dim, 1) for c in channels)
        self.smooth = nn.ModuleList(
            nn.Sequential(
                nn.Conv2d(dim, dim, 3, padding=1, groups=dim),
                nn.Conv2d(dim, dim, 1),
                nn.GroupNorm(1, dim),
                nn.SiLU(),
            )
            for _ in channels
        )

    def forward(self, features):
        pyramid = [layer(feature) for layer, feature in zip(self.lateral, features)]
        for index in range(len(pyramid) - 2, -1, -1):
            pyramid[index] = pyramid[index] + F.interpolate(
                pyramid[index + 1], size=pyramid[index].shape[-2:], mode="nearest"
            )
        return [layer(feature) for layer, feature in zip(self.smooth, pyramid)]
