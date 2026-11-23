# Architecture & Internals

Eldia Worlds combines a Python Endstone plugin (`endstone-eldia-worlds`) with a companion C++ native plugin (`eldia_worlds_native`) to create true separate dimensions (`Dimension.Type.CUSTOM = 999`) on a single Bedrock Dedicated Server instance without running multiple ports or proxies.

## 1. Native Dimension Registration (`eldia_worlds_native`)

Endstone (`0.11.x`) exposes existing dimensions via `Level.dimensions` and listens to `DimensionManager::getOnNewDimensionCreatedConnector()`, but does not expose a public `create_dimension` API.

On Linux x86_64 BDS (`1.26.60+`), `eldia_worlds_native` bridges this gap at runtime:

1. **In-Memory Signature Scan (`src/sigscan.cpp`)**
   - Uses `dl_iterate_phdr` on Linux (or `GetModuleHandleA` on Windows) to locate the main `bedrock_server` executable's `.text` segment.
   - Scans `.text` for `DimensionManager::registerCustomDimension`.
2. **Vtable Resolution on `ServerLevel` (`src/dimension_hook.cpp`)**
   - Reads the underlying `::Level *` handle from `EndstoneLevel` (`+0x10`).
   - Resolves `Level::getDimensionManager()` and `Level::getOrCreateDimension(DimensionType)` from the `ServerLevel` vtable.
3. **64-Byte `DimensionDefinition` Construction**
   - Allocates a `DimensionDefinitionGroup::DimensionDefinition` struct (`min_y = -64`, `max_y = 384`, `sea_level = 128`, `has_weather = 0`, `generator_type`: `5` = Void, `2` = Flat, `1` = Overworld, `biome = "minecraft:ocean"`).
   - Calls `DimensionManager::registerCustomDimension(dm, name.data(), name.size(), &def)` to allocate or reuse the dimension ID in `LevelStorage`.
   - Calls `Level::getOrCreateDimension(dim_id)` so `EndstoneLevel` automatically wraps the new `Dimension` into `server.level.dimensions`.

## 2. World Management & Persistence (`endstone-eldia-worlds`)

- All created worlds are persisted in `plugins/eldia_worlds/worlds.json`.
- On every server startup (`on_enable`), `EldiaWorlds` re-registers every saved custom dimension through `eldia_worlds_native` (`/nativeworld create <name> <type>`), ensuring player builds and terrain inside custom dimensions survive restarts.
- If `eldia_worlds_native` is not loaded (for example, on platforms without a native signature), `EldiaWorlds` falls back to coordinate-isolated virtual zones with chunk-level Void/Flat generation via `ChunkLoadEvent`.
