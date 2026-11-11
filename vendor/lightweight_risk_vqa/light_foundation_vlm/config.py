from dataclasses import asdict, dataclass


@dataclass
class LightVLMConfig:
    fpn_dim: int = 128
    memory_dim: int = 64
    visual_grid: int = 8
    memory_grid: int = 4
    seg_token_id: int = 0
    event_classes: int = 0
    max_gap_s: float = 2.0
    # Legacy max_gap_s is measured in this unit; frame-based data has no physical FPS.
    time_unit: str = "seconds"
    mask_bce_weight: float = 2.0
    mask_dice_weight: float = 2.0
    presence_weight: float = 1.0
    event_weight: float = 1.0
    language_weight: float = 1.0

    def __post_init__(self):
        if min(self.fpn_dim, self.memory_dim, self.visual_grid, self.memory_grid) <= 0:
            raise ValueError("Model dimensions must be positive")
        if self.seg_token_id < 0 or self.event_classes < 0 or self.max_gap_s <= 0:
            raise ValueError("Invalid token, event class count or time gap")
        if self.time_unit not in ("seconds", "frames"):
            raise ValueError("time_unit must be seconds or frames")

    def to_dict(self):
        return asdict(self)
