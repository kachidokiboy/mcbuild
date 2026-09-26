import os

import pytest

from mcbuild.cli import main
from mcbuild.demo import demo_hut
from mcbuild.executor import BuildAborted, place_build, undo_last
from mcbuild.history import History
from mcbuild.model import Build, normalize_block
from mcbuild.placement import offset_in_front, yaw_to_direction
from mcbuild.rcon import RconAuthError, RconClient
from mcbuild.sequence import build_order, needs_support
from mcbuild.server import MinecraftServer, parse_entity_list, parse_player_list, split_box
from mcbuild.serversetup import merge_properties, read_properties, setup_server

from .fake_minecraft import FakeMinecraft, FakeRconServer


# --- model & ordering ---------------------------------------------------------------------

def test_normalize_block():
    assert normalize_block("Oak_Planks") == "minecraft:oak_planks"
    assert normalize_block("oak_stairs[facing=east, half=top]") == "minecraft:oak_stairs[facing=east,half=top]"
    with pytest.raises(ValueError):
        normalize_block("oak planks!")


def test_fill_hollow():
    b = Build()
    b.fill((0, 0, 0), (2, 2, 2), "stone", hollow=True)
    assert len(b) == 26 and (1, 1, 1) not in b.blocks


def test_build_order_bottom_up_and_attachments_last():
    order = build_order(demo_hut())
    structural = [(p, blk) for p, blk in order if not needs_support(blk)]
    ys = [p[1] for p, _ in structural]
    assert ys == sorted(ys)
    first_attachment = next(i for i, (_, blk) in enumerate(order) if needs_support(blk))
    assert all(needs_support(blk) for _, blk in order[first_attachment:])
    doors = [p for p, blk in order if "door" in blk]
    assert doors[0][1] < doors[1][1]  # lower half before upper half


def test_needs_support():
    assert needs_support("minecraft:wall_torch[facing=north]")
    assert needs_support("minecraft:oak_door[half=lower]")
    assert not needs_support("minecraft:oak_planks")
    assert not needs_support("minecraft:glass_pane")


# --- parsing & geometry ---------------------------------------------------------------------

def test_parse_responses():
    assert parse_entity_list("Steve has the following entity data: [1.5d, -60.0d, 3.25d]") == [1.5, -60.0, 3.25]
    assert parse_entity_list("Steve has the following entity data: [-90.0f, 1.2E-4f]") == [-90.0, 1.2e-4]
    assert parse_player_list("There are 2 of a max of 20 players online: Steve, Alex") == ["Steve", "Alex"]
    assert parse_player_list("There are 0 of a max of 20 players online: ") == []


def test_split_box_respects_limit():
    for pmin, pmax in [((0, 0, 0), (99, 40, 99)), ((0, 0, 0), (255, 3, 255)), ((5, 5, 5), (5, 5, 5))]:
        boxes = list(split_box(pmin, pmax))
        total = 0
        for a, b in boxes:
            vol = (b[0] - a[0] + 1) * (b[1] - a[1] + 1) * (b[2] - a[2] + 1)
            assert vol <= 32768
            total += vol
        full = (pmax[0] - pmin[0] + 1) * (pmax[1] - pmin[1] + 1) * (pmax[2] - pmin[2] + 1)
        assert total == full


@pytest.mark.parametrize("yaw,expected", [(0, "south"), (-45.1, "east"), (90, "west"), (180, "north"),
                                          (-180, "north"), (270, "east"), (359, "south")])
def test_yaw_to_direction(yaw, expected):
    assert yaw_to_direction(yaw) == expected


@pytest.mark.parametrize("facing", ["north", "south", "east", "west"])
def test_offset_in_front(facing):
    b = Build()
    b.fill((0, 0, 0), (4, 2, 6), "stone")  # 5 wide in x, 7 deep in z
    dx, dy, dz = offset_in_front(b, (10.7, 64.0, -3.2), facing, distance=3)
    (x1, y1, z1), (x2, _, z2) = b.translated(dx, dy, dz).bounds()
    px, pz = 10, -4
    assert y1 == 64
    if facing == "south":
        assert z1 == pz + 3 and x1 <= px <= x2
    if facing == "north":
        assert z2 == pz - 3 and x1 <= px <= x2
    if facing == "east":
        assert x1 == px + 3 and z1 <= pz <= z2
    if facing == "west":
        assert x2 == px - 3 and z1 <= pz <= z2


# --- RCON over a real socket ----------------------------------------------------------------

def test_rcon_roundtrip_and_auth():
    game = FakeMinecraft()
    srv = FakeRconServer(game)
    try:
        with RconClient("127.0.0.1", srv.port, "secret") as rcon:
            assert "Steve" in rcon.command("list")
            assert rcon.command("setblock 1 70 1 minecraft:stone").startswith("Changed the block")
        with pytest.raises(RconAuthError):
            RconClient("127.0.0.1", srv.port, "wrong").connect()
    finally:
        srv.close()


# --- build & undo ---------------------------------------------------------------------------

def test_place_and_undo_restores_world(tmp_path):
    game = FakeMinecraft()
    server = MinecraftServer(game)
    history = History(str(tmp_path / "history.json"))
    hut = demo_hut("north")
    hut = hut.translated(*offset_in_front(hut, (10.5, 64.0, 20.5), "south"))
    before = {p: game.block(p) for p, _ in hut}

    entry = place_build(server, hut, history, rate=0, progress=lambda m: None)
    assert all(game.block(p) == blk for p, blk in hut)
    assert not game.forceloaded  # backup chunks released
    assert History(str(tmp_path / "history.json")).last().id == entry.id

    undo_last(server, history, progress=lambda m: None)
    assert all(game.block(p) == before[p] for p in before)
    assert history.last() is None
    assert not game.forceloaded


def test_undo_restores_older_build_when_builds_overlap(tmp_path):
    game = FakeMinecraft()
    server = MinecraftServer(game)
    history = History(str(tmp_path / "h.json"))
    a, b = Build("a"), Build("b")
    a.fill((0, 64, 0), (4, 66, 4), "stone")
    b.fill((2, 64, 2), (6, 66, 6), "oak_planks")
    quiet = lambda m: None
    place_build(server, a, history, rate=0, progress=quiet)
    place_build(server, b, history, rate=0, progress=quiet)
    undo_last(server, history, progress=quiet)
    assert game.block((3, 65, 3)) == "minecraft:stone"
    undo_last(server, history, progress=quiet)
    assert game.block((3, 65, 3)) == "minecraft:air"


def test_bad_blocks_abort(tmp_path):
    game = FakeMinecraft()
    b = Build("broken")
    b.fill((0, 64, 0), (3, 64, 3), "bogus_block")
    with pytest.raises(BuildAborted):
        place_build(MinecraftServer(game), b, History(str(tmp_path / "h.json")), rate=0,
                    progress=lambda m: None, max_errors=3)


def test_rejects_out_of_world_builds(tmp_path):
    b = Build()
    b.set(0, 400, 0, "stone")
    with pytest.raises(ValueError):
        place_build(MinecraftServer(FakeMinecraft()), b, History(str(tmp_path / "h.json")), rate=0)


def test_cli_demo_and_undo_end_to_end(tmp_path, monkeypatch):
    game = FakeMinecraft(players={"Alex": ((0.5, 64.0, 0.5), (180.0, 0.0))})  # facing north
    srv = FakeRconServer(game, password="pw")
    monkeypatch.chdir(tmp_path)
    try:
        conn = ["--port", str(srv.port), "--password", "pw"]
        assert main(["demo", "--rate", "0", *conn]) == 0
        placed = [p for p, blk in game.world.items() if "oak_door" in blk]
        assert placed and all(p[2] < 0 for p in placed)  # built to the north (negative z)
        assert main(["history"]) == 0
        assert main(["undo", *conn]) == 0
        assert all("oak" not in game.block(p) for p in list(game.world) if abs(p[0]) < 50)
        assert main(["undo", *conn]) == 1  # nothing left to undo
    finally:
        srv.close()


# --- server setup ---------------------------------------------------------------------------

def test_setup_server_with_fake_download(tmp_path):
    import hashlib
    import json

    jar = b"fake jar bytes"
    urls = {
        "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json": json.dumps(
            {"latest": {"release": "1.99"}, "versions": [{"id": "1.99", "url": "v.json"}]}).encode(),
        "v.json": json.dumps({"javaVersion": {"majorVersion": 21}, "downloads": {"server": {
            "url": "jar", "sha1": hashlib.sha1(jar).hexdigest()}}}).encode(),
        "jar": jar,
    }
    d = str(tmp_path / "server")
    setup_server(d, flat=True, accept_eula=True, fetch=urls.__getitem__, log=lambda m: None)
    props = read_properties(os.path.join(d, "server.properties"))
    assert props["enable-rcon"] == "true" and props["broadcast-rcon-to-ops"] == "false"
    assert len(props["rcon.password"]) > 10
    assert open(os.path.join(d, "server.jar"), "rb").read() == jar
    assert "eula=true" in open(os.path.join(d, "eula.txt")).read()
    assert os.access(os.path.join(d, "start.sh"), os.X_OK)

    # Re-running keeps the password.
    password = props["rcon.password"]
    setup_server(d, fetch=urls.__getitem__, log=lambda m: None)
    assert read_properties(os.path.join(d, "server.properties"))["rcon.password"] == password


def test_merge_properties_preserves_other_lines(tmp_path):
    path = str(tmp_path / "server.properties")
    with open(path, "w") as f:
        f.write("#comment\nmotd=hello\nlevel-type=minecraft\\:normal\nenable-rcon=false\n")
    merge_properties(path, {"enable-rcon": "true", "level-type": "flat", "rcon.port": "1"},
                     keep_existing=("level-type",))
    text = open(path).read()
    assert "#comment" in text and "motd=hello" in text
    assert "enable-rcon=true" in text and "level-type=minecraft\\:normal" in text and "rcon.port=1" in text
