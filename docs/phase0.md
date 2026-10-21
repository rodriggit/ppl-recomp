# Phase 0 — ROM survey (Pokémon Puzzle League USA, NPNE rev 0)

ROM: `source/Pokemon Puzzle League (USA).z64` (not tracked), big-endian, 32 MiB,
internal name "PUZZLE LEAGUE N64", header CRC 19C553A7 A70F4B52,
SHA1 8173866fc8c7652abd44c48efcab85441c6806a1 (not yet checked against a dump database).

Tools used: `tools/romdis.py` (disassemble, `--rsp` for RSP code), `tools/survey.py`
(signatures, strings, entropy), `tools/codescan.py` (code-looking blocks),
`tools/luiscan.py`, `tools/ptrscan.py` (address constants), `tools/rspscan.py`,
`tools/rspsplit.py` (RSP code area).

## Boot
- IPL3 copies ROM 0x1000 (1 MiB) to 0x80000400 and jumps there.
- 0x80000400: clears bss 0x800C98A0–0x80220F50 (0x1576B0 bytes), sp = 0x801AD9A0,
  jumps to 0x80000520 (game startup).
- 0x80000450 clears 2 × 0x25800 bytes at 0x803B5000 and 0x803DA800:
  two 320×240 16-bit framebuffers at the top of 4 MiB.
- No decompressor at boot: the main segment (code + data, 0xC94A0 bytes,
  ROM 0x1000–0xCA4A0) fits in the IPL3 copy and is stored uncompressed.

## Code
All CPU code is in the main segment: ROM 0x1000–~0xB05C0
(vram 0x80000400–~0x800AF9C0), with every `jal` inside 0x800xxxxx.
`codescan.py` finds no code anywhere else in the ROM: **no overlays and no
compressed code** (unlike Mario Tennis). To confirm while recompiling: nothing
is loaded into RAM and executed later (look at the DMA destinations).

## RSP microcode
RSP code area ROM 0xB05C0–~0xB4700, directly after the CPU code. Each start is
the `ucode` pointer the game stores in an OSTask (setup code at 0x80001860,
0x8003FB90 and 0x8008FED0).

| ROM | VRAM | Text size | Data (ROM) | What |
|---|---|---|---|---|
| 0xB05C0 | 0x800AF9C0 | 0xD0 | — | rspboot |
| 0xB0690 | 0x800AFA90 | 0xE20 | 0xC1FF0 (0x2C0) | aspMain (audio): the audio manager at 0x80090118 passes these two to the task builder at 0x8008FED0 |
| 0xB14B0 | 0x800B08B0 | 0x5D0 | 0xC22B0 (0x70) | task type 7: probably the HVQM2 video decoder |
| 0xB1A80 | 0x800B0E80 | 0x1430 | 0xC3000 (0x800) | F3DEX 1.23 (output buffer 0x2000: FIFO) |
| 0xB2EB0 | 0x800B22B0 | 0x17D8 (0x17F0 padded) | 0xC3800 (0x800) | S2DEX 1.07 |

Strings: "RSP Gfx ucode F3DEX 1.23 Yoshitaka Yasumoto Nintendo." at ROM 0xC32B0,
"RSP Gfx ucode S2DEX 1.07 ..." at ROM 0xC3939.

## Video
File names `INTRO.HVQM` and `EYECATCH.HVQM` in the main data: the intro is HVQM2
video. Its RSP decoder must be recompiled with RSPRecomp, or the decoding done
on the CPU.

## Save
**FlashRAM.** 0x800904A8–0x800905D4 is osFlashInit: a PI handle with type 8
(DEVICE_TYPE_FLASH), latency 5, pulse 0xC, page size 0xF, release 2, base
0xA8000000, and the Macronix maker ID 0x00C2. The FlashRAM commands
(0xD2 execute, 0xE1 status, 0x3C chip erase, 0x4B sector erase, 0xA5 load page)
are all in 0x80090600–0x80090D90.

## Audio
Nintendo libaudio: 26 bank pairs "N64 PtrTablesV2" / "N64 WaveTables",
ROM 0x17C2A80–0x1FCE670 (probably one per song).

## Data and compression
No Yay0/Yaz0/MIO0/PERS-SZP signatures. Entropy close to 8 bits/byte in
0xD00000–0x1240000: compressed data (graphics or the HVQM2 videos). The file names
in the main data (`TITLE.SBF`, `INTRO.HVQM`, ...) suggest a file table: to find in
phase 1.

## Risks for N64Recomp
1. HVQM2 video decoder (RSP task type 7): RSPRecomp or a CPU decoder.
2. S2DEX 1.07: check that RT64 supports it, besides F3DEX 1.23.
3. FlashRAM: N64ModernRuntime implements it (to check at the pinned commit).

## Next (phase 1)
- Confirm aspMain and the S2DEX text size.
- Find the file table and the loader (name → ROM offset), and the format of the
  compressed data.
- Name libultra's functions in main, so N64Recomp replaces them.
