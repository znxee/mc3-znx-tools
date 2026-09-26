#!/usr/bin/env python3
"""Protocol-level test for mc3_pine.py; no PCSX2 process is required."""

from __future__ import annotations

import socket
import struct
import threading

from mc3_pine import MAILBOX, MAGIC, Pine


def word(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def serve(listener: socket.socket) -> None:
    conn, _ = listener.accept()
    with conn:
        size = struct.unpack("<I", conn.recv(4))[0]
        request = bytearray()
        while len(request) < size - 4:
            request.extend(conn.recv(size - 4 - len(request)))

        addresses = []
        for offset in range(0, len(request), 5):
            assert request[offset] == 2
            addresses.append(struct.unpack_from("<I", request, offset + 1)[0])

        values = {
            MAILBOX + 0x00: MAGIC,
            MAILBOX + 0x04: (48 << 16) | 1,
            MAILBOX + 0x08: 42,
            MAILBOX + 0x0C: 0x0F,
            MAILBOX + 0x10: word(123.5),
            MAILBOX + 0x14: word(6789.0),
            MAILBOX + 0x18: 4,
            MAILBOX + 0x1C: word(1.25),
            MAILBOX + 0x20: word(-2.5),
            MAILBOX + 0x24: word(3.75),
            MAILBOX + 0x28: 0x005ABCDE,
            MAILBOX + 0x2C: 0,
        }
        payload = b"\0" + b"".join(struct.pack("<I", values[a]) for a in addresses)
        conn.sendall(struct.pack("<I", len(payload) + 4) + payload)


def main() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    thread = threading.Thread(target=serve, args=(listener,), daemon=True)
    thread.start()

    pine = Pine("127.0.0.1", port)
    try:
        sample = pine.telemetry()
    finally:
        pine.close()
        listener.close()
    thread.join(1)

    assert sample is not None
    assert sample["sequence"] == 42
    assert sample["flags"] == 0x0F
    assert sample["speed_kmh"] == 123.5
    assert sample["rpm"] == 6789.0
    assert sample["gear"] == 4
    assert sample["velocity"] == (1.25, -2.5, 3.75)
    assert sample["player"] == 0x005ABCDE
    print("PINE ABI v1 OK")


if __name__ == "__main__":
    main()
