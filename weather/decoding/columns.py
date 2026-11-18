"""WOBS2 multi-subset column packets.

The column packet shares one compressed bitstream across ordered subsets.  The
walker below reads the public differential columns once, reconstructs one
independent WOBS1-style payload bitstream per subset, and then reuses the V1
decoder, quality resolution, assembly and validation machinery for every
subset.  No subset is committed independently: any failure raises before any
publication and the whole packet is rejected.
"""
from dataclasses import dataclass, replace
from datetime import date

from weather.templates.descriptors import (
    FieldDescriptor, FixedGroup, DelayedGroup, QualityDescriptor,
)
from weather.decoding.bits import BitReader, DecodeError
from weather.decoding.header import read_identifier, verify_header_binding
from weather.decoding.groups import expand_template
from weather.observations.assemble import assemble_observations
from weather.observations.validate import validate_batch_observations
from weather.observations.models import ObservationError


@dataclass(frozen=True)
class ColumnsHeader:
    template_id: str
    revision: int
    subsets: tuple  # tuple of (station, date) in packet order
    payload_bits: int

    @property
    def station(self):
        # Publication keeps the existing batches row shape; the first subset is
        # the canonical packet-level station while each observation carries its
        # own subset station/day.
        return self.subsets[0][0]

    @property
    def day(self):
        return self.subsets[0][1]


def decode_columns_header(reader):
    if reader.read_bytes(4) != b"WOBS" or reader.read_uint(8) != 2:
        raise DecodeError("unsupported column packet header")
    template_id = read_identifier(reader)
    revision = reader.read_uint(32)
    subset_count = reader.read_uint(8)
    if not 1 <= subset_count <= 16:
        raise DecodeError("subset count must be between 1 and 16")
    subsets = []
    for _ in range(subset_count):
        station = read_identifier(reader)
        try:
            text = reader.read_bytes(10).decode("ascii")
            day = date.fromisoformat(text)
            if day.isoformat() != text:
                raise ValueError("noncanonical date")
        except (UnicodeDecodeError, ValueError) as error:
            raise DecodeError("invalid subset observation date") from error
        subsets.append((station, day))
    bit_count = reader.read_uint(32)
    reader.start_payload(bit_count)
    return ColumnsHeader(template_id, revision, tuple(subsets), bit_count)


def read_scalar_column(reader, width, active):
    """Read one scalar column (base + 6-bit k + per-active deltas)."""
    base = reader.read_uint(width)
    k = reader.read_uint(6)
    if k > width:
        raise DecodeError("column delta width exceeds scalar width")
    values = {}
    if k == 0:
        for index in active:
            values[index] = base
        return values
    limit = 1 << width
    for index in active:
        raw = base + reader.read_uint(k)
        if raw >= limit:
            raise DecodeError("column value overflows scalar width")
        values[index] = raw
    return values


def walk_columns(reader, nodes, path, parent_occurrence, active, buffers,
                 count_width=None):
    """Reconstruct per-subset V1 payload bits for one synchronized group call.

    ``active`` maps subset index -> replication count for the subsets that
    actually reach this call (identified by ``path`` and ``parent_occurrence``).
    The call runs rounds i in 0..max(count)-1; only subsets whose count exceeds
    i enter a round.  A child group is a separate call per parent round because
    its V1 GroupKey includes the parent occurrence, and its delayed-count column
    is read once per such round over the subsets present in that round.

    Each call builds a per-subset segment list in V1 depth-first order; body
    fields append bits while child calls splice their completed segments.  When
    this call is a delayed group body, ``count_width`` is that group's count
    width and each subset's segment starts with its count word (never a direct
    field member).  The wire places quality length/selection/word columns after
    every replication round finishes, so they are buffered and each subset's
    assembled V1 quality block is appended at this call's tail.
    """
    if not active:
        return
    quality = nodes[-1] if nodes and isinstance(nodes[-1], QualityDescriptor) else None
    body = nodes[:-1] if quality is not None else nodes
    segment = {index: [] for index in active}
    if count_width is not None:
        for index in active:
            segment[index].append(
                format(active[index], "0" + str(count_width) + "b"))
    member_counts = {index: 0 for index in active}
    max_count = max(active.values())
    for iteration in range(max_count):
        round_active = tuple(index for index in active if active[index] > iteration)
        occurrence = parent_occurrence + (iteration,) if path else ()
        for node_index, node in enumerate(body):
            node_path = path + (node_index,)
            if isinstance(node, FieldDescriptor):
                values = read_scalar_column(reader, node.width, round_active)
                for index in round_active:
                    segment[index].append(
                        format(values[index], "0" + str(node.width) + "b"))
                    member_counts[index] += 1
            else:
                if isinstance(node, FixedGroup):
                    child_counts = {index: node.count for index in round_active}
                else:
                    # One delayed-count column per parent round, shared by the
                    # subsets present in this round; an all-ones reserved count
                    # stays encoded and is rejected by the subset V1 decoder.
                    child_counts = read_scalar_column(
                        reader, node.count_width, round_active)
                child_buffers = {index: [] for index in round_active}
                walk_columns(reader, node.children, node_path, occurrence,
                             child_counts, child_buffers,
                             node.count_width if isinstance(node, DelayedGroup) else None)
                for index in round_active:
                    segment[index].extend(child_buffers[index])
    if quality is not None:
        lengths = read_scalar_column(reader, 32, tuple(active))
        for index in active:
            if lengths[index] != member_counts[index]:
                raise ObservationError("quality length does not match direct members")
        # Selection columns first, one per global member position, each spanning
        # only the subsets that still have a member at that position.
        selections = {}
        for position in range(max(lengths.values())):
            select_active = tuple(index for index in active if lengths[index] > position)
            selections[position] = read_scalar_column(reader, 1, select_active)
        # All selection columns finish before any quality-word column begins.
        # A subset's local member position equals the global position because
        # every subset's direct members occupy contiguous positions 0..L-1 and
        # a position-j column includes exactly the subsets with length > j.
        words = {}
        for position in range(max(lengths.values())):
            word_active = tuple(index for index in active
                                if lengths[index] > position and selections[position][index])
            if word_active:
                words[position] = read_scalar_column(reader, quality.width, word_active)
        for index in active:
            # V1 quality block: 32-bit length, every selection bit, then the
            # quality words in selection order (never interleaved).
            bitmap = []
            words_in_order = []
            for position in range(lengths[index]):
                selected = bool(selections[position][index])
                bitmap.append("1" if selected else "0")
                if selected:
                    words_in_order.append(format(
                        words[position][index], "0" + str(quality.width) + "b"))
            segment[index].append(format(lengths[index], "032b"))
            segment[index].extend(bitmap)
            segment[index].extend(words_in_order)
    for index in active:
        buffers[index].extend(segment[index])


def reconstruct_subsets(reader, binding, header):
    subset_indexes = tuple(range(len(header.subsets)))
    buffers = {index: [] for index in subset_indexes}
    # The root invocation contains every subset exactly once.
    walk_columns(reader, binding.descriptors, (), (),
                 {index: 1 for index in subset_indexes}, buffers)
    return {index: "".join(buffers[index]) for index in subset_indexes}


def _pack_bits(bits):
    padded = bits + "0" * (-len(bits) % 8)
    return bytes(int(padded[offset:offset + 8], 2)
                 for offset in range(0, len(padded), 8))


@dataclass(frozen=True)
class _SubsetHeader:
    station: str
    day: date


def _retag(item, subset_index, sequence, station, day):
    identity = replace(item.occurrence, sequence=sequence)
    return replace(item, occurrence=identity, station=station, day=day,
                   subset_index=subset_index)


def decode_column_packet(reader, binding):
    header = decode_columns_header(reader)
    verify_header_binding(header, binding)
    payloads = reconstruct_subsets(reader, binding, header)
    reader.finish_payload()
    observations = []
    sequence = 0
    for index, (station, day) in enumerate(header.subsets):
        bits = payloads[index]
        subset_reader = BitReader([_pack_bits(bits)])
        subset_reader.start_payload(len(bits))
        events = expand_template(subset_reader, binding)
        subset_reader.finish_payload()
        items = assemble_observations(events, _SubsetHeader(station, day))
        validate_batch_observations(events, items, binding)
        for item in items:
            observations.append(_retag(item, index, sequence, station, day))
            sequence += 1
    return header, tuple(observations)
