"""One computation graph: RGB clip + query -> text, referring mask, optional event.

One query produces one binary region (union for plural referents), not instance IDs.
"""

import math

import torch
import torch.nn.functional as F
from torch import nn

from .config import LightVLMConfig
from .losses import segmentation_losses
from .memory import SpatialLSTM
from .vision import LightFPN, MobileNetEncoder


class LightFoundationVLM(nn.Module):
    def __init__(self, config: LightVLMConfig, encoder, language_model):
        super().__init__()
        self.config = config
        self.encoder = encoder
        self.language_model = language_model
        embedding = language_model.get_input_embeddings().weight
        if config.seg_token_id >= embedding.shape[0]:
            raise ValueError("Register <seg> and resize language embeddings first")
        language_dim = embedding.shape[1]
        dim = config.fpn_dim
        self.fpn = LightFPN(encoder.channels, dim)
        self.memory = SpatialLSTM(dim, config.memory_dim)
        self.visual_projector = nn.Sequential(nn.LayerNorm(dim), nn.Linear(dim, language_dim))
        self.memory_projector = nn.Sequential(
            nn.LayerNorm(config.memory_dim), nn.Linear(config.memory_dim, language_dim)
        )
        self.visual_position = nn.Parameter(torch.randn(1, config.visual_grid**2, dim) * 0.02)
        self.memory_position = nn.Parameter(
            torch.randn(1, config.memory_grid**2, config.memory_dim) * 0.02
        )
        # Trainable replacement embedding lets a frozen Qwen learn the new <seg> token.
        self.seg_embedding = nn.Parameter(embedding[config.seg_token_id].detach().float().clone())
        self.temporal_to_pixel = nn.Conv2d(config.memory_dim, dim, 1)
        self.pixel_decoder = nn.Sequential(
            nn.Conv2d(dim, dim, 3, padding=1, groups=dim),
            nn.Conv2d(dim, dim, 1),
            nn.GroupNorm(1, dim),
            nn.GELU(),
        )
        self.mask_query = nn.Linear(language_dim, dim)
        self.mask_bias = nn.Linear(language_dim, 1)
        self.presence_head = nn.Linear(language_dim, 1)
        self.event_head = (
            nn.Linear(config.memory_dim, config.event_classes) if config.event_classes else None
        )
        self.use_temporal = True
        self._frozen_modules = []

    @classmethod
    def from_local_components(
        cls, language_path, vision_checkpoint, *, event_classes=0, dtype=torch.float32
    ):
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(language_path, local_files_only=True)
        tokenizer.add_special_tokens({"additional_special_tokens": ["<seg>"]})
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        language = AutoModelForCausalLM.from_pretrained(
            language_path, local_files_only=True, torch_dtype=dtype
        )
        language.resize_token_embeddings(len(tokenizer))
        config = LightVLMConfig(
            seg_token_id=tokenizer.convert_tokens_to_ids("<seg>"), event_classes=event_classes
        )
        return cls(config, MobileNetEncoder(vision_checkpoint), language), tokenizer

    def apply_stage(self, stage, language_last_layers=0, *, freeze_encoder=False, freeze_memory=False):
        """Explicit progressive unfreezing; frozen modules keep eval mode (incl. BN)."""
        if stage not in ("alignment", "segmentation", "temporal", "temporal_vqa", "joint"):
            raise ValueError(f"Unknown stage: {stage}")
        if (freeze_encoder or freeze_memory) and stage != "joint":
            raise ValueError("Selective encoder/memory freezing requires joint stage")
        if language_last_layers < 0 or (
            language_last_layers and stage not in ("temporal_vqa", "joint")
        ):
            raise ValueError("Unfreeze language layers only in temporal_vqa or joint stage")
        self.requires_grad_(False)
        trainable = [self.visual_projector]
        if stage != "temporal_vqa":
            trainable.append(self.fpn)
        for parameter in (self.visual_position, self.seg_embedding):
            parameter.requires_grad_(True)
        if stage not in ("alignment", "temporal_vqa"):
            trainable += [
                self.pixel_decoder,
                self.mask_query,
                self.mask_bias,
                self.presence_head,
            ]
        if stage in ("temporal", "joint"):
            trainable += [self.memory, self.memory_projector, self.temporal_to_pixel]
            self.memory_position.requires_grad_(True)
            if self.event_head is not None:
                trainable.append(self.event_head)
        elif stage == "temporal_vqa":
            trainable.append(self.memory_projector)
            self.memory_position.requires_grad_(True)
        if stage == "joint":
            # Local fine-tuning baseline: entire small encoder, with a smaller LR.
            trainable.append(self.encoder)
        for module in trainable:
            module.requires_grad_(True)
        if freeze_encoder:
            self.encoder.requires_grad_(False)
        if freeze_memory:
            self.memory.requires_grad_(False)
        self._frozen_modules = [
            self.encoder,
            self.language_model,
            self.fpn,
            self.memory,
            self.temporal_to_pixel,
        ]
        self._frozen_modules = [
            m for m in self._frozen_modules if not any(p.requires_grad for p in m.parameters())
        ]
        if language_last_layers:
            layers = self.language_model.model.layers
            if language_last_layers > len(layers):
                raise ValueError("Too many requested language layers")
            for layer in layers[-language_last_layers:]:
                layer.requires_grad_(True)
            self._frozen_modules.remove(self.language_model)
        self.use_temporal = stage in ("temporal", "temporal_vqa", "joint")
        self.train(self.training)

    @torch.no_grad()
    def generate_answer(
        self,
        frames,
        input_ids,
        *,
        attention_mask=None,
        frame_valid=None,
        dt=None,
        reset=None,
        state=None,
        max_new_tokens=96,
    ):
        """Generate text from current visual tokens and causal ConvLSTM memory."""
        if input_ids.ndim != 2 or input_ids.shape[0] != frames.shape[0]:
            raise ValueError("input_ids must be [B,L]")
        attention_mask = torch.ones_like(input_ids) if attention_mask is None else attention_mask
        marker = (input_ids == self.config.seg_token_id) & attention_mask.bool()
        if attention_mask.shape != input_ids.shape or not (marker.sum(1) == 1).all():
            raise ValueError("Each prompt needs exactly one attended <seg> token")
        pyramid, state = self.encode_clip(
            frames, frame_valid=frame_valid, dt=dt, reset=reset, state=state
        )
        visual = F.adaptive_avg_pool2d(
            pyramid[2], self.config.visual_grid
        ).flatten(2).transpose(1, 2)
        prefix = self.visual_projector(visual + self.visual_position)
        if self.use_temporal:
            memory = F.adaptive_avg_pool2d(
                state[0], self.config.memory_grid
            ).flatten(2).transpose(1, 2)
            prefix = torch.cat(
                [prefix, self.memory_projector(memory + self.memory_position)], dim=1
            )
        text = self.language_model.get_input_embeddings()(input_ids)
        text = torch.where(marker[:, :, None], self.seg_embedding.to(text), text)
        inputs = torch.cat([prefix.to(text), text], dim=1)
        mask = torch.cat(
            [attention_mask.new_ones(input_ids.shape[0], prefix.shape[1]), attention_mask], dim=1
        )
        generated = self.generate_from_context(
            inputs,
            mask,
            max_new_tokens=max_new_tokens,
        )
        return {"generated_ids": generated, "state": state,
                "visual_token_count": prefix.shape[1]}

    @torch.no_grad()
    def generate_from_context(self, inputs_embeds, attention_mask, max_new_tokens=96, *,
                              stopping_criteria=None, logits_processor=None):
        """Decode a context already produced by forward(), without re-encoding images."""
        options = {} if stopping_criteria is None else {'stopping_criteria': stopping_criteria}
        if logits_processor is not None:
            options['logits_processor'] = logits_processor
        return self.language_model.generate(
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=self.language_model.config.eos_token_id,
            **options,
        )

    def train(self, mode=True):
        super().train(mode)
        for module in self._frozen_modules:
            module.eval()
        return self

    def freeze_spatial_for_temporal(self):
        """Train only the new memory path; preserve learned spatial grounding."""
        if not self.use_temporal:
            raise ValueError("Memory-only tuning requires temporal stage")
        self.requires_grad_(False)
        for module in (self.memory, self.memory_projector, self.temporal_to_pixel):
            module.requires_grad_(True)
        self.memory_position.requires_grad_(True)
        self._frozen_modules = [
            self.encoder,
            self.language_model,
            self.fpn,
            self.visual_projector,
            self.pixel_decoder,
            self.mask_query,
            self.mask_bias,
            self.presence_head,
        ]
        self.train(self.training)

    @torch.no_grad()
    def predict_events(self, frames, *, frame_valid=None, dt=None, reset=None, state=None):
        """Fast recurrent readout without running the language model or mask decoder."""
        if self.event_head is None or not self.use_temporal:
            raise ValueError('A trained temporal event head is required')
        _, state = self.encode_clip(frames,frame_valid=frame_valid,dt=dt,reset=reset,state=state)
        return {'event_logits':self.event_head(state[0].mean((-2,-1))), 'state':state}

    def encode_clip(self, frames, *, frame_valid=None, dt=None, reset=None, state=None):
        if frames.ndim != 5 or frames.shape[2] != 3 or min(frames.shape[-2:]) < 32:
            raise ValueError("frames must be normalized RGB [B,T,3,H,W], H/W >= 32")
        batch, steps = frames.shape[:2]
        if steps < 1:
            raise ValueError("Empty clips are not supported")
        valid = (
            torch.ones(batch, steps, device=frames.device, dtype=torch.bool)
            if frame_valid is None
            else frame_valid.bool()
        )
        if (
            valid.shape != (batch, steps)
            or not valid[:, 0].all()
            or (valid[:, 1:] & ~valid[:, :-1]).any()
        ):
            raise ValueError("frame_valid must be a nonempty right-padded prefix per sample")
        dt = frames.new_full((batch, steps), 0.1) if dt is None else dt
        reset = torch.zeros_like(valid) if reset is None else reset.bool()
        if dt.shape != valid.shape or reset.shape != valid.shape or not torch.isfinite(dt).all():
            raise ValueError("dt/reset must be [B,T] and dt must be finite")
        last_pyramid = None
        for index in range(steps):
            # Sequential encoding avoids mixing future frames through BatchNorm statistics.
            active = valid[:, index].nonzero(as_tuple=True)[0]
            if active.numel() == 0:
                continue
            encoded = self.fpn(self.encoder(frames[active, index]))
            pyramid = [
                feature.new_zeros(batch, *feature.shape[1:]).index_copy(0, active, feature)
                for feature in encoded
            ]
            keep = valid[:, index].view(batch, 1, 1, 1)
            last_pyramid = (
                pyramid
                if last_pyramid is None
                else [torch.where(keep, new, old) for new, old in zip(pyramid, last_pyramid)]
            )
            if self.use_temporal:
                gap = dt[:, index]
                restart = reset[:, index] | (gap <= 0) | (gap > self.config.max_gap_s)
                new_state = self.memory(
                    pyramid[2],
                    state,
                    dt=gap.clamp(0, self.config.max_gap_s) / self.config.max_gap_s,
                    reset=restart,
                )
                state = (
                    new_state
                    if state is None
                    else tuple(torch.where(keep, new, old) for new, old in zip(new_state, state))
                )
        if not self.use_temporal:
            shape = (batch, self.config.memory_dim, *last_pyramid[2].shape[-2:])
            state = (frames.new_zeros(shape), frames.new_zeros(shape))
        return last_pyramid, state

    def forward(
        self,
        frames,
        input_ids,
        *,
        attention_mask=None,
        labels=None,
        mask_targets=None,
        mask_valid=None,
        event_targets=None,
        frame_valid=None,
        dt=None,
        reset=None,
        state=None,
        return_language_context=False,
        compute_masks=True,
    ):
        if not compute_masks and mask_targets is not None:
            raise ValueError("Mask supervision requires compute_masks=True")
        config = self.config
        if input_ids.ndim != 2 or input_ids.shape[0] != frames.shape[0]:
            raise ValueError("input_ids must be [B,L]")
        attention_mask = torch.ones_like(input_ids) if attention_mask is None else attention_mask
        if attention_mask.shape != input_ids.shape or (
            labels is not None and labels.shape != input_ids.shape
        ):
            raise ValueError("Text mask and labels must match input_ids")
        marker = (input_ids == config.seg_token_id) & attention_mask.bool()
        if not (marker.sum(1) == 1).all():
            raise ValueError("Each query must contain exactly one attended <seg> token")
        seg_positions = marker.long().argmax(1)
        if labels is not None:
            before_query = (
                torch.arange(input_ids.shape[1], device=input_ids.device)[None]
                <= seg_positions[:, None]
            )
            if ((labels != -100) & (before_query | ~attention_mask.bool())).any():
                raise ValueError(
                    "Labels must ignore prompt/<seg>/padding; answers come after <seg>"
                )
        pyramid, state = self.encode_clip(
            frames, frame_valid=frame_valid, dt=dt, reset=reset, state=state
        )
        visual = F.adaptive_avg_pool2d(pyramid[2], config.visual_grid).flatten(2).transpose(1, 2)
        prefix = self.visual_projector(visual + self.visual_position)
        if self.use_temporal:
            memory_tokens = (
                F.adaptive_avg_pool2d(state[0], config.memory_grid).flatten(2).transpose(1, 2)
            )
            prefix = torch.cat(
                [prefix, self.memory_projector(memory_tokens + self.memory_position)], 1
            )
        text = self.language_model.get_input_embeddings()(input_ids)
        text = torch.where(marker[:, :, None], self.seg_embedding.to(text), text)
        prefix = prefix.to(text)
        inputs = torch.cat([prefix, text], dim=1)
        mask = torch.cat(
            [attention_mask.new_ones(input_ids.shape[0], prefix.shape[1]), attention_mask], dim=1
        )
        fused_labels = None
        if labels is not None and (labels != -100).any():
            fused_labels = torch.cat([labels.new_full(prefix.shape[:2], -100), labels], dim=1)
        language = None
        mask_logits = presence_logits = None
        if compute_masks or fused_labels is not None:
            # Never no_grad() here: frozen language weights still transmit gradients to visual tokens.
            language = self.language_model(
                inputs_embeds=inputs,
                attention_mask=mask,
                labels=fused_labels,
                output_hidden_states=True,
                use_cache=False,
                return_dict=True,
            )
        if compute_masks:
            rows = torch.arange(input_ids.shape[0], device=input_ids.device)
            query = language.hidden_states[-1][rows, seg_positions + prefix.shape[1]]
            query = query.to(pyramid[0])
            pixels = pyramid[0]
            if self.use_temporal:
                pixels = pixels + F.interpolate(
                    self.temporal_to_pixel(state[0]),
                    size=pixels.shape[-2:],
                    mode="bilinear",
                    align_corners=False,
                )
            pixels = self.pixel_decoder(pixels)
            mask_logits = torch.einsum("bc,bchw->bhw", self.mask_query(query), pixels)[
                :, None
            ] / math.sqrt(config.fpn_dim)
            mask_logits = mask_logits + self.mask_bias(query)[:, :, None, None]
            mask_logits = F.interpolate(
                mask_logits, size=frames.shape[-2:], mode="bilinear", align_corners=False
            )
            presence_logits = self.presence_head(query).squeeze(-1)
        losses = {}
        if mask_targets is not None:
            losses.update(
                segmentation_losses(mask_logits, mask_targets, presence_logits, mask_valid)
            )
        if fused_labels is not None:
            losses["language"] = language.loss
        event_logits = (
            self.event_head(state[0].mean((-2, -1)))
            if self.event_head is not None and self.use_temporal
            else None
        )
        if event_targets is not None:
            if event_logits is None:
                raise ValueError("Event labels require an enabled temporal model and event head")
            valid_events = event_targets != -100
            if valid_events.any():
                losses["event"] = F.cross_entropy(
                    event_logits[valid_events], event_targets[valid_events]
                )
        total = (
            sum(value * getattr(config, key + "_weight") for key, value in losses.items())
            if losses
            else None
        )
        result = {
            "loss": total,
            "losses": losses,
            "mask_logits": mask_logits,
            "presence_logits": presence_logits,
            "event_logits": event_logits,
            "language_logits": language.logits if language is not None else None,
            "state": state,
            "visual_token_count": prefix.shape[1],
        }
        if return_language_context:
            result["language_inputs"] = inputs
            result["language_attention_mask"] = mask
        return result
