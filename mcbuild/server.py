"""High-level Minecraft commands on top of an RCON connection."""

from __future__ import annotations

import json
import re
import time
from typing import Iterator, List, Optional, Protocol, Tuple

from .model import Pos

# Vanilla limit on blocks per /clone or /fill (gamerule commandModificationBlockLimit).
CLONE_BLOCK_LIMIT = 32768
# Vanilla limit on chunks per /forceload command.
FORCELOAD_CHUNK_LIMIT = 256
MIN_Y, MAX_Y = -64, 319


class CommandConnection(Protocol):
    def command(self, command: str) -> str: ...


class CommandError(Exception):
    def __init__(self, command: str, response: str):
        super().__init__(f"`{command}` failed: {response or '(no response)'}")
        self.command = command
        self.response = response


_FLOAT = r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?"


def _looks_like_error(response: str) -> bool:
    lowered = response.lower()
    return any(s in lowered for s in (
        "<--[here]", "unknown", "incorrect", "invalid", "expected", "too many",
        "not loaded", "no entity was found", "no player was found", "out of the world",
    ))


def parse_entity_list(response: str) -> List[float]:
    """Parse '<name> has the following entity data: [1.5d, 64.0d, -3.2d]' into floats."""
    match = re.search(r"\[([^\]]*)\]", response)
    if not match:
        raise ValueError(f"Unexpected entity data response: {response!r}")
    return [float(v) for v in re.findall(_FLOAT, match.group(1))]


def parse_player_list(response: str) -> List[str]:
    """Parse 'There are 1 of a max of 20 players online: Steve, Alex'."""
    if ":" not in response:
        return []
    names = response.split(":", 1)[1]
    return [n.strip() for n in names.split(",") if n.strip()]


def split_box(pmin: Pos, pmax: Pos, limit: int = CLONE_BLOCK_LIMIT) -> Iterator[Tuple[Pos, Pos]]:
    """Split a box into sub-boxes of at most `limit` blocks (by y slabs, then x strips)."""
    (x1, y1, z1), (x2, y2, z2) = pmin, pmax
    dx, dz = x2 - x1 + 1, z2 - z1 + 1
    if dz > limit:
        raise ValueError("Box is too large to split")
    layer = dx * dz
    if layer <= limit:
        step = limit // layer
        for y in range(y1, y2 + 1, step):
            yield (x1, y, z1), (x2, min(y + step - 1, y2), z2)
    else:
        step = limit // dz
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1, step):
                yield (x, y, z1), (min(x + step - 1, x2), y, z2)


class MinecraftServer:
    def __init__(self, conn: CommandConnection):
        self.conn = conn

    def run(self, command: str) -> str:
        return self.conn.command(command)

    # --- players -----------------------------------------------------------------------

    def players(self) -> List[str]:
        return parse_player_list(self.run("list"))

    def resolve_player(self, name: Optional[str]) -> str:
        if name:
            return name
        online = self.players()
        if len(online) == 1:
            return online[0]
        if not online:
            raise RuntimeError("No players are online. Join the server first, or use --at X Y Z.")
        raise RuntimeError(f"Several players online ({', '.join(online)}); pick one with --player.")

    def player_position(self, name: str) -> Tuple[float, float, float]:
        response = self.run(f"data get entity {name} Pos")
        if _looks_like_error(response):
            raise CommandError(f"data get entity {name} Pos", response)
        x, y, z = parse_entity_list(response)[:3]
        return x, y, z

    def player_yaw(self, name: str) -> float:
        response = self.run(f"data get entity {name} Rotation")
        if _looks_like_error(response):
            raise CommandError(f"data get entity {name} Rotation", response)
        return parse_entity_list(response)[0]

    def tell(self, message: str, target: str = "@a") -> None:
        component = {"text": "[mcbuild] ", "color": "gold", "extra": [{"text": message, "color": "white"}]}
        self.run(f"tellraw {target} " + json.dumps(component))

    # --- blocks --------------------------------------------------------------------------

    def setblock(self, pos: Pos, block: str) -> None:
        command = "setblock {} {} {} {}".format(*pos, block)
        response = self.run(command)
        # "Could not set the block" just means the block was already identical.
        if response.startswith("Changed the block") or response.startswith("Could not set the block"):
            return
        raise CommandError(command, response)

    def forceload(self, pmin: Pos, pmax: Pos, add: bool = True) -> None:
        """Force-load (or release) all chunks covering a box, in batches under the per-command cap."""
        cx1, cx2 = pmin[0] >> 4, pmax[0] >> 4
        cz1, cz2 = pmin[2] >> 4, pmax[2] >> 4
        rows = max(1, FORCELOAD_CHUNK_LIMIT // (cx2 - cx1 + 1))
        if cx2 - cx1 + 1 > FORCELOAD_CHUNK_LIMIT:
            raise ValueError("Area is too wide to force-load")
        action = "add" if add else "remove"
        for cz in range(cz1, cz2 + 1, rows):
            cz_end = min(cz + rows - 1, cz2)
            command = f"forceload {action} {cx1 * 16} {cz * 16} {cx2 * 16} {cz_end * 16}"
            response = self.run(command)
            if "too many" in response.lower() or "<--[here]" in response.lower():
                raise CommandError(command, response)

    def clone(self, src_min: Pos, src_max: Pos, dst_min: Pos, timeout: float = 60.0) -> None:
        """Copy a box to another location, splitting it to respect the block limit.

        Chunks must be loaded; freshly force-loaded chunks can take a moment, so
        "not loaded" responses are retried until `timeout`.
        """
        offset = tuple(dst_min[i] - src_min[i] for i in range(3))
        for (a, b) in split_box(src_min, src_max):
            dst = tuple(a[i] + offset[i] for i in range(3))
            command = "clone {} {} {} {} {} {} {} {} {}".format(*a, *b, *dst)
            deadline = time.monotonic() + timeout
            while True:
                response = self.run(command)
                lowered = response.lower()
                if "cloned" in lowered and not _looks_like_error(response):
                    break
                if "not loaded" in lowered and time.monotonic() < deadline:
                    time.sleep(0.25)
                    continue
                raise CommandError(command, response)
