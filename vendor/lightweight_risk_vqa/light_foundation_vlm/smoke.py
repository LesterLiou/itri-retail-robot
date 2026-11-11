"""Offline architecture verification with toy components; never a quality benchmark."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import torch
import torch.nn.functional as F
from torch import nn

from .config import LightVLMConfig
from .model import LightFoundationVLM


class ToyEncoder(nn.Module):
    channels = (8, 8, 12, 16)

    def __init__(self):
        super().__init__()
        dims = (3, 8, 8, 8, 12, 16)
        self.layers = nn.ModuleList(
            nn.Sequential(nn.Conv2d(a, b, 3, 2, 1), nn.SiLU()) for a, b in zip(dims, dims[1:])
        )

    def forward(self, images):
        outputs = []
        for index, layer in enumerate(self.layers):
            images = layer(images)
            if index >= 1:
                outputs.append(images)
        return outputs


class ToyCausalLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(32, 16)
        self.recurrent = nn.GRU(16, 16, batch_first=True)
        self.head = nn.Linear(16, 32)

    def get_input_embeddings(self):
        return self.embedding

    def forward(self, inputs_embeds, labels=None, **kwargs):
        hidden, _ = self.recurrent(inputs_embeds)
        logits = self.head(hidden)
        loss = (
            None
            if labels is None
            else F.cross_entropy(
                logits[:, :-1].reshape(-1, 32), labels[:, 1:].reshape(-1), ignore_index=-100
            )
        )
        return SimpleNamespace(logits=logits, hidden_states=(hidden,), loss=loss)


def make_toy():
    config = LightVLMConfig(
        fpn_dim=8, memory_dim=4, visual_grid=2, memory_grid=1, seg_token_id=31, event_classes=2
    )
    return LightFoundationVLM(config, ToyEncoder(), ToyCausalLM())


def toy_batch():
    mask = torch.zeros(2, 1, 32, 32)
    mask[0, :, 8:20, 10:22] = 1
    return {
        "frames": torch.randn(2, 3, 3, 32, 32),
        "input_ids": torch.tensor([[3, 4, 31, 8], [5, 6, 31, 9]]),
        "labels": torch.tensor([[-100, -100, -100, 8], [-100, -100, -100, 9]]),
        "mask_targets": mask,
        "event_targets": torch.tensor([0, 1]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--output", default="runs/smoke")
    args = parser.parse_args(argv)
    if args.steps < 1:
        parser.error("--steps must be positive")
    torch.set_num_threads(1)
    torch.manual_seed(7)
    model, batch = make_toy(), toy_batch()
    model.apply_stage("temporal")
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=0.003)
    losses = []
    for _ in range(args.steps):
        optimizer.zero_grad(set_to_none=True)
        output = model(**batch)
        output["loss"].backward()
        optimizer.step()
        losses.append(float(output["loss"].detach()))
    report = {
        "kind": "synthetic architecture smoke, not pretrained accuracy",
        "losses": losses,
        "mask_shape": list(output["mask_logits"].shape),
        "visual_tokens": output["visual_token_count"],
        "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
    }
    path = Path(args.output)
    path.mkdir(parents=True, exist_ok=False)
    torch.save(model.state_dict(), path / "toy_state.pt")
    restored = make_toy()
    restored.load_state_dict(torch.load(path / "toy_state.pt", weights_only=True), strict=True)
    model.eval()
    restored.eval()
    with torch.no_grad():
        torch.testing.assert_close(model(**batch)["mask_logits"], restored(**batch)["mask_logits"])
    report["checkpoint_roundtrip"] = "passed"
    (path / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
