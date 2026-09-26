"""Named building materials: the matching full block, stairs, slab, etc. for each family."""

from __future__ import annotations

from typing import Dict

_WOODS = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "pale_oak")


def _wood(name: str) -> Dict[str, str]:
    return {
        "full": f"{name}_planks", "stairs": f"{name}_stairs", "slab": f"{name}_slab",
        "fence": f"{name}_fence", "door": f"{name}_door", "trapdoor": f"{name}_trapdoor",
        "log": f"{name}_log", "wood": f"{name}_wood",
    }


def _stone(full: str, prefix: str, wall: bool = True) -> Dict[str, str]:
    m = {"full": full, "stairs": f"{prefix}_stairs", "slab": f"{prefix}_slab"}
    if wall:
        m["wall"] = f"{prefix}_wall"
    return m


MATERIALS: Dict[str, Dict[str, str]] = {name: _wood(name) for name in _WOODS}
MATERIALS.update({
    "stone": _stone("stone", "stone", wall=False),
    "cobblestone": _stone("cobblestone", "cobblestone"),
    "mossy_cobblestone": _stone("mossy_cobblestone", "mossy_cobblestone"),
    "stone_brick": _stone("stone_bricks", "stone_brick"),
    "mossy_stone_brick": _stone("mossy_stone_bricks", "mossy_stone_brick"),
    "brick": _stone("bricks", "brick"),
    "sandstone": _stone("sandstone", "sandstone"),
    "red_sandstone": _stone("red_sandstone", "red_sandstone"),
    "smooth_stone": {"full": "smooth_stone", "slab": "smooth_stone_slab"},
    "andesite": _stone("polished_andesite", "polished_andesite", wall=False),
    "deepslate_brick": _stone("deepslate_bricks", "deepslate_brick"),
    "deepslate_tile": _stone("deepslate_tiles", "deepslate_tile"),
    "blackstone": _stone("polished_blackstone_bricks", "polished_blackstone_brick"),
    "nether_brick": _stone("nether_bricks", "nether_brick"),
    "mud_brick": _stone("mud_bricks", "mud_brick"),
    "tuff_brick": _stone("tuff_bricks", "tuff_brick"),
    "quartz": _stone("quartz_block", "quartz", wall=False),
    "prismarine": _stone("prismarine_bricks", "prismarine_brick", wall=False),
    "end_stone_brick": _stone("end_stone_bricks", "end_stone_brick"),
})


def material(name: str) -> Dict[str, str]:
    """Look up a material family by name, e.g. material("spruce")["stairs"] -> "spruce_stairs"."""
    try:
        return MATERIALS[name]
    except KeyError:
        raise ValueError(f"Unknown material {name!r}. Known: {', '.join(sorted(MATERIALS))}") from None


def part(name: str, kind: str) -> str:
    m = material(name)
    if kind not in m:
        raise ValueError(f"Material {name!r} has no {kind} (it has: {', '.join(m)})")
    return m[kind]
