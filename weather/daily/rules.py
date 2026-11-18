from dataclasses import dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class QualityRule:
    accepted_codes: tuple = (0,)
    allow_unselected: bool = False
    allow_quality_missing: bool = False

    def __post_init__(self):
        if not isinstance(self.accepted_codes, tuple):
            raise ValueError("quality codes must be an immutable tuple")
        if any(type(code) is not int or code < 0 for code in self.accepted_codes):
            raise ValueError("nonnegative integer quality codes required")
        if type(self.allow_unselected) is not bool or type(self.allow_quality_missing) is not bool:
            raise ValueError("quality flags must be booleans")
        object.__setattr__(self, "accepted_codes", tuple(sorted(set(self.accepted_codes))))

    def record(self):
        return dict(accepted_codes=list(self.accepted_codes),
                    allow_unselected=self.allow_unselected,
                    allow_quality_missing=self.allow_quality_missing)


DEFAULT_QUALITY_RULE = QualityRule()


def compile_quality_rule(rule=None):
    if rule is None:
        return DEFAULT_QUALITY_RULE
    if isinstance(rule, QualityRule):
        return rule
    if not isinstance(rule, dict) or set(rule) - {
            "accepted_codes", "allow_unselected", "allow_quality_missing"}:
        raise ValueError("unknown quality rule")
    codes = rule.get("accepted_codes", [0])
    if not isinstance(codes, (list, tuple)):
        raise ValueError("accepted_codes must be a sequence")
    return QualityRule(tuple(codes), rule.get("allow_unselected", False),
                       rule.get("allow_quality_missing", False))


def quality_rule_fingerprint(rule):
    encoded = json.dumps(compile_quality_rule(rule).record(), sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    return sha256(encoded).hexdigest()
