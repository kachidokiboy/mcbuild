"""Ask Claude to design a building as a script against the building library."""

from __future__ import annotations

import inspect
import os
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import primitives
from .blocks import BlockCatalog, check_blocks
from .materials import MATERIALS
from .model import Build
from .sandbox import API_NAMES, ScriptError, run_script_isolated

DEFAULT_MODEL = "claude-opus-5"
MAX_ATTEMPTS = 3
MAX_FOOTPRINT = 256
MAX_HEIGHT = 200

Log = Callable[[str], None]


class DesignError(Exception):
    pass


@dataclass
class Design:
    build: Build
    script: str
    attempts: int


def _signature(func) -> str:
    params = []
    for p in inspect.signature(func).parameters.values():
        text = p.name
        if p.annotation is not inspect.Parameter.empty and p.name != "b":
            text += f": {p.annotation}"
        if p.default is not inspect.Parameter.empty:
            text += f" = {p.default!r}"
        params.append(text)
    return f"{func.__name__}({', '.join(params)})"


def _library_reference() -> str:
    parts = []
    for name in API_NAMES:
        obj = getattr(primitives, name)
        if callable(obj):
            doc = inspect.getdoc(obj) or ""
            parts.append(f"{_signature(obj)}\n    " + doc.replace("\n", "\n    "))
    return "\n\n".join(parts)


def _examples() -> str:
    from . import gallery
    source = inspect.getsource(gallery)
    start = source.index("def cottage")
    return source[start:source.index("SAMPLES")].strip()


def system_prompt() -> str:
    materials = ", ".join(f"{n} ({'/'.join(sorted(MATERIALS[n]))})" for n in sorted(MATERIALS))
    return f"""You are an expert Minecraft architect. You design buildings by writing a short Python
script that uses a building library; the script's blocks are then placed in the player's
Minecraft Java Edition world (creative mode), one by one, while they watch.

# How the script runs
- A Build named `b` already exists. Write blocks into it with the library functions below, or
  directly with `b.set(x, y, z, block)` and `b.fill((x1, y1, z1), (x2, y2, z2), block, hollow=False)`.
- `Pos` means an (x, y, z) tuple and `XZ` an (x, z) tuple.
- Set `b.name = "..."` to a short title for the building.
- Available: the library functions, `math`, and basic builtins (range, len, min, max, abs, round,
  int, float, list, dict, set, tuple, enumerate, zip, sorted, sum, any, all). No imports, no
  files, no underscore attributes. The script must finish within 60 seconds.

# Coordinates and orientation
- x = east, y = up, z = south. Blocks are placed relative to your design; mcbuild moves it in
  front of the player afterwards, so use small coordinates near (0, 0, 0).
- y = 0 is the first layer above the ground. Use y = -1 for foundations and ground floors (they
  replace the grass). The ground may be slightly uneven, so a 1-3 block foundation below y = 0
  is a good idea for larger builds.
- The FRONT of the design (main entrance, the side the player sees) must face NORTH (-z, the
  smallest z). mcbuild rotates the design so the front faces the player.
- Size limits: at most {MAX_FOOTPRINT}x{MAX_FOOTPRINT} blocks across and {MAX_HEIGHT} tall. Typical sizes: a house 9-15
  across, a mansion 20-35, a castle 50-120 across.

# Blocks
- Block ids are Minecraft Java ids without the namespace, optionally with states:
  "stone_bricks", "spruce_stairs[facing=north,half=bottom]", "lantern[hanging=true]".
  Only use real blocks and valid states for the current Java Edition; invalid ones are
  rejected and you will be asked to fix them.
- Stairs `facing` is the direction you walk to climb them (the tall back side). Doors: use the
  door() helper. Panes and fences connect automatically when placed next to solid blocks.
- "air" clears a space. Terrain is NOT removed automatically: clear() the insides of rooms so
  grass and flowers don't stay inside. Clearing costs time, so only clear what you need.
- Blocks that hang or stand on something (torches, lanterns, doors, carpets, flowers) are placed
  after everything else, so their support will exist; make sure it does.

# Materials (names for `material` arguments, with the parts each has)
{materials}

# Library reference
{_library_reference()}

# Examples (these build the gallery samples; `x, z` is where each one starts)
```python
{_examples()}
```

# Design quality
Design something that looks good in the game, not a plain box:
- Give walls depth and rhythm: log or stone corner pillars, a contrasting base course, window
  frames or shutters (trapdoors), recessed or protruding sections, trim under the roof.
- Roofs with overhangs, dormers or several roof sections for larger buildings; chimneys; porches.
- Matching palettes (2-4 main materials). Light the inside and the entrance.
- A few simple interior touches (floors, a table, a bed, bookshelves) for houses.
- For castles: keep, towers, curtain walls with walkways and battlements, a gatehouse; plan the
  layout first as comments, then build each part with its own helper function and loops.
- Keep it structurally believable: no floating pieces, every roof closed, doors reachable.

# Answer format
Briefly think about the layout, then reply with exactly one ```python code block containing the
complete script. When asked to fix problems, reply with the complete corrected script again."""


def _extract_code(text: str) -> Optional[str]:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    return max(blocks, key=len) if blocks else None


def validate(build: Build, catalog: Optional[BlockCatalog]) -> List[str]:
    if not len(build):
        return ["The script didn't place any blocks."]
    problems = []
    (x1, y1, z1), (x2, y2, z2) = build.bounds()
    if x2 - x1 + 1 > MAX_FOOTPRINT or z2 - z1 + 1 > MAX_FOOTPRINT:
        problems.append(f"The design is {x2 - x1 + 1}x{z2 - z1 + 1} across; the limit is {MAX_FOOTPRINT}x{MAX_FOOTPRINT}.")
    if y2 - y1 + 1 > MAX_HEIGHT:
        problems.append(f"The design is {y2 - y1 + 1} blocks tall; the limit is {MAX_HEIGHT}.")
    if catalog:
        bad = check_blocks([blk for _, blk in build], catalog)
        problems += [f"Invalid block {p}" for p in bad]
    return problems


def _ask(client, model: str, messages: list):
    # Newer request options go in extra_body so older SDK versions (e.g. on Python 3.9) still work.
    with client.beta.messages.stream(
        model=model,
        max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"],
        system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        messages=messages,
        extra_body={"output_config": {"effort": "high"}, "fallbacks": "default"},
    ) as stream:
        return stream.get_final_message()


def _ask_friendly(client, model: str, messages: list):
    """Call Claude, turning API failures into DesignErrors a player can understand."""
    import anthropic

    try:
        return _ask(client, model, messages)
    except anthropic.AuthenticationError:
        raise DesignError("the Anthropic API key was rejected; run `mcbuild set-key` with a valid key") from None
    except anthropic.PermissionDeniedError:
        raise DesignError(f"this API key can't use {model}; check your plan at console.anthropic.com") from None
    except anthropic.NotFoundError:
        raise DesignError(f"model {model} wasn't found (check MCBUILD_MODEL)") from None
    except anthropic.RateLimitError:
        raise DesignError("the Anthropic API is rate limiting requests; wait a minute and try again") from None
    except anthropic.APIStatusError as e:
        raise DesignError(f"the Anthropic API returned an error ({e.status_code}): {e.message}") from None
    except anthropic.APIConnectionError:
        raise DesignError("couldn't reach the Anthropic API; check your internet connection") from None


def design(request: str, client, catalog: Optional[BlockCatalog] = None, model: Optional[str] = None,
           log: Log = print, max_attempts: int = MAX_ATTEMPTS) -> Design:
    """Have Claude write a design script for `request`, fixing problems until it's valid."""
    model = model or os.environ.get("MCBUILD_MODEL", DEFAULT_MODEL)
    messages: list = [{"role": "user", "content": f"Design this: {request}"}]
    problems: List[str] = []
    for attempt in range(1, max_attempts + 1):
        response = _ask_friendly(client, model, messages)
        if response.stop_reason == "refusal":
            raise DesignError("Claude declined to design that; try describing it differently.")
        text = "".join(block.text for block in response.content if block.type == "text")
        script = _extract_code(text)
        if response.stop_reason == "max_tokens":
            problems = ["The reply was cut off. Write a more compact script (use loops and helper functions)."]
        elif script is None:
            problems = ["Your reply had no ```python code block."]
        else:
            try:
                build = run_script_isolated(script)
                problems = validate(build, catalog)
            except ScriptError as e:
                problems = [f"The script failed: {e}"]
            if not problems:
                return Design(build, script, attempts=attempt)
        log(f"Attempt {attempt}: {len(problems)} problem(s) found; asking Claude to fix them...")
        for p in problems[:5]:
            log(f"  - {p}")
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": "The script had these problems:\n- " + "\n- ".join(problems)
                         + "\nReply with the complete corrected script."})
    raise DesignError(f"Couldn't get a valid design after {max_attempts} attempts: {problems[0]}")
