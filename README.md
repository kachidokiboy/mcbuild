# mcbuild

Minecraft AI Building Builder: describe a building, and an AI designs it and builds it piece by piece
in your Minecraft world while you watch. It's built for Minecraft **Java Edition** in **Creative** and
targets anything from a cottage up to a castle.

> **Status: milestone 1 (plumbing).** mcbuild can connect to a server, build a hand-made test hut with
> animation, and undo it. The AI designer comes in milestone 3.

## Quick start (Mac)

```sh
pip install -e .
mcbuild setup-server --flat --accept-eula   # downloads the official server and enables RCON
./server/start.sh                           # in another terminal; then join localhost in Minecraft
mcbuild demo                                # builds a hut in front of you
mcbuild undo                                # removes it
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

## Roadmap

1. ✅ **Plumbing:** server setup, RCON, animated placement, undo
2. **Building library:** box, wall, cylinder, gable/hip roofs, crenellations, doors, windows and stairs, with correct block states
3. **AI builds:** `mcbuild "cozy oak cottage"`. Claude writes the build script, and mcbuild validates it, feeds errors back, then builds
4. **Castle scale:** towers, curtain walls, gatehouses, ground leveling, faster placement with `/fill`
5. **Extras:** `!build` from in-game chat, image-based self-review, style presets

## Development

```sh
pip install -e '.[dev]'
pytest
```

Tests run against a simulated Minecraft server (`tests/fake_minecraft.py`) that speaks real RCON,
so no Minecraft install is needed.
