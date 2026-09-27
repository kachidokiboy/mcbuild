import os
import threading
import time
from types import SimpleNamespace

import pytest

from mcbuild import designer
from mcbuild.blocks import BlockCatalog, check_blocks
from mcbuild.designer import DesignError, design, system_prompt
from mcbuild.history import History
from mcbuild.listen import follow, parse_chat, parse_command
from mcbuild.pipeline import PlaceOptions, ai_build
from mcbuild.rcon import RconClient
from mcbuild.sandbox import ScriptError, run_script, run_script_isolated

from .fake_minecraft import FakeMinecraft, FakeRconServer

GOOD_SCRIPT = """
b.name = "Tiny Tower"
square_tower(b, (0, 0, 0), size=5, height=6, block="stone_bricks")
for i in range(3):
    b.set(i, 0, -2, "oak_planks")
"""

CATALOG = BlockCatalog({
    "minecraft:air": {},
    "minecraft:stone_bricks": {},
    "minecraft:yellow_carpet": {},
    "minecraft:white_carpet": {},
    "minecraft:oak_planks": {},
    "minecraft:glass_pane": {"properties": {p: ["true", "false"] for p in ("north", "south", "east", "west", "waterlogged")}},
    "minecraft:oak_door": {"properties": {"facing": ["north", "south", "east", "west"], "half": ["upper", "lower"],
                                          "hinge": ["left", "right"], "open": ["true", "false"],
                                          "powered": ["true", "false"]}},
})


# --- sandbox --------------------------------------------------------------------------------

def test_sandbox_runs_library_scripts():
    b = run_script(GOOD_SCRIPT)
    assert b.name == "Tiny Tower" and len(b) > 100
    assert b.blocks[(1, 0, -2)] == "minecraft:oak_planks"


def test_isolated_run_matches_in_process():
    assert run_script_isolated(GOOD_SCRIPT).blocks == run_script(GOOD_SCRIPT).blocks


@pytest.mark.parametrize("script,message", [
    ("import os", "imports are not allowed"),
    ("from math import pi", "imports are not allowed"),
    ("x = b.__class__", "private attributes"),
    ("x = __builtins__", "not allowed"),
    ("open('x')", "NameError"),
    ("b.set(0, 0, 0, 'stone')\nbox(b, (0,0,0), (1,1,1), 'not a block!')", "Line 2"),
    ("def f(:\n  pass", "SyntaxError"),
])
def test_sandbox_rejects_and_reports(script, message):
    with pytest.raises(ScriptError, match=message):
        run_script_isolated(script)


def test_sandbox_times_out():
    with pytest.raises(ScriptError, match="longer than"):
        run_script_isolated("while True:\n    pass", timeout=2)


def test_prompt_examples_run_in_sandbox():
    """The gallery code shown to Claude as examples must work under the same rules."""
    examples = designer._examples()
    run_script(examples + "\ncottage(b, 0, 0)\nwizard_tower(b, 20, 0)\nstair_platform(b, 40, 0)")


def test_system_prompt_mentions_every_api_function():
    prompt = system_prompt()
    for name in ("gable_roof(", "round_tower(", "battlements(", "stone_brick", "FRONT"):
        assert name in prompt


# --- block catalog --------------------------------------------------------------------------

def test_block_catalog_checks():
    assert CATALOG.check("minecraft:oak_planks") is None
    assert CATALOG.check("minecraft:glass_pane[east=true,west=true]") is None
    assert "did you mean oak_planks" in CATALOG.check("minecraft:oak_plank")
    assert "no state 'facing'" in CATALOG.check("minecraft:glass_pane[facing=north]")
    assert "use: north, south" in CATALOG.check("minecraft:oak_door[facing=up]")
    assert len(check_blocks(["minecraft:nope"] * 5 + ["minecraft:air"], CATALOG)) == 1


# --- designer with a fake Claude ------------------------------------------------------------

class FakeClaude:
    """Stands in for anthropic.Anthropic: streams queued replies and records requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = SimpleNamespace(stream=self._stream)
        self._lock = threading.Lock()

    def _stream(self, **kwargs):
        with self._lock:
            self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
            reply = self.replies.pop(0)
        stop, text = reply if isinstance(reply, tuple) else ("end_turn", reply)
        message = SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])
        events = [SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="thinking")),
                  SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text"))]
        events += [SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=line))
                   for line in text.splitlines(keepends=True)]

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter(events)

            def get_final_message(self):
                return message
        return Stream()


def code(script):
    return f"Here's the plan.\n```python\n{script}\n```\n"


def test_design_fixes_invalid_blocks_on_second_attempt():
    bad = GOOD_SCRIPT + "\nb.set(9, 0, 9, 'oak_plank')"
    claude = FakeClaude([code(bad), code(GOOD_SCRIPT)])
    d = design("a tiny tower", claude, CATALOG, log=lambda m: None)
    assert d.attempts == 2 and d.build.name == "Tiny Tower"
    followup = claude.requests[1]["messages"][-1]["content"]
    assert "oak_plank" in followup and "did you mean oak_planks" in followup
    first = claude.requests[0]
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"}
    assert "betas" not in first and "fallbacks" not in first.get("extra_body", {})
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_design_reports_script_errors_and_missing_code():
    claude = FakeClaude(["I'd build a tower.", code("walls(b)"), code(GOOD_SCRIPT)])
    d = design("tower", claude, None, log=lambda m: None)
    assert d.attempts == 3
    assert "no ```python" in claude.requests[1]["messages"][-1]["content"]
    assert "TypeError" in claude.requests[2]["messages"][-1]["content"]


def test_design_gives_up_and_handles_refusal():
    with pytest.raises(DesignError, match="after 2 attempts"):
        design("x", FakeClaude([code("pass"), code("pass")]), None, log=lambda m: None, max_attempts=2)
    with pytest.raises(DesignError, match="declined"):
        design("x", FakeClaude([("refusal", "")]), None, log=lambda m: None)


# --- chat listener --------------------------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("[12:00:01] [Server thread/INFO]: <Chris> !build a cozy cottage", ("Chris", "!build a cozy cottage")),
    ("[12:00:01] [Server thread/INFO]: [Not Secure] <Chris_2> hello", ("Chris_2", "hello")),
    ("[12:00:01] [Server thread/INFO]: Chris joined the game", None),
    ("[12:00:01] [Server thread/INFO]: [Rcon] <Chris> !build x", None),
])
def test_parse_chat(line, expected):
    assert parse_chat(line) == expected


def test_parse_command():
    assert parse_command("!build  a big castle ") == ("build", "a big castle")
    assert parse_command("!UNDO") == ("undo", "")
    assert parse_command("hello") is None


def test_follow_skips_history_and_survives_restart(tmp_path):
    log = tmp_path / "latest.log"
    log.write_text("old line\n")
    seen, done = [], threading.Event()

    def reader():
        for line in follow(str(log), poll=0.02, should_stop=done.is_set):
            seen.append(line)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.2)
    with open(log, "a") as f:
        f.write("new line\npart")
    time.sleep(0.2)
    with open(log, "a") as f:
        f.write("ial\n")
    time.sleep(0.2)
    os.remove(log)  # server restart: a fresh latest.log
    log.write_text("after restart\n")
    time.sleep(0.3)
    done.set()
    t.join(2)
    assert seen == ["new line", "partial", "after restart"]


# --- whole pipeline -------------------------------------------------------------------------

def _site(name, width, depth, height, parts=()):
    import json
    return "```json\n" + json.dumps({"name": name, "width": width, "depth": depth, "height": height,
                                     "parts": list(parts)}) + "\n```"


def _run_ai_build(tmp_path, monkeypatch, replies, request="a tiny tower"):
    monkeypatch.chdir(tmp_path)
    game = FakeMinecraft(players={"Chris": ((0.5, 64.0, 0.5), (0.0, 0.0))})  # facing south
    srv = FakeRconServer(game, password="pw")

    def connect():
        client = RconClient("127.0.0.1", srv.port, "pw")
        client.connect()
        return client

    claude = FakeClaude(replies)
    try:
        entry = ai_build(request, connect, claude, CATALOG, History("h.json"),
                         PlaceOptions(player="Chris", rate=0), log=lambda m: None)
    finally:
        srv.close()
    return game, claude, entry


def test_ai_build_small_building_skips_structure_pass(tmp_path, monkeypatch):
    game, claude, entry = _run_ai_build(tmp_path, monkeypatch, [_site("Tiny Tower", 5, 5, 8), code(GOOD_SCRIPT)])
    assert len(claude.requests) == 2  # site plan, then the full design
    assert claude.requests[0]["extra_body"]["output_config"]["effort"] == "low"
    assert "PASS 1 OF 3" in claude.requests[0]["messages"][0]["content"]
    assert entry.name == "Tiny Tower"
    assert all(z > 0 for (x, y, z), blk in game.world.items() if "stone_bricks" in blk and y >= 64)
    assert not any("carpet" in blk for blk in game.world.values())  # site markers were removed
    tells = [c for c in game.log if c.startswith("tellraw Chris")]
    assert any("Designing" in t for t in tells) and any("Finished Tiny Tower" in t for t in tells)
    assert any(c.startswith("bossbar add mcbuild:progress") for c in game.log)
    assert game.log[-1] == "bossbar remove mcbuild:progress" or "bossbar remove mcbuild:progress" in game.log
    saved = os.listdir(".mcbuild/designs")
    assert len(saved) == 1 and saved[0].endswith("tiny-tower.py")
    assert open(f".mcbuild/designs/{saved[0]}").read().startswith("# Request: a tiny tower")


STRUCTURE = """
b.name = "Big Hall"
# == Hall ==
walls(b, (0, 0, 0), (29, 5, 21), "stone_bricks")
box(b, (0, -1, 0), (29, -1, 21), "oak_planks")
"""

DETAIL = STRUCTURE + """
# == Entrance ==
opening(b, (14, 0, 0), "x", width=2, height=3)
window(b, (4, 2, 0), "x", width=3)
"""


def test_ai_build_three_passes_removes_unused_blocks(tmp_path, monkeypatch):
    replies = [_site("Big Hall", 30, 22, 7, [{"name": "Hall", "x1": 0, "z1": 0, "x2": 29, "z2": 21}]),
               code(STRUCTURE), code(DETAIL)]
    game, claude, entry = _run_ai_build(tmp_path, monkeypatch, replies, "a big hall")
    assert len(claude.requests) == 3
    structure_req, detail_req = (r["messages"][0]["content"] for r in claude.requests[1:])
    assert "PASS 2 OF 3" in structure_req and "PASS 3 OF 3" in detail_req and "walls(b" in detail_req

    final = run_script(DETAIL)
    stone = sorted(p for p, blk in game.world.items() if blk == "minecraft:stone_bricks" and p[1] >= 64)
    panes = [p for p, blk in game.world.items() if "glass_pane" in blk]
    assert len(panes) == 3 * 2
    # The doorway: 2 wide x 3 tall of the front wall is open again, the rest of the wall stands.
    front_z = min(p[2] for p in stone)
    front = {(p[0], p[1]) for p in stone if p[2] == front_z}
    xs = sorted({x for x, _ in front})
    assert len(xs) == 30
    gaps = [(x, y) for x in xs for y in range(64, 70) if (x, y) not in front]
    assert len(gaps) == 2 * 3 + 3 * 2  # doorway (y 0-2) + window panes (y 2-3)
    # The doorway was restored from the backup: open air again, not left as stone.
    assert all(game.block((x, y, front_z)) == "minecraft:air" for x, y in gaps if (x, y, front_z) not in panes)
    assert not any("carpet" in blk for blk in game.world.values())
    assert entry.name == "Big Hall"
    assert History("h.json").last().blocks == len(final)


# --- saved designs --------------------------------------------------------------------------

def test_saved_designs_numbering_and_lookup(tmp_path, monkeypatch):
    from mcbuild.designer import Design
    from mcbuild.pipeline import find_design, list_designs, load_design, save_design

    monkeypatch.chdir(tmp_path)
    tower = Design(run_script(GOOD_SCRIPT), GOOD_SCRIPT, 1)
    save_design("a tiny tower", tower)
    save_design("another tiny tower by the lake", tower)
    os.remove(list_designs()[0].path)
    third = save_design("a tiny tower again", tower)
    assert os.path.basename(third).startswith("003-")  # numbers are never reused
    assert [d.number for d in list_designs()] == [2, 3]
    assert find_design("2").request == "another tiny tower by the lake"
    assert find_design("#3").number == 3
    assert find_design("lake").number == 2
    assert find_design("tiny tower").number == 3  # most recent match
    with pytest.raises(ValueError, match="no saved design #9"):
        find_design("9")
    with pytest.raises(ValueError, match="No saved design matches"):
        find_design("castle")
    assert load_design(third, CATALOG).name == "Tiny Tower"


def test_cli_rebuilds_saved_design_by_number(tmp_path, monkeypatch):
    from mcbuild.cli import main
    from mcbuild.designer import Design
    from mcbuild.pipeline import save_design

    monkeypatch.chdir(tmp_path)
    save_design("a tiny tower", Design(run_script(GOOD_SCRIPT), GOOD_SCRIPT, 1))
    game = FakeMinecraft(players={"Chris": ((0.5, 64.0, 0.5), (0.0, 0.0))})
    srv = FakeRconServer(game, password="pw")
    try:
        assert main(["designs"]) == 0
        assert main(["script", "1", "--rate", "0", "--port", str(srv.port), "--password", "pw"]) == 0
        assert main(["script", "tower", "--rate", "0", "--port", str(srv.port), "--password", "pw"]) == 0
    finally:
        srv.close()
    assert any("stone_bricks" in blk for blk in game.world.values())


def test_block_report_generated_outside_server_folder(tmp_path, monkeypatch):
    """The data generator writes logs/latest.log in its working folder; it must not be the
    server's, or `mcbuild listen` stops seeing chat."""
    import json
    import subprocess
    from mcbuild import blocks

    server = tmp_path / "server"
    (server / "logs").mkdir(parents=True)
    (server / "server.jar").write_text("jar")
    (server / "logs" / "latest.log").write_text("server log\n")
    calls = []

    def fake_run(cmd, cwd, **kwargs):
        calls.append(cwd)
        os.makedirs(os.path.join(cwd, "generated", "reports"))
        os.makedirs(os.path.join(cwd, "logs"))
        open(os.path.join(cwd, "logs", "latest.log"), "w").write("generator log\n")
        json.dump({"minecraft:stone": {}}, open(os.path.join(cwd, "generated", "reports", "blocks.json"), "w"))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(blocks.subprocess, "run", fake_run)
    monkeypatch.setattr(blocks, "find_java", lambda d: "java")
    catalog = blocks.ensure_catalog(str(server), log=lambda m: None)
    assert catalog.check("minecraft:stone") is None
    assert calls and os.path.abspath(calls[0]) != os.path.abspath(server)
    assert (server / "logs" / "latest.log").read_text() == "server log\n"


# --- passes: edge cases ---------------------------------------------------------------------

def test_site_builder_rebuilds_when_most_blocks_change(tmp_path):
    from mcbuild.model import Build
    from mcbuild.server import MinecraftServer
    from mcbuild.staging import SiteBuilder, _x_runs

    assert _x_runs([(3, 0, 0), (1, 0, 0), (2, 0, 0), (5, 0, 0), (1, 1, 0)]) == [
        ((1, 0, 0), (3, 0, 0)), ((5, 0, 0), (5, 0, 0)), ((1, 1, 0), (1, 1, 0))]

    game = FakeMinecraft()
    logs = []
    sb = SiteBuilder(MinecraftServer(game), History(str(tmp_path / "h.json")), (0, 60, 0), (9, 70, 9),
                     "test", rate=0, log=logs.append)
    sb.start()
    first, second = Build(), Build()
    first.fill((0, 64, 0), (9, 66, 9), "stone_bricks")
    second.fill((0, 64, 0), (9, 64, 9), "oak_planks")
    sb.apply(first)
    sb.apply(second)
    assert any("rebuilding" in m for m in logs)
    assert game.block((5, 65, 5)) == "minecraft:air" and game.block((5, 64, 5)) == "minecraft:oak_planks"
    assert game.block((5, 63, 5)) == "minecraft:grass_block"
    sb.finish()
    assert not game.forceloaded


def test_parse_site_plan_clamps_and_rejects():
    from mcbuild.designer import parse_site_plan
    plan = parse_site_plan('```json\n{"name": "A", "width": 10, "depth": 8, "height": 6, "parts": '
                           '[{"name": "big", "x1": -5, "z1": 2, "x2": 50, "z2": 1}, {"oops": 1}]}\n```')
    assert plan.parts == [{"name": "big", "x1": 0, "z1": 1, "x2": 9, "z2": 2, "height": 6}]
    assert plan.small and plan.box() == ((-2, -6, -2), (11, 18, 9))
    with pytest.raises(DesignError):
        parse_site_plan("no json here")
    with pytest.raises(DesignError, match="out of range"):
        parse_site_plan('{"width": 900, "depth": 8, "height": 6}')


def test_blocks_outside_the_site_are_sent_back():
    from mcbuild.designer import parse_site_plan
    site = parse_site_plan('{"name": "A", "width": 5, "depth": 5, "height": 8}')
    too_wide = GOOD_SCRIPT + "\nb.set(40, 0, 0, 'oak_planks')"
    claude = FakeClaude([code(too_wide), code(GOOD_SCRIPT)])
    d = design("tower", claude, CATALOG, site=site, log=lambda m: None)
    assert d.attempts == 2
    assert "outside the site" in claude.requests[1]["messages"][-1]["content"]


def test_failed_detail_pass_keeps_the_structure(tmp_path, monkeypatch):
    replies = [_site("Big Hall", 30, 22, 7), code(STRUCTURE)] + [code("pass")] * 3
    game, claude, entry = _run_ai_build(tmp_path, monkeypatch, replies, "a big hall")
    assert len([p for p, blk in game.world.items() if blk == "minecraft:stone_bricks"]) > 100
    assert any("keeping the basic structure" in c for c in game.log)
    assert os.listdir(".mcbuild/designs")  # the structure is saved as the design


def test_failed_structure_pass_goes_straight_to_details(tmp_path, monkeypatch):
    replies = [_site("Big Hall", 30, 22, 7)] + [code("pass")] * 3 + [code(DETAIL)]
    game, claude, entry = _run_ai_build(tmp_path, monkeypatch, replies, "a big hall")
    assert "PASS 3 OF 3" not in claude.requests[-1]["messages"][0]["content"]  # no structure to extend
    assert len([p for p, blk in game.world.items() if "glass_pane" in blk]) == 6


def test_progress_bar_text():
    from mcbuild.progress import ProgressBar

    class Quiet:
        def bossbar_update(self, *a):
            self.last = a
    bar = ProgressBar(Quiet(), "Chris", log=lambda m: None)
    bar.designing("Designing details")
    bar.note("writing Gatehouse")
    assert bar.text().startswith("Designing details: writing Gatehouse · 0:0")
    bar.building("Building the structure")
    bar.built(45)
    assert bar.text().startswith("Building the structure 45%  |  Designing details: writing Gatehouse")
    assert bar.value() == 45
    bar.designed()
    bar.done_building()
    assert bar.text() == "mcbuild"
