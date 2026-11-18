from dataclasses import dataclass
from hashlib import sha256
import json

from weather.templates.descriptors import parse_descriptor, descriptor_record, TemplateError
from weather.templates.validation import validate_template


@dataclass(frozen=True)
class TemplateBinding:
    template_id: str
    revision: int
    descriptors: tuple
    fingerprint: str


def canonical_descriptor_bytes(nodes):
    return json.dumps([descriptor_record(n) for n in nodes], sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True).encode("ascii")


def make_binding(template_id, revision, descriptors):
    if not isinstance(descriptors, (list, tuple)):
        raise TemplateError("descriptors must be a sequence")
    try:
        nodes = tuple(parse_descriptor(record) for record in descriptors)
    except RecursionError as error:
        raise TemplateError("descriptor tree too deep") from error
    validate_template(template_id, revision, nodes)
    data = canonical_descriptor_bytes(nodes)
    return TemplateBinding(template_id, revision, nodes, sha256(data).hexdigest())
