import pytest

torch = pytest.importorskip('torch')
pytest.importorskip('torchvision')

from light_foundation_vlm.smoke import make_toy, toy_batch


def test_streamed_memory_matches_the_same_causal_clip():
    torch.manual_seed(3)
    model = make_toy().eval()
    batch = toy_batch()
    with torch.no_grad():
        full = model(batch['frames'], batch['input_ids'])
        state = None
        for step in range(batch['frames'].shape[1]):
            streamed = model(batch['frames'][:, step:step + 1], batch['input_ids'], state=state)
            state = streamed['state']
    for expected, actual in zip(full['state'], state):
        torch.testing.assert_close(actual, expected)
    torch.testing.assert_close(streamed['mask_logits'], full['mask_logits'])


def test_joint_control_freezes_only_requested_modules():
    model = make_toy()
    batch = toy_batch()
    for freeze_memory in (True, False):
        model.apply_stage('joint', freeze_encoder=True, freeze_memory=freeze_memory)
        model.zero_grad(set_to_none=True)
        model(**batch)['loss'].backward()
        memory_norm = sum(float(p.grad.square().sum()) for p in model.memory.parameters()
                          if p.grad is not None)
        assert (memory_norm == 0) if freeze_memory else (memory_norm > 0)
        assert all(p.grad is None for p in model.encoder.parameters())
        assert not model.encoder.training


def test_fast_event_readout_skips_language_and_matches_forward():
    model=make_toy().eval()
    model.event_head=torch.nn.Linear(model.config.memory_dim,5)
    batch=toy_batch()
    with torch.no_grad():
        expected=model(batch['frames'],batch['input_ids'])['event_logits']
    def forbidden(*args,**kwargs):
        raise AssertionError('Fast readout must not call the language model')
    model.language_model.forward=forbidden
    actual=model.predict_events(batch['frames'])['event_logits']
    torch.testing.assert_close(actual,expected)


def test_optional_masks_keep_same_vqa_context_and_memory_without_running_decoders():
    model = make_toy().eval()
    batch = toy_batch()
    with torch.no_grad():
        expected = model(batch['frames'], batch['input_ids'], return_language_context=True)
    def forbidden(*args, **kwargs):
        raise AssertionError('Disabled segmentation must skip LM prepass and pixel decoding')
    model.language_model.forward = forbidden
    model.pixel_decoder.forward = forbidden
    with torch.no_grad():
        actual = model(batch['frames'], batch['input_ids'], compute_masks=False,
                       return_language_context=True)
    assert actual['mask_logits'] is None and actual['presence_logits'] is None
    assert actual['language_logits'] is None
    torch.testing.assert_close(actual['language_inputs'], expected['language_inputs'])
    for value, reference in zip(actual['state'], expected['state']):
        torch.testing.assert_close(value, reference)
    with pytest.raises(ValueError, match='Mask supervision'):
        model(**batch, compute_masks=False)
