"""Build a design in passes on a fixed site, each pass replacing the one before.

The site is backed up first (which also makes it undoable). Each pass then only places blocks
that are new or different, and puts the original ground back wherever the previous pass had a
block the new one doesn't want. If a pass changes most of the design, it's quicker and cleaner
to restore the whole site and build it fresh.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import groupby
from typing import Callable, Dict, List, Optional, Tuple

from .history import MAX_FOOTPRINT, History, HistoryEntry, backup_origin
from .model import Build, Pos
from . import placer
from .server import MAX_Y, MIN_Y, MinecraftServer

# The prepared-ground copy sits next to the undo backup, in the same far-away strip.
GROUND_COPY_OFFSET = 512

def ground_copy_origin(backup_min: Pos) -> Pos:
    return (backup_min[0], backup_min[1], backup_min[2] + GROUND_COPY_OFFSET)


# Rebuild from scratch when a pass would change more than this share of the blocks.
REBUILD_FRACTION = 0.5

Progress = Callable[[int], None]


def _x_runs(positions: List[Pos]) -> List[Tuple[Pos, Pos]]:
    """Group positions into straight runs along x, so each run is one /clone command."""
    runs = []
    ordered = sorted(positions, key=lambda p: (p[1], p[2], p[0]))
    for (y, z), group in groupby(ordered, key=lambda p: (p[1], p[2])):
        xs = [p[0] for p in group]
        start = prev = xs[0]
        for x in xs[1:] + [None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            runs.append(((start, y, z), (prev, y, z)))
            if x is not None:
                start = prev = x
    return runs


class SiteBuilder:
    def __init__(self, server: MinecraftServer, history: History, site_min: Pos, site_max: Pos, name: str,
                 rate: float = 300, backup: bool = True, log: Callable[[str], None] = print,
                 max_errors: int = 25):
        (x1, y1, z1), (x2, y2, z2) = site_min, site_max
        if y1 < MIN_Y or y2 > MAX_Y:
            raise ValueError(f"The site spans y={y1}..{y2}, outside the world height {MIN_Y}..{MAX_Y}; "
                             "try building from lower or higher ground")
        if x2 - x1 + 1 > MAX_FOOTPRINT or z2 - z1 + 1 > MAX_FOOTPRINT:
            raise ValueError(f"The site is larger than {MAX_FOOTPRINT}x{MAX_FOOTPRINT}")
        self.server, self.history, self.name = server, history, name
        self.site_min, self.site_max = site_min, site_max
        self.rate, self.backup, self.log, self.max_errors = rate, backup, log, max_errors
        self.current: Dict[Pos, str] = {}
        self.entry: Optional[HistoryEntry] = None
        self._backup_min: Optional[Pos] = None
        # A second copy taken after the ground is prepared: passes restore removed blocks from it,
        # while undo still restores the original terrain from the first.
        self._ground_min: Optional[Pos] = None
        self._site_loaded = False
        self.errors = 0

    # --- lifecycle -----------------------------------------------------------------------------

    @classmethod
    def resume(cls, server: MinecraftServer, history: History, entry: HistoryEntry, current: Build,
               rate: float = 300, log: Callable[[str], None] = print) -> "SiteBuilder":
        """Continue working on an earlier build (for edits): `current` is what's standing there now."""
        builder = cls(server, history, tuple(entry.min), tuple(entry.max), entry.name, rate=rate,
                      backup=bool(entry.backup), log=log)
        builder.entry = entry
        builder.current = dict(current.blocks)
        if entry.backup:
            builder._backup_min = tuple(entry.backup)
            server.forceload(builder._backup_min, builder._to_backup(builder.site_max), add=True)
        if entry.ground:
            builder._ground_min = tuple(entry.ground)
            server.forceload(builder._ground_min, builder._to_copy(builder._ground_min, builder.site_max), add=True)
        server.forceload(builder.site_min, builder.site_max, add=True)
        builder._site_loaded = True
        return builder

    @property
    def ground_min(self) -> Optional[Pos]:
        return self._ground_min

    def start(self) -> HistoryEntry:
        """Back up the whole site (for removals between passes, and for undo) and record it."""
        entry = HistoryEntry(
            id=self.history.allocate_id(), name=self.name, min=list(self.site_min), max=list(self.site_max),
            blocks=0, created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        if self.backup:
            self._backup_min = backup_origin(entry.id, self.site_min)
            entry.backup = list(self._backup_min)
            # Keep the backup loaded until finish(): every pass restores removed blocks from it.
            self.server.forceload(self._backup_min, self._to_backup(self.site_max), add=True)
            self.server.clone(self.site_min, self.site_max, self._backup_min)
        self.history.add(entry)
        self.entry = entry
        return entry

    def snapshot_ground(self) -> None:
        """Remember the site as it is now (after leveling) as the ground that removed blocks go back to."""
        if not self._backup_min:
            return
        self._ground_min = ground_copy_origin(self._backup_min)
        self.server.forceload(self._ground_min, self._to_copy(self._ground_min, self.site_max), add=True)
        self.server.clone(self.site_min, self.site_max, self._ground_min)

    def finish(self, name: Optional[str] = None) -> None:
        if self.entry:
            self.entry.name = name or self.name
            self.entry.blocks = len(self.current)
            self.history.save()
        for copy_min in (self._backup_min, self._ground_min):
            if copy_min:
                self.server.forceload(copy_min, self._to_copy(copy_min, self.site_max), add=False)
        if self._site_loaded:
            self.server.forceload(self.site_min, self.site_max, add=False)

    # --- passes --------------------------------------------------------------------------------

    def apply(self, build: Build, progress: Optional[Progress] = None) -> None:
        """Make the site match `build` (already in world coordinates)."""
        new = build.blocks
        outside = [p for p in new if not self._inside(p)]
        if outside:
            raise ValueError(f"{len(outside)} blocks fall outside the planned site")
        removed = [p for p in self.current if p not in new]
        changed = [(p, b) for p, b in new.items() if self.current.get(p) != b]
        if self.current and self.backup and len(changed) + len(removed) > REBUILD_FRACTION * max(len(new), 1):
            self.log("  This pass changes most of the design; clearing the site and rebuilding.")
            ground = self._ground_min or self._backup_min
            self.server.clone(ground, self._to_copy(ground, self.site_max), self.site_min)
            self.current, removed, changed = {}, [], list(new.items())
        self._restore(removed)
        self._place(changed, progress)
        self.current = dict(new)

    def _restore(self, positions: List[Pos]) -> None:
        """Put back whatever was at these positions before the build started."""
        if not positions:
            return
        self.log(f"  Removing {len(positions)} blocks the new pass doesn't use")
        if not self.backup:
            for p in positions:
                self.server.setblock(p, "minecraft:air")
            return
        ground = self._ground_min or self._backup_min
        for start, end in _x_runs(positions):
            self.server.clone(self._to_copy(ground, start), self._to_copy(ground, end), start)

    def _place(self, items: List[Tuple[Pos, str]], progress: Optional[Progress]) -> None:
        batch = Build()
        batch.blocks = dict(items)
        self.errors += placer.place(self.server, batch, rate=self.rate, progress=progress, log=self.log,
                                    max_errors=self.max_errors - self.errors)

    # --- helpers -------------------------------------------------------------------------------

    def _inside(self, p: Pos) -> bool:
        return all(self.site_min[i] <= p[i] <= self.site_max[i] for i in range(3))

    def _to_backup(self, p: Pos) -> Pos:
        assert self._backup_min is not None
        return self._to_copy(self._backup_min, p)

    def _to_copy(self, copy_min: Pos, p: Pos) -> Pos:
        """Where site position `p` is in the copy whose corner is `copy_min`."""
        return tuple(copy_min[i] + p[i] - self.site_min[i] for i in range(3))  # type: ignore[return-value]
