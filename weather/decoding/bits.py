class DecodeError(ValueError):
    pass


class BitReader:
    def __init__(self, chunks):
        # Exhausting the source here preserves exceptions even after a complete packet.
        blocks = []
        for chunk in chunks:
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("byte chunks required")
            blocks.append(bytes(chunk))
        self.data = b"".join(blocks)
        self.position = 0
        self.limit = len(self.data) * 8
        self.payload_end = None

    def read_uint(self, width):
        if type(width) is not int or width < 0:
            raise ValueError("nonnegative bit width required")
        end = self.position + width
        if end > self.limit:
            raise DecodeError("truncated bit field")
        value = 0
        while self.position < end:
            byte_index, offset = divmod(self.position, 8)
            take = min(8 - offset, end - self.position)
            value = (value << take) | (
                (self.data[byte_index] >> (8 - offset - take)) & ((1 << take) - 1))
            self.position += take
        return value

    def read_bytes(self, count):
        if self.position % 8:
            raise DecodeError("unaligned byte field")
        if type(count) is not int or count < 0:
            raise ValueError("nonnegative byte count required")
        start = self.position // 8
        self.read_uint(count * 8)
        return self.data[start:start + count]

    def start_payload(self, bit_count):
        if self.position % 8 or self.payload_end is not None:
            raise DecodeError("invalid payload boundary")
        remaining = len(self.data) - self.position // 8
        if remaining != (bit_count + 7) // 8:
            raise DecodeError("payload byte length mismatch")
        self.payload_end = self.position + bit_count
        self.limit = self.payload_end

    def finish_payload(self):
        if self.payload_end is None or self.position != self.payload_end:
            raise DecodeError("unconsumed payload bits")
        unused = (-self.payload_end) % 8
        if unused and self.data[-1] & ((1 << unused) - 1):
            raise DecodeError("nonzero padding")
