"""Decide the order blocks are placed in, so the build rises naturally and stays valid."""

from __future__ import annotations

from typing import List, Tuple

from .model import Build, Pos, block_id

# Blocks that need a neighbour to hang on / stand on. They are placed after all
# structural blocks, so their support always exists first.
_ATTACHMENT_SUFFIXES = (
    "torch", "lantern", "door", "trapdoor", "ladder", "button", "lever", "carpet",
    "pressure_plate", "sign", "banner", "rail", "vine", "bed", "candle", "sapling",
    "flower", "tulip", "_orchid", "poppy", "dandelion", "allium", "bluet", "daisy",
    "lily_of_the_valley", "fern", "bush", "_coral", "coral_fan", "item_frame",
    "redstone_wire", "repeater", "comparator", "tripwire_hook", "bell", "chain",
    "pointed_dripstone", "scaffolding", "snow", "moss_carpet", "flower_pot",
)
_ATTACHMENT_EXACT = {"short_grass", "tall_grass", "grass", "large_fern", "lily_pad", "cobweb"}


def needs_support(block: str) -> bool:
    name = block_id(block)
    if name.startswith("potted_") or name in _ATTACHMENT_EXACT:
        return True
    return name.endswith(_ATTACHMENT_SUFFIXES)


def build_order(build: Build) -> List[Tuple[Pos, str]]:
    """Structural blocks bottom-up in a snake pattern per layer, then attachments bottom-up."""

    def key(item: Tuple[Pos, str]):
        (x, y, z), block = item
        # Alternate z direction on each x row so placement sweeps back and forth.
        return (needs_support(block), y, x, z if x % 2 == 0 else -z)

    return sorted(build, key=key)
