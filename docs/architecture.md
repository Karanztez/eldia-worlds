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

## 3. Reverse-Engineering Map (`bedrock_server` 1.26.60+)

Read-only static analysis of `bedrock_server` (Linux ELF64) and `bedrock_server.exe` (Windows PE64) using [scan_bds_dimensions.py](../tools/scan_bds_dimensions.py) verified the following layout and vtable offsets:

### Linux ELF64 (`bedrock_server`)

- **Stripping status**: `.symtab` is stripped; `.dynsym` contains 390 entries, while RTTI typeinfo strings (`_ZTS...`) and `.rela.dyn` relocations remain intact in `.rodata` / `.data.rel.ro`.
- **`ServerLevel` vtable (`0x0f9b8be0`, base `Level` `0x0fbc5bd8`)**:
  - `slot[6]` (`+0x030`, `0x0ce2d1d0`): `Level::getOrCreateDimension(...)`
  - `slot[7]` (`+0x038`, `0x0ce2d1e0`): `Level::getDimension(...)`
  - `slot[11]` (`+0x058`, `0x0ce28230`): `Level::getDimensionManager()` (`this + 0x2f8 -> +0x18`)
  - `slot[12]` (`+0x060`, `0x0ce2d1c0`): `Level::getDimensionManager() const`
  - `slot[94]` (`+0x2f0`, `0x0ce222d0`): `Level::getPacketSender()` (`this + 0x2e0`)
- **`DimensionManager` & `DimensionDataPacket`**:
  - `DimensionManager::registerCustomDimension` (`0x0ce78dc0`) and `_registerCustomDimensionWithFactory` (`0x0ce79830`).
  - `DimensionManager + 0x38 / +0x40` holds the `std::map<std::string, DimensionDefinitionGroup::DimensionDefinition>` RB-tree.
  - `DimensionDataPacket` vtable (`0x0f8eebc0`, `getName()` at `0x086c7b50`) is populated from that map and broadcast/sent at `0x0694d620..0x0694d840` and `0x08ccfe50..0x08cd0080` via `PacketSender` (`0x08541ba0`).
- **`DBStorage` (`0x0fc419e8`) & `DBChunkStorage` (`0x0fc41860`)**:
  - `DBStorage` `slot[11]` (`0x0d728990`, `saveData` single key), `slot[12]` (`0x0d728dc0`, `saveData` batch), `slot[13]` (`0x0d7296d0`, `deleteData`).
  - `DBStorage::saveData` takes a hidden `sret` pointer (`rdi`) for `Bedrock::Threading::IAsyncResult<void>`, `this` in `rsi`, `std::string_view`, `std::string&&`, and `DBHelpers::Category`.

### Windows PE64 (`bedrock_server.exe`)

- Compiled without RTTI (`/GR-`, no `.?AV...` `TypeDescriptor` names).
- `ServerLevel` vtable pointers reside in `.rdata` at RVAs `0x0adfa1e0..0x0adfa1e8` (base `Level` `0x0adf3c80`):
  - `getDimensionManager`: `RVA 0x007b4350` (`48 8B 81 20 03 00 00 48 8B 40 18 C3`)
  - `getOrCreateDimension`: `RVA 0x007ba1f0` -> jumps to `DimensionManager::getOrCreateDimension` at `RVA 0x00e1bbc0` (nearby methods in `0x00e1aea0..0x00e1d0c3`).

### Native Safety Boundary

Runtime `DBStorage::saveData` calls and manual `DimensionDataPacket` stack construction are intentionally omitted from `eldia_worlds_native` until both the `ServerLevel::getLevelStorage()` vtable slot and the full `IAsyncResult<void>` / `DimensionDataPacket` struct ABIs are verified end-to-end, preventing segfaults on stripped servers. Imported worlds therefore stream safely via [importer.py](../src/eldia_worlds/importer.py) or can be merged offline into `db/` when the server is stopped.
