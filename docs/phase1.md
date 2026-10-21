# Phase 1 — Files, loader and libultra

## File archive
ROM 0x9BDB40–0x17C2A80: one archive, read with `tools/filetable.py ROM [--list]`.
- u32 count (0x1D1 = 465), then `count` entries of 0x18 bytes:
  `u32 size, u32 offset (from the archive start), char name[16]`.
- The first file is at +0x2B9C = 4 + 465 × 0x18. Files follow each other at
  2-byte alignment.

| Type | Files | Bytes | Header | What |
|---|---|---|---|---|
| .BIF | 440 | 8,261,807 | "3FIB" | images |
| .HVQM | 9 | 6,223,048 | "HVQM2 1.0" | videos (INTRO, EYECATCH, MEWINTRO, GAMEEND, ...) |
| .SBF | 15 | 178,518 | "SBF1" | screens/scenes (TITLE, MENU, CREDITS, ...) |
| .RDF | 1 | 25,606 | — | RIPPLE.RDF |

All data: nothing in the archive is code. The high-entropy areas from phase 0 are
mostly the HVQM2 videos (INTRO.HVQM alone is 0x2753D0 bytes at 0xE3E294).

Still unknown: ROM 0xCA4A0–0x9BDB40 (~9 MB between the main segment and the
archive). The audio banks follow the archive (0x17C2A80–0x1FCE670).

## Loader
- func_8001C8B0, file open by name: on first use copies 0x3800 bytes of the
  directory from ROM 0x9BDB40 to RAM 0x8018ABB0, then compares the name with
  each entry and fills {size, ROM address}.
- func_80001CAC(rom, ram, size): ROM read in 0x4000-byte chunks:
  osInvalDCache(ram, n), osPiStartDma(mb, 0, OS_READ, rom, ram, n, mq),
  osRecvMesg(mq, NULL, OS_MESG_BLOCK).

There are no code overlays (phase 0), so the loader needs **no N64Recomp hook**:
every load is data. To confirm at runtime: the runtime stops on any call to a
function that isn't in the recompiled code.

## libultra
libultra and the other libraries (libaudio, the HVQM2 library, ...) seem to be in
0x8008C000–0x800AF9C0: 336 of the 1055 jal targets of the main segment
(`tools/jaltargets.py ROM 8008C000 800AF9C0`). Named so far, from how they're called:

| Address | Function | Evidence |
|---|---|---|
| 0x800904A8–0x800905D4 | osFlashInit (and neighbours) | PI handle type 8, base 0xA8000000, maker ID 0x00C2 |
| 0x800924B0 | osPiStartDma | 7 arguments in libultra's order, called by the ROM read |
| 0x80099800 | osInvalDCache | (addr, size) before every DMA; 137 calls |
| 0x8009DB60 | osCreateMesgQueue | (mq, msgs, count) |
| 0x8009DDC0 | osRecvMesg | (mq, msg, flag), waits for the end of the DMA |
| 0x8009DF70 | osSendMesg | (mq, msg, flag), sends the RSP tasks |

The full list, about 120 functions with the evidence for each, is
`tools/libultra_names.txt`. It was built with `tools/funcinfo.py` (per function:
hardware and cop0 registers, callees, callers) and `tools/hwscan.py` (every lui
of an uncached or hardware address), without a decompilation as reference.

### Hardware access outside what the runtime replaces
`hwscan.py` over the whole main segment: game code (below 0x8008C000) builds no
hardware addresses; its 0xB9xx/0xBAxx/0xC000 `lui`s are float constants and
0xAAAA/0xABCD are magic numbers. In the libraries, besides the functions named
for N64Recomp:
- 0x800A0BB0 returns VI_CURRENT (0xA4400010) as is: osViGetCurrentLine. Its 20
  callers come in pairs (wait while != 0, then while == 0): waits for the vertical
  retrace. Probably not in N64Recomp's lists: the port will have to implement it,
  alternating values or waiting for the runtime's next VI to keep the pacing.
- 0x8009CD40 (__osRdbSend) writes the RDB port at 0xC0000000 (debug output to a
  development kit) when __osRdbWriteOK (0x800BE9C0) is set, and it starts at 1.
  The game calls osSyncPrintf (0x8009D620, 52 calls), whose output routine ends
  there: on a console the write goes nowhere, in the port it would fault.
  __osRdbSend is in N64Recomp's reimplemented list, and the runtime's version
  writes the buffer to stdout and returns its size, so the game's debug output
  ends up on the console.
  0x800A3CF8 and 0x800A3DBC also use the port, reached only from __osException,
  which the runtime replaces.
- 0x800A1F70 is __osError, the argument-check error path of the debug libultra:
  72 library callers, none in game code. It jumps through __osErrorHandler
  (0x800C0D94) or, when the PARTNER flag 0x801AB62C is set, through 0x800C0FD8.
  Neither pointer is NULL: the data holds 0x800A1FC0, a default handler that logs
  the error (0x800A5960) and flushes the log through __osRdbSend (0x800A5748),
  and 0x800A23C0, a PARTNER handler. The audio library reaches __osError
  (0x80097F60), and in the port the flush would wait forever in osRecvMesg for an
  RDB event, so __osError must be **stubbed** in the N64Recomp config.
- 0x800A7BE0–0x800AB9F8: debugger code on the SP registers (rmon-like). Its thread
  set-up (0x800AA3B0) is never referenced and its command table (0x800C1140) is
  read only by its own dispatcher: unreachable.

## Function boundaries
`tools/funcsplit.py ROM tools/libultra_names.txt [--out syms.toml]` splits the
code into 1316 functions: the entry and 1055 jal targets, plus 260 starts found
from code pointers (`lui` + `addiu`/`ori`, words in data that aren't jump tables)
or from a `addiu sp, sp, -N` prologue, accepted only right after a `jr ra` + delay
slot or an `eret` (no delay slot). That finds viMgrMain, the `...Through` flash
variants, the exception code and __osCleanupThread. Every function must end in a
jump, and every branch must stay inside its function or reach a function start
(N64Recomp turns that into a tail call; `jr` on another register becomes an
indirect tail call).

Six problems remain, none in code the port runs: __osException, send_mesg and
__osCleanupThread (one assembly file whose pieces jump into each other; N64Recomp
ignores them all) and 0x800AAA18 in the SP debugger, which shares a tail with
0x800AA9D4.

## Check against N64Recomp and the runtime
`tools/symcheck.py ROM tools/libultra_names.txt N64Recomp/src/symbol_lists.cpp
N64ModernRuntime/librecomp N64ModernRuntime/ultramodern [PORT_DIR] [--why ADDR]`
(N64Recomp a13e5cf, the pinned commit). A function is recompiled unless its name
is in N64Recomp's reimplemented or ignored list. Reachability starts at the entry
point and follows calls, tail calls and code addresses out of recompiled functions
only; a table of code pointers in data counts from the start unless code addresses
it, and then only once that code is reachable. `--why ADDR` prints the chain.

Result: 1196 functions recompiled (172 of them unreachable), 120 replaced or
ignored.
- Reachable recompiled code: no split problems, no instructions N64Recomp rejects
  (cop0 other than Status, eret, cache, tlb*, sync), no hardware or RDB-port
  accesses.
- Unreachable but rejected or touching hardware, so to be listed as **ignored** in
  the config (N64Recomp compiles every function it is given): 0x800914F0 and
  0x800A33E0 (PI raw DMA, referenced only by the PI manager), 0x800A1EB0 (PI
  domains, only from osInitialize), 0x800A3CF8 and 0x800A3DBC (RDB port, only
  from __osException), 0x800AA9B0, 0x800AA9D4, 0x800AAA18, 0x800AAA2C (SP
  debugger).
- Ignored by N64Recomp, called by recompiled code, and not defined by the
  runtime: only **osViGetCurrentLine** (20 calls, the retrace waits above). The
  port must define osViGetCurrentLine_recomp.
- Names in N64Recomp's reimplemented list that we don't have: EEPROM, Controller
  Pak, rumble and voice (hardware the game doesn't use), the `__osInitialize_*`
  and `__checkHardware_*` debug set-ups (only reachable from osInitialize), and
  the IDO 64-bit helpers (`__ll_div`, `__ull_rem`, ...): the only 64-bit
  instructions in the segment are in __osException, so the game has none.
  osGetThreadId, osStopTimer, osSetTime, osPiGetStatus and osPiReadIo are not
  identified. Any copy would be harmless: no reachable function touches hardware,
  and the only unnamed functions that disable interrupts (0x8009D030,
  0x800A5748, 0x800A5960) are RDB/log code that the port never reaches.

Added to the name list in this step: osContReset and __osPackResetData,
osContGetQuery, osContSetCh, osWritebackDCacheAll, __osError, and the exception
code (__osExceptionPreamble 0x800A2490, __osException moved from 0x800A20E0, which
is PARTNER output code, to 0x800A2564, send_mesg, __osCleanupThread).

## Next (phase 2)
- First N64Recomp configuration: `funcsplit.py --out` for the symbol file, the
  stub (__osError) and the ignored list above.
- The port's osViGetCurrentLine_recomp.
- First compile; check the result against this list.
