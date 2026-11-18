from dataclasses import dataclass
from datetime import date

from weather.decoding.bits import DecodeError


@dataclass(frozen=True)
class PacketHeader:
    template_id: str
    revision: int
    station: str
    day: date
    payload_bits: int


def read_identifier(reader):
    length = reader.read_uint(8)
    if not 1 <= length <= 63:
        raise DecodeError("invalid identifier length")
    try:
        return reader.read_bytes(length).decode("ascii")
    except UnicodeDecodeError as error:
        raise DecodeError("non-ASCII identifier") from error


def decode_header(reader):
    if reader.read_bytes(4) != b"WOBS" or reader.read_uint(8) != 1:
        raise DecodeError("unsupported packet header")
    template_id = read_identifier(reader)
    revision = reader.read_uint(32)
    station = read_identifier(reader)
    try:
        text = reader.read_bytes(10).decode("ascii")
        day = date.fromisoformat(text)
        if day.isoformat() != text:
            raise ValueError("noncanonical date")
    except (UnicodeDecodeError, ValueError) as error:
        raise DecodeError("invalid observation date") from error
    bit_count = reader.read_uint(32)
    reader.start_payload(bit_count)
    return PacketHeader(template_id, revision, station, day, bit_count)


def verify_header_binding(header, binding):
    from weather.observations.models import ObservationError
    if (header.template_id, header.revision) != (binding.template_id, binding.revision):
        raise ObservationError("packet references a different template revision")
