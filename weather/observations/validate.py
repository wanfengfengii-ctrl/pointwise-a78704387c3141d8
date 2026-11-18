from fractions import Fraction

from weather.decoding.events import GroupOpened, GroupClosed, RawObservationEvent, RawQualityEvent
from weather.templates.descriptors import FieldDescriptor, FixedGroup, DelayedGroup
from weather.observations.models import ObservationError, Quality, QualityState
from weather.observations.convert import canonical_unit


def descriptor_at(binding, path):
    nodes = binding.descriptors
    node = None
    for depth, index in enumerate(path):
        if type(index) is not int or not 0 <= index < len(nodes):
            raise ObservationError("invalid template path")
        node = nodes[index]
        if depth + 1 < len(path):
            if not isinstance(node, (FixedGroup, DelayedGroup)):
                raise ObservationError("path descends through a field")
            nodes = node.children
    return node


def validate_observation(item):
    if type(item.raw) is not int or type(item.missing) is not bool:
        raise ObservationError("invalid observation representation")
    if item.unit != canonical_unit(item.measurement):
        raise ObservationError("noncanonical unit")
    if (item.missing and item.value is not None) or (
            not item.missing and not isinstance(item.value, Fraction)):
        raise ObservationError("missing/value mismatch")
    if not isinstance(item.quality, Quality):
        raise ObservationError("unresolved quality")
    if item.quality.state is QualityState.VALUE and type(item.quality.code) is not int:
        raise ObservationError("invalid quality code")


def validate_batch_observations(events, observations, binding):
    stack = []
    opened = set()
    raw_events = []
    identity_set = set()
    for event in events:
        if isinstance(event, GroupOpened):
            key = event.key
            if key in opened or type(event.count) is not int or event.count < 0:
                raise ObservationError("invalid group invocation")
            if not stack:
                if key.template_path or key.parent_occurrence or opened or event.count != 1:
                    raise ObservationError("invalid root group")
            else:
                parent = stack[-1]
                if key.template_path[:-1] != parent.key.template_path:
                    raise ObservationError("group is not a direct child")
                node = descriptor_at(binding, key.template_path)
                if not isinstance(node, (FixedGroup, DelayedGroup)):
                    raise ObservationError("group path is not replication")
                parent_path = key.parent_occurrence
                if parent.key.template_path:
                    if (parent_path[:-1] != parent.key.parent_occurrence or not parent_path
                            or not 0 <= parent_path[-1] < parent.count):
                        raise ObservationError("invalid parent occurrence")
                elif parent_path:
                    raise ObservationError("root occurrence must be empty")
                if isinstance(node, FixedGroup) and event.count != node.count:
                    raise ObservationError("wrong fixed repetition count")
                if isinstance(node, DelayedGroup) and event.count >= (1 << node.count_width) - 1:
                    raise ObservationError("invalid delayed repetition count")
            opened.add(key)
            stack.append(event)
        elif isinstance(event, GroupClosed):
            if not stack or stack.pop().key != event.key:
                raise ObservationError("group boundary mismatch")
        elif isinstance(event, RawQualityEvent):
            if not stack or stack[-1].key != event.group:
                raise ObservationError("quality outside group")
        elif isinstance(event, RawObservationEvent):
            identity = event.occurrence
            if not stack or stack[-1].key != identity.group:
                raise ObservationError("field outside group")
            if identity.template_path[:-1] != identity.group.template_path:
                raise ObservationError("field not directly owned")
            node = descriptor_at(binding, identity.template_path)
            if not isinstance(node, FieldDescriptor) or node != event.descriptor:
                raise ObservationError("field descriptor mismatch")
            if identity.group.template_path:
                if (identity.occurrence_path[:-1] != identity.group.parent_occurrence
                        or not identity.occurrence_path
                        or not 0 <= identity.occurrence_path[-1] < stack[-1].count):
                    raise ObservationError("field occurrence outside replication")
            elif identity.occurrence_path:
                raise ObservationError("root field occurrence is not empty")
            key = (identity.template_path, identity.occurrence_path)
            if key in identity_set or identity.sequence != len(raw_events):
                raise ObservationError("duplicate identity or wrong order")
            identity_set.add(key)
            raw_events.append(event)
        else:
            raise ObservationError("unknown event")
    if stack or not opened or len(raw_events) != len(observations):
        raise ObservationError("incomplete observation collection")
    for event, item in zip(raw_events, observations):
        validate_observation(item)
        if item.occurrence != event.occurrence or item.raw != event.raw:
            raise ObservationError("raw and normalized occurrences differ")
        missing = event.raw == (1 << event.descriptor.width) - 1
        if item.missing != missing or item.measurement != event.descriptor.measurement:
            raise ObservationError("lost raw field semantics")
