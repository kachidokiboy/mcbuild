# Setting up on a Mac

This gets you from nothing to watching a test hut build itself in front of you. It takes about 10 minutes, once.

mcbuild talks to a small Minecraft server running on your Mac through **RCON** (a remote-console
feature built into the official server). You join that server from your normal Minecraft game.

## 1. Install Java and Python

Open **Terminal** and install [Homebrew](https://brew.sh) if you don't have it, then:

```sh
brew install --cask temurin@21   # Java for the Minecraft server
brew install python              # Python 3.9 or newer
java -version                    # should say 21 or higher
```

> If `mcbuild setup-server` later says a newer Java version is needed (Mojang sometimes raises it),
> install that version the same way, for example `brew install --cask temurin@25`.

## 2. Install mcbuild

```sh
git clone https://github.com/kachidokiboy/mcbuild.git
cd mcbuild
python3 -m venv .venv
source .venv/bin/activate        # run this again in each new Terminal window
pip install -e .
```

## 3. Create the server

```sh
mcbuild setup-server --flat --accept-eula
```

- Downloads the official server for the **latest Minecraft release**. Your game must be on the same
  version. If your launcher is on an older one, add `--version 1.21.x`.
- `--flat` makes a superflat creative world, which is best for testing. Leave it out for normal terrain.
- `--accept-eula` accepts the [Minecraft EULA](https://aka.ms/MinecraftEULA). Read it first.
- Everything goes in the `server/` folder. It is git-ignored and includes a randomly generated RCON password.

## 4. Start the server

In a **separate Terminal window**:

```sh
cd mcbuild
./server/start.sh
```

Wait for `Done (...)! For help, type "help"`. Leave this window open. Type `stop` in it to shut down.

## 5. Join from Minecraft

Launch Minecraft (Java Edition), then choose **Multiplayer → Direct Connection → `localhost`**.

## 6. Build

Back in the first Terminal window (with the `.venv` activated):

```sh
mcbuild ping        # checks the connection and lists who's online
mcbuild demo        # builds a hut 3 blocks in front of you, facing you
mcbuild undo        # puts the area back exactly as it was
```

Useful options:

| Command | What it does |
|---|---|
| `mcbuild demo --rate 50` | Build more slowly (blocks per second; `0` = as fast as possible) |
| `mcbuild demo --distance 8` | Leave more room between you and the build |
| `mcbuild demo --at 100 64 100` | Build at fixed coordinates |
| `mcbuild history` | List builds that can still be undone |

## Troubleshooting

- **`Could not connect to RCON`**: the server isn't running or hasn't finished starting.
- **`RCON password was rejected`**: `server/server.properties` was changed while the server was running. Restart the server.
- **"Outdated server" / "Outdated client" when joining**: the game and server versions differ. Re-run
  `mcbuild setup-server --version <your game version>`, then restart the server.
- **SSL error during setup**: if you installed Python from python.org, run *Install Certificates.command*
  from `Applications/Python 3.x`.
- **Undo restores the wrong thing**: undo backups live far away in the same world (around x=1,000,000).
  They only work on the world the build was made in. Don't edit that far-away area.
