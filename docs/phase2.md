# Phase 2 — N64Recomp configuration and first recompile

## Generating the inputs
```
cd tools
..\.venv\Scripts\python.exe gen_config.py <ROM> ..\build
```
writes `build/syms.toml` (the 1316 functions of `funcsplit.py`, named from
`libultra_names.txt`) and `build/recomp.toml`. The code is stored uncompressed
(phase 0), so N64Recomp reads the original ROM, with no synthetic ROM holding the
decompressed code as in [mt64-recomp](https://github.com/rodriggit/mt64-recomp), the
earlier port this one follows.

- Entry point 0x80000400: clears bss, sets sp to 0x801AD9A0 and leaves with
  `jr $t2` to the game's startup (0x80000520). That `jr` is patched into
  `j 0x80000520`, a direct tail call (N64Recomp names the entry function
  `recomp_entrypoint` before applying patches).
- Stubbed (empty body; N64Recomp doesn't analyze them): __osError (see phase 1),
  and seven functions of the unreachable SP debugger that are called by the rest
  of it: the only callers of __osSpRawReadIo / __osSpRawWriteIo (0x800A7BE0,
  0x800A7C30, 0x800AA258) and the ones that touch SP registers (0x800AA9B0,
  0x800AA9D4, 0x800AAA18, 0x800AAA2C).
- Ignored (not generated at all, so nothing generated may call them):
  0x800914F0 and 0x800A33E0 (PI raw DMA, referenced only by osCreatePiManager),
  0x800A1EB0 (only from osInitialize), and the RDB receive chain only reached
  from __osException: 0x800A3F24 → 0x800A3E34 → 0x800A3CF8 / 0x800A3DBC.

`symcheck.py` reads both lists from `gen_config.py`, so after a change it shows
what is left. It now also lists the calls from unreachable code that would not
link; that is why the debugger functions are stubs instead of ignored.

## Recompiling
```
cd build
<repos>\N64Recomp\cmake-build\Release\N64Recomp.exe recomp.toml
```
(N64Recomp a13e5cf.) Result: 1189 functions in `build/RecompiledFuncs/`, no
errors. The log only reports tail calls: the patched entry, three indirect ones
(`jr` on a register in 0x80007E68, 0x80008EA4, 0x8004DCC0) and four between
0x800AF7F0, 0x800AF960 and its neighbours.

The generated code calls one function that neither it nor the runtime defines:
**osViGetCurrentLine_recomp**, which the port must provide.

## Next
- The port: CMake project on N64ModernRuntime and RT64 as in mt64-recomp, the
  game entry, and osViGetCurrentLine_recomp. Its 20 callers wait for the
  vertical retrace in pairs (`while (line != 0)`, then `while (line == 0)`), so
  returning 0 and then a non-zero value, with a wait for the next VI in between,
  keeps the pacing.
- RSP microcode for RSPRecomp: aspMain (ROM 0xB0690, text 0xE20) and the HVQM2
  decoder (task type 7, ROM 0xB14B0).
