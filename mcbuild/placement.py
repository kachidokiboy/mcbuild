"""Work out where a build goes relative to the player."""

from __future__ import annotations

import math
from typing import Tuple

from .model import Build, Pos

# Unit (dx, dz) for each cardinal direction.
DIRECTIONS = {"south": (0, 1), "north": (0, -1), "east": (1, 0), "west": (-1, 0)}
OPPOSITE = {"south": "north", "north": "south", "east": "west", "west": "east"}


def yaw_to_direction(yaw: float) -> str:
    """Minecraft yaw: 0 = south (+z), 90 = west (-x), 180 = north (-z), 270/-90 = east (+x)."""
    yaw = yaw % 360
    if yaw >= 315 or yaw < 45:
        return "south"
    if yaw < 135:
        return "west"
    if yaw < 225:
        return "north"
    return "east"


def offset_in_front(build: Build, player: Tuple[float, float, float], facing: str, distance: int = 3) -> Pos:
    """Translation that puts `build` `distance` blocks in front of the player, centred on
    their line of sight, with the build's y=0 at the player's feet."""
    (x1, y1, z1), (x2, _, z2) = build.bounds()
    px, py, pz = (math.floor(v) for v in player)
    width_x, width_z = x2 - x1 + 1, z2 - z1 + 1

    if facing == "south":
        tx, tz = px - width_x // 2, pz + distance
    elif facing == "north":
        tx, tz = px - width_x // 2, pz - distance - (width_z - 1)
    elif facing == "east":
        tx, tz = px + distance, pz - width_z // 2
    elif facing == "west":
        tx, tz = px - distance - (width_x - 1), pz - width_z // 2
    else:
        raise ValueError(f"Unknown direction {facing!r}")
    return tx - x1, py, tz - z1


def turns_to_face(player_facing: str) -> int:
    """Clockwise quarter turns that make a north-fronted design face a player looking `player_facing`."""
    return ["north", "east", "south", "west"].index(OPPOSITE[player_facing])


def face_toward_player(design: Build, player_facing: str) -> Build:
    """Rotate a north-fronted design so its front faces a player looking `player_facing`."""
    return design.rotated(turns_to_face(player_facing))
