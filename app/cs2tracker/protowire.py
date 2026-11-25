"""Minimal protobuf wire-format reader/writer (stdlib only)."""


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def parse(buf: bytes) -> list[tuple[int, int, int | bytes]]:
    """Return (field, wiretype, value) for every field in order.

    Varint/fixed fields give an int; length-delimited fields give bytes.
    """
    out: list[tuple[int, int, int | bytes]] = []
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, pos = _read_varint(buf, pos)
        elif wt == 1:
            if pos + 8 > len(buf):
                raise ValueError("truncated fixed64")
            val = int.from_bytes(buf[pos:pos + 8], "little")
            pos += 8
        elif wt == 2:
            n, pos = _read_varint(buf, pos)
            if pos + n > len(buf):
                raise ValueError("truncated length-delimited field")
            val = bytes(buf[pos:pos + n])
            pos += n
        elif wt == 5:
            if pos + 4 > len(buf):
                raise ValueError("truncated fixed32")
            val = int.from_bytes(buf[pos:pos + 4], "little")
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        out.append((field, wt, val))
    return out


def packed_varints(buf: bytes) -> list[int]:
    out: list[int] = []
    pos = 0
    while pos < len(buf):
        v, pos = _read_varint(buf, pos)
        out.append(v)
    return out


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative varint")
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_varint_field(field: int, value: int) -> bytes:
    return _encode_varint(field << 3) + _encode_varint(value)


def encode_bytes_field(field: int, payload: bytes) -> bytes:
    return _encode_varint(field << 3 | 2) + _encode_varint(len(payload)) + payload
