import json

import pytest

torch = pytest.importorskip('torch')

from lightweight_risk_vqa.robot_decoding import RobotJsonDecoder
from lightweight_risk_vqa.robot_vqa import robot_schema_error


class CharacterTokenizer:
    all_special_ids = [128]
    eos_token_id = 128
    def __len__(self):
        return 130
    def encode(self, text, **kwargs):
        return [ord(c) for c in text]
    def decode(self, ids, **kwargs):
        return ''.join(chr(i) if i < 128 else '<eos>' if i == 128 else 'bad"\n' for i in ids)
    def batch_decode(self, sequences, **kwargs):
        return [self.decode(ids) for ids in sequences]


@pytest.fixture(scope='module')
def decoder():
    return RobotJsonDecoder(CharacterTokenizer())


def fields(motion='TOWARD_CAMERA', intent='LIKELY', risk='ELEVATED', action='DANGER_WARNING'):
    return {'observation': 'People walk.', 'motion': motion, 'approach_intent': intent,
            'collision_risk': risk, 'action': action, 'explanation': 'Observed movement.'}


def accepts(decoder, text):
    processor, tokens = decoder.processor(), []
    for token in decoder.tokenizer.encode(text):
        scores = processor(torch.tensor([tokens], dtype=torch.long), torch.zeros((1, 130)))
        if not torch.isfinite(scores[0, token]):
            return False
        tokens.append(token)
    scores = processor(torch.tensor([tokens]), torch.zeros((1, 130)))
    return torch.isfinite(scores[0, 128]).item()


@pytest.mark.parametrize('row', [
    fields(), fields(risk='IMMINENT', action='STOP'),
    fields('AWAY_FROM_CAMERA', 'UNLIKELY', 'LOW', 'CONTINUE'),
    fields('STATIONARY', 'UNCERTAIN', 'UNCERTAIN', 'OBSERVE'),
    fields('LATERAL_ACROSS_VIEW', 'UNLIKELY', 'LOW', 'CONTINUE'),
])
def test_complete_generation_is_valid_json_with_consistent_model_choices(decoder, row):
    assert accepts(decoder, json.dumps(row, separators=(',', ':')))
    assert robot_schema_error(row) == ''


@pytest.mark.parametrize('row', [
    fields(risk='IMMINENT', action='DANGER_WARNING'),
    fields(risk='LOW', action='CONTINUE'),
    fields('STATIONARY', 'LIKELY', 'LOW', 'CONTINUE'),
    fields('UNCERTAIN', 'UNCERTAIN', 'LOW', 'CONTINUE'),
    {**fields(), 'observation': 'TOWARD_CAMERA'},
    {**fields(), 'observation': 'LOW'},
    {**fields(), 'observation': 'Bad"quote'},
    {**fields(), 'observation': 'Bad\\slash'},
])
def test_contradictions_codes_and_broken_strings_are_masked_before_emission(decoder, row):
    assert not accepts(decoder, json.dumps(row, separators=(',', ':')))


def test_narrative_is_bounded_and_eos_is_not_allowed_early(decoder):
    prefix = '{"observation":"'
    processor = decoder.processor()
    tokens = decoder.tokenizer.encode(prefix)
    mask = processor(torch.tensor([tokens]), torch.zeros((1, 130)))
    assert not torch.isfinite(mask[0, 128])
    assert not torch.isfinite(mask[0, 129])  # Combined quote/newline token.
    tokens.extend(decoder.tokenizer.encode('a' * 24))
    mask = processor(torch.tensor([tokens]), torch.zeros((1, 130)))
    assert torch.isfinite(mask).sum() == 1
    assert torch.isfinite(mask[0, ord('"')])


def test_each_answer_has_its_own_processor(decoder):
    assert accepts(decoder, json.dumps(fields(), separators=(',', ':')))
    assert accepts(decoder, json.dumps(fields(risk='IMMINENT', action='STOP'), separators=(',', ':')))


def test_natural_json_safe_words_numbers_and_apostrophes_are_allowed(decoder):
    row = {**fields(), 'observation':"It's 2 people.", 'explanation':'They approach.'}
    assert accepts(decoder,json.dumps(row,separators=(',',':')))


def test_narrative_stops_after_one_complete_sentence(decoder):
    tokens = decoder.tokenizer.encode('{"observation":"People walk.')
    scores = decoder.processor()(torch.tensor([tokens]),torch.zeros((1,130)))
    assert torch.isfinite(scores).sum() == 1
    assert torch.isfinite(scores[0,ord('"')])
