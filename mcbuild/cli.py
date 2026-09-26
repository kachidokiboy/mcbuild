"""Command-line entry point: `mcbuild <command>`."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .demo import demo_hut
from .executor import BuildAborted, place_build, undo_last
from .history import History
from .placement import OPPOSITE, offset_in_front, yaw_to_direction
from .rcon import RconClient, RconError
from .sequence import build_order
from .server import CommandError, MinecraftServer
from .serversetup import read_properties, setup_server

STATE_DIR = ".mcbuild"


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


def cmd_ping(args) -> None:
    with _connect(args) as rcon:
        server = MinecraftServer(rcon)
        players = server.players()
        print("Connected to RCON.")
        print(f"Players online: {', '.join(players) if players else '(none)'}")


def cmd_demo(args) -> None:
    if args.dry_run:
        build = demo_hut(args.door or "north")
        order = build_order(build)
        print(f"{build.name}: {len(build)} blocks, bounds {build.bounds()}")
        for pos, block in order[: args.dry_run]:
            print("setblock {} {} {} {}".format(*pos, block))
        return

    with _connect(args) as rcon:
        server = MinecraftServer(rcon)
        if args.at:
            origin, facing = tuple(args.at), args.facing or "south"
        else:
            player = server.resolve_player(args.player)
            origin = server.player_position(player)
            facing = args.facing or yaw_to_direction(server.player_yaw(player))
            print(f"{player} is at {tuple(round(v, 1) for v in origin)}, facing {facing}")
        # Door faces back toward the player.
        build = demo_hut(args.door or OPPOSITE[facing])
        build = build.translated(*offset_in_front(build, origin, facing, args.distance))
        history = History(os.path.join(STATE_DIR, "history.json"))
        entry = place_build(server, build, history, rate=args.rate, backup=not args.no_backup)
        print(f"Build #{entry.id} placed at {tuple(entry.min)}..{tuple(entry.max)}")


def cmd_undo(args) -> None:
    history = History(os.path.join(STATE_DIR, "history.json"))
    with _connect(args) as rcon:
        entry = undo_last(MinecraftServer(rcon), history)
    print(f"Undid build #{entry.id} '{entry.name}'")


def cmd_history(args) -> None:
    history = History(os.path.join(STATE_DIR, "history.json"))
    if not history.entries:
        print("No builds yet.")
    for e in history.entries:
        undo = "" if e.backup else "  (no backup)"
        print(f"#{e.id:<4} {e.created}  {e.name:<20} {e.blocks:>7} blocks  {tuple(e.min)}..{tuple(e.max)}{undo}")


def cmd_setup_server(args) -> None:
    setup_server(args.dir, version=args.version, flat=args.flat, accept_eula=args.accept_eula,
                 jar=args.jar, memory=args.memory)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcbuild", description="AI building builder for Minecraft")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_connection(p):
        p.add_argument("--host", help="RCON host (default: localhost)")
        p.add_argument("--port", type=int, help="RCON port (default: from server.properties)")
        p.add_argument("--password", help="RCON password (default: from server.properties)")
        p.add_argument("--server-dir", default="server", help="Server folder to read settings from")

    p = sub.add_parser("setup-server", help="Download and configure a local Minecraft server")
    p.add_argument("--dir", default="server")
    p.add_argument("--version", default="latest", help="Minecraft version, e.g. 1.21.8 (default: latest release)")
    p.add_argument("--flat", action="store_true", help="Create a superflat world (good for testing)")
    p.add_argument("--accept-eula", action="store_true", help="Accept the Minecraft EULA (https://aka.ms/MinecraftEULA)")
    p.add_argument("--jar", help="Use an already-downloaded server.jar instead of downloading")
    p.add_argument("--memory", default="4G", help="Max server memory (default 4G)")
    p.set_defaults(func=cmd_setup_server)

    p = sub.add_parser("ping", help="Check the RCON connection and list players")
    add_connection(p)
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("demo", help="Build a test hut in front of you")
    add_connection(p)
    p.add_argument("--player", help="Player to build in front of (default: the only player online)")
    p.add_argument("--at", nargs=3, type=int, metavar=("X", "Y", "Z"), help="Build here instead of near a player")
    p.add_argument("--facing", choices=["north", "south", "east", "west"], help="Override the build direction")
    p.add_argument("--door", choices=["north", "south", "east", "west"], help="Which wall gets the door")
    p.add_argument("--distance", type=int, default=3, help="Blocks between you and the build")
    p.add_argument("--rate", type=float, default=150, help="Blocks per second (0 = as fast as possible)")
    p.add_argument("--no-backup", action="store_true", help="Skip the undo backup")
    p.add_argument("--dry-run", type=int, nargs="?", const=20, metavar="N",
                   help="Don't connect; print the first N commands")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("undo", help="Revert the most recent build")
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
        print("\nInterrupted. Run `mcbuild undo` to revert a partial build.", file=sys.stderr)
        return 130
    except (RconError, CommandError, BuildAborted, RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
