import math

import pytest

from mcbuild.gallery import SAMPLES, gallery, sample
from mcbuild.history import MAX_FOOTPRINT
from mcbuild.materials import material, part
from mcbuild.model import Build, block_id, rotate_block
from mcbuild.placement import face_toward_player, offset_in_front
from mcbuild.primitives import (
    DIRECTIONS, battlements, cylinder, disc_points, door, gable_roof, hip_roof, line_xz,
    opening, rect_perimeter, ring_points, stairs_run, walls, window,
)


def states(block):
    if "[" not in block:
        return {}
    return dict(kv.split("=") for kv in block[:-1].split("[", 1)[1].split(","))


def stairs_of(b):
    return {p: states(blk)["facing"] for p, blk in b if block_id(blk).endswith("_stairs")}


# --- rotation -------------------------------------------------------------------------------

def test_rotate_block_states():
    assert rotate_block("minecraft:oak_stairs[facing=north,half=bottom]", 1) == \
        "minecraft:oak_stairs[facing=east,half=bottom]"
    assert rotate_block("minecraft:oak_door[facing=east,half=lower,hinge=left]", 2) == \
        "minecraft:oak_door[facing=west,half=lower,hinge=left]"
    assert rotate_block("minecraft:glass_pane[east=true,west=true]", 1) == "minecraft:glass_pane[north=true,south=true]"
    assert rotate_block("minecraft:oak_log[axis=x]", 1) == "minecraft:oak_log[axis=z]"
    assert rotate_block("minecraft:oak_log[axis=y]", 1) == "minecraft:oak_log[axis=y]"
    assert rotate_block("minecraft:oak_sign[rotation=14]", 1) == "minecraft:oak_sign[rotation=2]"
    assert rotate_block("minecraft:rail[shape=north_east]", 1) == "minecraft:rail[shape=south_east]"
    assert rotate_block("minecraft:rail[shape=ascending_north]", 3) == "minecraft:rail[shape=ascending_west]"
    assert rotate_block("minecraft:stone", 1) == "minecraft:stone"


def test_four_rotations_are_identity():
    b = sample("cottage")
    back = b.rotated(1).rotated(1).rotated(1).rotated(1)
    assert back.blocks.keys() == b.blocks.keys()
    for p, blk in b:
        assert states(back.blocks[p]) == states(blk)


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_roof_stairs_climb_toward_their_facing(turns):
    """Each roof stair's next step up the slope sits one block along its facing, one level up."""
    for name in ("cottage", "brick-house"):
        b = sample(name).rotated(turns)
        stairs = stairs_of(b)
        top = max(p[1] for p in stairs)
        for (x, y, z), facing in stairs.items():
            if y == top:
                continue
            dx, dz = DIRECTIONS[facing]
            # Straight up the slope, or diagonally at a hip roof's corners.
            steps = [(x + dx + side * -dz, y + 1, z + dz + side * dx) for side in (0, 1, -1)]
            assert any(block_id(b.blocks.get(p, "air")) != "air" for p in steps), (name, turns, (x, y, z), facing)


@pytest.mark.parametrize("player_facing", ["north", "south", "east", "west"])
def test_front_door_faces_player(player_facing):
    player = (0.5, 64.0, 0.5)
    design = face_toward_player(sample("cottage"), player_facing)
    placed = design.translated(*offset_in_front(design, player, player_facing, 3))
    doors = [p for p, blk in placed if block_id(blk).endswith("_door")]
    (x1, _, z1), (x2, _, z2) = placed.bounds()
    centre = ((x1 + x2) / 2, (z1 + z2) / 2)
    dist = lambda x, z: math.hypot(x - player[0], z - player[2])  # noqa: E731
    assert dist(doors[0][0], doors[0][2]) < dist(*centre) - 2


# --- shapes ---------------------------------------------------------------------------------

def test_walls_and_perimeter():
    b = Build()
    walls(b, (0, 0, 0), (4, 2, 3), "stone", corner="oak_log")
    assert len(b) == 3 * (2 * 5 + 2 * 2)
    assert block_id(b.blocks[(0, 1, 0)]) == "oak_log" and block_id(b.blocks[(2, 1, 0)]) == "stone"
    assert (2, 1, 1) not in b.blocks
    perim = rect_perimeter((0, 0), (4, 3))
    assert len(perim) == len(set(perim)) == 14
    assert all(abs(a[0] - c[0]) + abs(a[1] - c[1]) == 1 for a, c in zip(perim, perim[1:]))


def test_line_xz_is_connected():
    pts = line_xz((0, 0), (7, -3))
    assert pts[0] == (0, 0) and pts[-1] == (7, -3)
    assert all(max(abs(a[0] - c[0]), abs(a[1] - c[1])) == 1 for a, c in zip(pts, pts[1:]))


@pytest.mark.parametrize("radius", [1, 2, 3, 4.5, 8])
def test_disc_and_ring_are_symmetric(radius):
    disc = set(disc_points((0, 0), radius))
    for x, z in disc:
        assert {(-x, z), (x, -z), (z, x)} <= disc
    ring = ring_points((0, 0), radius)
    assert set(ring) <= disc and len(ring) == len(set(ring))


def test_hollow_cylinder_is_empty_inside():
    b = Build()
    cylinder(b, (0, 0, 0), 4, 3, "stone")
    assert (0, 1, 0) not in b.blocks and (4, 1, 0) in b.blocks and (0, 2, -4) in b.blocks


@pytest.mark.parametrize("ridge", ["x", "z"])
def test_gable_roof(ridge):
    b = Build()
    top = gable_roof(b, (0, 5, 0), (6, 5, 8), "spruce", ridge=ridge)
    across = 9 if ridge == "x" else 7  # footprint depth across the ridge
    assert top == 5 + (across + 2) // 2
    stairs = stairs_of(b)
    assert set(stairs.values()) == ({"north", "south"} if ridge == "x" else {"east", "west"})
    assert all(block_id(blk).startswith("spruce") for _, blk in b)


def test_hip_roof_shrinks_to_a_ridge():
    b = Build()
    top = hip_roof(b, (0, 0, 0), (8, 0, 4), "oak")
    assert top == 3  # 11x7 with overhang -> 9x5 -> 7x3 -> 5x1 ridge
    layer = lambda y: {(x, z) for (x, yy, z) in b.blocks if yy == y}  # noqa: E731
    assert len(layer(3)) == 5 and all(block_id(b.blocks[(x, 3, z)]) == "oak_planks" for x, z in layer(3))
    assert set(stairs_of(b).values()) == {"north", "south", "east", "west"}


def test_battlements_alternate():
    b = Build()
    battlements(b, line_xz((0, 0), (6, 0)), 10, "stone")
    assert all((x, 10, 0) in b.blocks for x in range(7))
    assert [(x, 11, 0) in b.blocks for x in range(7)] == [True, False] * 3 + [True]


def test_window_panes_connect_along_wall():
    b = Build()
    window(b, (0, 1, 0), "x", width=2)
    window(b, (5, 1, 0), "z", width=1, block="glass")
    assert states(b.blocks[(0, 1, 0)]) == {"east": "true", "west": "true"}
    assert b.blocks[(5, 2, 0)] == "minecraft:glass"


def test_arched_opening():
    b = Build()
    opening(b, (0, 0, 0), "x", width=3, height=3, arched=True, depth=2)
    heights = [max(y for (x, y, z) in b.blocks if x == i) + 1 for i in range(3)]
    assert heights == [4, 5, 4]
    assert (1, 4, 1) in b.blocks  # cut through the full depth


def test_door_and_stairs_run():
    b = Build()
    door(b, (0, 0, 0), "south", "spruce_door")
    assert states(b.blocks[(0, 0, 0)])["half"] == "lower" and states(b.blocks[(0, 1, 0)])["half"] == "upper"
    with pytest.raises(ValueError):
        door(b, (0, 0, 0), "up")
    s = Build()
    landing = stairs_run(s, (0, 0, 0), "east", steps=4, material="stone_brick", width=2)
    assert landing == (4, 4, 0)
    assert stairs_of(s)[(3, 3, 0)] == "east" and (3, 3, 1) in s.blocks  # right of east is south
    assert block_id(s.blocks[(3, 0, 0)]) == "stone_bricks"  # filled underneath


def test_materials():
    assert part("stone_brick", "stairs") == "stone_brick_stairs"
    assert material("cherry")["door"] == "cherry_door"
    with pytest.raises(ValueError, match="Known"):
        material("unobtainium")
    with pytest.raises(ValueError, match="no wall"):
        part("oak", "wall")


# --- gallery --------------------------------------------------------------------------------

def test_gallery_samples_fit_and_are_named():
    g = gallery()
    (x1, y1, z1), (x2, y2, z2) = g.bounds()
    assert x2 - x1 + 1 <= MAX_FOOTPRINT and z2 - z1 + 1 <= MAX_FOOTPRINT
    for name, width, _ in SAMPLES:
        s = sample(name.replace(" ", "-"))
        (sx1, _, _), (sx2, _, _) = s.bounds()
        assert sx2 - sx1 + 1 <= width + 2, name  # declared width, plus roof overhang
    with pytest.raises(ValueError):
        sample("space station")


# --- castle parts ---------------------------------------------------------------------------

from mcbuild.primitives import bridge, curtain_wall, gatehouse, moat, spiral_staircase  # noqa: E402


def test_curtain_wall_has_walkway_and_battlements_outside():
    b = Build()
    curtain_wall(b, (0, 0, 0), (10, 0), height=6, block="stone_bricks", thickness=3, outer="south")
    assert all((x, 6, z) in b.blocks for x in range(11) for z in (0, 2))  # parapets at y=6
    assert all((x, 6, 1) not in b.blocks for x in range(11))  # walkway open
    assert [(x, 7, 2) in b.blocks for x in range(11)] == [x % 2 == 0 for x in range(11)]  # merlons outside
    assert not any((x, 7, 0) in b.blocks for x in range(11))
    with pytest.raises(ValueError, match="outer"):
        curtain_wall(Build(), (0, 0, 0), (10, 0), 6, "stone_bricks", outer="east")


def test_gatehouse_passage_goes_through():
    b = Build()
    gatehouse(b, (0, 0, 0), width=13, depth=7, height=9, gate_width=3, gate_height=4)
    for z in range(7):
        for x in range(5, 8):
            for y in range(0, 3):
                assert block_id(b.blocks[(x, y, z)]) == "air", (x, y, z)
    assert any(block_id(blk) == "iron_bars" for blk in b.blocks.values())
    tower_top = max(y for (x, y, z) in b.blocks if x == 0)
    middle_top = max(y for (x, y, z) in b.blocks if x == 6)
    assert tower_top > middle_top
    assert sum(block_id(blk).endswith("_door") for blk in b.blocks.values()) == 4  # two doors, two halves


def test_spiral_staircase_is_walkable():
    b = Build()
    top = spiral_staircase(b, (0, 0, 0), height=8)
    assert top == 8
    steps = sorted(((y + (0.5 if "type=top" in blk else 0), (x, z)) for (x, y, z), blk in b
                    if block_id(blk).endswith("_slab")))
    assert len(steps) == 16
    for (h1, c1), (h2, c2) in zip(steps, steps[1:]):
        assert h2 - h1 == 0.5  # half a block up each step: no jumping
        assert max(abs(c1[0] - c2[0]), abs(c1[1] - c2[1])) == 1  # to a neighbouring cell
    # Headroom: the two blocks above each step's surface are free of steps.
    solid = {p for p, blk in b if block_id(blk) != "air"}
    for (x, y, z), blk in b:
        if block_id(blk).endswith("_slab"):
            assert (x, y + 1, z) not in solid and (x, y + 2, z) not in solid
    assert (0, 8, 0) in solid and (0, 9, 0) not in solid  # pillar ends at the top


def test_bridge_has_deck_rails_and_arch():
    b = Build()
    bridge(b, (0, 5, 0), (8, 0), width=3, material="stone_brick")
    assert all(block_id(b.blocks[(x, 4, z)]) == "stone_bricks" for x in range(9) for z in range(3))
    assert all(block_id(b.blocks[(x, 5, z)]) == "air" for x in range(9) for z in range(3))
    assert all(block_id(b.blocks[(x, 5, z)]) == "stone_brick_wall" for x in range(9) for z in (-1, 3))
    depth = lambda x: min(y for (px, y, pz) in b.blocks if px == x and pz == 1)  # noqa: E731
    assert depth(0) < depth(4) and depth(8) < depth(4)


def test_moat_rings_the_area():
    b = Build()
    moat(b, (0, 0), (9, 9), y=0, width=2, depth=2)
    cells = {(x, z) for (x, y, z) in b.blocks}
    assert len(cells) == 14 * 14 - 10 * 10
    assert all(block_id(blk) == "water" and y in (-1, -2) for (x, y, z), blk in b)
