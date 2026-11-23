# eldia-worlds-native

Native C++ Endstone plugin scaffold for runtime signature scanning and `DimensionManager` hooking on Bedrock Dedicated Server (`bedrock_server.exe` / `bedrock_server`).

## Discovered Targets (`bedrock_server.exe`)

- `ServerLevel.cpp` `LEA` xref: `RVA 0x009505dd`
- `DimensionManager::_registerCustomDimensionWithFactory` lambda RTTI: `TD_RVA 0x0d32ad30`
- `DimensionManager::serverInitialize` lambda RTTI: `TD_RVA 0x0d32aca0`
- `ScriptCustomDimensionManager::registerCustomDimension` string: `RVA 0x0b117ca8`

## Build (Windows x64 with MSVC + Ninja)

```bat
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

## Build (Linux x86_64 for Debian VPS)

```bash
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Copy the resulting `.dll` (Windows) or `.so` (Linux) into `bedrock_server/plugins/`.

## Commands

- `/nativeworld status` — Show loaded `bedrock_server` base address and hook status
- `/nativeworld scan` — Run live in-memory byte-signature scan on `bedrock_server`
- `/nativeworld probe <rva_hex>` — Inspect bytes at a given RVA relative to `bedrock_server` base
