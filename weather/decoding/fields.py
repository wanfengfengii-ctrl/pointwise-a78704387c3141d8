from weather.decoding.bits import DecodeError
from weather.decoding.events import RawObservationEvent, RawQualityEvent


def read_raw_field(reader, descriptor, occurrence):
    return RawObservationEvent(occurrence, descriptor, reader.read_uint(descriptor.width))


def read_replication_count(reader, descriptor):
    count = reader.read_uint(descriptor.count_width)
    if count == (1 << descriptor.count_width) - 1:
        raise DecodeError("reserved delayed replication count")
    return count


def read_quality_block(reader, descriptor, group):
    length = reader.read_uint(32)
    bitmap = tuple(bool(reader.read_uint(1)) for _ in range(length))
    words = tuple(reader.read_uint(descriptor.width) for selected in bitmap if selected)
    return RawQualityEvent(group, bitmap, words, descriptor.width)
