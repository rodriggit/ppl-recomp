"""Summarize functions in a vram range to help identify library code.

funcinfo.py ROM vram_lo vram_hi [names.txt]
Function starts are the jal targets of the main segment. For each function:
size, number of callers, hardware register blocks touched (from lui 0xA4xx /
0xA0xx-style bases + offsets), cop0 registers moved, special instructions,
and the functions it calls (by name when names.txt gives one: "ADDR name").
"""
import sys
from collections import Counter, defaultdict

import rabbitizer

MAIN_ROM, MAIN_VRAM, CODE_END = 0x1000, 0x80000400, 0xB05C0
HW = {
    0xA400: "SP", 0xA404: "SPreg", 0xA408: "SPpc", 0xA410: "DPC", 0xA420: "DPS", 0xA430: "MI",
    0xA440: "VI", 0xA450: "AI", 0xA460: "PI", 0xA470: "RI", 0xA480: "SI", 0xA800: "dom2",
    0xBFC0: "PIF",
}


def word(rom, vram):
    i = vram - MAIN_VRAM + MAIN_ROM
    return int.from_bytes(rom[i:i + 4], "big")


def load_names(path):
    names = {}
    if path:
        for line in open(path, encoding="utf-8"):
            parts = line.split("#")[0].split()
            if len(parts) >= 2:
                names[int(parts[0], 16)] = parts[1]
    return names


def main():
    rom = open(sys.argv[1], "rb").read()
    lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
    names = load_names(sys.argv[4] if len(sys.argv) > 4 else None)

    callers = Counter()
    for v in range(MAIN_VRAM, MAIN_VRAM + CODE_END - MAIN_ROM, 4):
        w = word(rom, v)
        if w >> 26 == 3:
            callers[0x80000000 | (w & 0x3FFFFFF) << 2] += 1
    starts = sorted(set(callers) | set(names))
    starts = [s for s in starts if lo <= s < hi]

    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else hi
        hw, cop0, special, calls = set(), set(), set(), []
        luis = {}
        for v in range(start, end, 4):
            w = word(rom, v)
            opcode, rs, rt, imm = w >> 26, (w >> 21) & 0x1F, (w >> 16) & 0x1F, w & 0xFFFF
            if opcode == 0x0F:  # lui
                luis[rt] = imm
                if imm in (0xA800, 0xBFC0):
                    hw.add(HW[imm])
            elif opcode in (0x23, 0x2B, 0x09, 0x0D) and rs in luis and luis[rs] >> 8 in (0xA4, 0xB4):
                base = luis[rs] if luis[rs] >> 8 == 0xA4 else luis[rs] - 0x1000
                hw.add(f"{HW.get(base, hex(base))}+{imm:X}")
            elif opcode == 0x10:  # cop0
                ins = rabbitizer.Instruction(w, v)
                op = ins.getOpcodeName()
                if op in ("mfc0", "mtc0"):
                    cop0.add(f"{op[:2]}:{ins.disassemble().split()[-1]}")
                else:
                    special.add(op)
            elif opcode == 0x2F:
                special.add("cache")
            elif opcode == 0 and w & 0x3F in (0x0C, 0x0D, 0x0F):
                special.add({0x0C: "syscall", 0x0D: "break", 0x0F: "sync"}[w & 0x3F])
            elif opcode == 3:
                t = 0x80000000 | (w & 0x3FFFFFF) << 2
                calls.append(names.get(t, f"{t:08X}"))
        label = names.get(start, "")
        print(f"{start:08X} {label:24} size={end - start:5X} callers={callers[start]:3}"
              + (f"  hw={','.join(sorted(hw))}" if hw else "")
              + (f"  cop0={','.join(sorted(cop0))}" if cop0 else "")
              + (f"  {','.join(sorted(special))}" if special else "")
              + (f"  calls={','.join(dict.fromkeys(calls))}" if calls else ""))


if __name__ == "__main__":
    main()
