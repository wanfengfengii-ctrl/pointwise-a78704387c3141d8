from dataclasses import dataclass


class TemplateError(ValueError):
    pass


MEASUREMENT_UNITS = {
    "air_temperature": ("K", "degC", "degF"),
    "pressure": ("Pa", "hPa"),
    "precipitation": ("m", "mm"),
    "wind_speed": ("m/s", "knot"),
}


@dataclass(frozen=True)
class FieldDescriptor:
    measurement: str
    width: int
    unit: str
    reference: int = 0
    scale: int = 0


@dataclass(frozen=True)
class FixedGroup:
    count: int
    children: tuple


@dataclass(frozen=True)
class DelayedGroup:
    count_width: int
    children: tuple


@dataclass(frozen=True)
class QualityDescriptor:
    width: int


def parse_descriptor(record):
    if not isinstance(record, dict):
        raise TemplateError("descriptor must be an object")
    kind = record.get("kind")
    allowed = {
        "field": {"kind", "measurement", "width", "unit", "reference", "scale"},
        "fixed": {"kind", "count", "children"},
        "delayed": {"kind", "count_width", "children"},
        "quality": {"kind", "width"},
    }
    if not isinstance(kind, str) or kind not in allowed or set(record) - allowed[kind]:
        raise TemplateError("unknown descriptor or property")
    try:
        if kind == "field":
            return FieldDescriptor(record["measurement"], record["width"], record["unit"],
                                   record.get("reference", 0), record.get("scale", 0))
        if kind == "quality":
            return QualityDescriptor(record["width"])
        children = record["children"]
        if not isinstance(children, (list, tuple)):
            raise TemplateError("children must be a sequence")
        nodes = tuple(parse_descriptor(child) for child in children)
        if kind == "fixed":
            return FixedGroup(record["count"], nodes)
        return DelayedGroup(record["count_width"], nodes)
    except (KeyError, RecursionError) as error:
        raise TemplateError("incomplete or excessively deep descriptor") from error


def descriptor_record(node):
    if isinstance(node, FieldDescriptor):
        return dict(kind="field", measurement=node.measurement, width=node.width,
                    unit=node.unit, reference=node.reference, scale=node.scale)
    if isinstance(node, QualityDescriptor):
        return dict(kind="quality", width=node.width)
    if isinstance(node, FixedGroup):
        return dict(kind="fixed", count=node.count,
                    children=[descriptor_record(n) for n in node.children])
    if isinstance(node, DelayedGroup):
        return dict(kind="delayed", count_width=node.count_width,
                    children=[descriptor_record(n) for n in node.children])
    raise TemplateError("unknown node")
