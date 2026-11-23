# eldia_worlds_native (C++ Native Plugin)

Native C++ Endstone plugin that unlocks runtime `CustomDimension` (`Dimension.Type.CUSTOM = 999`) creation on Minecraft Bedrock Dedicated Server (`1.26.60+`).

## Reverse-Engineered Internals (Linux ELF64 `bedrock_server`)

- **`DimensionManager::registerCustomDimension`**:
  - Signature: `55 41 57 41 56 41 54 53 49 89 CC 48 89 D3 49 89 F6 49 89 FF 48 8B 7F 08 E8`
  - Allocates or retrieves the persisted `DimensionType` ID in `LevelStorage`, registers a 64-byte `DimensionDefinitionGroup::DimensionDefinition` (`generator_type = 5` for `VoidGenerator`, `2` for `FlatWorldGenerator`, `1` for `OverworldGenerator`), and registers the `CustomDimension` factory lambda via `DimensionManager::_registerCustomDimensionWithFactory`.
- **`ServerLevel` Vtable Resolution**:
  - Dynamically scans `ServerLevel`'s vtable for `getDimensionManager` (`48 8B 87 ? ? 00 00 48 8B 40 18 C3`) and `getOrCreateDimension` (`48 8B 86 ? ? 00 00 48 8B 70 18 E9`).
  - Calling `getOrCreateDimension` triggers `DimensionManager::getOnNewDimensionCreatedConnector()`, which `EndstoneLevel` listens to and wraps into `server.level.dimensions`.

## Build on Linux (Clang 18 + `libc++`)

```bash
CC=clang-18 CXX=clang++-18 cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```
