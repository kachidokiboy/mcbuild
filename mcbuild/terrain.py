"""Survey the ground under a site and level it before building.

Heights come from Minecraft's own height maps (`execute positioned over`), read through one
invisible marker entity, so leaves are ignored and no mods are needed. Leveling is a handful
of /fill commands: clear everything above the chosen ground level, and fill dips and water
below it with grass, dirt and stone.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Callable, Dict, Tuple

from .server import CLONE_BLOCK_LIMIT, CommandError, MinecraftServer, split_box

PROBE = "@e[type=minecraft:marker,tag=mcbuild_probe,limit=1]"
MAX_SAMPLES = 300
MAX_FILL_DEPTH = 16


@dataclass
class Survey:
    heights: Dict[Tuple[int, int], int]  # (x, z) -> y of the first free block above the surface
    floors: Dict[Tuple[int, int], int]   # the same, but seeing through water (the bottom of ponds)

    @property
    def ground(self) -> int:
        """The typical ground level: the median, so a tree trunk or a pit doesn't skew it."""
        return int(round(statistics.median(self.heights.values())))

    @property
    def lowest(self) -> int:
        """The lowest ground, including the bottom of any water."""
        return min(list(self.heights.values()) + list(self.floors.values()))

    @property
    def highest(self) -> int:
        return max(self.heights.values())

    def describe(self) -> str:
        low, high, ground = self.lowest - self.ground, self.highest - self.ground, self.ground
        if low == high == 0:
            return f"flat ground at y={ground}"
        return f"ground from {low:+d} to {high:+d} around y={ground}"


def survey(server: MinecraftServer, x1: int, z1: int, x2: int, z2: int) -> Survey:
    """Sample surface heights on a grid across the area (at most MAX_SAMPLES columns)."""
    area = (x2 - x1 + 1) * (z2 - z1 + 1)
    step = max(1, math.ceil(math.sqrt(area / MAX_SAMPLES)))
    xs = sorted(set(range(x1, x2 + 1, step)) | {x2})
    zs = sorted(set(range(z1, z2 + 1, step)) | {z2})
    server.run("kill @e[type=minecraft:marker,tag=mcbuild_probe]")
    server.run(f"summon minecraft:marker {x1} 0 {z1} " + '{Tags:["mcbuild_probe"]}')
    heights: Dict[Tuple[int, int], int] = {}
    floors: Dict[Tuple[int, int], int] = {}
    try:
        for x in xs:
            for z in zs:
                for heightmap, found in (("motion_blocking_no_leaves", heights), ("ocean_floor", floors)):
                    server.run(f"execute positioned {x} 0 {z} positioned over {heightmap} run tp {PROBE} ~ ~ ~")
                    try:
                        found[(x, z)] = parse_height(server.run(f"data get entity {PROBE} Pos[1]"))
                    except ValueError:
                        pass
    finally:
        server.run("kill @e[type=minecraft:marker,tag=mcbuild_probe]")
    if not heights:
        raise CommandError("execute positioned over", "couldn't read any ground heights")
    return Survey(heights, floors)


def level(server: MinecraftServer, x1: int, z1: int, x2: int, z2: int, ground: int, top: int, depth: int,
          log: Callable[[str], None] = print) -> None:
    """Clear everything from `ground` up to `top`, and fill air, water and plants below `ground`
    (down to `depth` blocks) with grass on top, then dirt, then stone."""
    def fill(p1, p2, block: str, replace: str = "") -> None:
        for a, b in split_box(p1, p2, CLONE_BLOCK_LIMIT):
            command = "fill {} {} {} {} {} {} {}".format(*a, *b, block) + (f" replace {replace}" if replace else "")
            response = server.run(command)
            if "filled" not in response.lower() or "<--[here]" in response.lower():
                log(f"  ! ground preparation step failed: {response[:120]}")

    fill((x1, ground, z1), (x2, top, z2), "minecraft:air")
    for i in range(1, depth + 1):
        y = ground - i
        block = "minecraft:grass_block" if i == 1 else "minecraft:dirt" if i <= 4 else "minecraft:stone"
        fill((x1, y, z1), (x2, y, z2), block, replace="#minecraft:replaceable")


def parse_height(response: str) -> int:
    """'Marker has the following entity data: -60.0d' -> -60"""
    match = re.search(r":\s*(-?\d+(?:\.\d+)?)", response)
    if not match:
        raise ValueError(f"unexpected response {response!r}")
    return int(math.floor(float(match.group(1))))
