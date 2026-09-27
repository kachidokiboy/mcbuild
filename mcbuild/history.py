"""Local record of builds, so they can be undone."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .model import Pos

# Backups live in a far-away strip of the world, one 512-block slot per build.
BACKUP_BASE_X = 1_000_000
BACKUP_BASE_Z = 1_000_000
BACKUP_SLOT_SIZE = 512
BACKUP_SLOTS = 100
MAX_FOOTPRINT = 256  # must stay well under BACKUP_SLOT_SIZE


@dataclass
class HistoryEntry:
    id: int
    name: str
    min: List[int]
    max: List[int]
    blocks: int
    created: str
    backup: Optional[List[int]] = None  # min corner of the backup copy, if one was made
    # For builds that can be edited: how the design was placed, and each version of it.
    turns: Optional[int] = None
    offset: Optional[List[int]] = None
    site: Optional[List[List[int]]] = None  # the box (design coordinates) edits must stay inside
    ground: Optional[List[int]] = None  # min corner of the prepared-ground copy
    designs: List[str] = field(default_factory=list)  # saved design scripts, oldest version first

    @property
    def editable(self) -> bool:
        return bool(self.designs and self.turns is not None and self.offset and self.site and self.backup)


def backup_origin(build_id: int, pmin: Pos) -> Pos:
    slot = build_id % BACKUP_SLOTS
    return (BACKUP_BASE_X + slot * BACKUP_SLOT_SIZE, pmin[1], BACKUP_BASE_Z)


class History:
    def __init__(self, path: str):
        self.path = path
        self.entries: List[HistoryEntry] = []
        self.next_id = 1
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            self.entries = [HistoryEntry(**e) for e in data.get("entries", [])]
            self.next_id = data.get("next_id", 1)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # Only the most recent builds can be undone; older backup slots get reused.
        self.entries = self.entries[-BACKUP_SLOTS:]
        with open(self.path, "w") as f:
            json.dump({"next_id": self.next_id, "entries": [asdict(e) for e in self.entries]}, f, indent=2)

    def allocate_id(self) -> int:
        build_id = self.next_id
        self.next_id += 1
        return build_id

    def add(self, entry: HistoryEntry) -> None:
        self.entries.append(entry)
        self.save()

    def last(self) -> Optional[HistoryEntry]:
        return self.entries[-1] if self.entries else None

    def pop(self) -> HistoryEntry:
        entry = self.entries.pop()
        self.save()
        return entry
