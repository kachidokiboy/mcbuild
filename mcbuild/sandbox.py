"""Run an AI-written design script safely and turn it into a Build.

Scripts get the building library, `math` and a Build called `b`, and nothing else: no imports,
no file access, no private attributes. They run in a separate Python process with a time limit,
so a runaway loop can't hang mcbuild.
"""

from __future__ import annotations

import ast
import builtins
import json
import math
import os
import subprocess
import sys
import traceback
from typing import Dict

from . import primitives
from .materials import MATERIALS, material, part
from .model import Build

MAX_BLOCKS = 600_000
TIMEOUT_SECONDS = 60

_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "int",
        "isinstance", "len", "list", "map", "max", "min", "pow", "print", "range", "reversed",
        "round", "set", "sorted", "str", "sum", "tuple", "zip", "ValueError", "KeyError",
        "IndexError", "Exception",
    )
}

# Everything a design script can use besides `b`, `math` and the builtins above.
API_NAMES = [
    "box", "clear", "pillar", "walls", "wall_line", "cylinder",
    "gable_roof", "hip_roof", "cone_roof", "battlements",
    "window", "opening", "door", "stairs_run", "lantern", "torch",
    "square_tower", "round_tower", "gatehouse", "curtain_wall", "spiral_staircase", "bridge", "moat",
    "rect_perimeter", "line_xz", "disc_points", "ring_points",
    "DIRECTIONS", "OPPOSITE",
]


class ScriptError(Exception):
    """The script is not allowed, crashed, or produced something unusable."""


def check_script(source: str) -> None:
    """Reject imports and access to private/dunder names before running anything."""
    try:
        tree = ast.parse(source, "<design>")
    except SyntaxError as e:
        raise ScriptError(f"Line {e.lineno}: SyntaxError: {e.msg}") from None
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ScriptError(f"Line {line}: imports are not allowed (math and the library are already available)")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ScriptError(f"Line {line}: private attributes like .{node.attr} are not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ScriptError(f"Line {line}: {node.id} is not allowed")


def namespace() -> Dict[str, object]:
    ns: Dict[str, object] = {name: getattr(primitives, name) for name in API_NAMES}
    ns.update({
        "__builtins__": _SAFE_BUILTINS, "math": math, "Build": Build,
        "MATERIALS": MATERIALS, "material": material, "part": part,
        "b": Build("design"),
    })
    return ns


def _describe_error(source: str, exc: BaseException) -> str:
    lines = source.splitlines()
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<design>"]
    where = ""
    if frames and frames[-1].lineno:
        n = frames[-1].lineno
        code = lines[n - 1].strip() if 0 < n <= len(lines) else ""
        where = f"Line {n} (`{code}`): "
    return f"{where}{type(exc).__name__}: {exc}"


def run_script(source: str) -> Build:
    """Run a design script in this process. Use run_script_isolated for untrusted scripts."""
    check_script(source)
    ns = namespace()
    try:
        exec(compile(source, "<design>", "exec"), ns)
    except Exception as e:  # noqa: BLE001 - every script failure is reported back to the AI
        raise ScriptError(_describe_error(source, e)) from None
    build = ns["b"]
    if not isinstance(build, Build):
        raise ScriptError("`b` must stay the Build you were given")
    if len(build) > MAX_BLOCKS:
        raise ScriptError(f"The design has {len(build)} blocks; the limit is {MAX_BLOCKS}")
    return build


def run_script_isolated(source: str, timeout: float = TIMEOUT_SECONDS) -> Build:
    """Run a design script in a separate Python process with a time limit."""
    check_script(source)
    # Make sure the child process imports this same copy of mcbuild, whatever the working directory.
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [package_parent, os.environ.get("PYTHONPATH")]))}
    try:
        proc = subprocess.run([sys.executable, "-m", "mcbuild.sandbox"], input=source, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ScriptError(f"The script took longer than {timeout:g}s (infinite loop?)") from None
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ScriptError(f"The script runner failed: {proc.stderr.strip()[-500:]}") from None
    if "error" in result:
        raise ScriptError(result["error"])
    build = Build(result["name"])
    build.blocks = {(x, y, z): blk for x, y, z, blk in result["blocks"]}
    return build


def _main() -> None:
    source = sys.stdin.read()
    try:
        build = run_script(source)
        out = {"name": build.name, "blocks": [[x, y, z, blk] for (x, y, z), blk in build.blocks.items()]}
    except ScriptError as e:
        out = {"error": str(e)}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    _main()
