"""Turn a build into as few commands as possible and send them at a steady pace.

Within each layer, rectangles of the same block become one /fill; everything else is a
/setblock. The build still rises bottom-up, layer by layer, but a 50x50 floor is one command
instead of 2,500. Blocks that hang or stand on something (doors, torches...) are still placed
one by one, last.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from itertools import groupby
from typing import Callable, Dict, List, Optional, Tuple

from .model import Build, Pos
from .sequence import build_order, needs_support
from .server import CommandError, MinecraftServer, split_box

# Rectangles smaller than this are placed block by block (it looks smoother).
MIN_FILL = 4
# For pacing, a filled block costs this fraction of a separately placed block.
FILL_COST = 0.1


@dataclass
class Command:
    p1: Pos
    p2: Pos
    block: str

    @property
    def volume(self) -> int:
        return (self.p2[0] - self.p1[0] + 1) * (self.p2[1] - self.p1[1] + 1) * (self.p2[2] - self.p1[2] + 1)

    @property
    def is_fill(self) -> bool:
        return self.p1 != self.p2

    def positions(self) -> List[Pos]:
        return [(x, y, z) for x in range(self.p1[0], self.p2[0] + 1) for y in range(self.p1[1], self.p2[1] + 1)
                for z in range(self.p1[2], self.p2[2] + 1)]


def _rectangles(cells: Dict[Tuple[int, int], str]) -> List[Tuple[int, int, int, int, str]]:
    """Greedily cover a layer's cells with same-block rectangles (x1, z1, x2, z2, block)."""
    taken = set()
    rects = []
    for x, z in sorted(cells):
        if (x, z) in taken:
            continue
        block = cells[(x, z)]

        def free(cx, cz):
            return cells.get((cx, cz)) == block and (cx, cz) not in taken

        z2 = z
        while free(x, z2 + 1):
            z2 += 1
        x2 = x
        while all(free(x2 + 1, cz) for cz in range(z, z2 + 1)):
            x2 += 1
        taken.update((cx, cz) for cx in range(x, x2 + 1) for cz in range(z, z2 + 1))
        rects.append((x, z, x2, z2, block))
    return rects


def plan(build: Build) -> List[Command]:
    structural = {p: b for p, b in build if not needs_support(b)}
    commands: List[Command] = []
    for y, layer in groupby(sorted(structural, key=lambda p: p[1]), key=lambda p: p[1]):
        cells = {(p[0], p[2]): structural[p] for p in layer}
        for x1, z1, x2, z2, block in _rectangles(cells):
            if (x2 - x1 + 1) * (z2 - z1 + 1) >= MIN_FILL:
                for a, b in split_box((x1, y, z1), (x2, y, z2)):
                    commands.append(Command(a, b, block))
            else:
                commands += [Command((x, y, z), (x, y, z), block)
                             for x in range(x1, x2 + 1) for z in range(z1, z2 + 1)]
    attachments = Build()
    attachments.blocks = {p: b for p, b in build if needs_support(b)}
    commands += [Command(p, p, b) for p, b in build_order(attachments)]
    return commands


def place(server: MinecraftServer, build: Build, rate: float = 300, progress: Optional[Callable[[int], None]] = None,
          log: Callable[[str], None] = print, max_errors: int = 25) -> int:
    """Place `build` at about `rate` blocks per second (filled blocks count less). Returns the
    number of blocks that failed; raises BuildAborted after `max_errors`."""
    from .executor import BuildAborted

    commands = plan(build)
    total = max(1, len(build))
    done = errors = 0
    cost = 0.0
    start = time.monotonic()
    def failed(e: CommandError) -> None:
        nonlocal errors
        errors += 1
        log(f"  ! {e}")
        if errors >= max_errors:
            raise BuildAborted(f"Too many failed blocks ({errors}); stopping. Undo reverts the build.")

    for i, command in enumerate(commands):
        if command.is_fill:
            try:
                server.fill(command.p1, command.p2, command.block)
            except CommandError:
                # Fall back to one block at a time so a single odd block can't sink the rest.
                for p in command.positions():
                    try:
                        server.setblock(p, command.block)
                    except CommandError as e:
                        failed(e)
        else:
            try:
                server.setblock(command.p1, command.block)
            except CommandError as e:
                failed(e)
        done += command.volume
        if progress and (i % 10 == 0 or i == len(commands) - 1):
            progress(min(100, done * 100 // total))
        cost += 1 + (command.volume - 1) * FILL_COST
        if rate > 0:
            delay = start + cost / rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    return errors
