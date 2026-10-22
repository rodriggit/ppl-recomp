# Building

You need your own ROM of the game (USA release, NPNE rev 0). Nothing from the game is
in this repository; everything game-derived is generated into `build/`.

## Layout

The pinned upstream checkouts (docs/versions.md) sit next to this repository:

```
repos/
  pokemonpuzzleleague/  this repository
  N64Recomp/            a13e5cf, built: N64Recomp.exe, RSPRecomp.exe
  N64ModernRuntime/     df7e820, with submodules
  rt64/                 23cab60, with submodules and mt64-recomp's RT64 patches (docs/versions.md)
```

Other locations work too: pass `-DPPL_RUNTIME_DIR=...` and `-DPPL_RT64_DIR=...` to CMake.

## Requirements (Windows)

- Visual Studio with "Desktop development with C++" and **C++ Clang tools for
  Windows** (the runtime and the generated code need clang-cl).
- A native CMake first in `PATH`.
- Python 3.11+ with a virtual environment in `.venv` (`pip install rabbitizer==1.16.2 spimdisasm==1.42.4`;
  the asset tools `tools/portrait.py` also need `pillow==13.0.0 numpy==2.5.4`).

## 1. Generate the recompiler inputs

From `tools/`:

```
..\.venv\Scripts\python.exe gen_config.py <ROM> ..\build
```

This writes `build/syms.toml`, `build/recomp.toml` and `build/aspMain.toml`. The
recompilers read the ROM itself (the code is stored uncompressed).

Optional check (must list nothing under "rejects" and only functions the port defines):

```
..\.venv\Scripts\python.exe symcheck.py <ROM> libultra_names.txt ..\..\N64Recomp\src\symbol_lists.cpp ..\..\N64ModernRuntime\librecomp ..\..\N64ModernRuntime\ultramodern ..\port
```

## 2. Recompile

Delete `build/RecompiledFuncs` if it exists, then from `build/`:

```
<repos>\N64Recomp\cmake-build\Release\N64Recomp.exe recomp.toml
<repos>\N64Recomp\cmake-build\Release\RSPRecomp.exe aspMain.toml
```

Output: `build/RecompiledFuncs/` and `build/rsp/aspMain.cpp`.

## 3. Build the executable

From this repository:

```
cmake -S . -B cmake-build -G "Visual Studio 18 2026" -A x64 -T ClangCL
cmake --build cmake-build --config RelWithDebInfo --target PPLRecomp
```

CMake downloads the SDL2 2.30.3 development package from the official SDL releases.
RelWithDebInfo keeps a console with the runtime's logs; Release has none.

## 4. Run

The first time, pass the ROM (or drop it onto the executable):

```
.\cmake-build\RelWithDebInfo\PPLRecomp.exe <ROM>
```

The runtime keeps a copy of the ROM and the FlashRAM save in
`%APPDATA%\ppl-recomp\PPLRecomp\`, so later runs need no argument.

Controls and graphics options are in `config.ini`, written with the defaults and the
description of every option on the first run: next to the executable, or in
`%APPDATA%\ppl-recomp\PPLRecomp\` if that folder isn't writable. Default controls
(keyboard = controller 1, together with the first gamepad): arrows = stick, X = A, C = B,
Z = Z, A = L, S = R, Return = Start, IJKL = C buttons, TFGH = D-pad.

F11 or Alt+Return toggles fullscreen. For diagnosis: `PPL_RT64_DEV=1` turns on RT64's
developer mode (F1 inspector), `PPL_TRACE=1` prints the RSP tasks and the VI state once
per second, F9 dumps the next four display lists and the RDRAM, and
`PPL_DUMP_OFFSCREEN=1` the first ones that draw outside the screen.
