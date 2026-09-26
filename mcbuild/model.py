"""In-memory voxel model of a build: a mapping from (x, y, z) to a block string."""

from __future__ import annotations

import re
from typing import Dict, Iterator, Tuple

Pos = Tuple[int, int, int]

_BLOCK_RE = re.compile(
    r"^[a-z0-9_.-]+:[a-z0-9_./-]+"  # namespaced id
    r"(\[[a-z0-9_]+=[a-z0-9_]+(,[a-z0-9_]+=[a-z0-9_]+)*\])?$"  # optional block states
)


def normalize_block(block: str) -> str:
    """Return a canonical block string, adding the minecraft: namespace if missing.

    >>> normalize_block("oak_stairs[facing=east]")
    'minecraft:oak_stairs[facing=east]'
    """
    block = block.strip().lower().replace(" ", "")
    name = block.split("[", 1)[0]
    if ":" not in name:
        block = "minecraft:" + block
    if not _BLOCK_RE.match(block):
        raise ValueError(f"Invalid block string: {block!r}")
    return block


_CW = {"north": "east", "east": "south", "south": "west", "west": "north"}
_RAIL_SHAPES = {"north_south", "east_west", "south_east", "south_west", "north_west", "north_east"}


def _turn(direction: str, turns: int) -> str:
    for _ in range(turns % 4):
        direction = _CW[direction]
    return direction


def rotate_block(block: str, turns: int) -> str:
    """Rotate a block's orientation states clockwise (seen from above) by 90 degrees x turns.

    Handles facing, axis, side connections (panes, fences, walls), sign rotation and rail shapes.
    >>> rotate_block("minecraft:oak_stairs[facing=north,half=bottom]", 1)
    'minecraft:oak_stairs[facing=east,half=bottom]'
    """
    turns %= 4
    if not turns or "[" not in block:
        return block
    name, states = block[:-1].split("[", 1)
    rotated = []
    for pair in states.split(","):
        key, value = pair.split("=", 1)
        if key in ("facing", "horizontal_facing") and value in _CW:
            value = _turn(value, turns)
        elif key in _CW:
            key = _turn(key, turns)
        elif key == "axis" and turns % 2 and value in ("x", "z"):
            value = "z" if value == "x" else "x"
        elif key == "rotation" and value.isdigit():
            value = str((int(value) + 4 * turns) % 16)
        elif key == "shape" and value.startswith("ascending_"):
            value = "ascending_" + _turn(value[len("ascending_"):], turns)
        elif key == "shape" and value in _RAIL_SHAPES:
            a, b = (_turn(d, turns) for d in value.split("_"))
            value = f"{a}_{b}" if f"{a}_{b}" in _RAIL_SHAPES else f"{b}_{a}"
        rotated.append(f"{key}={value}")
    # Keep a stable order so identical blocks compare equal.
    return f"{name}[{','.join(sorted(rotated))}]"


def block_id(block: str) -> str:
    """The block id without namespace or states: 'minecraft:oak_door[half=lower]' -> 'oak_door'."""
    return block.split("[", 1)[0].split(":", 1)[-1]


class Build:
    """A set of blocks to place. Later writes to the same position win."""

    def __init__(self, name: str = "build"):
        self.name = name
        self.blocks: Dict[Pos, str] = {}

    def __len__(self) -> int:
        return len(self.blocks)

    def __iter__(self) -> Iterator[Tuple[Pos, str]]:
        return iter(self.blocks.items())

    def set(self, x: int, y: int, z: int, block: str) -> None:
        self.blocks[(int(x), int(y), int(z))] = normalize_block(block)

    def fill(self, p1: Pos, p2: Pos, block: str, hollow: bool = False) -> None:
        """Fill the box between two corners (inclusive). hollow=True sets only the outer shell."""
        block = normalize_block(block)
        (x1, x2), (y1, y2), (z1, z2) = (sorted((p1[i], p2[i])) for i in range(3))
        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                for z in range(z1, z2 + 1):
                    if hollow and x1 < x < x2 and y1 < y < y2 and z1 < z < z2:
                        continue
                    self.blocks[(x, y, z)] = block

    def bounds(self) -> Tuple[Pos, Pos]:
        if not self.blocks:
            raise ValueError("Build is empty")
        xs, ys, zs = zip(*self.blocks)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def translated(self, dx: int, dy: int, dz: int) -> "Build":
        moved = Build(self.name)
        moved.blocks = {(x + dx, y + dy, z + dz): b for (x, y, z), b in self.blocks.items()}
        return moved

    def rotated(self, turns: int) -> "Build":
        """Rotate clockwise (seen from above) by 90 degrees x turns around the origin,
        turning block orientations (stairs, doors, panes...) along with the positions."""
        turned = Build(self.name)
        turns %= 4
        for (x, y, z), block in self.blocks.items():
            for _ in range(turns):
                x, z = -z, x
            turned.blocks[(x, y, z)] = rotate_block(block, turns)
        return turned

    def merge(self, other: "Build") -> None:
        """Copy another build's blocks into this one (the other build wins on overlap)."""
        self.blocks.update(other.blocks)
