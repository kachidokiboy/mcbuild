"""Download and configure an official Minecraft Java server with RCON enabled."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import ssl
import stat
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
EULA_URL = "https://aka.ms/MinecraftEULA"

START_SCRIPT = """#!/bin/sh
# Starts the Minecraft server. Stop it by typing `stop` in this window.
cd "$(dirname "$0")"
if [ -z "$JAVA" ] && [ -x /usr/libexec/java_home ]; then
  JAVA_HOME_DIR=$(/usr/libexec/java_home -v {java}+ 2>/dev/null) && JAVA="$JAVA_HOME_DIR/bin/java"
fi
exec "${{JAVA:-java}}" -Xms1G -Xmx{memory} -jar server.jar nogui
"""

Fetch = Callable[[str], bytes]


def default_fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            raise RuntimeError(
                f"SSL error downloading {url}. With the python.org installer on macOS, run "
                "'Install Certificates.command' from your Python folder in Applications, or "
                "download server.jar manually and pass --jar."
            ) from e
        raise


def rcon_properties(password: str, flat: bool) -> Dict[str, str]:
    props = {
        "enable-rcon": "true",
        "rcon.port": "25575",
        "rcon.password": password,
        # Don't echo every /setblock to operators' chat.
        "broadcast-rcon-to-ops": "false",
        "gamemode": "creative",
        "difficulty": "peaceful",
        "spawn-protection": "0",
        "motd": "mcbuild test server",
    }
    if flat:
        props["level-type"] = "minecraft\\:flat"
    return props


def read_properties(path: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    if not os.path.exists(path):
        return props
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                props[key.strip()] = value.strip()
    return props


def merge_properties(path: str, updates: Dict[str, str], keep_existing: tuple = ()) -> None:
    """Set keys in a server.properties file, preserving other lines and comments.
    Keys listed in `keep_existing` are only written if not already present."""
    lines: List[str] = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.read().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                seen.add(key)
                if key not in keep_existing:
                    lines[i] = f"{key}={updates[key]}"
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def setup_server(
    directory: str = "server",
    version: str = "latest",
    flat: bool = False,
    accept_eula: bool = False,
    jar: Optional[str] = None,
    memory: str = "4G",
    fetch: Fetch = default_fetch,
    log: Callable[[str], None] = print,
) -> None:
    os.makedirs(directory, exist_ok=True)
    jar_path = os.path.join(directory, "server.jar")
    java_major = 25

    if jar:
        with open(jar, "rb") as src, open(jar_path, "wb") as dst:
            dst.write(src.read())
        log(f"Copied {jar} -> {jar_path}")
    else:
        manifest = json.loads(fetch(MANIFEST_URL))
        version_id = manifest["latest"]["release"] if version == "latest" else version
        matches = [v for v in manifest["versions"] if v["id"] == version_id]
        if not matches:
            raise RuntimeError(f"Unknown Minecraft version {version_id!r}")
        info = json.loads(fetch(matches[0]["url"]))
        java_major = info.get("javaVersion", {}).get("majorVersion", java_major)
        download = info["downloads"]["server"]
        if _sha1(jar_path) == download["sha1"]:
            log(f"server.jar for {version_id} already present")
        else:
            log(f"Downloading Minecraft server {version_id}...")
            data = fetch(download["url"])
            if hashlib.sha1(data).hexdigest() != download["sha1"]:
                raise RuntimeError("Downloaded server.jar failed its checksum; try again")
            with open(jar_path, "wb") as f:
                f.write(data)
            log(f"Saved {jar_path}")

    props_path = os.path.join(directory, "server.properties")
    password = read_properties(props_path).get("rcon.password") or secrets.token_urlsafe(16)
    # Keep an existing world type; only choose flat for a brand-new setup.
    merge_properties(props_path, rcon_properties(password, flat), keep_existing=("level-type",))
    log(f"Wrote {props_path} (RCON enabled on port 25575)")

    start_path = os.path.join(directory, "start.sh")
    with open(start_path, "w") as f:
        f.write(START_SCRIPT.format(java=java_major, memory=memory))
    os.chmod(start_path, os.stat(start_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"Wrote {start_path}")

    eula_path = os.path.join(directory, "eula.txt")
    if accept_eula:
        with open(eula_path, "w") as f:
            f.write(f"# Accepted via mcbuild setup-server --accept-eula ({EULA_URL})\neula=true\n")
        log("Accepted the Minecraft EULA")
    elif "eula=true" not in (open(eula_path).read() if os.path.exists(eula_path) else ""):
        log(f"NOTE: read the Minecraft EULA ({EULA_URL}) and re-run with --accept-eula, "
            f"or set eula=true in {eula_path}")

    log("")
    log(f"Next: make sure Java {java_major}+ is installed (`java -version`), then run {start_path}")


def _sha1(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()
