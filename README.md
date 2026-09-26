# mcbuild

Minecraft AI Building Builder: describe a building, and an AI designs it and builds it piece by piece
in your Minecraft world while you watch. It's built for Minecraft **Java Edition** in **Creative** and
targets anything from a cottage up to a castle.

> **Status: milestone 2 (building library).** mcbuild can connect to a server, build hand-made designs
> from its building library with animation, and undo them. The AI designer comes in milestone 3.

## Quick start (Mac)

```sh
pip install -e .
mcbuild setup-server --flat --accept-eula   # downloads the official server and enables RCON
./server/start.sh                           # in another terminal; then join localhost in Minecraft
mcbuild demo                                # builds a hut in front of you
mcbuild gallery                             # builds a row of sample buildings
mcbuild undo                                # removes the last build
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
- **AI (coming):** Claude writes a short Python script against a library of building primitives
  (walls, towers, roofs, crenellations...). This handles castle-scale builds with loops and symmetry
  in one pass, instead of placing blocks one at a time.

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
3. **AI builds from in-game chat:** type `!build a cozy oak cottage` in Minecraft. Claude writes the
   build script, and mcbuild validates it, feeds errors back, then builds in front of you
4. **Castle scale:** gatehouses, curtain walls, ground leveling, faster placement with `/fill`
5. **Extras:** image-based self-review, style presets, glass-outline preview

## Development

```sh
pip install -e '.[dev]'
pytest
```

Tests run against a simulated Minecraft server (`tests/fake_minecraft.py`) that speaks real RCON,
so no Minecraft install is needed.
