from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from weather.observations.models import ObservationError
from weather.daily.filtering import filter_observations


@dataclass(frozen=True)
class DailyGroup:
    station: str
    measurement: str
    day: date
    unit: str
    input_count: int
    accepted_count: int
    observation_missing: int
    unselected: int
    quality_missing: int
    quality_rejected: int
    exact_sum: Fraction
    mean: Fraction | None


def finalize_group(key, accumulator):
    excluded = sum(accumulator[name] for name in (
        "observation_missing", "unselected", "quality_missing", "quality_rejected"))
    if accumulator["input_count"] != accumulator["accepted_count"] + excluded:
        raise ObservationError("daily counts do not reconcile")
    count = accumulator["accepted_count"]
    total = accumulator["exact_sum"]
    return DailyGroup(*key, accumulator["unit"], accumulator["input_count"], count,
                      accumulator["observation_missing"], accumulator["unselected"],
                      accumulator["quality_missing"], accumulator["quality_rejected"],
                      total, total / count if count else None)


def aggregate_daily(observations, rule):
    groups = {}
    for item, decision in filter_observations(observations, rule):
        key = (item.station, item.measurement, item.day)
        if key not in groups:
            groups[key] = dict(unit=item.unit, input_count=0, accepted_count=0,
                               observation_missing=0, unselected=0,
                               quality_missing=0, quality_rejected=0, exact_sum=Fraction(0))
        group = groups[key]
        if item.unit != group["unit"]:
            raise ObservationError("mixed canonical units")
        group["input_count"] += 1
        if decision.accepted:
            if not isinstance(item.value, Fraction):
                raise ObservationError("accepted observation has no exact value")
            group["accepted_count"] += 1
            group["exact_sum"] += item.value
        else:
            group[decision.reason] += 1
    return tuple(finalize_group(key, groups[key]) for key in sorted(groups))
