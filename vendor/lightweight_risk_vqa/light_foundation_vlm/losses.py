import torch
import torch.nn.functional as F


def segmentation_losses(logits, targets, presence_logits, sample_valid=None):
    """Binary referring mask; targets are 0/1/255 (255 = unannotated pixel).

    Presence labels are derived only for fully annotated masks. A partial mask
    cannot establish that the target is absent elsewhere in the image.
    """
    if targets.ndim == 3:
        targets = targets[:, None]
    if targets.shape != logits.shape:
        raise ValueError("Mask targets must match image resolution [B,1,H,W]")
    if not torch.all((targets == 0) | (targets == 1) | (targets == 255)):
        raise ValueError("Mask targets must contain only 0, 1, or ignore=255")
    valid = targets != 255
    if sample_valid is not None:
        valid = valid & sample_valid.bool().view(-1, 1, 1, 1)
    clean = torch.where(valid, targets, 0).float()
    raw = F.binary_cross_entropy_with_logits(logits.float(), clean, reduction="none")
    bce = (raw * valid).sum() / valid.sum().clamp_min(1)
    probability = logits.float().sigmoid() * valid
    dims = (1, 2, 3)
    dice = 1 - (2 * (probability * clean).sum(dims) + 1) / (
        probability.sum(dims) + clean.sum(dims) + 1
    )
    annotated = valid.flatten(1).any(1)
    dice = (dice * annotated).sum() / annotated.sum().clamp_min(1)
    complete = valid.flatten(1).all(1)
    presence_target = (clean.flatten(1).sum(1) > 0).float()
    presence = F.binary_cross_entropy_with_logits(
        presence_logits.float(), presence_target, reduction="none"
    )
    presence = (presence * complete).sum() / complete.sum().clamp_min(1)
    return {"mask_bce": bce, "mask_dice": dice, "presence": presence}
