"""A tiny simulation of a vanilla Minecraft server's command responses, plus an RCON TCP server."""

from __future__ import annotations

import re
import socket
import struct
import threading
from typing import Dict, Set, Tuple

from mcbuild.rcon import TYPE_AUTH, TYPE_COMMAND, TYPE_RESPONSE, decode_packet, encode_packet

Pos = Tuple[int, int, int]


class FakeMinecraft:
    """Implements just enough commands, with vanilla's response wording, to exercise mcbuild."""

    def __init__(self, players=None, unload_ticks: int = 1, ground_y: int = 64):
        self.ground_y = ground_y
        self.world: Dict[Pos, str] = {}  # missing = grass at y<0, air otherwise
        self.players = players if players is not None else {"Steve": ((10.5, 64.0, 20.5), (0.0, 5.0))}
        self.forceloaded: Set[Tuple[int, int]] = set()
        # Chunks near spawn are "loaded"; far chunks need forceload + some polls.
        self.pending: Dict[Tuple[int, int], int] = {}
        self.unload_ticks = unload_ticks
        self.log = []
        self.entities: Dict[str, list] = {}  # tag -> [x, y, z]

    def block(self, pos: Pos) -> str:
        return self.world.get(pos, "minecraft:grass_block" if pos[1] < self.ground_y else "minecraft:air")

    def _loaded(self, x: int, z: int) -> bool:
        chunk = (x >> 4, z >> 4)
        if abs(chunk[0]) < 32 and abs(chunk[1]) < 32:
            return True
        if chunk in self.forceloaded:
            if self.pending.get(chunk, 0) > 0:
                self.pending[chunk] -= 1
                return False
            return True
        return False

    REPLACEABLE = {"minecraft:air", "minecraft:water", "minecraft:short_grass", "minecraft:tall_grass"}

    def surface(self, x: int, z: int, see_through_water: bool = False) -> int:
        """First free y above the highest motion-blocking block (leaves ignored; water counts
        unless see_through_water, like the ocean_floor height map)."""
        skip = {"minecraft:air", "minecraft:short_grass"} | ({"minecraft:water"} if see_through_water else set())
        for y in range(319, -65, -1):
            b = self.block((x, y, z))
            if b not in skip and not b.endswith("_leaves"):
                return y + 1
        return -64

    def handle(self, cmd: str) -> str:
        self.log.append(cmd)
        parts = cmd.split()
        name = parts[0]
        if name == "summon" and parts[1] == "minecraft:marker":
            tag = re.search(r'Tags:\["([^"]+)"\]', cmd).group(1)
            self.entities[tag] = [float(v) for v in parts[2:5]]
            return "Summoned new Marker"
        if name == "kill":
            tag = re.search(r"tag=([a-z_]+)", cmd).group(1)
            return "Killed 1 entity" if self.entities.pop(tag, None) else "No entity was found"
        if name == "execute" and "positioned over" in cmd and "run tp" in cmd:
            x, z = int(parts[2]), int(parts[4])
            tag = re.search(r"tag=([a-z_]+)", cmd).group(1)
            if tag not in self.entities:
                return "No entity was found"
            y = self.surface(x, z, see_through_water=parts[7] == "ocean_floor")
            self.entities[tag] = [x + 0.5, float(y), z + 0.5]
            return "Teleported Marker"
        if name == "data" and parts[1:3] == ["get", "entity"] and parts[3].startswith("@e"):
            tag = re.search(r"tag=([a-z_]+)", cmd).group(1)
            if tag not in self.entities:
                return "No entity was found"
            return f"Marker has the following entity data: {self.entities[tag][1]}d"
        if name == "list":
            return f"There are {len(self.players)} of a max of 20 players online: " + ", ".join(self.players)
        if name == "data" and parts[1:3] == ["get", "entity"]:
            player, field = parts[3], parts[4]
            if player not in self.players:
                return "No entity was found"
            pos, rot = self.players[player]
            if field == "Pos":
                return f"{player} has the following entity data: [{pos[0]}d, {pos[1]}d, {pos[2]}d]"
            return f"{player} has the following entity data: [{rot[0]}f, {rot[1]}f]"
        if name == "tellraw":
            return ""
        if name == "setblock":
            x, y, z = map(int, parts[1:4])
            block = parts[4]
            if not re.match(r"^minecraft:[a-z_]+(\[.*\])?$", block) or "bogus" in block:
                return f"Unknown block type '{block}'...<--[HERE]"
            if not self._loaded(x, z):
                return "That position is not loaded"
            if self.block((x, y, z)) == block:
                return "Could not set the block"
            self.world[(x, y, z)] = block
            return f"Changed the block at {x}, {y}, {z}"
        if name == "fill":
            x1, y1, z1, x2, y2, z2 = map(int, parts[1:7])
            block = parts[7]
            volume = (abs(x2 - x1) + 1) * (abs(y2 - y1) + 1) * (abs(z2 - z1) + 1)
            if volume > 32768:
                return f"Too many blocks in the specified area (maximum 32768, specified {volume})"
            if "bogus" in block:
                return f"Unknown block type '{block}'...<--[HERE]"
            only = self.REPLACEABLE if parts[8:10] == ["replace", "#minecraft:replaceable"] else None
            changed = 0
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    for z in range(min(z1, z2), max(z1, z2) + 1):
                        if only is not None and self.block((x, y, z)) not in only:
                            continue
                        if self.block((x, y, z)) != block:
                            self.world[(x, y, z)] = block
                            changed += 1
            return f"Successfully filled {changed} block(s)" if changed else "No blocks were filled"
        if name == "forceload":
            action = parts[1]
            x1, z1, x2, z2 = map(int, parts[2:6])
            chunks = {(cx, cz) for cx in range(x1 >> 4, (x2 >> 4) + 1) for cz in range(z1 >> 4, (z2 >> 4) + 1)}
            if len(chunks) > 256:
                return f"Too many chunks in the specified area (maximum 256, found {len(chunks)})"
            if action == "add":
                for c in chunks - self.forceloaded:
                    self.pending[c] = self.unload_ticks
                self.forceloaded |= chunks
                return f"Marked {len(chunks)} chunks to be force loaded"
            self.forceloaded -= chunks
            return f"Unmarked {len(chunks)} chunks"
        if name == "clone":
            c = list(map(int, parts[1:10]))
            (x1, y1, z1), (x2, y2, z2), (dx, dy, dz) = c[0:3], c[3:6], c[6:9]
            volume = (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1)
            if volume > 32768:
                return f"Too many blocks in the specified area (maximum 32768, specified {volume})"
            corners = [(x1, z1), (x2, z2), (dx, dz), (dx + x2 - x1, dz + z2 - z1)]
            if not all(self._loaded(x, z) for x, z in corners):
                return "That position is not loaded"
            copy = {}
            for x in range(x1, x2 + 1):
                for y in range(y1, y2 + 1):
                    for z in range(z1, z2 + 1):
                        copy[(dx + x - x1, dy + y - y1, dz + z - z1)] = self.block((x, y, z))
            self.world.update(copy)
            return f"Successfully cloned {volume} block(s)"
        return f"Unknown or incomplete command, see below for error...{cmd}<--[HERE]"

    def command(self, cmd: str) -> str:  # lets tests use FakeMinecraft directly as a connection
        return self.handle(cmd)


class FakeRconServer:
    """Serves FakeMinecraft over the real RCON wire protocol on localhost."""

    def __init__(self, game: FakeMinecraft, password: str = "secret"):
        self.game = game
        self.password = password
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        authed = False
        with conn:
            while True:
                header = conn.recv(4)
                if len(header) < 4:
                    return
                (length,) = struct.unpack("<i", header)
                body = b""
                while len(body) < length:
                    body += conn.recv(length - len(body))
                req_id, ptype, payload = decode_packet(body)
                if ptype == TYPE_AUTH:
                    authed = payload == self.password
                    conn.sendall(encode_packet(req_id if authed else -1, TYPE_COMMAND, ""))
                elif ptype == TYPE_COMMAND and authed:
                    conn.sendall(encode_packet(req_id, TYPE_RESPONSE, self.game.handle(payload)))

    def close(self) -> None:
        self.sock.close()
