"""Single-device research trainer. No implicit downloads or fabricated temporal labels."""

import argparse
from collections import Counter
import json
import math
import random
import time
from functools import partial
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

from .checkpoint import load_bundle, save_bundle
from .data import ClipDataset, check_split, collate
from .model import LightFoundationVLM
from .sampling import domain_weights


def validate_evaluation_split(train_data, val_data, train_path, val_path, overfit_smoke=False):
    if overfit_smoke:
        if Path(train_path).resolve() != Path(val_path).resolve():
            raise ValueError("--overfit-smoke requires the SAME manifest for train and val")
        return "training_fit_only"
    check_split(train_data, val_data)
    return "held_out_validation"


def stage_batch(batch, stage, device, event_enabled):
    batch = {key: value.to(device) for key, value in batch.items()}
    if stage == "alignment":
        if not (batch["labels"] != -100).any():
            raise ValueError("Alignment requires supervised answers")
        batch.pop("mask_targets")
    if stage == "temporal_vqa":
        if not (batch["labels"] != -100).any():
            raise ValueError("Temporal VQA requires supervised answers")
        batch.pop("mask_targets")
    if not event_enabled:
        if (batch["event_targets"] != -100).any():
            raise ValueError("Event annotations require temporal/joint stage and --event-classes")
        batch.pop("event_targets")
    has_answer = (batch["labels"] != -100).any()
    has_mask = "mask_targets" in batch and (batch["mask_targets"] != 255).any()
    has_event = "event_targets" in batch and (batch["event_targets"] != -100).any()
    if not (has_answer or has_mask or has_event):
        raise ValueError("Every batch must contain annotated masks, answers, or events")
    return batch


@torch.no_grad()
def evaluate(model, loader, stage, device, last_frame_only=False):
    model.eval()
    losses, positive_ious, negative_fp = [], [], []
    event_correct, event_total = 0, 0
    answer_correct, answer_total = 0, 0
    for raw in loader:
        batch = stage_batch(raw, stage, device, model.event_head is not None and model.use_temporal)
        if last_frame_only:
            rows = torch.arange(batch["frames"].shape[0], device=device)
            last = batch["frame_valid"].sum(1) - 1
            batch["frames"] = batch["frames"][rows, last][:, None]
            batch["dt"] = batch["dt"][rows, last][:, None]
            batch["frame_valid"] = batch["frame_valid"][:, :1]
        output = model(**batch)
        losses.extend([float(output["loss"])] * len(batch["frames"]))
        if (batch["labels"] != -100).any():
            # forward() prepends visual/memory tokens before the text sequence.
            # Shift once for causal next-token prediction, then score only
            # supervised answer tokens (prompt and padding remain -100).
            prefix = output["visual_token_count"]
            ignored = batch["labels"].new_full((len(batch["labels"]), prefix), -100)
            targets = torch.cat([ignored, batch["labels"]], dim=1)[:, 1:]
            predictions = output["language_logits"][:, :-1].argmax(-1)
            selected = targets != -100
            answer_total += int(selected.sum())
            answer_correct += int(((predictions == targets) & selected).sum())
        if "mask_targets" in batch:
            target = batch["mask_targets"]
            valid = target != 255
            predicted = (output["mask_logits"] > 0) & valid
            truth = (target == 1) & valid
            for index in range(len(target)):
                if truth[index].any():
                    intersection = (predicted[index] & truth[index]).sum()
                    union = (predicted[index] | truth[index]).sum()
                    positive_ious.append(float(intersection / union.clamp_min(1)))
                elif valid[index].all():
                    negative_fp.append(float(predicted[index].any()))
        if output["event_logits"] is not None and "event_targets" in batch:
            selected = batch["event_targets"] != -100
            event_total += int(selected.sum())
            event_correct += int(
                ((output["event_logits"].argmax(-1) == batch["event_targets"]) & selected).sum()
            )
    return {
        "loss": float(np.mean(losses)),
        "positive_mask_miou": float(np.mean(positive_ious)) if positive_ious else None,
        "negative_image_any_pixel_fp_rate": float(np.mean(negative_fp)) if negative_fp else None,
        "event_accuracy": event_correct / event_total if event_total else None,
        "answer_token_accuracy": answer_correct / answer_total if answer_total else None,
    }


def evaluate_groups(model, loader, stage, device, key):
    """Each source gets its own metrics; do not hide a small target-domain val set."""
    if not key:
        return {}
    data = loader.dataset
    if any(key not in row for row in data.records):
        raise ValueError(f'Every validation record must include {key}')
    result = {}
    for group in sorted({row[key] for row in data.records}):
        indices = [index for index, row in enumerate(data.records) if row[key] == group]
        subset = DataLoader(Subset(data, indices), batch_size=loader.batch_size,
                            collate_fn=loader.collate_fn)
        result[group] = evaluate(model, subset, stage, device)
    return result


def selection_loss(metrics, groups, selected_groups=None):
    # Opt-in grouped evaluation selects a checkpoint with equal domain weight.
    if selected_groups:
        if not set(selected_groups) <= set(groups):
            raise ValueError('Selection groups must exist in grouped validation')
        groups = {key: groups[key] for key in selected_groups}
    return float(np.mean([row['loss'] for row in groups.values()])) if groups else metrics['loss']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", required=True,
        choices=["alignment", "segmentation", "temporal", "temporal_vqa", "joint"]
    )
    parser.add_argument("--language-path")
    parser.add_argument("--vision-checkpoint")
    parser.add_argument("--init", help="Previous stage's complete bundle; optimizer starts fresh")
    parser.add_argument("--train-jsonl", required=True)
    parser.add_argument("--val-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--image-size", type=int, default=320)
    parser.add_argument("--max-frames", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument('--augment', action='store_true',
                        help='Train-only consistent flip/color augmentation on explicitly safe records')
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--vision-lr", type=float, default=1e-5)
    parser.add_argument("--language-lr", type=float, default=5e-6)
    parser.add_argument("--language-last-layers", type=int, default=0)
    parser.add_argument('--lora-rank', type=int, default=0, help='0 disables native language LoRA')
    parser.add_argument('--lora-alpha', type=float, default=16.)
    parser.add_argument('--lora-targets', default='q_proj,k_proj,v_proj,o_proj')
    parser.add_argument("--event-classes", type=int, default=0)
    parser.add_argument('--attach-event-head',action='store_true',
                        help='Explicitly initialize an absent event head on a loaded bundle')
    parser.add_argument('--event-label-names',help='Comma-separated ordered event classes saved with the bundle')
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--dtype", choices=["float32", "bfloat16"], default="float32")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument('--sampling-group-key', help='Manifest metadata key, e.g. mix_group')
    parser.add_argument('--group-sampling-weights', help='JSON group proportions, e.g. {"jaad":0.7,"store":0.3}')
    parser.add_argument('--epoch-samples', type=int, default=0,
                        help='Replacement draws per epoch with group sampling; 0 uses dataset length')
    parser.add_argument('--eval-group-key',
                        help='Report source metrics and select best by equal-domain mean loss')
    parser.add_argument('--selection-groups', help='Comma-separated validation groups for loss selection; '
                        'allows VQA-only selection while reporting mask metrics separately')
    parser.add_argument("--time-unit", choices=["seconds", "frames"], default="seconds")
    parser.add_argument("--log-every", type=int, default=10)
    parser.add_argument("--temporal-only", action="store_true")
    parser.add_argument("--freeze-encoder", action="store_true",
                        help="Joint stage: retain pretrained encoder and frozen BN statistics")
    parser.add_argument("--freeze-memory", action="store_true",
                        help="Joint stage control: freeze ConvLSTM while training the same other modules")
    parser.add_argument("--patience", type=int, default=0, help="0 disables early stopping")
    parser.add_argument("--min-delta", type=float, default=0.001)
    parser.add_argument(
        "--save-best-only", action="store_true", help="Save space; omit last bundle"
    )
    parser.add_argument(
        "--eval-history-ablation",
        action="store_true",
        help="Also measure temporal model with only the final frame",
    )
    parser.add_argument(
        "--overfit-smoke",
        action="store_true",
        help="Fit and measure the SAME manifest for debugging; never held-out validation",
    )
    args = parser.parse_args(argv)
    if args.event_label_names and (len(args.event_label_names.split(',')) != args.event_classes or len(set(args.event_label_names.split(','))) != args.event_classes):
        parser.error('event-label-names must uniquely name each event class in index order')
    if args.attach_event_head and (not args.init or args.event_classes < 2 or args.stage != 'joint'):
        parser.error('attach-event-head requires an initialized joint model and at least two classes')
    if args.lora_rank < 0 or not math.isfinite(args.lora_alpha) or args.lora_alpha <= 0 or (args.lora_rank and (args.language_last_layers or args.temporal_only)):
        parser.error('Use nonnegative LoRA rank and positive alpha; LoRA cannot mix with last-layer or temporal-only tuning')
    selected_groups = args.selection_groups.split(',') if args.selection_groups else None
    if selected_groups and (not args.eval_group_key or any(not key for key in selected_groups)):
        parser.error('selection-groups requires grouped evaluation and nonempty names')
    if (args.freeze_encoder or args.freeze_memory) and args.stage != 'joint':
        parser.error('freeze-encoder/freeze-memory require joint stage')
    if bool(args.sampling_group_key) != bool(args.group_sampling_weights):
        parser.error('Provide both sampling-group-key and group-sampling-weights')
    if args.epoch_samples < 0 or (args.epoch_samples and not args.sampling_group_key):
        parser.error('Nonnegative epoch-samples requires group sampling')
    if args.temporal_only and (args.stage != "temporal" or args.event_classes):
        parser.error("--temporal-only requires temporal stage without event classes")
    if args.patience < 0 or args.min_delta < 0:
        parser.error("patience and min-delta must be nonnegative")
    if min(args.epochs, args.batch_size, args.lr, args.vision_lr, args.language_lr) <= 0:
        parser.error("Positive epochs, batch size and learning rates required")
    if args.init and (args.language_path or args.vision_checkpoint):
        parser.error("Use --init or component paths, not both")
    if not args.init and not (args.language_path and args.vision_checkpoint):
        parser.error("Provide local language/vision weights, or --init a saved bundle")
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    dtype = getattr(torch, args.dtype)
    if args.init:
        model, tokenizer, _ = load_bundle(args.init, dtype)
        if args.attach_event_head:
            if model.event_head is not None:
                parser.error('Cannot replace an existing event head')
            model.config.event_classes = args.event_classes
            model.event_head = torch.nn.Linear(model.config.memory_dim,args.event_classes)
        if args.event_classes != model.config.event_classes:
            parser.error(
                "--event-classes must match the bundle; --attach-event-head explicitly adds an absent head"
            )
    else:
        model, tokenizer = LightFoundationVLM.from_local_components(
            args.language_path,
            args.vision_checkpoint,
            event_classes=args.event_classes,
            dtype=dtype,
        )
    max_frames = 1 if args.stage in ("alignment", "segmentation") else args.max_frames
    train_data = ClipDataset(args.train_jsonl, tokenizer, args.image_size, max_frames,
                             augment=args.augment)
    val_data = ClipDataset(args.val_jsonl, tokenizer, args.image_size, max_frames)
    sampling_weights = (domain_weights(train_data.records, args.sampling_group_key,
                                      json.loads(args.group_sampling_weights))
                        if args.sampling_group_key else None)
    sampling_generator = torch.Generator().manual_seed(args.seed)
    evaluation_mode = validate_evaluation_split(
        train_data, val_data, args.train_jsonl, args.val_jsonl, args.overfit_smoke
    )
    if train_data.time_units != {args.time_unit} or val_data.time_units != {args.time_unit}:
        parser.error("--time-unit must match both manifests; seconds and frame steps cannot mix")
    if args.stage in ("temporal", "temporal_vqa", "joint") and not any(
        len(row["frames"]) > 1 for row in train_data.records
    ):
        parser.error("Temporal training requires real multi-frame clips")
    model.config.time_unit = args.time_unit
    model.apply_stage(args.stage, args.language_last_layers,
                      freeze_encoder=args.freeze_encoder, freeze_memory=args.freeze_memory)
    if args.temporal_only:
        model.freeze_spatial_for_temporal()
    if args.lora_rank:
        from .lora import inject_lora
        selected = inject_lora(model,args.lora_rank,args.lora_alpha,tuple(args.lora_targets.split(',')))
        print(json.dumps({'lora_modules': selected, 'lora_rank':args.lora_rank}),flush=True)
    model.to(args.device)
    groups = {"new": [], "vision": [], "language": []}
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            group = (
                "vision"
                if name.startswith("encoder.")
                else "language"
                if name.startswith("language_model.")
                else "new"
            )
            groups[group].append(parameter)
    optimizer = torch.optim.AdamW(
        [
            {"params": groups["new"], "lr": args.lr},
            {"params": groups["vision"], "lr": args.vision_lr},
            {"params": groups["language"], "lr": args.language_lr},
        ],
        weight_decay=0.01,
    )
    collator = partial(collate, pad_token_id=tokenizer.pad_token_id)
    train_loader = DataLoader(
        train_data, batch_size=args.batch_size, shuffle=True, collate_fn=collator
    )
    val_loader = DataLoader(val_data, batch_size=args.batch_size, collate_fn=collator)
    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=False)
    (output_path / "training_args.json").write_text(json.dumps(vars(args), indent=2) + "\n")
    (output_path / "trainable_parameters.json").write_text(
        json.dumps(
            {
                "names": [name for name, p in model.named_parameters() if p.requires_grad],
                "trainable_count": sum(p.numel() for p in model.parameters() if p.requires_grad),
                "total_count": sum(p.numel() for p in model.parameters()),
            },
            indent=2,
        )
        + "\n"
    )
    baseline = evaluate(model, val_loader, args.stage, args.device)
    baseline_groups = evaluate_groups(model, val_loader, args.stage, args.device, args.eval_group_key)
    (output_path / "baseline.json").write_text(
        json.dumps({"evaluation_mode": evaluation_mode, "metrics": baseline,
                    'by_group': baseline_groups,
                    'selection_loss': selection_loss(baseline, baseline_groups, selected_groups)}, indent=2) + "\n"
    )
    print(json.dumps({"before_training": baseline, "evaluation_mode": evaluation_mode}), flush=True)
    best = float("inf")
    stopping_best = selection_loss(baseline, baseline_groups, selected_groups)
    bad_epochs = 0
    started = time.monotonic()
    step = 0
    for epoch in range(args.epochs):
        sampled_counts = None
        if sampling_weights is not None:
            indices = torch.multinomial(torch.tensor(sampling_weights, dtype=torch.double),
                                        args.epoch_samples or len(train_data), replacement=True,
                                        generator=sampling_generator).tolist()
            sampled_counts = dict(Counter(train_data.records[index][args.sampling_group_key]
                                          for index in indices))
            train_loader = DataLoader(train_data, batch_size=args.batch_size, sampler=indices,
                                      collate_fn=collator)
            print(json.dumps({'epoch': epoch + 1, 'sampled_group_counts': sampled_counts}), flush=True)
        model.train()
        training_losses = []
        for raw in train_loader:
            batch = stage_batch(
                raw, args.stage, args.device, model.event_head is not None and model.use_temporal
            )
            optimizer.zero_grad(set_to_none=True)
            output = model(**batch)
            loss = output["loss"]
            if loss is None or not torch.isfinite(loss) or not loss.requires_grad:
                raise ValueError("Batch has no finite trainable supervision")
            loss.backward()
            memory_grad_norm = None
            if model.use_temporal:
                memory_grad_norm = (
                    sum(
                        float(p.grad.detach().float().square().sum())
                        for p in model.memory.parameters()
                        if p.grad is not None
                    )
                    ** 0.5
                )
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            training_losses.append(float(loss.detach()))
            step += 1
            if step == 1 or (args.log_every > 0 and step % args.log_every == 0):
                progress = {
                    "step": step,
                    "epoch": epoch + 1,
                    "loss": float(loss.detach()),
                    "memory_grad_norm": memory_grad_norm,
                    "elapsed_s": round(time.monotonic() - started, 2),
                }
                print(json.dumps(progress), flush=True)
                with (output_path / "progress.jsonl").open("a") as file:
                    file.write(json.dumps(progress) + "\n")
        metrics = evaluate(model, val_loader, args.stage, args.device)
        record = {
            "epoch": epoch + 1,
            "train_loss": float(np.mean(training_losses)),
            "evaluation_mode": evaluation_mode,
            "training_fit" if args.overfit_smoke else "validation": metrics,
        }
        groups = evaluate_groups(model, val_loader, args.stage, args.device, args.eval_group_key)
        chosen_loss = selection_loss(metrics, groups, selected_groups)
        if groups:
            record['validation_by_group'] = groups
            record['selection_loss'] = chosen_loss
            record['selection_rule'] = 'equal_domain_mean_validation_loss'
            record['selection_groups'] = selected_groups or sorted(groups)
        if sampled_counts is not None:
            record['sampled_group_counts'] = sampled_counts
        if args.eval_history_ablation and model.use_temporal:
            record["last_frame_only_ablation"] = evaluate(
                model, val_loader, args.stage, args.device, last_frame_only=True
            )
        print(json.dumps(record), flush=True)
        with (output_path / "metrics.jsonl").open("a") as file:
            file.write(json.dumps(record) + "\n")
        metadata = {
            "training_args": vars(args),
            "metrics": record,
            "note": "TRAINING FIT ONLY; no independent validation or generalization claim."
            if args.overfit_smoke
            else "Research training output; validation metrics determine usable capability.",
        }
        if not args.save_best_only:
            save_bundle(model, tokenizer, output_path / "last", metadata=metadata)
        if chosen_loss < best:
            best = chosen_loss
            save_bundle(model, tokenizer, output_path / "best", metadata=metadata)
        if chosen_loss < stopping_best - args.min_delta:
            stopping_best, bad_epochs = chosen_loss, 0
        else:
            bad_epochs += 1
        if args.patience and bad_epochs >= args.patience:
            print(
                json.dumps({"early_stop_epoch": epoch + 1, "patience": args.patience}), flush=True
            )
            break

    (output_path/'TRAINING_COMPLETE').write_text(json.dumps({'steps':step,'best_selection_loss':best})+'\n')


if __name__ == "__main__":
    main()
