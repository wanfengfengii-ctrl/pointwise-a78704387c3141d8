from dataclasses import dataclass

from weather.observations.models import QualityState


@dataclass(frozen=True)
class FilterDecision:
    accepted: bool
    reason: str | None


def evaluate_observation(observation, rule):
    if observation.missing:
        return FilterDecision(False, "observation_missing")
    quality = observation.quality
    if quality.state is QualityState.UNSELECTED:
        return FilterDecision(rule.allow_unselected,
                              None if rule.allow_unselected else "unselected")
    if quality.state is QualityState.MISSING:
        return FilterDecision(rule.allow_quality_missing,
                              None if rule.allow_quality_missing else "quality_missing")
    accepted = quality.code in rule.accepted_codes
    return FilterDecision(accepted, None if accepted else "quality_rejected")


def filter_observations(observations, rule):
    for observation in observations:
        yield observation, evaluate_observation(observation, rule)
