from fractions import Fraction

from weather.templates.descriptors import MEASUREMENT_UNITS
from weather.observations.models import ObservationError


def canonical_unit(measurement):
    try:
        return MEASUREMENT_UNITS[measurement][0]
    except (KeyError, TypeError) as error:
        raise ObservationError("unknown measurement") from error


def normalize_value(raw, descriptor):
    if type(raw) is not int or not 0 <= raw < (1 << descriptor.width):
        raise ObservationError("raw value outside field width")
    unit = canonical_unit(descriptor.measurement)
    if descriptor.unit not in MEASUREMENT_UNITS[descriptor.measurement]:
        raise ObservationError("incompatible field unit")
    if raw == (1 << descriptor.width) - 1:
        return None, True, unit
    factor = (Fraction(1, 10**descriptor.scale) if descriptor.scale >= 0
              else Fraction(10**(-descriptor.scale)))
    value = (raw + descriptor.reference) * factor
    if descriptor.unit == "degC":
        value += Fraction(27315, 100)
    elif descriptor.unit == "degF":
        value = (value - 32) * Fraction(5, 9) + Fraction(27315, 100)
    elif descriptor.unit == "hPa":
        value *= 100
    elif descriptor.unit == "mm":
        value /= 1000
    elif descriptor.unit == "knot":
        value *= Fraction(463, 900)
    return value, False, unit
