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
    """Stands in for anthropic.Anthropic: replies with queued texts and records requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        reply = self.replies.pop(0)
        stop, text = reply if isinstance(reply, tuple) else ("end_turn", reply)
        message = SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])

        class Stream:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: message)

            def __exit__(self, *exc):
                return False
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
    assert first["model"] == "claude-opus-5" and first["thinking"] == {"type": "adaptive"}
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

def test_ai_build_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game = FakeMinecraft(players={"Chris": ((0.5, 64.0, 0.5), (0.0, 0.0))})  # facing south
    srv = FakeRconServer(game, password="pw")

    def connect():
        client = RconClient("127.0.0.1", srv.port, "pw")
        client.connect()
        return client

    try:
        claude = FakeClaude([code(GOOD_SCRIPT)])
        entry = ai_build("a tiny tower", connect, claude, CATALOG, History("h.json"),
                         PlaceOptions(player="Chris", rate=0), log=lambda m: None)
    finally:
        srv.close()
    assert entry.name == "Tiny Tower"
    assert all(z > 0 for (x, y, z), blk in game.world.items() if "stone_bricks" in blk and y >= 64)
    tells = [c for c in game.log if c.startswith("tellraw Chris")]
    assert any("Designing" in t for t in tells) and any("Building Tiny Tower" in t for t in tells)
    saved = os.listdir(".mcbuild/designs")
    assert len(saved) == 1 and saved[0].endswith("tiny-tower.py")
    assert open(f".mcbuild/designs/{saved[0]}").read().startswith("# Request: a tiny tower")
