"""Minimal client for Minecraft's RCON protocol (no third-party dependencies).

Protocol reference: https://minecraft.wiki/w/RCON
Each packet is: int32 length | int32 request id | int32 type | payload | 0x00 0x00
(all integers little-endian; length excludes its own 4 bytes).
"""

from __future__ import annotations

import socket
import struct
from typing import Optional, Tuple

TYPE_RESPONSE = 0
TYPE_COMMAND = 2
TYPE_AUTH = 3

# The vanilla server rejects client packets with payloads larger than this.
MAX_COMMAND_BYTES = 1446


class RconError(Exception):
    """Connection or protocol failure."""


class RconAuthError(RconError):
    """The server rejected the RCON password."""


def encode_packet(request_id: int, packet_type: int, payload: str) -> bytes:
    body = struct.pack("<ii", request_id, packet_type) + payload.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


def decode_packet(body: bytes) -> Tuple[int, int, str]:
    """Decode a packet body (everything after the length prefix)."""
    if len(body) < 10:
        raise RconError(f"RCON packet too short ({len(body)} bytes)")
    request_id, packet_type = struct.unpack("<ii", body[:8])
    payload = body[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, payload


class RconClient:
    """Synchronous RCON connection. Use as a context manager or call connect()/close()."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._next_id = 1

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise RconError(
                f"Could not connect to RCON at {self.host}:{self.port} ({e}). "
                "Is the server running with enable-rcon=true?"
            ) from e
        request_id = self._send(TYPE_AUTH, self.password)
        response_id, _, _ = self._read_packet()
        if response_id == -1 or response_id != request_id:
            self.close()
            raise RconAuthError("RCON password was rejected (check rcon.password in server.properties)")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def command(self, command: str) -> str:
        """Run a server command (without the leading slash) and return its text output."""
        if self._sock is None:
            raise RconError("Not connected")
        if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise RconError(f"Command too long for RCON ({len(command)} chars)")
        request_id = self._send(TYPE_COMMAND, command)
        response_id, _, payload = self._read_packet()
        if response_id != request_id:
            raise RconError(f"RCON response id mismatch (sent {request_id}, got {response_id})")
        return payload

    def _send(self, packet_type: int, payload: str) -> int:
        request_id = self._next_id
        self._next_id = self._next_id % 2_000_000_000 + 1
        try:
            self._sock.sendall(encode_packet(request_id, packet_type, payload))  # type: ignore[union-attr]
        except OSError as e:
            raise RconError(f"RCON send failed: {e}") from e
        return request_id

    def _read_packet(self) -> Tuple[int, int, str]:
        (length,) = struct.unpack("<i", self._recv_exact(4))
        if length < 10 or length > 1_000_000:
            raise RconError(f"Invalid RCON packet length {length}")
        return decode_packet(self._recv_exact(length))

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))  # type: ignore[union-attr]
            except OSError as e:
                raise RconError(f"RCON receive failed: {e}") from e
            if not chunk:
                raise RconError("RCON connection closed by server")
            buf.extend(chunk)
        return bytes(buf)
