"""LoRA must train adapters and export into the original strict bundle graph."""
import pytest
torch=pytest.importorskip('torch')
from torch import nn

from light_foundation_vlm.lora import inject_lora, merged_state_dict,LoRALinear


class Tiny(nn.Module):
    def __init__(self):
        super().__init__()
        self.language_model = nn.ModuleDict({'layer':nn.ModuleDict({'q_proj':nn.Linear(4,3)})})
        self._frozen_modules = [self.language_model]
        self.requires_grad_(False)


@pytest.mark.parametrize('alpha',[float('nan'),float('inf'),0.])
def test_invalid_lora_scale_is_rejected(alpha):
    with pytest.raises(ValueError):
        LoRALinear(nn.Linear(4,3),rank=2,alpha=alpha)


def test_lora_gradients_merge_and_original_strict_reload():
    torch.manual_seed(42)
    model = Tiny()
    original = Tiny()
    original.load_state_dict(model.state_dict())
    names = inject_lora(model,rank=2,alpha=4)
    assert names == ['layer.q_proj']
    layer = model.language_model['layer']['q_proj']
    x = torch.randn(5,4)
    assert torch.equal(layer(x), original.language_model['layer']['q_proj'](x))
    layer(x).square().mean().backward()
    assert layer.base.weight.grad is None
    assert layer.lora_B.grad.abs().sum() > 0
    with torch.no_grad():
        layer.lora_B.add_(.1)
    expected = layer(x).detach()
    exported = merged_state_dict(model)
    assert not any('lora_' in k or '.base.' in k for k in exported)
    original.load_state_dict(exported,strict=True)
    assert torch.allclose(expected,original.language_model['layer']['q_proj'](x),atol=1e-6)
    assert layer.lora_B.requires_grad
    assert layer.lora_B.abs().sum() > 0
    assert not model._frozen_modules
