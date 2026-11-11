"""Native linear LoRA for local Qwen; release bundles contain merged plain weights."""
import math
import torch
from torch import nn
import torch.nn.functional as F


class LoRALinear(nn.Module):
    def __init__(self, base, rank=8, alpha=16.):
        super().__init__()
        if not isinstance(base, nn.Linear) or type(rank) is not int or rank < 1 or not math.isfinite(alpha) or alpha <= 0:
            raise ValueError('LoRA requires a linear layer and positive rank/alpha')
        self.base = base
        self.base.requires_grad_(False)
        self.scale = alpha / rank
        self.lora_A = nn.Parameter(torch.empty(rank, base.in_features, device=base.weight.device, dtype=torch.float32))
        self.lora_B = nn.Parameter(torch.zeros(base.out_features, rank, device=base.weight.device, dtype=torch.float32))
        nn.init.kaiming_uniform_(self.lora_A, a=5**.5)

    def forward(self, x):
        adaptation = F.linear(F.linear(x, self.lora_A.to(x)), self.lora_B.to(x))
        return self.base(x) + adaptation * self.scale


def inject_lora(model, rank=8, alpha=16., targets=('q_proj','k_proj','v_proj','o_proj')):
    selected = [(name, module) for name, module in model.language_model.named_modules()
                if isinstance(module, nn.Linear) and name.rsplit('.',1)[-1] in targets]
    if not selected:
        raise ValueError('No language modules matched LoRA targets')
    for name, module in selected:
        parent_name, key = name.rsplit('.',1)
        parent = model.language_model.get_submodule(parent_name)
        setattr(parent, key, LoRALinear(module, rank, alpha))
    model._frozen_modules = [m for m in model._frozen_modules if m is not model.language_model]
    return [name for name, _ in selected]


def merged_state_dict(model):
    state = dict(model.state_dict())
    for name, module in model.named_modules():
        if not isinstance(module, LoRALinear):
            continue
        weight = module.base.weight.detach().float() + module.lora_B.detach() @ module.lora_A.detach() * module.scale
        for key in [k for k in state if k.startswith(name + '.')]:
            del state[key]
        state[name + '.weight'] = weight.to(module.base.weight.dtype)
        if module.base.bias is not None:
            state[name + '.bias'] = module.base.bias.detach()
    return state
