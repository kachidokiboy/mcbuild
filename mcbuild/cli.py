"""Command-line entry point: `mcbuild <command>`."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import List, Optional

from .blocks import ensure_catalog
from .config import STATE_DIR, anthropic_client, save_api_key
from .demo import demo_hut
from .designer import DesignError, design
from .executor import BuildAborted
from .gallery import SAMPLES, gallery, sample
from .history import History
from .listen import HELP, ChatInbox, follow
from .model import Build
from .pipeline import (Cancelled, PlaceOptions, ai_build, edit_last, find_design, list_designs, load_design,
                       place_in_front, save_design, undo)
from .rcon import RconClient, RconError
from .sandbox import ScriptError
from .sequence import build_order
from .server import CommandError, MinecraftServer
from .serversetup import read_properties, setup_server


def _connect(args) -> RconClient:
    props = read_properties(os.path.join(args.server_dir, "server.properties"))
    host = args.host or os.environ.get("MCBUILD_RCON_HOST", "localhost")
    port = int(args.port or os.environ.get("MCBUILD_RCON_PORT") or props.get("rcon.port", 25575))
    password = args.password or os.environ.get("MCBUILD_RCON_PASSWORD") or props.get("rcon.password")
    if not password:
        raise RconError(
            f"No RCON password found. Run `mcbuild setup-server`, pass --password, "
            f"or set MCBUILD_RCON_PASSWORD (looked in {args.server_dir}/server.properties)."
        )
    client = RconClient(host, port, password)
    client.connect()
    return client


def _history() -> History:
    return History(os.path.join(STATE_DIR, "history.json"))


def _options(args) -> PlaceOptions:
    return PlaceOptions(player=args.player, at=tuple(args.at) if args.at else None, facing=args.facing,
                        distance=args.distance, rate=args.rate, backup=not args.no_backup)


def _place(args, design_build: Build, design_path: Optional[str] = None) -> None:
    if args.dry_run:
        order = build_order(design_build)
        print(f"{design_build.name}: {len(design_build)} blocks, bounds {design_build.bounds()}")
        for pos, block in order[: args.dry_run]:
            print("setblock {} {} {} {}".format(*pos, block))
        return
    with _connect(args) as rcon:
        entry = place_in_front(MinecraftServer(rcon), design_build, _history(), _options(args),
                               design_path=design_path)
    print(f"Build #{entry.id} placed at {tuple(entry.min)}..{tuple(entry.max)}")


def cmd_ping(args) -> None:
    with _connect(args) as rcon:
        players = MinecraftServer(rcon).players()
    print("Connected to RCON.")
    print(f"Players online: {', '.join(players) if players else '(none)'}")


def cmd_demo(args) -> None:
    _place(args, demo_hut("north"))


def cmd_gallery(args) -> None:
    _place(args, sample(args.sample) if args.sample else gallery())


def cmd_build(args) -> None:
    request = " ".join(args.request)
    client = anthropic_client()
    catalog = ensure_catalog(args.server_dir)
    if args.dry_run:
        d = design(request, client, catalog)
        path = save_design(request, d)
        print(f"Design '{d.build.name}' ({len(d.build)} blocks, bounds {d.build.bounds()}) saved to {path}")
        print(f"Build it with: mcbuild script {os.path.basename(path).split('-', 1)[0].lstrip('0')}")
        return
    entry = ai_build(request, lambda: _connect(args), client, catalog, _history(), _options(args))
    print(f"Build #{entry.id} placed at {tuple(entry.min)}..{tuple(entry.max)}")


def _resolve_design_path(query: str) -> str:
    return query if os.path.exists(query) else find_design(query).path


def cmd_script(args) -> None:
    path = _resolve_design_path(args.design)
    _place(args, load_design(path, ensure_catalog(args.server_dir)), design_path=path)


def cmd_edit(args) -> None:
    instruction = " ".join(args.instruction)
    entry = edit_last(instruction, lambda: _connect(args), anthropic_client(), ensure_catalog(args.server_dir),
                      _history(), PlaceOptions(player=args.player, rate=args.rate))
    print(f"Edited build #{entry.id} '{entry.name}' (version {len(entry.designs)})")


def cmd_designs(args) -> None:
    designs = list_designs()
    if not designs:
        print("No saved designs yet. Make one with `mcbuild build ...` or !build in chat.")
    for d in designs:
        print(f"#{d.number:<4} {d.name:<30} {d.request}")


def _recent_designs_message(limit: int = 8) -> str:
    designs = list_designs()[-limit:]
    if not designs:
        return "No saved designs yet. Make one with !build <description>."
    return "Saved designs: " + ", ".join(f"#{d.number} {d.name}" for d in reversed(designs)) + \
        ". Use !rebuild <number> to build one again."


def cmd_listen(args) -> None:
    client = anthropic_client()
    catalog = ensure_catalog(args.server_dir)
    history = _history()
    log_path = os.path.join(args.server_dir, "logs", "latest.log")
    connect = lambda: _connect(args)  # noqa: E731
    with connect() as rcon:
        MinecraftServer(rcon).tell("mcbuild is listening. " + HELP)
    print(f"Listening for !build commands in Minecraft chat (watching {log_path}). Press Ctrl+C to stop.")
    inbox = ChatInbox(follow(log_path))
    while True:
        player, name, rest = inbox.get()
        print(f"<{player}> !{name} {rest}")
        try:
            _handle_chat(args, player, name, rest, inbox, connect, client, catalog, history)
        except Cancelled as e:
            print(e)
        except (RconError, CommandError, BuildAborted, DesignError, ScriptError, RuntimeError, ValueError,
                OSError) as e:
            print(f"Error: {e}", file=sys.stderr)
            try:
                with connect() as rcon:
                    MinecraftServer(rcon).tell(f"Error: {e}", player)
            except RconError:
                pass


def _handle_chat(args, player, name, rest, inbox, connect, client, catalog, history) -> None:
    opts = PlaceOptions(player=player, distance=args.distance, rate=args.rate, backup=not args.no_backup)
    if name == "build" and rest:
        def confirm(site) -> bool:
            return inbox.wait_for(player, ("go", "cancel"), timeout=args.confirm_timeout) == "go"

        ai_build(rest, connect, client, catalog, history, opts, confirm=None if args.no_confirm else confirm)
    elif name == "rebuild" and rest:
        saved = find_design(rest)
        build = load_design(saved.path, catalog)
        with connect() as rcon:
            server = MinecraftServer(rcon)
            server.tell(f"Rebuilding design #{saved.number} {build.name} ({len(build)} blocks).", player)
            place_in_front(server, build, history, opts, design_path=saved.path)
    elif name == "edit" and rest:
        edit_last(rest, connect, client, catalog, history, opts)
    elif name == "designs":
        with connect() as rcon:
            MinecraftServer(rcon).tell(_recent_designs_message(), player)
    elif name == "undo":
        with connect() as rcon:
            server = MinecraftServer(rcon)
            message = undo(server, history)
            server.tell(message + ".", player)
        print(message)
    elif name in ("go", "cancel"):
        with connect() as rcon:
            MinecraftServer(rcon).tell("There's no planned build waiting; start one with !build.", player)
    else:
        with connect() as rcon:
            MinecraftServer(rcon).tell(HELP, player)


def cmd_undo(args) -> None:
    with _connect(args) as rcon:
        print(undo(MinecraftServer(rcon), _history()))


def cmd_history(args) -> None:
    history = _history()
    if not history.entries:
        print("No builds yet.")
    for e in history.entries:
        undo = "" if e.backup else "  (no backup)"
        print(f"#{e.id:<4} {e.created}  {e.name:<20} {e.blocks:>7} blocks  {tuple(e.min)}..{tuple(e.max)}{undo}")


def cmd_setup_server(args) -> None:
    setup_server(args.dir, version=args.version, flat=args.flat, accept_eula=args.accept_eula,
                 jar=args.jar, memory=args.memory)


def cmd_set_key(args) -> None:
    key = getpass.getpass("Paste your Anthropic API key (it won't be shown), then press Return: ").strip()
    if not key.startswith("sk-"):
        raise ValueError("That doesn't look like an API key (they start with sk-). Nothing was saved.")
    print(f"Saved to {save_api_key(key)} (only readable by you; not committed to git).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcbuild", description="AI building builder for Minecraft")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_connection(p):
        p.add_argument("--host", help="RCON host (default: localhost)")
        p.add_argument("--port", type=int, help="RCON port (default: from server.properties)")
        p.add_argument("--password", help="RCON password (default: from server.properties)")
        p.add_argument("--server-dir", default="server", help="Server folder to read settings from")

    def add_placement(p, rate=150):
        add_connection(p)
        p.add_argument("--player", help="Player to build in front of (default: the only player online)")
        p.add_argument("--at", nargs=3, type=int, metavar=("X", "Y", "Z"), help="Build here instead of near a player")
        p.add_argument("--facing", choices=["north", "south", "east", "west"],
                       help="Direction you're looking (default: read from the game)")
        p.add_argument("--distance", type=int, default=3, help="Blocks between you and the build")
        p.add_argument("--rate", type=float, default=rate, help="Blocks per second (0 = as fast as possible)")
        p.add_argument("--no-backup", action="store_true", help="Skip the undo backup")
        p.add_argument("--dry-run", type=int, nargs="?", const=20, metavar="N",
                       help="Don't connect; print the first N commands")

    p = sub.add_parser("setup-server", help="Download and configure a local Minecraft server")
    p.add_argument("--dir", default="server")
    p.add_argument("--version", default="latest", help="Minecraft version, e.g. 1.21.8 (default: latest release)")
    p.add_argument("--flat", action="store_true", help="Create a superflat world (good for testing)")
    p.add_argument("--accept-eula", action="store_true", help="Accept the Minecraft EULA (https://aka.ms/MinecraftEULA)")
    p.add_argument("--jar", help="Use an already-downloaded server.jar instead of downloading")
    p.add_argument("--memory", default="4G", help="Max server memory (default 4G)")
    p.set_defaults(func=cmd_setup_server)

    p = sub.add_parser("set-key", help="Save your Anthropic API key for AI builds")
    p.set_defaults(func=cmd_set_key)

    p = sub.add_parser("ping", help="Check the RCON connection and list players")
    add_connection(p)
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("listen", help="Take !build requests from Minecraft chat")
    add_connection(p)
    p.add_argument("--distance", type=int, default=3, help="Blocks between the player and the build")
    p.add_argument("--rate", type=float, default=300, help="Blocks per second")
    p.add_argument("--no-backup", action="store_true", help="Skip undo backups")
    p.add_argument("--no-confirm", action="store_true",
                   help="Don't wait for !go after the site is planned; design and build straight away")
    p.add_argument("--confirm-timeout", type=float, default=300,
                   help="Seconds to wait for !go before cancelling (default 300)")
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("build", help='Design a building with AI and build it, e.g. mcbuild build "a stone tower"')
    add_placement(p, rate=300)
    p.add_argument("request", nargs="+", help="What to build")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("script", help="Build a saved design again without the AI (free)")
    add_placement(p, rate=300)
    p.add_argument("design", help="Design number or name (see `mcbuild designs`), or a script file")
    p.set_defaults(func=cmd_script)

    p = sub.add_parser("designs", help="List saved designs")
    p.set_defaults(func=cmd_designs)

    p = sub.add_parser("demo", help="Build a test hut in front of you")
    add_placement(p)
    p.set_defaults(func=cmd_demo)

    names = ", ".join(n.replace(" ", "-") for n, _, _ in SAMPLES)
    p = sub.add_parser("gallery", help="Build a row of sample buildings from the building library")
    add_placement(p)
    p.add_argument("--sample", help=f"Build just one sample: {names}")
    p.set_defaults(func=cmd_gallery)

    p = sub.add_parser("edit", help='Change the last build with AI, e.g. mcbuild edit "make the towers taller"')
    add_connection(p)
    p.add_argument("--player", help="Player to send progress messages to")
    p.add_argument("--rate", type=float, default=300, help="Blocks per second")
    p.add_argument("instruction", nargs="+", help="What to change")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("undo", help="Revert the last edit, or remove the most recent build")
    add_connection(p)
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("history", help="List builds that can be undone")
    p.set_defaults(func=cmd_history)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nStopped. Run `mcbuild undo` to revert a partial build.", file=sys.stderr)
        return 130
    except (RconError, CommandError, BuildAborted, DesignError, ScriptError, RuntimeError, ValueError,
            OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
