# Setting up on a Mac

This gets you from nothing to watching a test hut build itself in front of you. It takes about 10 minutes, once.

mcbuild talks to a small Minecraft server running on your Mac through **RCON** (a remote-console
feature built into the official server). You join that server from your normal Minecraft game.

## 1. Install Java and Python

Open **Terminal** and install [Homebrew](https://brew.sh) if you don't have it, then:

```sh
brew install --cask temurin@25   # Java for the Minecraft server
brew install python              # Python 3.9 or newer
java -version                    # should say 25 or higher
```

> `mcbuild setup-server` prints the Java version your Minecraft release needs. If it asks for a
> newer one, install that version the same way. You can keep older versions installed:
> `server/start.sh` picks a new enough one automatically.

## 2. Install mcbuild

```sh
git clone https://github.com/kachidokiboy/mcbuild.git
cd mcbuild
python3 -m venv .venv
source .venv/bin/activate        # run this again in each new Terminal window
pip install --upgrade pip        # the pip bundled with macOS's Python is too old
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

The first time, if it says you're **not white-listed**, add yourself in the server window
(the one running `start.sh`), using your Minecraft username:

```
whitelist add YourMinecraftName
op YourMinecraftName
```

`op` is optional. It lets you use commands like `/gamemode` in game.

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
| `mcbuild gallery` | Build a row of sample buildings: cottage, brick house, towers, gate wall, stairs, castle gate, wall walk |
| `mcbuild gallery --sample wizard-tower` | Build just one of them |
| `mcbuild history` | List builds that can still be undone |

## 7. AI builds from Minecraft chat

1. Get an API key: sign in at [console.anthropic.com](https://console.anthropic.com), add a payment
   method or credits under **Billing**, then create a key under **API Keys**.
2. Update mcbuild and save the key (in the `(.venv)` window):

   ```sh
   git pull
   pip install -e .          # installs the new anthropic package
   mcbuild set-key           # paste the key; it's hidden as you paste and stays on your Mac
   ```

3. Start listening:

   ```sh
   mcbuild listen
   ```

   The first time, it spends about 30 seconds exporting the list of valid blocks from your server.
   Leave this window open while you play. Press Ctrl+C to stop.

4. In Minecraft, open chat (**T**), face where you want the building, and type:

   ```
   !build a cozy oak cottage with a stone chimney
   ```

   A progress bar appears at the top of the screen. Within seconds the site is marked on the
   ground with yellow carpet (white for the main parts), with glass posts at the corners showing
   how tall it will be. Type `!go` to build it or `!cancel` to drop it; only the quick planning
   step has been paid for at that point. After `!go`, larger buildings get their basic structure
   first, and the details are added on top once Claude has designed them.

   To skip the preview, start the listener with `mcbuild listen --no-confirm`.

| Chat command | What it does |
|---|---|
| `!build <description>` | Design something new with AI and build it |
| `!go` / `!cancel` | Build the planned site, or drop it (after `!build`) |
| `!edit <change>` | Change the last build, e.g. `!edit make the towers taller` or `!edit add a moat` |
| `!designs` | List your saved designs |
| `!rebuild <number or name>` | Build a saved design again, with no AI and no cost (e.g. `!rebuild 3`, `!rebuild tower`) |
| `!undo` | Revert the last edit, or remove the last build |
| `!help` | Show the commands |

Each new design costs a little API usage, typically well under $1; bigger builds and fixes cost more.
Every design is saved automatically, so rebuilding one is free. From the terminal,
`mcbuild build "a stone watchtower"` designs something new, `mcbuild designs` lists saved designs, and
`mcbuild script 3` rebuilds design #3.

## Troubleshooting

- **"You are not white-listed on this server"**: see step 5.
- **`mcbuild: command not found`**: run `source .venv/bin/activate` in that window first.
- **"the Anthropic API key was rejected"**: run `mcbuild set-key` again with a fresh key.
- **Nothing happens when you type `!build`**: make sure `mcbuild listen` is running and says
  "Listening", and that the message starts with `!build`.
- **`Could not connect to RCON`**: the server isn't running or hasn't finished starting.
- **`RCON password was rejected`**: `server/server.properties` was changed while the server was running. Restart the server.
- **"Outdated server" / "Outdated client" when joining**: the game and server versions differ. Re-run
  `mcbuild setup-server --version <your game version>`, then restart the server.
- **SSL error during setup**: if you installed Python from python.org, run *Install Certificates.command*
  from `Applications/Python 3.x`.
- **Undo restores the wrong thing**: undo backups live far away in the same world (around x=1,000,000).
  They only work on the world the build was made in. Don't edit that far-away area.
