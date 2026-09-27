"""Place a build in the world with animation, backing up the area first so it can be undone."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .history import MAX_FOOTPRINT, History, HistoryEntry, backup_origin
from .model import Build
from . import placer
from .server import MAX_Y, MIN_Y, MinecraftServer

Progress = Callable[[str], None]


class BuildAborted(Exception):
    pass


def _backup_box(entry: HistoryEntry):
    size = [entry.max[i] - entry.min[i] for i in range(3)]
    bmin = tuple(entry.backup)  # type: ignore[arg-type]
    return bmin, tuple(bmin[i] + size[i] for i in range(3))


def trim_below_world(build: Build, progress: Progress = print) -> Build:
    """Drop blocks below the bottom of the world (deep foundations when building near it)."""
    below = [p for p in build.blocks if p[1] < MIN_Y]
    if not below:
        return build
    progress(f"Skipping {len(below)} foundation blocks below the bottom of the world")
    trimmed = Build(build.name)
    trimmed.blocks = {p: b for p, b in build.blocks.items() if p[1] >= MIN_Y}
    return trimmed


def check_placeable(build: Build) -> None:
    (x1, y1, z1), (x2, y2, z2) = build.bounds()
    if y1 < MIN_Y or y2 > MAX_Y:
        raise ValueError(f"Build spans y={y1}..{y2}, outside the world height {MIN_Y}..{MAX_Y}")
    if x2 - x1 + 1 > MAX_FOOTPRINT or z2 - z1 + 1 > MAX_FOOTPRINT:
        raise ValueError(f"Build footprint exceeds {MAX_FOOTPRINT}x{MAX_FOOTPRINT}")


def place_build(
    server: MinecraftServer,
    build: Build,
    history: History,
    rate: float = 150.0,
    backup: bool = True,
    progress: Progress = print,
    max_errors: int = 10,
    prepare: Optional[Callable[[], None]] = None,
) -> HistoryEntry:
    """Place `build` (already in world coordinates). Returns the history entry for undo.
    `prepare` runs after the backup and before placing (e.g. to level the ground)."""
    build = trim_below_world(build, progress)
    check_placeable(build)
    pmin, pmax = build.bounds()
    entry = HistoryEntry(
        id=history.allocate_id(),
        name=build.name,
        min=list(pmin),
        max=list(pmax),
        blocks=len(build),
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    if backup:
        entry.backup = list(backup_origin(entry.id, pmin))
        bmin, bmax = _backup_box(entry)
        progress(f"Backing up area {pmin}..{pmax} for undo...")
        server.forceload(bmin, bmax, add=True)
        try:
            server.clone(pmin, pmax, bmin)
        finally:
            server.forceload(bmin, bmax, add=False)

    # Record before placing, so an interrupted build can still be undone.
    history.add(entry)
    if prepare:
        prepare()

    total = len(build)
    layers = pmax[1] - pmin[1] + 1
    progress(f"Placing {total} blocks ({layers} layers) at ~{rate:g} blocks/s")
    server.tell(f"Building '{build.name}' ({total} blocks)...")

    reported = [-1]

    def report(percent: int) -> None:
        if percent // 10 != reported[0]:
            reported[0] = percent // 10
            progress(f"  {percent:3d}%")

    start = time.monotonic()
    errors = placer.place(server, build, rate=rate, progress=report, log=progress, max_errors=max_errors)

    elapsed = time.monotonic() - start
    progress(f"Done in {elapsed:.1f}s" + (f" with {errors} failed blocks" if errors else ""))
    server.tell(f"Finished '{build.name}'. Use `mcbuild undo` to remove it.")
    return entry


def undo_last(server: MinecraftServer, history: History, progress: Progress = print) -> HistoryEntry:
    entry = history.last()
    if entry is None:
        raise RuntimeError("Nothing to undo")
    if entry.backup is None:
        raise RuntimeError(f"Build #{entry.id} '{entry.name}' was placed with --no-backup and can't be undone")
    bmin, bmax = _backup_box(entry)
    pmin, pmax = tuple(entry.min), tuple(entry.max)
    progress(f"Restoring area {pmin}..{pmax} from backup...")
    # The build area may be unloaded if the player has walked away, so load both sides.
    server.forceload(bmin, bmax, add=True)
    server.forceload(pmin, pmax, add=True)
    try:
        server.clone(bmin, bmax, pmin)
    finally:
        server.forceload(bmin, bmax, add=False)
        server.forceload(pmin, pmax, add=False)
    history.pop()
    server.tell(f"Undid '{entry.name}'.")
    return entry
