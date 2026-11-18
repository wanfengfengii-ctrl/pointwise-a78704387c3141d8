from dataclasses import dataclass
from datetime import date
from enum import Enum
from fractions import Fraction

from weather.decoding.events import FieldOccurrence


class ObservationError(ValueError):
    pass


class QualityState(str, Enum):
    UNSELECTED = "UNSELECTED"
    MISSING = "MISSING"
    VALUE = "VALUE"


@dataclass(frozen=True)
class Quality:
    state: QualityState
    code: int | None = None

    def __post_init__(self):
        if not isinstance(self.state, QualityState):
            raise ObservationError("unknown quality state")
        if self.state is QualityState.VALUE:
            if type(self.code) is not int or self.code < 0:
                raise ObservationError("quality value requires a nonnegative integer")
        elif self.code is not None:
            raise ObservationError("non-value quality cannot carry a code")


@dataclass(frozen=True)
class Observation:
    occurrence: FieldOccurrence
    station: str
    day: date
    measurement: str
    unit: str
    raw: int
    value: Fraction | None
    missing: bool
    quality: Quality
