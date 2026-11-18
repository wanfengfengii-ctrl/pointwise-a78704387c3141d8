from dataclasses import dataclass
from hashlib import sha256
import json

from weather.daily.rules import compile_quality_rule, quality_rule_fingerprint


@dataclass(frozen=True)
class DailyInputBinding:
    batches: tuple
    rule: object

    def record(self):
        return dict(batches=[list(pair) for pair in self.batches], rule=self.rule.record(),
                    rule_fingerprint=quality_rule_fingerprint(self.rule))


def freeze_inputs(queries, batch_ids, quality_rule=None):
    rule = compile_quality_rule(quality_rule)
    if isinstance(batch_ids, (str, bytes)):
        raise ValueError("batch_ids must be a collection of ids")
    ids = tuple(batch_ids)
    if any(not isinstance(batch_id, str) or not batch_id for batch_id in ids):
        raise ValueError("batch ids must be nonempty text")
    bindings = tuple((batch_id, queries.read_batch_binding(batch_id)[2])
                     for batch_id in sorted(set(ids)))
    return DailyInputBinding(bindings, rule)


def binding_fingerprint(binding):
    encoded = json.dumps(binding.record(), sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    return sha256(encoded).hexdigest()
