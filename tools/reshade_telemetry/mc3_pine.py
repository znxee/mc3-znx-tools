#!/usr/bin/env python3
"""Minimal PCSX2 PINE client for the MC3 public telemetry mailbox."""

from __future__ import annotations

import argparse
import socket
import struct
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 28011
MAILBOX = 0x0061C990
MAGIC = 0x4D433354
WORDS = 12
READ32 = 2


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("PCSX2 closed the PINE connection")
        data.extend(chunk)
    return bytes(data)


class Pine:
    def __init__(self, host: str, port: int, timeout: float = 1.0):
        self.sock = socket.create_connection((host, port), timeout)
        self.sock.settimeout(timeout)

    def close(self) -> None:
        self.sock.close()

    def read32_many(self, addresses: list[int]) -> list[int]:
        body = b"".join(bytes((READ32,)) + struct.pack("<I", a)
                        for a in addresses)
        self.sock.sendall(struct.pack("<I", len(body) + 4) + body)
        reply_size = struct.unpack("<I", recv_exact(self.sock, 4))[0]
        if reply_size < 5:
            raise RuntimeError(f"short PINE reply: {reply_size}")
        reply = recv_exact(self.sock, reply_size - 4)
        if reply[0] != 0:
            raise RuntimeError(f"PCSX2 refused the PINE read: {reply[0]:02X}")
        expected = 1 + 4 * len(addresses)
        if len(reply) != expected:
            raise RuntimeError(
                f"PINE reply has {len(reply)} bytes, expected {expected}")
        return list(struct.unpack("<%dI" % len(addresses), reply[1:]))

    def telemetry(self) -> dict[str, object] | None:
        # Sequence is read on both sides of one batched request. An odd or
        # changed value means the game updated the block during our snapshot.
        addresses = [MAILBOX + 8]
        addresses += [MAILBOX + i * 4 for i in range(WORDS)]
        addresses += [MAILBOX + 8]
        values = self.read32_many(addresses)
        before, after = values[0], values[-1]
        words = values[1:-1]
        if before != after or before & 1:
            return None
        if words[0] != MAGIC:
            raise RuntimeError(
                f"mailbox without the MC3T magic: {words[0]:08X}; "
                "are the game and mc3_telemetry.mod running?")
        version = words[1] & 0xFFFF
        size = words[1] >> 16
        if version != 1 or size != 48:
            raise RuntimeError(f"incompatible ABI: v{version}, {size} bytes")
        def as_float(word: int) -> float:
            return struct.unpack("<f", struct.pack("<I", word))[0]
        gear = struct.unpack("<i", struct.pack("<I", words[6]))[0]
        return {
            "sequence": before,
            "flags": words[3],
            "speed_kmh": as_float(words[4]),
            "rpm": as_float(words[5]),
            "gear": gear,
            "velocity": tuple(as_float(words[i]) for i in (7, 8, 9)),
            "player": words[10],
        }


def gear_name(gear: int) -> str:
    return "R" if gear == -1 else "N" if gear == 0 else str(gear)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read the telemetry published by mc3_telemetry.mod through PINE")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--once", action="store_true",
                        help="read one sample and exit")
    parser.add_argument("--hz", type=float, default=20.0,
                        help="display rate in continuous mode (default: 20)")
    args = parser.parse_args()

    try:
        pine = Pine(args.host, args.port)
    except OSError as exc:
        parser.error(
            f"could not connect to PINE {args.host}:{args.port}: {exc}. "
            "Turn on Settings > Advanced > Enable PINE Server and restart PCSX2")

    try:
        while True:
            sample = pine.telemetry()
            if sample is None:
                continue
            vx, vy, vz = sample["velocity"]
            line = (f"seq={sample['sequence']:8d} flags={sample['flags']:02X}  "
                    f"{sample['speed_kmh']:7.2f} km/h  "
                    f"{sample['rpm']:7.1f} rpm  "
                    f"gear={gear_name(sample['gear'])}  "
                    f"v=({vx:.3f},{vy:.3f},{vz:.3f})")
            print(line if args.once else "\r" + line,
                  end="\n" if args.once else "", flush=True)
            if args.once:
                break
            time.sleep(max(0.001, 1.0 / args.hz))
    except KeyboardInterrupt:
        if not args.once:
            print()
    finally:
        pine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
