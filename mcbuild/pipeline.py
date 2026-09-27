"""Put designs in the world: in front of a player, facing them, with undo."""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, ContextManager, Iterator, List, Optional, Tuple

from .blocks import check_blocks
from .config import DESIGNS_DIR
from .designer import Design, DesignError, SitePlan, design, edit_design, plan_site
from .executor import place_build, undo_last
from .history import History, HistoryEntry
from .model import Build, Pos
from .placement import offset_in_front, turns_to_face, yaw_to_direction
from .progress import ProgressBar
from .rcon import RconClient
from .sandbox import ScriptError, run_script_isolated
from . import terrain
from .server import CommandError, MinecraftServer
from .staging import SiteBuilder

Log = Callable[[str], None]


@dataclass
class PlaceOptions:
    player: Optional[str] = None
    at: Optional[Tuple[int, int, int]] = None
    facing: Optional[str] = None
    distance: int = 3
    rate: float = 300
    backup: bool = True
    review: bool = True  # let Claude check pictures of the final design before building it


def place_in_front(server: MinecraftServer, design_build: Build, history: History, opts: PlaceOptions,
                   log: Log = print, design_path: Optional[str] = None) -> HistoryEntry:
    """Place a north-fronted design in front of the player (or at opts.at), front facing them,
    on leveled ground."""
    placement = placement_for(server, *design_build.bounds(), opts, log)
    (x1, _, z1), (x2, _, z2) = placement.apply(design_build).bounds()
    prepare = None
    server.forceload((x1, 0, z1), (x2, 0, z2), add=True)
    try:
        ground, lowest = _survey_ground(server, (x1, z1, x2, z2), opts, None, log)
        if ground is not None:
            placement = Placement(placement.turns, (placement.offset[0], ground, placement.offset[2]))
        build = placement.apply(design_build)
        if ground is not None:
            (_, by1, _), (_, by2, _) = build.bounds()
            # Stay inside the area the undo backup covers.
            depth = max(0, min(ground - by1, ground - lowest + 1, terrain.MAX_FILL_DEPTH))
            top = max(by2, ground)
            prepare = lambda: terrain.level(server, x1, z1, x2, z2, ground, top, depth, log)  # noqa: E731
        entry = place_build(server, build, history, rate=opts.rate, backup=opts.backup, progress=log,
                            prepare=prepare)
        if design_path:
            _remember_placement(history, entry, placement, design_build.bounds(), design_path,
                                tuple(entry.ground) if entry.ground else None)
        return entry
    finally:
        server.forceload((x1, 0, z1), (x2, 0, z2), add=False)


@dataclass
class SavedDesign:
    number: int
    name: str
    request: str
    path: str


def list_designs() -> List[SavedDesign]:
    """Saved design scripts, oldest first. Files are named like 007-stone-watchtower.py."""
    designs = []
    if os.path.isdir(DESIGNS_DIR):
        for filename in sorted(os.listdir(DESIGNS_DIR)):
            match = re.match(r"(\d+)-(.*)\.py$", filename)
            if not match:
                continue
            path = os.path.join(DESIGNS_DIR, filename)
            with open(path) as f:
                first = f.readline()
            request = first[len("# Request: "):].strip() if first.startswith("# Request: ") else ""
            designs.append(SavedDesign(int(match.group(1)), match.group(2), request, path))
    return sorted(designs, key=lambda d: d.number)


def find_design(query: str) -> SavedDesign:
    """Find a saved design by number ("7") or by words in its name or request ("tower")."""
    designs = list_designs()
    if not designs:
        raise ValueError("There are no saved designs yet; make one with !build")
    query = query.strip().lower()
    if query.lstrip("#").isdigit():
        number = int(query.lstrip("#"))
        for d in designs:
            if d.number == number:
                return d
        raise ValueError(f"There's no saved design #{number}")
    words = query.split()
    matches = [d for d in designs if all(w in f"{d.name} {d.request}".lower() for w in words)]
    if not matches:
        raise ValueError(f"No saved design matches '{query}'; !designs lists them")
    return matches[-1]  # the most recent match


def save_design(request: str, d: Design) -> str:
    """Save a design's script so it can be rebuilt later without calling the AI."""
    os.makedirs(DESIGNS_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", d.build.name.lower()).strip("-")[:40] or "design"
    number = max((x.number for x in list_designs()), default=0) + 1
    path = os.path.join(DESIGNS_DIR, f"{number:03d}-{slug}.py")
    with open(path, "w") as f:
        f.write(f"# Request: {request}\n{d.script.rstrip()}\n")
    return path


def load_design(path: str, catalog) -> Build:
    """Run a saved design script and check its blocks, without calling the AI."""
    with open(path) as f:
        build = run_script_isolated(f.read())
    if catalog:
        problems = check_blocks([blk for _, blk in build], catalog)
        if problems:
            raise ScriptError("the design has invalid blocks: " + "; ".join(problems[:3]))
    return build


Connect = Callable[[], ContextManager[RconClient]]


@contextmanager
def _server(connect: Connect) -> Iterator[MinecraftServer]:
    with connect() as rcon:
        yield MinecraftServer(rcon)


@dataclass
class Placement:
    """Where a design goes in the world: quarter turns, then a translation."""
    turns: int
    offset: Pos

    def apply(self, build: Build) -> Build:
        return build.rotated(self.turns).translated(*self.offset)


def placement_for(server: MinecraftServer, box_min: Pos, box_max: Pos, opts: PlaceOptions, log: Log = print) -> Placement:
    """Place a design box in front of the player (or at opts.at), its front (north side) facing them."""
    if opts.at:
        origin, facing = opts.at, opts.facing or "south"
    else:
        player = server.resolve_player(opts.player)
        origin = server.player_position(player)
        facing = opts.facing or yaw_to_direction(server.player_yaw(player))
        log(f"{player} is at {tuple(round(v, 1) for v in origin)}, facing {facing}")
    box = Build()
    box.set(*box_min, "stone")
    box.set(*box_max, "stone")
    turns = turns_to_face(facing)
    return Placement(turns, offset_in_front(box.rotated(turns), origin, facing, opts.distance))


class Cancelled(Exception):
    """The player cancelled the build at the preview."""


Confirm = Callable[[SitePlan], bool]


def ai_build(request: str, connect: Connect, client, catalog, history: History, opts: PlaceOptions,
             log: Log = print, confirm: Optional[Confirm] = None) -> HistoryEntry:
    """Design `request` with Claude in three passes and build each as it's ready, with a progress
    bar at the top of the player's screen:

    1. site plan: size and layout, marked on the ground within seconds
    2. structure: walls, floors, roofs and towers, built while pass 3 is being designed
    3. details: the final design; only changed blocks are placed, and removed ones restored

    Small buildings skip pass 2. With `confirm`, the player sees the marked site first and the
    detailed design only starts if confirm(site) returns True."""
    with _server(connect) as server:
        player = opts.player if opts.at else server.resolve_player(opts.player)
        target = player or "@a"
        server.tell(f"Designing \"{request}\"...", target)
        with ProgressBar(server, target, log) as bar:
            try:
                return _staged_build(request, server, target, bar, client, catalog, history, opts, log, confirm)
            except DesignError as e:
                server.tell(f"Sorry, that didn't work: {e}", target)
                raise


def _staged_build(request, server, target, bar, client, catalog, history, opts, log, confirm=None) -> HistoryEntry:
    bar.designing("Planning the site")
    site = plan_site(request, client, status=bar.note, log=log)
    bar.designed()
    log(f"Site: {site.name}, {site.width}x{site.depth}, {site.height} tall, parts: "
        + ", ".join(p["name"] for p in site.parts))

    placement = placement_for(server, *site.box(), opts, log)
    (x1, _, z1), (x2, _, z2) = placement.apply(_box_build(*site.box())).bounds()
    server.forceload((x1, 0, z1), (x2, 0, z2), add=True)  # keep the whole site loaded while we work
    try:
        return _build_on_site(request, site, placement, (x1, z1, x2, z2), server, target, bar, client, catalog,
                              history, opts, log, confirm)
    finally:
        server.forceload((x1, 0, z1), (x2, 0, z2), add=False)


def _survey_ground(server, area, opts, bar, log) -> Tuple[Optional[int], Optional[int]]:
    """The ground level to build on, and the lowest point of the site (None if unknown)."""
    if bar:
        bar.building("Surveying the ground")
    try:
        survey = terrain.survey(server, *area)
    except CommandError as e:
        log(f"Couldn't survey the ground ({e}); building at the player's level without leveling.")
        return None, None
    finally:
        if bar:
            bar.done_building()
    log(f"Ground: {survey.describe()}")
    ground = opts.at[1] if opts.at else survey.ground
    return ground, survey.lowest


def _build_on_site(request, site, placement, area, server, target, bar, client, catalog, history, opts,
                   log, confirm=None) -> HistoryEntry:
    ground, lowest = _survey_ground(server, area, opts, bar, log)
    if ground is not None:
        placement = Placement(placement.turns, (placement.offset[0], ground, placement.offset[2]))
    site.fit_to_ground(placement.offset[1], lowest)
    world_box = placement.apply(_box_build(*site.box()))
    builder = SiteBuilder(server, history, *world_box.bounds(), name=site.name, rate=opts.rate,
                          backup=opts.backup, log=log)
    entry = builder.start()
    final: Optional[Design] = None
    try:
        if ground is not None:
            bar.building("Preparing the ground")
            (bx1, by1, bz1), (bx2, by2, bz2) = builder.site_min, builder.site_max
            depth = min(ground - by1, ground - lowest + 1, terrain.MAX_FILL_DEPTH)
            terrain.level(server, bx1, bz1, bx2, bz2, ground, by2, max(0, depth), log)
            builder.snapshot_ground()
            bar.done_building()

        bar.building("Marking the site")
        builder.apply(placement.apply(site.outline()), bar.built)
        bar.done_building()
        if confirm is None:
            server.tell(f"Planned {site.name} ({site.width}x{site.depth}); the site is marked.", target)
        else:
            server.tell(f"Planned {site.name}: {site.width}x{site.depth}, {site.height} tall (see the markers and "
                        "corner posts). Type !go to build it, or !cancel.", target)
            bar.designing("Waiting for !go")
            bar.note("type !go or !cancel")
            go = confirm(site)
            bar.designed()
            if not go:
                builder.abandon()
                undo_last(server, history, progress=log)
                server.tell("Cancelled; the site is back the way it was.", target)
                raise Cancelled(f"{site.name} was cancelled")

        structure: Optional[Design] = None
        if not site.small:
            bar.designing("Designing the structure")
            try:
                structure = design(request, client, catalog, site=site, stage="structure", status=bar.note, log=log)
            except DesignError as e:
                log(f"The structure pass failed ({e}); going straight to the detailed design.")
            bar.designed()

        # Design the details while the structure goes up.
        result: dict = {}

        def design_details() -> None:
            try:
                result["design"] = design(request, client, catalog, site=site, stage="final",
                                          base_script=structure.script if structure else None,
                                          status=bar.note, log=log, review=opts.review)
            except BaseException as e:  # noqa: BLE001 - re-raised in the main thread
                result["error"] = e

        bar.designing("Designing details" if structure else "Designing")
        worker = threading.Thread(target=design_details, daemon=True)
        worker.start()
        if structure:
            bar.building("Building the structure")
            builder.apply(placement.apply(structure.build), bar.built)
            bar.done_building()
        worker.join()
        bar.designed()

        if "error" in result:
            if structure and isinstance(result["error"], DesignError):
                server.tell(f"Couldn't add the details ({result['error']}); keeping the basic structure.", target)
                final = structure
            else:
                raise result["error"]
        else:
            final = result["design"]
        name = final.build.name if final.build.name != "design" else site.name
        bar.building("Adding details" if structure else f"Building {name}")
        builder.apply(placement.apply(final.build), bar.built)
        bar.done_building()
    finally:
        builder.finish(final.build.name if final and final.build.name != "design" else site.name)

    path = save_design(request, final)
    _remember_placement(history, entry, placement, site.box(), path, builder.ground_min)
    number = int(os.path.basename(path).split("-", 1)[0])
    log(f"Design '{entry.name}' ({len(final.build)} blocks) saved to {path}")
    server.tell(f"Finished {entry.name}! Saved as design #{number}: !rebuild {number} builds it again for free, "
                "!undo removes it.", target)
    return entry


def _remember_placement(history: History, entry: HistoryEntry, placement: Placement, box, design_path: str,
                        ground_min: Optional[Pos]) -> None:
    """Store what's needed to edit this build later."""
    entry.turns = placement.turns
    entry.offset = list(placement.offset)
    entry.site = [list(box[0]), list(box[1])]
    entry.ground = list(ground_min) if ground_min else entry.ground
    entry.designs = [design_path]
    history.save()


# --- editing -------------------------------------------------------------------------------

def read_design(path: str) -> Tuple[str, str]:
    """(request, script) of a saved design file."""
    with open(path) as f:
        text = f.read()
    first, _, rest = text.partition("\n")
    if first.startswith("# Request: "):
        return first[len("# Request: "):].strip(), rest
    return "", text


def _placed(entry: HistoryEntry, design_build: Build) -> Build:
    """A design as placed in the world for this build, clipped to the build's area."""
    placed = Placement(entry.turns, tuple(entry.offset)).apply(design_build)
    clipped = Build(placed.name)
    clipped.blocks = {p: b for p, b in placed.blocks.items()
                      if all(entry.min[i] <= p[i] <= entry.max[i] for i in range(3))}
    return clipped


def _editable_entry(history: History) -> HistoryEntry:
    entry = history.last()
    if entry is None:
        raise ValueError("There's nothing to edit yet; make something with !build")
    if not entry.editable or not os.path.exists(entry.designs[-1]):
        raise ValueError(f"'{entry.name}' can't be edited (only builds made with this version of mcbuild, "
                         "with undo backups, can be)")
    return entry


def edit_last(instruction: str, connect: Connect, client, catalog, history: History, opts: PlaceOptions,
              log: Log = print) -> HistoryEntry:
    """Change the most recent build as described, rebuilding only what differs."""
    entry = _editable_entry(history)
    request, script = read_design(entry.designs[-1])
    with _server(connect) as server:
        target = opts.player or "@a"
        server.tell(f"Editing {entry.name}: \"{instruction}\"...", target)
        with ProgressBar(server, target, log) as bar:
            bar.designing("Designing the change")
            try:
                d = edit_design(instruction, request or entry.name, script, tuple(map(tuple, entry.site)), client,
                                catalog, log=log, status=bar.note, review=opts.review)
            except DesignError as e:
                server.tell(f"Sorry, that didn't work: {e}", target)
                raise
            finally:
                bar.designed()
            current = _placed(entry, run_script_isolated(script))
            builder = SiteBuilder.resume(server, history, entry, current, rate=opts.rate, log=log)
            try:
                bar.building("Applying the change")
                builder.apply(_placed(entry, d.build), bar.built)
                bar.done_building()
            finally:
                builder.finish(d.build.name if d.build.name != "design" else entry.name)
        path = save_design(f"{request} | edit: {instruction}", d)
        entry.designs.append(path)
        history.save()
        number = int(os.path.basename(path).split("-", 1)[0])
        server.tell(f"Updated {entry.name}; saved as design #{number}. !undo reverts this edit.", target)
    return entry


def undo(server: MinecraftServer, history: History, log: Log = print) -> str:
    """Revert the last edit if the last build was edited, else remove the last build."""
    entry = history.last()
    if entry is not None and entry.editable and len(entry.designs) > 1 and all(map(os.path.exists, entry.designs[-2:])):
        current = _placed(entry, run_script_isolated(read_design(entry.designs[-1])[1]))
        previous = _placed(entry, run_script_isolated(read_design(entry.designs[-2])[1]))
        builder = SiteBuilder.resume(server, history, entry, current, rate=0, log=log)
        try:
            builder.apply(previous)
        finally:
            entry.designs.pop()
            builder.finish(previous.name if previous.name != "design" else entry.name)
        return f"Reverted the last edit of {entry.name}"
    entry = undo_last(server, history, progress=log)
    return f"Removed {entry.name}"


def _box_build(box_min: Pos, box_max: Pos) -> Build:
    b = Build()
    b.set(*box_min, "stone")
    b.set(*box_max, "stone")
    return b
