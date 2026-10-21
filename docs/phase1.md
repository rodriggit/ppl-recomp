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

Next: name the rest (threads, VI, SI/controllers, timers, the FlashRAM
functions, the RSP task functions) from these anchors, by call graph and by the
hardware registers each function touches, without a decompilation as reference.
