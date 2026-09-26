"""Put designs in the world: in front of a player, facing them, with undo."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, ContextManager, Iterator, Optional, Tuple

from .config import DESIGNS_DIR
from .designer import Design, DesignError, design
from .executor import place_build
from .history import History, HistoryEntry
from .model import Build
from .placement import face_toward_player, offset_in_front, yaw_to_direction
from .rcon import RconClient
from .server import MinecraftServer

Log = Callable[[str], None]


@dataclass
class PlaceOptions:
    player: Optional[str] = None
    at: Optional[Tuple[int, int, int]] = None
    facing: Optional[str] = None
    distance: int = 3
    rate: float = 300
    backup: bool = True


def place_in_front(server: MinecraftServer, design_build: Build, history: History, opts: PlaceOptions,
                   log: Log = print) -> HistoryEntry:
    """Place a north-fronted design in front of the player (or at opts.at), front facing them."""
    if opts.at:
        origin, facing = opts.at, opts.facing or "south"
    else:
        player = server.resolve_player(opts.player)
        origin = server.player_position(player)
        facing = opts.facing or yaw_to_direction(server.player_yaw(player))
        log(f"{player} is at {tuple(round(v, 1) for v in origin)}, facing {facing}")
    build = face_toward_player(design_build, facing)
    build = build.translated(*offset_in_front(build, origin, facing, opts.distance))
    return place_build(server, build, history, rate=opts.rate, backup=opts.backup, progress=log)


def save_design(request: str, d: Design) -> str:
    os.makedirs(DESIGNS_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", d.build.name.lower()).strip("-")[:40] or "design"
    n = len(os.listdir(DESIGNS_DIR)) + 1
    path = os.path.join(DESIGNS_DIR, f"{n:03d}-{slug}.py")
    with open(path, "w") as f:
        f.write(f"# Request: {request}\n{d.script.rstrip()}\n")
    return path


Connect = Callable[[], ContextManager[RconClient]]


@contextmanager
def _server(connect: Connect) -> Iterator[MinecraftServer]:
    with connect() as rcon:
        yield MinecraftServer(rcon)


def ai_build(request: str, connect: Connect, client, catalog, history: History, opts: PlaceOptions,
             log: Log = print) -> HistoryEntry:
    """Design `request` with Claude and build it in front of the player, keeping them posted in chat.
    The RCON connection is opened only when needed, since designing can take a few minutes."""
    with _server(connect) as server:
        player = opts.player if opts.at else server.resolve_player(opts.player)
        target = player or "@a"
        server.tell(f"Designing \"{request}\"... this usually takes a minute or two.", target)

    try:
        d = design(request, client, catalog, log=log)
    except DesignError as e:
        with _server(connect) as server:
            server.tell(f"Sorry, that didn't work: {e}", target)
        raise
    path = save_design(request, d)
    log(f"Design '{d.build.name}' ({len(d.build)} blocks) saved to {path}")

    with _server(connect) as server:
        seconds = len(d.build) / opts.rate if opts.rate > 0 else 0
        server.tell(f"Building {d.build.name}: {len(d.build)} blocks, about {max(1, round(seconds))}s. "
                    "Type !undo to remove it.", target)
        return place_in_front(server, d.build, history, PlaceOptions(**{**opts.__dict__, "player": player}), log)
