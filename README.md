# Eldia Worlds (`endstone-eldia-worlds` + `eldia_worlds_native`)

Multi-world and native Custom Dimension (`type=999`) plugin suite for [Endstone](https://github.com/EndstoneMC/endstone) on Minecraft Bedrock Dedicated Server (`1.26.60+`).

## Architecture

Eldia Worlds consists of two companion plugins that run side-by-side in `bedrock_server/plugins/`:

1. **`eldia_worlds_native` (C++ Native `.so` / `.dll`)**
   - Hooks directly into Bedrock Dedicated Server's in-memory `ServerLevel` and `DimensionManager`.
   - Resolves `DimensionManager::registerCustomDimension` via `.text` byte-signature scanning and constructs a 64-byte `DimensionDefinitionGroup::DimensionDefinition` (`min_y = -64`, `max_y = 384`, `generator_type = 5` for Void, `2` for Flat, `1` for Overworld).
   - Calls `ServerLevel::getOrCreateDimension` so `EndstoneLevel`'s `OnNewDimensionCreatedConnector` wraps the new dimension into `server.level.dimensions` as a true separate dimension (`Dimension.Type.CUSTOM = 999`) at origin `(0.5, 64.0, 0.5)`.
2. **`endstone-eldia-worlds` (Python `.whl`)**
   - Provides `/world` (aliases `/dim`, `/worlds`) commands, an interactive Bedrock `ActionForm` / `ModalForm` UI, world border enforcement (`PlayerMoveEvent`), per-world `GameMode` switching, and spawn platform construction.
   - Persists custom worlds in `plugins/eldia_worlds/worlds.json` and automatically re-registers all custom dimensions with `eldia_worlds_native` on every server startup (`on_enable`) so blocks and terrain inside custom dimensions survive restarts.

---

## Quick Installation (Linux VPS)

Download both release artifacts from [GitHub Releases](https://github.com/Karanztez/eldia-worlds/releases) into `bedrock_server/plugins/` and restart Endstone:

```bash
cd bedrock_server/plugins
rm -f endstone_eldia_worlds-*.whl endstone_eldia_worlds_native.so
curl -LO https://github.com/Karanztez/eldia-worlds/releases/download/v0.2.0/endstone_eldia_worlds-0.2.0-py3-none-any.whl
curl -LO https://github.com/Karanztez/eldia-worlds/releases/download/v0.2.0/endstone_eldia_worlds_native.so
sudo systemctl restart endstone
```

---

## In-Game Commands

### `/world` (Aliases: `/dim`, `/worlds`)

| Command | Description | Permission |
| --- | --- | --- |
| `/world` or `/world gui` | Open the interactive World Manager UI | `eldia_worlds.command.world` |
| `/world list` | List all registered worlds and dimensions | `eldia_worlds.command.world` |
| `/world info <name>` | Show dimension, spawn coordinates, border, and gamemode | `eldia_worlds.command.world` |
| `/world tp <name> [player]` | Teleport to a world's spawn point | `eldia_worlds.command.tp` |
| `/world create <name> [void\|flat\|normal] [overworld\|nether\|the_end] [radius]` | Create a new native Custom Dimension (`type=999`) and teleport to `(0.5, 64.0, 0.5)` | `eldia_worlds.command.admin` |
| `/world setspawn [name]` | Set a world's spawn point to your current location | `eldia_worlds.command.admin` |
| `/world setborder <name> <radius>` | Set a world's border radius (`0` disables border) | `eldia_worlds.command.admin` |
| `/world setgamemode <name> <survival\|creative\|adventure\|spectator\|none>` | Set per-world game mode applied on teleport | `eldia_worlds.command.admin` |
| `/world delete <name>` | Remove a custom world from `worlds.json` | `eldia_worlds.command.admin` |

### `/nativeworld` (C++ Native Bridge)

| Command | Description | Permission |
| --- | --- | --- |
| `/nativeworld status` | Display loaded `bedrock_server` module base and `.text` size | `eldia_worlds_native.command.nativeworld` |
| `/nativeworld scan` | Run live `.text` signature scan for `DimensionManager` functions | `eldia_worlds_native.command.nativeworld` |
| `/nativeworld list` | List all native dimensions currently registered in `EndstoneLevel` (`0`, `1`, `2`, `999`) | `eldia_worlds_native.command.nativeworld` |
| `/nativeworld create <name> [void\|flat\|normal]` | Directly register and instantiate a native `CustomDimension` (`type=999`) | `eldia_worlds_native.command.nativeworld` |
| `/nativeworld probe <rva_hex>` | Dump 16 bytes at the given hexadecimal RVA from `bedrock_server` base | `eldia_worlds_native.command.nativeworld` |

---

## Building from Source

GitHub Actions ([.github/workflows/build.yml](./.github/workflows/build.yml)) automatically builds `.whl`, `.so` (Clang 18 + `libc++-18-dev` on Ubuntu 22.04), and `.dll` (MSVC x64 on Windows) and publishes them to GitHub Releases on `v*` tags.

- **Python package & tests:**
  ```bash
  pip install -e ".[dev]"
  ruff check src tests
  pytest -v
  ```
- **Native Linux plugin (`.so`):**
  ```bash
  CC=clang-18 CXX=clang++-18 cmake -S native -B build-linux -G Ninja -DCMAKE_BUILD_TYPE=Release
  cmake --build build-linux
  ```
