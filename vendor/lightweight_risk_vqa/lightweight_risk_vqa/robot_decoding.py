"""Small dependency-free token grammar for the robot VQA UI.

The LM chooses descriptive text, motion, intent and risk. The grammar enforces
the configured risk/action contract during decoding, without repairing an answer.
Narrative tokens allow English words, numbers and normal sentence punctuation.
"""
import json

from .robot_vqa import MOTIONS, RISK_ACTIONS


def legal_risks(motion, intent):
    if motion == 'TOWARD_CAMERA' or intent == 'LIKELY':
        return ('ELEVATED', 'IMMINENT')
    if motion != 'UNCERTAIN' and intent == 'UNLIKELY':
        return ('LOW', 'UNCERTAIN', 'ELEVATED', 'IMMINENT')
    return ('UNCERTAIN', 'ELEVATED', 'IMMINENT')


class RobotJsonDecoder:
    """Cache a finite JSON enum trie and a safe English-text vocabulary once."""
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.root = {}
        self.headers = []
        for motion in sorted(MOTIONS):
            for intent in ('LIKELY', 'UNLIKELY', 'UNCERTAIN'):
                for risk in legal_risks(motion, intent):
                    fields = {'motion': motion, 'approach_intent': intent,
                              'collision_risk': risk, 'action': RISK_ACTIONS[risk]}
                    text = ',' + json.dumps(fields, separators=(',', ':'))[1:-1] + ',"explanation":"'
                    tokens = tokenizer.encode(text, add_special_tokens=False)
                    if tokenizer.decode(tokens, skip_special_tokens=True) != text:
                        raise ValueError('Tokenizer cannot round-trip the robot JSON grammar')
                    node = self.root
                    for token in tokens:
                        node = node.setdefault(token, {})
                    node[None] = True
                    self.headers.append(fields)
        self.quote = tokenizer.encode('"', add_special_tokens=False)
        if len(self.quote) != 1:
            raise ValueError('Robot JSON grammar requires a standalone quote token')
        self.quote = self.quote[0]
        self.opening = tokenizer.encode('{"observation":"', add_special_tokens=False)
        self.close = tokenizer.encode('}', add_special_tokens=False)
        # Permit English words, apostrophes, numbers and sentence punctuation.
        # Exclude JSON escapes, metadata underscores and markup delimiters.
        excluded = set(tokenizer.all_special_ids)
        self.text_ids = []
        vocab_size = len(tokenizer)
        for start in range(0, vocab_size, 4096):
            ids = list(range(start, min(start + 4096, vocab_size)))
            strings = tokenizer.batch_decode([[i] for i in ids], skip_special_tokens=False)
            self.text_ids.extend(i for i, value in zip(ids, strings)
                                 if i not in excluded and value
                                 and all(c.isascii() and (c.isalnum() or c in " .,!?-';:") for c in value))
        if not self.text_ids:
            raise ValueError('Tokenizer has no safe English narrative tokens')
        self.eos = tokenizer.eos_token_id
        if self.eos is None:
            raise ValueError('Tokenizer requires an EOS token')
        self._text_masks = {}

    def processor(self):
        return _RobotJsonProcessor(self)


class _RobotJsonProcessor:
    """Single-row greedy generation state, reset for each individual answer."""
    def __init__(self, grammar):
        self.g = grammar
        self.node = grammar.root
        self.processed = 0
        self.phase = 'opening'
        self.field_tokens = []
        self.fixed = grammar.opening
        self.fixed_offset = 0

    def _text_valid(self):
        text = self.g.tokenizer.decode(self.field_tokens, skip_special_tokens=True).strip()
        codes = MOTIONS | set(RISK_ACTIONS) | set(RISK_ACTIONS.values()) | {'LIKELY', 'UNLIKELY'}
        return len(text) >= 4 and any(c.isalpha() for c in text) and text not in codes

    def _consume(self, token):
        if self.phase == 'header':
            if token not in self.node:
                raise ValueError('Generated token violates robot JSON enum grammar')
            self.node = self.node[token]
            if None in self.node:
                self.phase, self.field_tokens = 'explanation', []
        elif self.phase in ('observation', 'explanation'):
            if token == self.g.quote:
                if not self._text_valid():
                    raise ValueError('Robot VQA narrative must be descriptive text, not a label')
                if self.phase == 'observation':
                    self.phase = 'header'
                else:
                    self.fixed, self.phase, self.fixed_offset = self.g.close, 'closing', 0
            else:
                self.field_tokens.append(token)
        elif self.phase in ('opening', 'closing'):
            if token != self.fixed[self.fixed_offset]:
                raise ValueError('Generated token violates robot JSON structure')
            self.fixed_offset += 1
            if self.fixed_offset == len(self.fixed):
                self.phase = 'observation' if self.phase == 'opening' else 'done'
                self.field_tokens = []
        elif token != self.g.eos:
            raise ValueError('Unexpected token after robot JSON completion')

    def __call__(self, input_ids, scores):
        import torch
        if input_ids.shape[0] != 1 or scores.shape[0] != 1:
            raise ValueError('Robot JSON decoding supports a single greedy stream')
        tokens = input_ids[0].tolist()
        if len(tokens) < self.processed:
            raise ValueError('Reuse a new robot JSON processor for every answer')
        for token in tokens[self.processed:]:
            self._consume(token)
        self.processed = len(tokens)
        if self.phase == 'header':
            allowed = list(self.node)
        elif self.phase in ('observation', 'explanation'):
            valid = self._text_valid()
            limit = 24 if self.phase == 'observation' else 32
            text = self.g.tokenizer.decode(self.field_tokens, skip_special_tokens=True).strip()
            sentence_complete = valid and text.endswith(('.', '!', '?'))
            if len(self.field_tokens) >= limit or sentence_complete:
                if not valid:
                    raise ValueError('Robot VQA failed to produce descriptive text within the token budget')
                allowed = [self.g.quote]
            else:
                key = (str(scores.device), scores.dtype, scores.shape[-1])
                if key not in self.g._text_masks:
                    mask = torch.full_like(scores, float('-inf'))
                    ids = [i for i in self.g.text_ids if i < scores.shape[-1]]
                    mask[:, ids] = 0
                    self.g._text_masks[key] = mask
                mask = self.g._text_masks[key]
                output = scores + mask
                if valid:
                    output[:, self.g.quote] = scores[:, self.g.quote]
                return output
        elif self.phase in ('opening', 'closing'):
            allowed = [self.fixed[self.fixed_offset]]
        else:
            allowed = [self.g.eos]
        mask = torch.full_like(scores, float('-inf'))
        mask[:, allowed] = 0
        return scores + mask
