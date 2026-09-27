"""The building library: shapes and architectural parts that write blocks into a Build.

Conventions
- Coordinates: x = east, y = up, z = south. Corner arguments are inclusive and can be given
  in any order.
- Directions are "north" (-z), "south" (+z), "east" (+x) and "west" (-x).
- Designs face north by convention (front door on the north side); `Build.rotated` turns
  them to face any direction.
- `material` arguments are names from materials.MATERIALS ("oak", "stone_brick", ...);
  `block` arguments are block ids ("stone_bricks", "glass_pane", ...).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .materials import material as material_parts
from .materials import part
from .model import Build, Pos

XZ = Tuple[int, int]

DIRECTIONS = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _span(a: int, b: int) -> range:
    return range(min(a, b), max(a, b) + 1)


def _dir(direction: str) -> XZ:
    try:
        return DIRECTIONS[direction]
    except KeyError:
        raise ValueError(f"Unknown direction {direction!r}; use north/south/east/west") from None


# --- basic shapes --------------------------------------------------------------------------

def box(b: Build, p1: Pos, p2: Pos, block: str, hollow: bool = False) -> None:
    """Solid box between two corners, or just its outer shell with hollow=True."""
    b.fill(p1, p2, block, hollow=hollow)


def clear(b: Build, p1: Pos, p2: Pos) -> None:
    """Fill a box with air (removes terrain, grass, trees inside it)."""
    b.fill(p1, p2, "air")


def pillar(b: Build, base: Pos, height: int, block: str) -> None:
    """A column `height` blocks tall standing on `base` (its bottom block)."""
    x, y, z = base
    for dy in range(height):
        b.set(x, y + dy, z, block)


def walls(b: Build, p1: Pos, p2: Pos, block: str, corner: Optional[str] = None) -> None:
    """Four walls around a rectangle (the box's sides, no floor or ceiling).
    `corner` optionally uses a different block for the four corner columns (e.g. logs)."""
    xs, ys, zs = _span(p1[0], p2[0]), _span(p1[1], p2[1]), _span(p1[2], p2[2])
    for x, z in rect_perimeter((xs[0], zs[0]), (xs[-1], zs[-1])):
        is_corner = x in (xs[0], xs[-1]) and z in (zs[0], zs[-1])
        for y in ys:
            b.set(x, y, z, corner if (corner and is_corner) else block)


def line_xz(a: XZ, c: XZ) -> List[XZ]:
    """Grid points on the straight line from a to c (Bresenham), inclusive."""
    (x0, z0), (x1, z1) = a, c
    dx, dz = abs(x1 - x0), -abs(z1 - z0)
    sx, sz = (1 if x0 < x1 else -1), (1 if z0 < z1 else -1)
    err, points = dx + dz, []
    while True:
        points.append((x0, z0))
        if (x0, z0) == (x1, z1):
            return points
        e2 = 2 * err
        if e2 >= dz:
            err += dz
            x0 += sx
        if e2 <= dx:
            err += dx
            z0 += sz


def wall_line(b: Build, start: Pos, end: XZ, height: int, block: str, thickness: int = 1) -> List[XZ]:
    """A wall from `start` (x, y, z) to `end` (x, z), `height` blocks tall. Extra thickness
    grows toward +z for east-west walls and toward +x otherwise. Returns the centre-line points."""
    x0, y0, z0 = start
    points = line_xz((x0, z0), end)
    east_west = abs(end[0] - x0) >= abs(end[1] - z0)
    for x, z in points:
        for t in range(thickness):
            for dy in range(height):
                if east_west:
                    b.set(x, y0 + dy, z + t, block)
                else:
                    b.set(x + t, y0 + dy, z, block)
    return points


def rect_perimeter(c1: XZ, c2: XZ) -> List[XZ]:
    """Points around a rectangle's edge, in order (clockwise from the north-west corner)."""
    (x1, x2), (z1, z2) = sorted((c1[0], c2[0])), sorted((c1[1], c2[1]))
    if x1 == x2 or z1 == z2:
        return [(x, z) for x in range(x1, x2 + 1) for z in range(z1, z2 + 1)]
    pts = [(x, z1) for x in range(x1, x2 + 1)]
    pts += [(x2, z) for z in range(z1 + 1, z2 + 1)]
    pts += [(x, z2) for x in range(x2 - 1, x1 - 1, -1)]
    pts += [(x1, z) for z in range(z2 - 1, z1, -1)]
    return pts


def disc_points(center: XZ, radius: float) -> List[XZ]:
    """All grid points inside a circle, which looks rounder than a plain distance test."""
    cx, cz = center
    r = int(math.ceil(radius))
    limit = (radius + 0.5) ** 2 - 0.5
    return [(cx + dx, cz + dz) for dx in range(-r, r + 1) for dz in range(-r, r + 1)
            if dx * dx + dz * dz <= limit]


def ring_points(center: XZ, radius: float) -> List[XZ]:
    """The edge of a disc, one block thick, ordered by angle."""
    disc = set(disc_points(center, radius))
    ring = [(x, z) for x, z in disc
            if any((x + dx, z + dz) not in disc for dx, dz in DIRECTIONS.values())]
    cx, cz = center
    return sorted(ring, key=lambda p: math.atan2(p[1] - cz, p[0] - cx))


def cylinder(b: Build, base_center: Pos, radius: float, height: int, block: str, hollow: bool = True) -> None:
    """Upright cylinder; hollow=True makes only the wall (1 block thick)."""
    x, y, z = base_center
    points = ring_points((x, z), radius) if hollow else disc_points((x, z), radius)
    for px, pz in points:
        for dy in range(height):
            b.set(px, y + dy, pz, block)


# --- roofs ---------------------------------------------------------------------------------

def gable_roof(b: Build, p1: Pos, p2: Pos, material: str, ridge: str = "x", overhang: int = 1,
               gable_block: Optional[str] = None) -> int:
    """Two sloping stair roofs meeting at a ridge, over the rectangle p1..p2 (x/z corners);
    the roof starts at p1's y. ridge="x" runs the ridge east-west, "z" north-south.
    The triangular gable ends are filled with `gable_block` (default: the material's full block).
    Returns the y of the ridge."""
    stairs, full, slab = part(material, "stairs"), part(material, "full"), part(material, "slab")
    gable_block = gable_block or full
    y0 = p1[1]
    (x1, x2), (z1, z2) = sorted((p1[0], p2[0])), sorted((p1[2], p2[2]))
    if ridge not in ("x", "z"):
        raise ValueError('ridge must be "x" or "z"')
    # Work in (u along the ridge, v across it) and map back to x/z.
    if ridge == "x":
        u1, u2, v1, v2 = x1, x2, z1, z2
        up_low, up_high = "south", "north"  # stairs climb toward the ridge
        at = lambda u, v: (u, v)  # noqa: E731
    else:
        u1, u2, v1, v2 = z1, z2, x1, x2
        up_low, up_high = "east", "west"
        at = lambda u, v: (v, u)  # noqa: E731

    k = 0
    while True:
        y = y0 + k
        lo, hi = v1 - overhang + k, v2 + overhang - k
        if lo > hi:
            return y - 1
        for u in range(u1 - overhang, u2 + overhang + 1):
            if lo == hi:
                x, z = at(u, lo)
                b.set(x, y, z, full)
                b.set(x, y + 1, z, f"{slab}[type=bottom]")
            else:
                x, z = at(u, lo)
                b.set(x, y, z, f"{stairs}[facing={up_low},half=bottom]")
                x, z = at(u, hi)
                b.set(x, y, z, f"{stairs}[facing={up_high},half=bottom]")
        # Gable end triangles, in the plane of the end walls.
        for v in range(max(lo + 1, v1), min(hi - 1, v2) + 1):
            for u in (u1, u2):
                x, z = at(u, v)
                b.set(x, y, z, gable_block)
        if lo == hi:
            return y
        k += 1


def hip_roof(b: Build, p1: Pos, p2: Pos, material: str, overhang: int = 1) -> int:
    """A roof sloping up from all four sides, over the rectangle p1..p2, starting at p1's y.
    Corner stairs are shaped automatically by the game. Returns the top y."""
    stairs, full = part(material, "stairs"), part(material, "full")
    y0 = p1[1]
    (x1, x2), (z1, z2) = sorted((p1[0], p2[0])), sorted((p1[2], p2[2]))
    xa, xb, za, zb = x1 - overhang, x2 + overhang, z1 - overhang, z2 + overhang
    y = y0
    while xa <= xb and za <= zb:
        if xa == xb or za == zb:
            box(b, (xa, y, za), (xb, y, zb), full)  # ridge line
            return y
        for x, z in rect_perimeter((xa, za), (xb, zb)):
            if z == za:
                facing = "south"
            elif z == zb:
                facing = "north"
            elif x == xa:
                facing = "east"
            else:
                facing = "west"
            b.set(x, y, z, f"{stairs}[facing={facing},half=bottom]")
        xa, xb, za, zb, y = xa + 1, xb - 1, za + 1, zb - 1, y + 1
    return y - 1


def cone_roof(b: Build, base_center: Pos, radius: float, block: str, steep: int = 2,
              tip: Optional[str] = None) -> int:
    """Pointed roof for round towers: shrinking rings, each `steep` blocks tall.
    Returns the top y."""
    x, y, z = base_center
    r = radius
    while r >= 1:
        for _ in range(steep):
            for px, pz in ring_points((x, z), r):
                b.set(px, y, pz, block)
            y += 1
        r -= 1
    b.set(x, y, z, block)
    if tip:
        b.set(x, y + 1, z, tip)
        return y + 1
    return y


def battlements(b: Build, points: Sequence[XZ], y: int, block: str) -> None:
    """Castle crenellations: a solid parapet layer at y and merlons on every other point
    at y + 1. `points` is an ordered edge, e.g. from rect_perimeter or ring_points."""
    for i, (x, z) in enumerate(points):
        b.set(x, y, z, block)
        if i % 2 == 0:
            b.set(x, y + 1, z, block)


# --- openings and details ------------------------------------------------------------------

def window(b: Build, pos: Pos, along: str, width: int = 1, height: int = 2, block: str = "glass_pane") -> None:
    """A window in a wall. `pos` is its bottom corner with the smallest x/z; `along` is the
    axis the wall runs on ("x" or "z"). Panes and bars are connected into the wall."""
    x, y, z = pos
    connected = block.endswith(("glass_pane", "iron_bars"))
    if connected:
        block += "[east=true,west=true]" if along == "x" else "[north=true,south=true]"
    for i in range(width):
        for dy in range(height):
            if along == "x":
                b.set(x + i, y + dy, z, block)
            else:
                b.set(x, y + dy, z + i, block)


def opening(b: Build, pos: Pos, along: str, width: int, height: int, arched: bool = False,
            depth: int = 1) -> None:
    """Cut a doorway/gate (air) into a wall. pos = bottom corner with the smallest x/y/z;
    `depth` is the wall thickness to cut through (toward +z or +x).
    arched=True adds a rounded top above `height`."""
    x, y, z = pos
    r = width / 2
    for i in range(width):
        extra = 0
        if arched:
            offset = i + 0.5 - r
            extra = int(math.sqrt(max(r * r - offset * offset, 0)) + 0.5)
        for d in range(depth):
            for dy in range(height + extra):
                if along == "x":
                    b.set(x + i, y + dy, z + d, "air")
                else:
                    b.set(x + d, y + dy, z + i, "air")


def door(b: Build, pos: Pos, facing: str, block: str = "oak_door", hinge: str = "left") -> None:
    """Two-block door with its bottom at pos. `facing` is the direction you walk through it
    going in (a door in a north wall, entered from outside, faces south)."""
    _dir(facing)
    x, y, z = pos
    states = f"facing={facing},hinge={hinge},open=false"
    b.set(x, y, z, f"{block}[{states},half=lower]")
    b.set(x, y + 1, z, f"{block}[{states},half=upper]")


def stairs_run(b: Build, start: Pos, direction: str, steps: int, material: str, width: int = 1,
               fill_below: bool = True) -> Pos:
    """A straight staircase climbing one block per step toward `direction`.
    Extra width extends to the right-hand side (as seen climbing). Returns the landing position
    (the block just past the top step, one level up)."""
    dx, dz = _dir(direction)
    rx, rz = -dz, dx  # right-hand side
    stairs, full = part(material, "stairs"), part(material, "full")
    x, y, z = start
    for i in range(steps):
        for w in range(width):
            sx, sz = x + dx * i + rx * w, z + dz * i + rz * w
            b.set(sx, y + i, sz, f"{stairs}[facing={direction},half=bottom]")
            if fill_below:
                for fy in range(y, y + i):
                    b.set(sx, fy, sz, full)
    return x + dx * steps, y + steps, z + dz * steps


def lantern(b: Build, pos: Pos, hanging: bool = False) -> None:
    """A lantern standing on the block below, or hanging from the block above."""
    b.set(*pos, f"lantern[hanging={'true' if hanging else 'false'}]")


def torch(b: Build, pos: Pos, wall: Optional[str] = None) -> None:
    """A torch standing on the block below, or on a wall: `wall` is the direction the torch
    points away from the wall (a torch on a north wall points south)."""
    if wall:
        _dir(wall)
        b.set(*pos, f"wall_torch[facing={wall}]")
    else:
        b.set(*pos, "torch")


# --- components ----------------------------------------------------------------------------

def square_tower(b: Build, corner: Pos, size: int, height: int, block: str,
                 roof: str = "battlements", roof_material: Optional[str] = None,
                 door_block: Optional[str] = "oak_door", window_every: int = 5) -> None:
    """A square tower with its north-west base corner at `corner`, `size` wide, walls
    `height` tall, floor at the base, windows on each side and a door in the north wall.
    roof: "battlements", "hip", "gable" or "flat"."""
    x, y, z = corner
    x2, z2, top = x + size - 1, z + size - 1, y + height - 1
    clear(b, (x + 1, y, z + 1), (x2 - 1, top, z2 - 1))
    box(b, (x, y - 1, z), (x2, y - 1, z2), block)  # floor
    walls(b, (x, y, z), (x2, top, z2), block)
    mid = x + size // 2
    for wy in range(y + 3, top - 1, window_every):
        window(b, (mid, wy, z), "x", 1, 2)
        window(b, (mid, wy, z2), "x", 1, 2)
        window(b, (x, wy, z + size // 2), "z", 1, 2)
        window(b, (x2, wy, z + size // 2), "z", 1, 2)
    if door_block:
        door(b, (mid, y, z), "south", door_block)
    _tower_top(b, rect_perimeter((x, z), (x2, z2)), (x, top + 1, z), (x2, top + 1, z2), block, roof, roof_material)


def round_tower(b: Build, base_center: Pos, radius: float, height: int, block: str,
                roof: str = "cone", roof_material: Optional[str] = None,
                door_block: Optional[str] = "oak_door", window_every: int = 5) -> None:
    """A round tower centred on `base_center`, walls `height` tall, with a floor, arrow-slit
    windows facing the four directions and a door on the north side.
    roof: "cone", "battlements" or "flat". roof_material is a block id for cones."""
    x, y, z = base_center
    top = y + height - 1
    for px, pz in disc_points((x, z), radius - 1):
        for dy in range(height):
            b.set(px, y + dy, pz, "air")
    for px, pz in disc_points((x, z), radius):
        b.set(px, y - 1, pz, block)  # floor
    cylinder(b, (x, y, z), radius, height, block)
    ring = set(ring_points((x, z), radius))
    for d, (dx, dz) in DIRECTIONS.items():
        # The wall block straight out from the centre in this direction.
        edge = next((x + dx * n, z + dz * n) for n in range(int(radius) + 1, 0, -1)
                    if (x + dx * n, z + dz * n) in ring)
        for wy in range(y + 3, top - 1, window_every):
            window(b, (edge[0], wy, edge[1]), "x" if dz else "z", 1, 2)
        if d == "north" and door_block:
            door(b, (edge[0], y, edge[1]), "south", door_block)
    if roof == "cone":
        cone_roof(b, (x, top + 1, z), radius + 1, roof_material or "dark_oak_planks")
    elif roof in ("battlements", "flat"):
        for px, pz in disc_points((x, z), radius):
            b.set(px, top + 1, pz, block)
        if roof == "battlements":
            battlements(b, ring_points((x, z), radius), top + 2, block)
    else:
        raise ValueError('round_tower roof must be "cone", "battlements" or "flat"')


# --- castle parts --------------------------------------------------------------------------

def curtain_wall(b: Build, start: Pos, end: XZ, height: int, block: str, thickness: int = 3,
                 outer: str = "north") -> List[XZ]:
    """A thick castle wall from `start` (x, y, z) to `end` (x, z) with a walkway on top,
    battlements along the `outer` side and a low parapet along the inner side. Extra thickness
    grows toward +z for east-west walls and toward +x for north-south walls, so `outer` is
    "north"/"south" for east-west walls and "west"/"east" for north-south ones.
    Returns the centre-line points."""
    x0, y0, z0 = start
    east_west = abs(end[0] - x0) >= abs(end[1] - z0)
    sides = ("north", "south") if east_west else ("west", "east")
    if outer not in sides:
        raise ValueError(f"outer must be {' or '.join(sides)} for this wall")
    points = wall_line(b, start, end, height, block, thickness)
    out_offset = 0 if outer == sides[0] else thickness - 1
    in_offset = thickness - 1 - out_offset

    def row(offset: int) -> List[XZ]:
        return [(x, z + offset) if east_west else (x + offset, z) for x, z in points]

    top = y0 + height
    battlements(b, row(out_offset), top, block)
    if thickness > 1:
        for x, z in row(in_offset):
            b.set(x, top, z, block)
    return points


def gatehouse(b: Build, corner: Pos, width: int = 13, depth: int = 7, height: int = 9, block: str = "stone_bricks",
              gate_width: int = 3, gate_height: int = 4, tower_extra: int = 4) -> None:
    """A castle gatehouse: two square towers flanking an arched gate passage that runs north-south
    through the middle, with a half-raised portcullis (iron bars) at the front and battlements on
    top. `corner` is the north-west base corner; the front faces north."""
    x, y, z = corner
    tw = max(3, (width - gate_width - 2) // 2)  # tower width
    if width < 2 * tw + gate_width + 2:
        width = 2 * tw + gate_width + 2
    x2, z2 = x + width - 1, z + depth - 1
    top = y + height - 1
    # Towers on both sides, taller than the middle, entered from the passage.
    square_tower(b, (x, y, z), tw, height + tower_extra, block, door_block=None)
    square_tower(b, (x2 - tw + 1, y, z), tw, height + tower_extra, block, door_block=None)
    door(b, (x + tw - 1, y, z + tw // 2), "west", "spruce_door")
    door(b, (x2 - tw + 1, y, z + tw // 2), "east", "spruce_door")
    # The middle block with the passage through it.
    mx1, mx2 = x + tw, x2 - tw
    box(b, (mx1, y - 1, z), (mx2, y - 1, z2), block)
    box(b, (mx1, y, z), (mx2, top, z2), block)
    gx = x + (width - gate_width) // 2
    opening(b, (gx, y, z), "x", gate_width, gate_height, arched=True, depth=depth)
    box(b, (gx, y - 1, z), (gx + gate_width - 1, y - 1, z2), "cobblestone")
    # Portcullis, half raised: bars in the top of the arch, one block inside the front.
    arch_top = y + gate_height + (gate_width + 1) // 2
    for bx in range(gx, gx + gate_width):
        for by in range(y + gate_height - 1, arch_top + 1):
            if b.blocks.get((bx, by, z + 1)) == "minecraft:air":
                b.set(bx, by, z + 1, "iron_bars[east=true,west=true]")
    battlements(b, rect_perimeter((mx1, z), (mx2, z2)), top + 1, block)
    torch(b, (gx - 1, y + 2, z - 1), wall="north")
    torch(b, (gx + gate_width, y + 2, z - 1), wall="north")


def spiral_staircase(b: Build, base_center: Pos, height: int, material: str = "stone_brick",
                     pillar_block: Optional[str] = None, clockwise: bool = True) -> int:
    """Slab steps winding around a central pillar through the 8 cells around it (a 3x3 footprint),
    rising half a block per step so it can be walked without jumping, with headroom cleared above
    every step. It climbs `height` blocks; put it in a tower with an inner radius of at least 2.
    Returns the y you arrive at on top."""
    x, y, z = base_center
    slab, full = part(material, "slab"), part(material, "full")
    ring = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]  # clockwise from north
    if not clockwise:
        ring = [ring[0]] + ring[:0:-1]
    steps = []
    for i in range(2 * height):
        dx, dz = ring[i % 8]
        steps.append(((x + dx, y + i // 2, z + dz), "bottom" if i % 2 == 0 else "top"))
    for (sx, sy, sz), _ in steps:
        for h in range(1, 4):
            b.set(sx, sy + h, sz, "air")
    for (sx, sy, sz), half in steps:
        b.set(sx, sy, sz, f"{slab}[type={half}]")
    pillar(b, (x, y, z), height + 1, pillar_block or full)
    return y + height


def bridge(b: Build, start: Pos, end: XZ, width: int = 3, material: str = "stone_brick", arch: bool = True) -> None:
    """A bridge you walk on at `start`'s y, from `start` (x, y, z) to `end` (x, z). The deck is one
    block below, with railings on both sides and, with arch=True, an arch underneath that
    reaches down at both ends. Extra width grows toward +z (east-west) or +x (north-south)."""
    x0, y0, z0 = start
    full = part(material, "full")
    m = material_parts(material)
    rail = m.get("wall") or m.get("fence") or full
    points = line_xz((x0, z0), end)
    east_west = abs(end[0] - x0) >= abs(end[1] - z0)
    n = len(points)
    r = (n - 1) / 2
    for t, (px, pz) in enumerate(points):
        depth = 1 + (int(round(r - math.sqrt(max(r * r - (t - r) ** 2, 0)))) if arch else 0)
        for w in range(width):
            cx, cz = (px, pz + w) if east_west else (px + w, pz)
            for d in range(1, depth + 1):
                b.set(cx, y0 - d, cz, full)
            b.set(cx, y0, cz, "air")
            b.set(cx, y0 + 1, cz, "air")
        for w in (-1, width):
            cx, cz = (px, pz + w) if east_west else (px + w, pz)
            b.set(cx, y0 - 1, cz, full)
            b.set(cx, y0, cz, rail)


def moat(b: Build, c1: XZ, c2: XZ, y: int = 0, width: int = 3, depth: int = 3) -> None:
    """A water-filled ring `width` wide around the rectangle c1..c2 (x, z corners), `depth` deep,
    with its surface one block below `y` (ground level)."""
    (x1, x2), (z1, z2) = sorted((c1[0], c2[0])), sorted((c1[1], c2[1]))
    for x in range(x1 - width, x2 + width + 1):
        for z in range(z1 - width, z2 + width + 1):
            if x1 <= x <= x2 and z1 <= z <= z2:
                continue
            for d in range(1, depth + 1):
                b.set(x, y - d, z, "water")


def _tower_top(b: Build, perimeter: List[XZ], p1: Pos, p2: Pos, block: str, roof: str,
               roof_material: Optional[str]) -> None:
    if roof in ("battlements", "flat"):
        box(b, p1, (p2[0], p1[1], p2[2]), block)
        if roof == "battlements":
            battlements(b, perimeter, p1[1] + 1, block)
    elif roof == "hip":
        hip_roof(b, p1, p2, roof_material or "dark_oak")
    elif roof == "gable":
        gable_roof(b, p1, p2, roof_material or "dark_oak")
    else:
        raise ValueError('roof must be "battlements", "hip", "gable" or "flat"')

