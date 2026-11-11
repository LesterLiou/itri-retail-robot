import json
from pathlib import Path

import torch

from .config import LightVLMConfig
from .model import LightFoundationVLM
from .vision import MobileNetEncoder


def save_bundle(model, tokenizer, directory, *, metadata=None):
    """Complete model weights, architecture metadata and tokenizer; no external base needed."""
    if not isinstance(model.encoder, MobileNetEncoder):
        raise TypeError("Release bundle currently supports MobileNetEncoder only")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    model.language_model.config.save_pretrained(directory / "language_config")
    tokenizer.save_pretrained(directory / "tokenizer")
    manifest = {
        "format_version": 1,
        "architecture": "mobilenetv3_convlstm_qwen",
        "config": model.config.to_dict(),
        "use_temporal": model.use_temporal,
        "metadata": metadata or {},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    from .lora import merged_state_dict
    torch.save(merged_state_dict(model), directory / "model.pt")


def load_bundle(directory, dtype=torch.float32):
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["format_version"] != 1 or manifest["architecture"] != "mobilenetv3_convlstm_qwen":
        raise ValueError("Unsupported bundle format")
    config = AutoConfig.from_pretrained(directory / "language_config", local_files_only=True)
    language = AutoModelForCausalLM.from_config(config, torch_dtype=dtype)
    model = LightFoundationVLM(LightVLMConfig(**manifest["config"]), MobileNetEncoder(), language)
    model.load_state_dict(
        torch.load(directory / "model.pt", map_location="cpu", weights_only=True), strict=True
    )
    model.use_temporal = manifest["use_temporal"]
    tokenizer = AutoTokenizer.from_pretrained(directory / "tokenizer", local_files_only=True)
    if tokenizer.convert_tokens_to_ids("<seg>") != model.config.seg_token_id:
        raise ValueError("Tokenizer does not match the checkpoint")
    model.eval()
    return model, tokenizer, manifest
