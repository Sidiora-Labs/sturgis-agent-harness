"""Bounded four-byte network-order framing for internal byte transports."""

import asyncio
from agent.protocol import CoreError


def encode_frame(payload: bytes, max_frame=1048576) -> bytes:
    if not isinstance(payload, bytes) or len(payload) > max_frame or max_frame < 0:
        raise CoreError("frame_size", "Invalid or oversized frame")
    return len(payload).to_bytes(4, "big") + payload


class FrameCodec:
    def __init__(self, max_frame=1048576):
        if type(max_frame) is not int or not 0 <= max_frame <= 0xFFFFFFFF:
            raise CoreError("frame_size", "Invalid frame limit")
        self.max_frame, self._buffer, self._size = max_frame, bytearray(), None

    def feed(self, data: bytes) -> list[bytes]:
        result, offset = [], 0
        while offset < len(data):
            needed = 4 if self._size is None else self._size
            take = min(needed-len(self._buffer), len(data)-offset)
            self._buffer.extend(data[offset:offset+take])
            offset += take
            if len(self._buffer) < needed:
                continue
            if self._size is None:
                self._size = int.from_bytes(self._buffer,"big")
                self._buffer.clear()
                if self._size > self.max_frame:
                    raise CoreError("frame_size", "Incoming frame exceeds limit")
                if self._size != 0:
                    continue
            result.append(bytes(self._buffer))
            self._buffer.clear()
            self._size = None
        return result

    def finish(self):
        if self._buffer or self._size is not None:
            raise CoreError("truncated_frame", "EOF inside a frame")


async def read_frame(reader, max_frame=1048576):
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise CoreError("truncated_frame", "EOF inside header") from exc
    size = int.from_bytes(header,"big")
    if size > max_frame:
        raise CoreError("frame_size", "Incoming frame exceeds limit")
    try:
        return await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:
        raise CoreError("truncated_frame", "EOF inside payload") from exc


async def write_frame(writer, payload, max_frame=1048576):
    writer.write(encode_frame(payload,max_frame))
    await writer.drain()
