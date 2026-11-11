"""Trainable research architecture, not a pretrained segmentation model."""

from .config import LightVLMConfig
from .model import LightFoundationVLM

__all__ = ["LightVLMConfig", "LightFoundationVLM"]
