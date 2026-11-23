# endstone-eldia-worlds

Virtual multi-world and dimension manager plugin for [Endstone](https://github.com/EndstoneMC/endstone) (Minecraft Bedrock Dedicated Server).

## Why Virtual Worlds?

Endstone (`0.11.x`) exposes a single server `Level` (`server.level`) with the three built-in dimensions (`overworld`, `nether`, `the_end`) and does not provide a runtime `create_dimension` API. This plugin provides a Multiverse-style workflow by allocating isolated coordinate zones (default `100,000` blocks apart) inside any base dimension, complete with:

- Named worlds with persistent spawn points (`worlds.json` in the plugin data folder)
- World types: `void` (sky island platform), `flat` (layered grass platform), `normal` (natural terrain)
- Per-world border radius enforcement on player movement
- Optional per-world `GameMode` (`survival`, `creative`, `adventure`, `spectator`)
- Interactive Bedrock Form UI (`/world` or `/world gui`) and full CLI/chat commands

## Commands

Alias: `/world` or `/dim`

| Command | Description | Permission |
| --- | --- | --- |
| `/world` or `/world gui` | Open the interactive World Manager UI | `eldia_worlds.command.world` |
| `/world list` | List all registered worlds | `eldia_worlds.command.world` |
| `/world info <name>` | Show details for a world | `eldia_worlds.command.world` |
| `/world tp <name> [player]` | Teleport to a world's spawn | `eldia_worlds.command.tp` |
| `/world create <name> [void\|flat\|normal] [overworld\|nether\|the_end] [radius]` | Create a new virtual world | `eldia_worlds.command.admin` |
| `/world setspawn [name]` | Set a world's spawn to your current position | `eldia_worlds.command.admin` |
| `/world setborder <name> <radius>` | Update a world's border radius (`0` disables) | `eldia_worlds.command.admin` |
| `/world setgamemode <name> <mode\|none>` | Set per-world game mode | `eldia_worlds.command.admin` |
| `/world delete <name>` | Delete a custom world from the registry | `eldia_worlds.command.admin` |

## Installation

Install into the server's `.venv`:

```bash
.venv/bin/pip install ./eldia-worlds
```

Or build a `.whl` and copy it into `bedrock_server/plugins/`:

```bash
pip wheel ./eldia-worlds --no-deps -w bedrock_server/plugins/
```
