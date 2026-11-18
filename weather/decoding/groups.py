from weather.templates.descriptors import (
    FieldDescriptor, FixedGroup, DelayedGroup, QualityDescriptor,
)
from weather.decoding.events import GroupKey, FieldOccurrence, GroupOpened, GroupClosed
from weather.decoding.fields import (
    read_raw_field, read_replication_count, read_quality_block,
)


def decode_group(reader, nodes, path, parent_occurrence, count, sequence):
    key = GroupKey(path, parent_occurrence)
    yield GroupOpened(key, count)
    quality = nodes[-1] if nodes and isinstance(nodes[-1], QualityDescriptor) else None
    body = nodes[:-1] if quality is not None else nodes
    for iteration in range(count):
        occurrence = parent_occurrence + (iteration,) if path else ()
        for index, node in enumerate(body):
            node_path = path + (index,)
            if isinstance(node, FieldDescriptor):
                identity = FieldOccurrence(node_path, occurrence, key, sequence[0])
                sequence[0] += 1
                yield read_raw_field(reader, node, identity)
            else:
                repetitions = node.count if isinstance(node, FixedGroup) else (
                    read_replication_count(reader, node))
                yield from decode_group(reader, node.children, node_path,
                                        occurrence, repetitions, sequence)
    if quality is not None:
        yield read_quality_block(reader, quality, key)
    yield GroupClosed(key)


def expand_template(reader, binding):
    return tuple(decode_group(reader, binding.descriptors, (), (), 1, [0]))
