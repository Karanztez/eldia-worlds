# Building & Updating Signatures

## Automated Builds (GitHub Actions)

The workflow in [build.yml](../.github/workflows/build.yml) builds all three targets on every push and publishes a GitHub Release on `v*` tags:

1. `python-wheel` — Runs `ruff check`, `pytest`, and builds `endstone_eldia_worlds-*.whl`.
2. `native-linux` — Installs Clang 18 + `libc++-18-dev` on `ubuntu-22.04` and builds `endstone_eldia_worlds_native.so`.
3. `native-windows` — Uses MSVC x64 + Ninja on `windows-latest` to build `endstone_eldia_worlds_native.dll`.

## Local Development

### Python Plugin

```bash
pip install -e ".[dev]"
ruff check src tests tools
pytest -v
python -m build --wheel --outdir dist
```

### Native C++ Plugin (Linux)

Endstone requires Clang 18+ with LLVM `libc++` on Linux:

```bash
sudo apt-get install -y clang-18 libc++-18-dev libc++abi-18-dev cmake ninja-build git
CC=clang-18 CXX=clang++-18 cmake -S native -B build-linux -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build-linux
```

## Scanning New BDS Releases

When a new `bedrock_server` binary is released, run the scanner in [scan_bds_dimensions.py](../tools/scan_bds_dimensions.py) to verify or update the signature in `native/src/dimension_hook.cpp`:

```bash
python tools/scan_bds_dimensions.py /path/to/bedrock_server
```
