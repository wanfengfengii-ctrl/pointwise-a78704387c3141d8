from datetime import date
from fractions import Fraction
import json

from weather.decoding.events import FieldOccurrence, GroupKey
from weather.observations.models import Observation, Quality, QualityState


def fraction_record(value):
    return None if value is None else [str(value.numerator), str(value.denominator)]


def fraction_from_record(value):
    return None if value is None else Fraction(int(value[0]), int(value[1]))


def observation_to_record(item):
    identity = item.occurrence
    return json.dumps({
        "path": identity.template_path, "occurrence": identity.occurrence_path,
        "group_path": identity.group.template_path,
        "group_parent": identity.group.parent_occurrence, "sequence": identity.sequence,
        "station": item.station, "day": item.day.isoformat(), "measurement": item.measurement,
        "unit": item.unit, "raw": item.raw, "value": fraction_record(item.value),
        "missing": item.missing, "quality": item.quality.state.value, "code": item.quality.code,
        "subset_index": item.subset_index,
    }, sort_keys=True, separators=(",", ":"))


def observation_from_record(record):
    data = json.loads(record)
    group = GroupKey(tuple(data["group_path"]), tuple(data["group_parent"]))
    identity = FieldOccurrence(tuple(data["path"]), tuple(data["occurrence"]),
                               group, data["sequence"])
    return Observation(identity, data["station"], date.fromisoformat(data["day"]),
                       data["measurement"], data["unit"], data["raw"],
                       fraction_from_record(data["value"]), data["missing"],
                       Quality(QualityState(data["quality"]), data["code"]),
                       data.get("subset_index"))
