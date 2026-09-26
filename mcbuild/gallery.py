"""Sample builds made from the building library, shown side by side by `mcbuild gallery`.

Each sample faces north (front door on the north side) and sits on y=0, like any design.
They double as worked examples of the library for the AI designer.
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from .model import Build
from .primitives import (
    battlements, box, clear, door, gable_roof, hip_roof, line_xz, opening,
    round_tower, square_tower, stairs_run, torch, wall_line, walls, window,
)


def cottage(b: Build, x: int, z: int) -> None:
    """9x7 spruce cottage with log corners, windows and a dark oak gable roof."""
    x2, z2 = x + 8, z + 6
    box(b, (x, -1, z), (x2, -1, z2), "cobblestone")
    clear(b, (x + 1, 0, z + 1), (x2 - 1, 3, z2 - 1))
    walls(b, (x, 0, z), (x2, 3, z2), "spruce_planks", corner="spruce_log")
    door(b, (x + 4, 0, z), "south", "spruce_door")
    window(b, (x + 1, 1, z), "x", width=2)
    window(b, (x + 6, 1, z), "x", width=2)
    window(b, (x + 3, 1, z2), "x", width=3)
    window(b, (x, 1, z + 2), "z", width=3)
    window(b, (x2, 1, z + 2), "z", width=3)
    torch(b, (x + 1, 2, z + 3), wall="east")
    gable_roof(b, (x, 4, z), (x2, 4, z2), "dark_oak", ridge="x")


def brick_house(b: Build, x: int, z: int) -> None:
    """9x9 brick house with a deepslate hip roof."""
    x2, z2 = x + 8, z + 8
    box(b, (x, -1, z), (x2, -1, z2), "stone_bricks")
    clear(b, (x + 1, 0, z + 1), (x2 - 1, 3, z2 - 1))
    walls(b, (x, 0, z), (x2, 3, z2), "bricks", corner="stone_bricks")
    door(b, (x + 4, 0, z), "south", "dark_oak_door")
    for wx in (x + 1, x + 6):
        window(b, (wx, 1, z), "x", width=2)
        window(b, (wx, 1, z2), "x", width=2)
    window(b, (x, 1, z + 3), "z", width=3)
    window(b, (x2, 1, z + 3), "z", width=3)
    box(b, (x, 4, z), (x2, 4, z2), "spruce_planks")  # ceiling
    hip_roof(b, (x, 5, z), (x2, 5, z2), "deepslate_tile")


def keep_tower(b: Build, x: int, z: int) -> None:
    """7x7 stone brick tower with battlements."""
    square_tower(b, (x, 0, z), size=7, height=14, block="stone_bricks")


def wizard_tower(b: Build, x: int, z: int) -> None:
    """Round tower (radius 4) with a steep cone roof."""
    round_tower(b, (x + 4, 0, z + 4), radius=4, height=16, block="stone_bricks",
                roof="cone", roof_material="deepslate_tiles")


def gate_wall(b: Build, x: int, z: int) -> None:
    """A 13-block curtain wall, 2 thick, with an arched gate and battlements on the front."""
    box(b, (x, -1, z), (x + 12, -1, z + 1), "cobblestone")
    wall_line(b, (x, 0, z), (x + 12, z), height=7, block="stone_bricks", thickness=2)
    opening(b, (x + 5, 0, z), "x", width=3, height=3, arched=True, depth=2)
    battlements(b, line_xz((x, z), (x + 12, z)), 7, "stone_bricks")


def stair_platform(b: Build, x: int, z: int) -> None:
    """A 2-wide staircase climbing south onto a 4x4 platform with a parapet."""
    # Extra stair width goes to the climber's right, which is west when climbing south.
    _, top, lz = stairs_run(b, (x + 1, 0, z), "south", steps=5, material="stone_brick", width=2)
    box(b, (x, 0, lz), (x + 3, top - 1, lz + 3), "stone_bricks")
    battlements(b, line_xz((x, lz + 3), (x + 3, lz + 3)), top, "stone_bricks")


SAMPLES: List[Tuple[str, int, Callable[[Build, int, int], None]]] = [
    # (name, width in x, builder)
    ("cottage", 9, cottage),
    ("brick house", 9, brick_house),
    ("keep tower", 7, keep_tower),
    ("wizard tower", 9, wizard_tower),
    ("gate wall", 13, gate_wall),
    ("stair platform", 4, stair_platform),
]


def gallery(gap: int = 5) -> Build:
    """All samples in a row along +x, fronts facing north."""
    b = Build("gallery")
    x = 0
    for _, width, make in SAMPLES:
        make(b, x, 0)
        x += width + gap
    return b


def sample(name: str) -> Build:
    for sample_name, _, make in SAMPLES:
        if sample_name.replace(" ", "-") == name.replace(" ", "-"):
            b = Build(sample_name)
            make(b, 0, 0)
            return b
    raise ValueError(f"Unknown sample {name!r}. Choose from: {', '.join(s[0].replace(' ', '-') for s in SAMPLES)}")
