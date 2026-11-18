from weather.templates.descriptors import (
    TemplateError, FieldDescriptor, FixedGroup, DelayedGroup, QualityDescriptor,
    MEASUREMENT_UNITS,
)


def integer(value, label, minimum=None, maximum=None):
    if type(value) is not int:
        raise TemplateError(label + " must be an integer")
    if minimum is not None and value < minimum:
        raise TemplateError(label + " below minimum")
    if maximum is not None and value > maximum:
        raise TemplateError(label + " above maximum")


def validate_identifier(value, label):
    if not isinstance(value, str):
        raise TemplateError(label + " must be ASCII text")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise TemplateError(label + " must be ASCII") from error
    if not 1 <= len(encoded) <= 63:
        raise TemplateError(label + " length must be 1..63")


def validate_field(node):
    integer(node.width, "field width", 1, 32)
    integer(node.reference, "reference")
    integer(node.scale, "scale")
    if not isinstance(node.measurement, str) or node.measurement not in MEASUREMENT_UNITS:
        raise TemplateError("unknown measurement")
    if node.unit not in MEASUREMENT_UNITS[node.measurement]:
        raise TemplateError("unit does not belong to measurement")


def validate_group(nodes, depth=0):
    if not isinstance(nodes, tuple):
        raise TemplateError("immutable descriptor sequence required")
    quality_seen = False
    for node in nodes:
        if quality_seen:
            raise TemplateError("quality must occur once at group tail")
        if isinstance(node, FieldDescriptor):
            validate_field(node)
        elif isinstance(node, QualityDescriptor):
            integer(node.width, "quality width", 1, 32)
            quality_seen = True
        elif isinstance(node, (FixedGroup, DelayedGroup)):
            if depth >= 2:
                raise TemplateError("at most two replication levels")
            if isinstance(node, FixedGroup):
                integer(node.count, "fixed count", 0)
            else:
                integer(node.count_width, "count width", 2, 16)
            validate_group(node.children, depth + 1)
        else:
            raise TemplateError("unknown descriptor")


def validate_template(template_id, revision, nodes):
    validate_identifier(template_id, "template id")
    integer(revision, "revision", 0, 2**32 - 1)
    validate_group(nodes)
