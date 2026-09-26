"""The game's own list of valid blocks and block states, used to catch mistakes before building.

The official server can export this list ("data reports"). mcbuild generates it once per
server version into server/generated/reports/blocks.json.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional

REPORT_PATH = os.path.join("generated", "reports", "blocks.json")


class BlockCatalog:
    def __init__(self, data: Dict[str, dict]):
        # {"minecraft:oak_stairs": {"facing": ["east", ...], "half": [...], ...}, ...}
        self.properties = {name: info.get("properties", {}) for name, info in data.items()}
        self._short_names = [n.split(":", 1)[1] for n in self.properties]

    @classmethod
    def load(cls, server_dir: str) -> Optional["BlockCatalog"]:
        path = os.path.join(server_dir, REPORT_PATH)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return cls(json.load(f))

    def check(self, block: str) -> Optional[str]:
        """Return a description of what's wrong with a block string, or None if it's valid."""
        name, _, states = block.partition("[")
        if name not in self.properties:
            short = name.split(":", 1)[-1]
            close = difflib.get_close_matches(short, self._short_names, n=3)
            hint = f" (did you mean {', '.join(close)}?)" if close else ""
            return f"unknown block '{short}'{hint}"
        allowed = self.properties[name]
        for pair in filter(None, states.rstrip("]").split(",")):
            key, _, value = pair.partition("=")
            if key not in allowed:
                have = ", ".join(sorted(allowed)) or "no states"
                return f"'{name.split(':', 1)[1]}' has no state '{key}' (it has: {have})"
            if value not in allowed[key]:
                return f"'{name.split(':', 1)[1]}' state {key}={value} is invalid (use: {', '.join(allowed[key])})"
        return None


def find_java(server_dir: str) -> str:
    """The same Java the server's start.sh would use."""
    if os.environ.get("JAVA"):
        return os.environ["JAVA"]
    if os.path.exists("/usr/libexec/java_home"):
        version = "21"
        start = os.path.join(server_dir, "start.sh")
        if os.path.exists(start):
            match = re.search(r"java_home -v (\d+)\+", open(start).read())
            version = match.group(1) if match else version
        try:
            home = subprocess.run(["/usr/libexec/java_home", "-v", f"{version}+"],
                                  capture_output=True, text=True, check=True).stdout.strip()
            return os.path.join(home, "bin", "java")
        except (OSError, subprocess.CalledProcessError):
            pass
    return shutil.which("java") or "java"


def generate_report(server_dir: str) -> None:
    """Run the server's data generator and copy blocks.json into the server folder.

    The generator runs in a temporary folder: it writes its own logs/latest.log, which would
    otherwise replace the running server's log that `mcbuild listen` reads chat from."""
    jar = os.path.abspath(os.path.join(server_dir, "server.jar"))
    with tempfile.TemporaryDirectory(prefix="mcbuild-reports-") as work:
        subprocess.run(
            [find_java(server_dir), "-DbundlerMainClass=net.minecraft.data.Main", "-jar", jar, "--reports"],
            cwd=work, capture_output=True, text=True, timeout=600, check=True,
        )
        target = os.path.join(server_dir, REPORT_PATH)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(work, REPORT_PATH), target)


def ensure_catalog(server_dir: str, log: Callable[[str], None] = print) -> Optional[BlockCatalog]:
    """Load the block list, generating it first if it's missing or older than server.jar."""
    jar = os.path.join(server_dir, "server.jar")
    report = os.path.join(server_dir, REPORT_PATH)
    stale = os.path.exists(jar) and (not os.path.exists(report) or os.path.getmtime(report) < os.path.getmtime(jar))
    if stale:
        log("Generating the block list from the server (one time, about 30 seconds)...")
        try:
            generate_report(server_dir)
        except (OSError, subprocess.SubprocessError) as e:
            log(f"Couldn't generate the block list ({e}); block names won't be checked in advance.")
    catalog = BlockCatalog.load(server_dir)
    if catalog is None and not stale:
        log("No block list found (is --server-dir right?); block names won't be checked in advance.")
    return catalog


def check_blocks(blocks: List[str], catalog: BlockCatalog, limit: int = 15) -> List[str]:
    problems = []
    for block in sorted(set(blocks)):
        problem = catalog.check(block)
        if problem:
            problems.append(f"{block}: {problem}")
            if len(problems) >= limit:
                break
    return problems
