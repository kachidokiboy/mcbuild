"""Watch the server log for chat commands like `!build a cozy cottage` and act on them."""

from __future__ import annotations

import os
import re
import time
from typing import Callable, Iterator, Optional, Tuple

# e.g. "[12:34:56] [Server thread/INFO]: <Chris> !build a tower"
#      "[12:34:56] [Server thread/INFO]: [Not Secure] <Chris> !build a tower"
_CHAT_RE = re.compile(r"\]: (?:\[Not Secure\] )?<([A-Za-z0-9_]{1,16})> (.*)$")

HELP = ("Commands: !build <description> (e.g. !build a cozy oak cottage with a chimney), "
        "!edit <change> (change the last build, e.g. !edit make the towers taller), "
        "!designs (list saved designs), !rebuild <number or name> (build a saved design again, free), "
        "!undo (revert the last edit, or remove the last build), !help")


def parse_chat(line: str) -> Optional[Tuple[str, str]]:
    """Return (player, message) for a chat line in the server log, else None."""
    match = _CHAT_RE.search(line.rstrip())
    return (match.group(1), match.group(2).strip()) if match else None


def parse_command(message: str) -> Optional[Tuple[str, str]]:
    """'!build a tower' -> ('build', 'a tower'); non-commands -> None."""
    if not message.startswith("!"):
        return None
    name, _, rest = message[1:].partition(" ")
    return name.lower(), rest.strip()


def follow(path: str, poll: float = 0.5, should_stop: Callable[[], bool] = lambda: False) -> Iterator[str]:
    """Yield lines appended to a log file, like `tail -F`: starts at the end and
    reopens the file when the server restarts and starts a new log."""
    handle, inode, partial, from_start = None, None, "", False
    while not should_stop():
        if handle is None:
            if not os.path.exists(path):
                from_start = True  # the log appears later: everything in it is new
                time.sleep(poll)
                continue
            handle = open(path, encoding="utf-8", errors="replace")
            inode = os.fstat(handle.fileno()).st_ino
            if not from_start:
                handle.seek(0, os.SEEK_END)  # skip what was logged before we started
        line = handle.readline()
        if line:
            partial += line
            if partial.endswith("\n"):
                yield partial.rstrip("\n")
                partial = ""
            continue
        try:
            st = os.stat(path)
            replaced = st.st_ino != inode or st.st_size < handle.tell()
        except FileNotFoundError:
            replaced = True
        if replaced:  # the server restarted and began a new log
            handle.close()
            handle, partial, from_start = None, "", True
            continue
        time.sleep(poll)
    if handle:
        handle.close()
