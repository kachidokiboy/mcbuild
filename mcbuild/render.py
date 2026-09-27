"""Draw pictures of a design (before it's built) so Claude can review how it looks.

Blocks are drawn as shaded cubes in approximate Minecraft colours: two 3D views (front and
back) and a top-down plan. Hidden blocks are skipped, so even castles draw quickly.
"""

from __future__ import annotations

import hashlib
import io
from typing import Dict, List, Tuple

from .model import Build, Pos, block_id

RGB = Tuple[int, int, int]

# First matching keyword wins, so specific names come before general ones.
_COLOURS: List[Tuple[str, RGB]] = [
    ("water", (60, 100, 220)), ("lava", (230, 100, 20)), ("ice", (160, 190, 250)),
    ("glass", (190, 225, 240)), ("iron_bars", (160, 160, 165)), ("leaves", (60, 120, 40)),
    ("grass_block", (95, 159, 53)), ("moss", (90, 120, 50)), ("dirt", (134, 96, 67)), ("mud", (80, 70, 60)),
    ("red_sand", (190, 100, 40)), ("sandstone", (216, 203, 155)), ("sand", (219, 207, 163)),
    ("gravel", (130, 125, 120)), ("snow", (245, 250, 250)), ("hay", (200, 170, 50)),
    ("iron_door", (190, 190, 195)), ("door", (70, 45, 25)), ("bookshelf", (120, 80, 50)), ("lantern", (255, 200, 90)), ("torch", (255, 210, 90)),
    ("glowstone", (250, 210, 120)), ("sea_lantern", (200, 230, 225)),
    ("mossy", (100, 120, 90)), ("cobblestone", (115, 115, 115)), ("stone_brick", (122, 122, 122)),
    ("deepslate", (72, 72, 78)), ("blackstone", (45, 40, 48)), ("tuff", (110, 110, 100)),
    ("andesite", (135, 135, 135)), ("granite", (150, 105, 85)), ("diorite", (200, 200, 200)),
    ("smooth_stone", (160, 160, 160)), ("stone", (125, 125, 125)), ("mud_brick", (140, 105, 80)),
    ("nether_brick", (60, 30, 35)), ("brick", (150, 75, 60)), ("quartz", (235, 230, 225)),
    ("prismarine", (90, 160, 150)), ("end_stone", (220, 220, 160)), ("purpur", (170, 125, 170)),
    ("terracotta", (150, 90, 70)), ("copper", (190, 110, 80)), ("iron", (215, 215, 215)),
    ("gold", (240, 200, 60)), ("dark_oak", (66, 43, 20)), ("pale_oak", (220, 210, 200)),
    ("oak", (162, 130, 78)), ("spruce", (114, 84, 48)), ("birch", (196, 179, 123)),
    ("jungle", (160, 115, 80)), ("acacia", (168, 90, 50)), ("mangrove", (117, 54, 48)),
    ("cherry", (226, 178, 172)), ("bamboo", (190, 170, 80)), ("crimson", (100, 40, 60)),
    ("warped", (40, 110, 110)),
    ("light_gray", (160, 160, 155)), ("light_blue", (100, 170, 220)), ("white", (230, 230, 230)),
    ("gray", (70, 75, 80)), ("black", (25, 25, 30)), ("red", (160, 40, 35)), ("orange", (225, 110, 20)),
    ("yellow", (240, 190, 40)), ("lime", (110, 180, 30)), ("green", (80, 110, 30)),
    ("cyan", (20, 130, 140)), ("blue", (50, 60, 150)), ("purple", (110, 40, 160)),
    ("magenta", (170, 60, 160)), ("pink", (230, 130, 160)), ("brown", (110, 70, 40)),
]
_SEE_THROUGH = ("air", "glass", "water", "iron_bars", "torch", "lantern", "carpet", "fence", "_wall", "door",
                "trapdoor", "sign", "flower", "grass", "vine", "ladder", "rail", "button", "slab", "chain")


def colour(block: str) -> RGB:
    name = block_id(block)
    for keyword, rgb in _COLOURS:
        if keyword in name:
            return rgb
    digest = hashlib.md5(name.encode()).digest()
    return tuple(90 + d % 110 for d in digest[:3])  # type: ignore[return-value]


def _see_through(block: str) -> bool:
    name = block_id(block)
    return any(k in name for k in _SEE_THROUGH)


def _shade(rgb: RGB, factor: float) -> RGB:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)  # type: ignore[return-value]


def isometric(build: Build, max_size: int = 1100):
    """A 3D view from above the south-east corner (showing top, south and east faces)."""
    from PIL import Image, ImageDraw

    blocks = {p: b for p, b in build if block_id(b) != "air"}
    if not blocks:
        return Image.new("RGB", (64, 64), (235, 240, 245))
    (x1, y1, z1), (x2, y2, z2) = Build.bounds(_as_build(blocks))
    span = (x2 - x1 + 1) + (z2 - z1 + 1)
    s = max(2, min(16, max_size // max(span, 1)))  # half-width of a cube on screen
    width = span * s + 2 * s
    height = int(span * s / 2 + (y2 - y1 + 1) * s + 2 * s)
    img = Image.new("RGB", (width, height), (235, 240, 245))
    draw = ImageDraw.Draw(img)

    def screen(x: float, y: float, z: float) -> Tuple[float, float]:
        return ((x - x1) - (z - z1) + (z2 - z1 + 1)) * s + s, ((x - x1) + (z - z1)) * s / 2 + (y2 - y) * s + s

    def hidden(p: Pos) -> bool:
        return p in blocks and not _see_through(blocks[p])

    order = sorted(blocks, key=lambda p: (p[0] + p[2], p[1], p[0]))
    for (x, y, z) in order:
        if hidden((x, y + 1, z)) and hidden((x + 1, y, z)) and hidden((x, y, z + 1)):
            continue
        rgb = colour(blocks[(x, y, z)])
        top = [screen(x, y + 1, z), screen(x + 1, y + 1, z), screen(x + 1, y + 1, z + 1), screen(x, y + 1, z + 1)]
        east = [screen(x + 1, y + 1, z), screen(x + 1, y + 1, z + 1), screen(x + 1, y, z + 1), screen(x + 1, y, z)]
        south = [screen(x, y + 1, z + 1), screen(x + 1, y + 1, z + 1), screen(x + 1, y, z + 1), screen(x, y, z + 1)]
        outline = _shade(rgb, 0.55)
        draw.polygon(south, fill=_shade(rgb, 0.65), outline=outline)
        draw.polygon(east, fill=_shade(rgb, 0.82), outline=outline)
        draw.polygon(top, fill=rgb, outline=outline)
    return img


def plan(build: Build, max_size: int = 900):
    """A top-down view: the highest block in each column, darker where it's lower."""
    from PIL import Image

    tops: Dict[Tuple[int, int], Tuple[int, str]] = {}
    for (x, y, z), b in build:
        if block_id(b) != "air" and ((x, z) not in tops or y > tops[(x, z)][0]):
            tops[(x, z)] = (y, b)
    if not tops:
        return Image.new("RGB", (64, 64), (235, 240, 245))
    xs, zs = [k[0] for k in tops], [k[1] for k in tops]
    ys = [v[0] for v in tops.values()]
    lo, hi = min(ys), max(ys)
    w, d = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
    s = max(2, min(20, max_size // max(w, d)))
    img = Image.new("RGB", (w * s, d * s), (235, 240, 245))
    for (x, z), (y, b) in tops.items():
        factor = 0.6 + 0.4 * ((y - lo) / (hi - lo) if hi > lo else 1)
        cell = Image.new("RGB", (s, s), _shade(colour(b), factor))
        img.paste(cell, ((x - min(xs)) * s, (z - min(zs)) * s))
    return img


def _as_build(blocks: Dict[Pos, str]) -> Build:
    b = Build()
    b.blocks = blocks
    return b


def review_images(build: Build) -> List[Tuple[str, bytes]]:
    """(caption, PNG bytes) for the front view, back view and plan of a north-fronted design."""
    views = [
        ("Front view (the north side, where the entrance is), seen from above the north-west",
         isometric(build.rotated(2))),
        ("Back view (the south side), seen from above the south-east", isometric(build)),
        ("Top-down plan (north is up; darker means lower)", plan(build)),
    ]
    out = []
    for caption, img in views:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        out.append((caption, buf.getvalue()))
    return out
