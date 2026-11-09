"""NIIMBOT framing and row encoding, independent of BLE notification boundaries."""

from dataclasses import dataclass
from functools import reduce
from operator import xor

from .errors import ProtocolError


def parse_print_status(data):
    """Decode B3 progress; only the known 10-byte layout has an error field."""
    if len(data) < 4:
        raise ProtocolError("Truncated print status")
    return dict(
        pages=int.from_bytes(data[:2], "big"),
        print_percent=data[2],
        feed_percent=data[3],
        error_code=data[6] if len(data) == 10 else None,
        raw=data.hex(),
    )


@dataclass(frozen=True)
class Packet:
    command: int
    data: bytes

    def encode(self):
        if not 0 <= self.command <= 255 or len(self.data) > 255:
            raise ValueError("Invalid command or payload length")
        body = bytes((self.command, len(self.data))) + self.data
        return b"\x55\x55" + body + bytes((reduce(xor, body, 0),)) + b"\xaa\xaa"


class FrameDecoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        packets = []
        while len(self.buffer) >= 2:
            start = self.buffer.find(b"\x55\x55")
            if start < 0:
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1] == 0x55 else b""
                break
            del self.buffer[:start]
            if len(self.buffer) < 4:
                break
            size = self.buffer[3] + 7
            if len(self.buffer) < size:
                break
            frame = bytes(self.buffer[:size])
            del self.buffer[:size]
            if frame[-2:] != b"\xaa\xaa" or reduce(xor, frame[2:-2], 0) != 0:
                self.buffer.clear()
                raise ProtocolError("Invalid packet checksum or trailer")
            packets.append(Packet(frame[2], frame[4:-3]))
        return packets


def row_packets(page):
    page.validate()
    stride = (page.width + 7) // 8
    row = 0
    while row < page.height:
        bitmap = page.bitmap[row * stride : (row + 1) * stride]
        run = 1
        while (
            run < 200
            and row + run < page.height
            and page.bitmap[(row + run) * stride : (row + run + 1) * stride] == bitmap
        ):
            run += 1
        prefix = row.to_bytes(2, "big")
        black = sum(byte.bit_count() for byte in bitmap)
        if not black:
            yield Packet(0x84, prefix + bytes((run,)))
        else:
            yield Packet(
                0x85, prefix + b"\x00" + black.to_bytes(2, "little") + bytes((run,)) + bitmap
            )
        row += run
