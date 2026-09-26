"""A hand-written test build: a small oak hut. Used to check the whole pipeline end to end."""

from __future__ import annotations

from .model import Build

# The door stands in this wall; its "facing" points into the hut.
_DOOR_WALL = {
    "north": ((3, 0), "south"),
    "south": ((3, 6), "north"),
    "west": ((0, 3), "east"),
    "east": ((6, 3), "west"),
}


def demo_hut(door_side: str = "north") -> Build:
    """7x7 hut with log corners, windows, a gable roof and a door on `door_side`.

    y=0 is the first layer above ground; the cobblestone floor replaces the ground at y=-1.
    """
    b = Build("demo hut")

    # Foundation and a cleared interior (removes grass, flowers, snow...).
    b.fill((0, -1, 0), (6, -1, 6), "cobblestone")
    b.fill((1, 0, 1), (5, 3, 5), "air")

    # Walls with log corner posts.
    for x in range(7):
        for z in range(7):
            if x in (0, 6) or z in (0, 6):
                corner = x in (0, 6) and z in (0, 6)
                for y in range(4):
                    b.set(x, y, z, "oak_log" if corner else "oak_planks")

    # Windows: glass panes centred in the walls without the door.
    for side, (pos, _) in _DOOR_WALL.items():
        if side == door_side:
            continue
        x, z = pos
        along_x = z in (0, 6)  # wall runs along x
        pane = "glass_pane[east=true,west=true]" if along_x else "glass_pane[north=true,south=true]"
        for d in (-1, 0, 1):
            for y in (1, 2):
                b.set(x + d if along_x else x, y, z if along_x else z + d, pane)

    # Gable roof: stair rows step up from both long sides to a ridge along x.
    for k in range(4):
        y = 4 + k
        for x in range(-1, 8):
            b.set(x, y, k - 1, "oak_stairs[facing=south,half=bottom]")
            b.set(x, y, 7 - k, "oak_stairs[facing=north,half=bottom]")
        # Gable ends and attic air between the slopes.
        for z in range(k, 7 - k):
            for x in range(7):
                b.set(x, y, z, "oak_planks" if x in (0, 6) else "air")
    for x in range(-1, 8):
        b.set(x, 7, 3, "oak_planks")
        b.set(x, 8, 3, "oak_slab[type=bottom]")

    # Door and lighting (placed last by the sequencer, once their support exists).
    (dx, dz), facing = _DOOR_WALL[door_side]
    b.set(dx, 0, dz, f"oak_door[facing={facing},half=lower,hinge=left,open=false]")
    b.set(dx, 1, dz, f"oak_door[facing={facing},half=upper,hinge=left,open=false]")
    b.set(3, 4, 3, "oak_planks")
    b.set(3, 3, 3, "lantern[hanging=true]")
    return b
