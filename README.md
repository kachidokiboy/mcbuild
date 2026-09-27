# mcbuild

Minecraft AI Building Builder: describe a building, and an AI designs it and builds it piece by piece
in your Minecraft world while you watch. It's built for Minecraft **Java Edition** in **Creative** and
targets anything from a cottage up to a castle.

> **Status:** type `!build a cozy oak cottage` (or a castle) in Minecraft chat. Claude plans the site,
> you confirm with `!go`, and the building goes up in front of you, structure first and then details.
> `!edit` changes it, `!undo` reverts it.

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

- **Game bridge:** the official Minecraft server with RCON enabled. No mods or plugins are needed.
- **Fast placement:** within each layer, rectangles of the same block are placed with one `/fill`
  and the rest with `/setblock`, at a steady pace, so the build still rises layer by layer. A
  castle-sized build of about 55,000 blocks takes about 30 seconds instead of 3 minutes.
- **Build order:** structural blocks go bottom-up in a sweeping pattern. Blocks that need support
  (doors, torches, lanterns and so on) go last.
- **Uneven ground:** before building, mcbuild surveys ground heights across the site using
  Minecraft's own height maps (leaves ignored, the bottom of ponds included) and builds at the
  typical ground level. It clears hills, trees and plants above that level and fills dips and
  water below it with grass, dirt and stone. Undo restores the original landscape.
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
- **Self-review:** before the final design (or an edit) is built, mcbuild draws a front view, a
  back view and a top-down plan of it (`mcbuild/render.py`) and shows them to Claude, which either
  confirms it or sends a corrected script. If the correction can't be made valid, the original is
  kept. `mcbuild listen --no-review` skips this.
- **Preview and confirm:** after the quick site plan, the site is outlined with carpet and glass
  corner posts at full height, and nothing more happens until you type `!go` (or `!cancel`,
  which puts the site back). `mcbuild listen --no-confirm` skips this.
- **Progress bar:** a bar at the top of the screen shows what's happening: planning, which part
  Claude is writing, build percentage, and fixes.
- **Chat commands:** `mcbuild listen` watches the server log for `!build <description>`,
  `!edit <change>`, `!designs`, `!rebuild <number or name>`, `!undo` and `!help`, and replies in chat.
- **Editing:** `!edit make the towers taller` gives Claude the last build's script and the change;
  only the blocks that differ are rebuilt, inside the same site. Each version is saved, and
  `!undo` right after an edit reverts just that edit.
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
| Castle parts | `gatehouse`, `curtain_wall`, `spiral_staircase`, `bridge`, `moat` |
| Helpers | `rect_perimeter`, `line_xz`, `disc_points`, `ring_points` |

Materials such as `"spruce"`, `"stone_brick"` and `"deepslate_tile"` (`mcbuild/materials.py`) give
the matching stairs, slab, full block and so on. Designs are made facing north, and `Build.rotated`
turns them, including every stair, door and pane, so the front faces you.
`mcbuild/gallery.py` has worked examples. Run `mcbuild gallery` to see them in game.

## Roadmap

1. ✅ **Plumbing:** server setup, RCON, animated placement, undo
2. ✅ **Building library:** shapes, roofs, battlements, doors, windows, arches, stairs, towers, rotation
3. ✅ **AI builds from in-game chat:** `!build ...` in Minecraft, with checks and automatic fixes
4. ✅ **Castle scale:** faster placement with `/fill`, uneven ground, `!edit`, castle parts (gatehouse,
   curtain walls, spiral stairs, bridges, moats)
5. **Extras:** ✅ preview and confirm, ✅ self-review with pictures; ideas: style presets, multiplayer queue

## Development

```sh
pip install -e '.[dev]'
pytest
```

Tests run against a simulated Minecraft server (`tests/fake_minecraft.py`) that speaks real RCON,
so no Minecraft install is needed.
