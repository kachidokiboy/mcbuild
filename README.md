# mcbuild

Minecraft AI Building Builder: describe a building, and an AI designs it and builds it piece by piece
in your Minecraft world while you watch. It's built for Minecraft **Java Edition** in **Creative** and
targets anything from a cottage up to a castle.

> **Status: milestone 3 (AI builds from chat).** Type `!build a cozy oak cottage` in Minecraft chat, and
> Claude designs the building and mcbuild builds it in front of you.

## Quick start (Mac)

```sh
pip install -e .
mcbuild setup-server --flat --accept-eula   # downloads the official server and enables RCON
./server/start.sh                           # in another terminal; then join localhost in Minecraft
mcbuild demo                                # builds a hut in front of you
mcbuild gallery                             # builds a row of sample buildings
mcbuild undo                                # removes the last build
mcbuild set-key                             # save your Anthropic API key
mcbuild listen                              # then type in Minecraft chat: !build a wizard tower
```

Full step-by-step instructions are in **[docs/setup-mac.md](docs/setup-mac.md)**.

## How it works

```
prompt ──► Claude writes a build script ──► run it ──► voxel model
              ▲                                          │
              └──── errors / invalid blocks ◄── validate ┘
                                                         │
              backup area (/clone) ◄─────────────────────┤
                                                         ▼
                                    RCON: bottom-up animated /setblock
```

- **Game bridge:** the official Minecraft server with RCON enabled. mcbuild sends `/setblock` commands
  at a steady rate, so the build rises layer by layer. No mods or plugins are needed.
- **Build order:** structural blocks go bottom-up in a sweeping pattern. Blocks that need support
  (doors, torches, lanterns and so on) go last.
- **Undo:** before building, the area is copied with `/clone` to a backup slot far away in the same
  world. `mcbuild undo` copies it back.
- **AI designer:** Claude writes a short Python script against the building library (walls,
  towers, roofs, battlements...), which handles castle-scale builds with loops and symmetry in one
  pass. The script runs in a separate, locked-down process: no imports, no files, a time limit.
  Every block is checked against the list of valid blocks exported from your own server, and any
  problems go back to Claude to fix, up to 3 attempts.
- **Three passes:** Claude first plans the site (size and layout), which is marked on the ground
  within seconds. Then it writes the basic structure, which starts building right away, and
  finally the detailed design, written while the structure goes up. Each pass places only what
  changed and restores the original ground where a block is no longer needed; a pass that changes
  most of the design clears the site and rebuilds. Small buildings skip the structure pass.
- **Progress bar:** a bar at the top of the screen shows what's happening: planning, which part
  Claude is writing, build percentage, and fixes.
- **Chat commands:** `mcbuild listen` watches the server log for `!build <description>`,
  `!designs`, `!rebuild <number or name>`, `!undo` and `!help`, and replies in chat.
- **Saved designs:** every design is saved automatically as a numbered script in
  `.mcbuild/designs/`. `!rebuild 3` (or `mcbuild script 3`) builds it again with no AI and no API
  cost. `mcbuild designs` lists them. The model defaults to Claude Opus 5.5 (`MCBUILD_MODEL`
  overrides it).

## Building library

`mcbuild/primitives.py` has the pieces that designs are made from. The AI will write scripts
using them:

| Kind | Functions |
|---|---|
| Shapes | `box`, `clear`, `pillar`, `walls`, `wall_line`, `cylinder` |
| Roofs | `gable_roof`, `hip_roof`, `cone_roof`, `battlements` |
| Details | `window`, `opening` (with arches), `door`, `stairs_run`, `torch`, `lantern` |
| Components | `square_tower`, `round_tower` |
| Helpers | `rect_perimeter`, `line_xz`, `disc_points`, `ring_points` |

Materials such as `"spruce"`, `"stone_brick"` and `"deepslate_tile"` (`mcbuild/materials.py`) give
the matching stairs, slab, full block and so on. Designs are made facing north, and `Build.rotated`
turns them, including every stair, door and pane, so the front faces you.
`mcbuild/gallery.py` has worked examples. Run `mcbuild gallery` to see them in game.

## Roadmap

1. ✅ **Plumbing:** server setup, RCON, animated placement, undo
2. ✅ **Building library:** shapes, roofs, battlements, doors, windows, arches, stairs, towers, rotation
3. ✅ **AI builds from in-game chat:** `!build ...` in Minecraft, with checks and automatic fixes
4. **Castle scale:** gatehouses, curtain walls, ground leveling, faster placement with `/fill`
5. **Extras:** image-based self-review, style presets, glass-outline preview

## Development

```sh
pip install -e '.[dev]'
pytest
```

Tests run against a simulated Minecraft server (`tests/fake_minecraft.py`) that speaks real RCON,
so no Minecraft install is needed.
