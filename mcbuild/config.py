"""Local settings: where mcbuild keeps its state, and the Anthropic API key."""

from __future__ import annotations

import os
from typing import Optional

STATE_DIR = ".mcbuild"
KEY_FILE = os.path.join(STATE_DIR, "api_key")
DESIGNS_DIR = os.path.join(STATE_DIR, "designs")


def api_key() -> Optional[str]:
    """ANTHROPIC_API_KEY from the environment, else the key saved by `mcbuild set-key`."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            return f.read().strip() or None
    return None


def save_api_key(key: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    # Create the file readable only by you.
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.strip() + "\n")
    os.chmod(KEY_FILE, 0o600)
    return KEY_FILE


def anthropic_client():
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("The anthropic package is missing; run `pip install -e .` again.") from None
    key = api_key()
    if not key:
        raise RuntimeError("No Anthropic API key. Run `mcbuild set-key` (get a key at console.anthropic.com).")
    return anthropic.Anthropic(api_key=key)
