from dataclasses import replace

from weather.decoding.events import (
    GroupOpened, GroupClosed, RawObservationEvent, RawQualityEvent,
)
from weather.observations.models import Observation, ObservationError, Quality, QualityState
from weather.observations.convert import normalize_value
from weather.observations.quality import resolve_group_quality


def assemble_observations(events, header):
    stack = []
    seen = set()
    observations = []
    for event in events:
        if isinstance(event, GroupOpened):
            if event.key in seen or (stack and stack[-1]["block"] is not None):
                raise ObservationError("duplicate or late group opening")
            seen.add(event.key)
            stack.append({"key": event.key, "members": [], "block": None})
        elif isinstance(event, RawObservationEvent):
            if not stack or stack[-1]["key"] != event.occurrence.group:
                raise ObservationError("observation outside owning group")
            if stack[-1]["block"] is not None:
                raise ObservationError("observation after quality")
            if event.occurrence.sequence != len(observations):
                raise ObservationError("noncontiguous observation order")
            value, missing, unit = normalize_value(event.raw, event.descriptor)
            item = Observation(event.occurrence, header.station, header.day,
                               event.descriptor.measurement, unit, event.raw, value,
                               missing, Quality(QualityState.UNSELECTED))
            stack[-1]["members"].append(len(observations))
            observations.append(item)
        elif isinstance(event, RawQualityEvent):
            if not stack or stack[-1]["key"] != event.group or stack[-1]["block"] is not None:
                raise ObservationError("unowned or repeated quality block")
            stack[-1]["block"] = event
        elif isinstance(event, GroupClosed):
            if not stack or stack[-1]["key"] != event.key:
                raise ObservationError("unpaired group boundary")
            context = stack.pop()
            indexes = context["members"]
            members = tuple(observations[index] for index in indexes)
            qualities = resolve_group_quality(event.key, members, context["block"])
            for index, quality in zip(indexes, qualities):
                observations[index] = replace(observations[index], quality=quality)
        else:
            raise ObservationError("unknown decode event")
    if stack:
        raise ObservationError("unclosed group")
    return tuple(observations)
