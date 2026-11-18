from dataclasses import dataclass

from weather.templates.descriptors import FieldDescriptor


@dataclass(frozen=True)
class GroupKey:
    template_path: tuple
    parent_occurrence: tuple


@dataclass(frozen=True)
class FieldOccurrence:
    template_path: tuple
    occurrence_path: tuple
    group: GroupKey
    sequence: int


@dataclass(frozen=True)
class GroupOpened:
    key: GroupKey
    count: int


@dataclass(frozen=True)
class GroupClosed:
    key: GroupKey


@dataclass(frozen=True)
class RawObservationEvent:
    occurrence: FieldOccurrence
    descriptor: FieldDescriptor
    raw: int


@dataclass(frozen=True)
class RawQualityEvent:
    group: GroupKey
    bitmap: tuple
    words: tuple
    width: int
