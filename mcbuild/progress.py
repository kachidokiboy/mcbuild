"""A progress bar at the top of the player's screen (a Minecraft boss bar).

It shows what's happening right now: designing ("Designing details: writing Gatehouse · 1:05"),
building ("Building the structure 45%"), or both at once while the next pass is designed.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable, Optional

from .server import MinecraftServer

BAR_ID = "mcbuild:progress"
MAX_TEXT = 90


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class ProgressBar:
    def __init__(self, server: MinecraftServer, player: str, log: Callable[[str], None] = print,
                 tick: float = 2.0):
        self.server, self.player, self.log, self.tick = server, player, log, tick
        self._lock = threading.Lock()
        self._design: Optional[str] = None
        self._note = ""
        self._design_start = 0.0
        self._build: Optional[str] = None
        self._pct = 0
        self._shown = ("", -1)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle -----------------------------------------------------------------------------

    def __enter__(self) -> "ProgressBar":
        self.server.bossbar_show(BAR_ID, "mcbuild", self.player)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
        try:
            self.server.bossbar_hide(BAR_ID)
        except Exception:  # noqa: BLE001 - never hide the real error behind a cleanup failure
            pass

    def _run(self) -> None:
        while not self._stop.wait(self.tick):
            try:
                self.render()
            except Exception:  # noqa: BLE001 - a missed update must not stop the build
                pass

    # --- state ---------------------------------------------------------------------------------

    def designing(self, label: str) -> None:
        with self._lock:
            self._design, self._note, self._design_start = label, "starting", time.monotonic()
        self.log(f"{label}...")
        self.render()

    def note(self, text: str) -> None:
        """Latest design status from Claude's stream: "thinking", "writing Gatehouse", ..."""
        with self._lock:
            changed = text != self._note
            self._note = text
        if changed and text.startswith(("writing ", "fixing")):
            self.log(f"  {self._design}: {text}")

    def designed(self) -> None:
        with self._lock:
            self._design = None
        self.render()

    def building(self, label: str) -> None:
        with self._lock:
            self._build, self._pct = label, 0
        self.log(f"{label}...")
        self.render()

    def built(self, pct: int) -> None:
        with self._lock:
            self._pct = pct

    def done_building(self) -> None:
        with self._lock:
            self._build = None
        self.render()

    # --- display -------------------------------------------------------------------------------

    def text(self) -> str:
        with self._lock:
            design = None
            if self._design:
                elapsed = time.monotonic() - self._design_start
                design = f"{self._design}: {self._note} · {_clock(elapsed)}"
            if self._build:
                text = f"{self._build} {self._pct}%"
                if design:
                    text += f"  |  {design}"
            else:
                text = design or "mcbuild"
        return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"

    def value(self) -> int:
        with self._lock:
            if self._build:
                return self._pct
            if self._design:
                # No real percentage while Claude works: creep toward 90% so the bar visibly moves.
                return int(90 * (1 - math.exp(-(time.monotonic() - self._design_start) / 60)))
            return 100

    def render(self) -> None:
        shown = (self.text(), self.value())
        if shown != self._shown:
            self._shown = shown
            self.server.bossbar_update(BAR_ID, *shown)
